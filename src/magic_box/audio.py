"""Small subprocess-based audio player."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import queue
import random
import select
import shlex
import shutil
import subprocess
import threading
import time
from typing import Callable, Iterable

from .amp import AmpGate, NoopAmpGate
from .volume import DEFAULT_MAX_OUTPUT_VOLUME_PERCENT, effective_output_volume


LOGGER = logging.getLogger(__name__)
PLAYABLE_EXTENSIONS = {".mp3"}
CONTINUOUS_PCM_RATE = 48_000
CONTINUOUS_PCM_CHANNELS = 2
CONTINUOUS_PCM_SAMPLE_BYTES = 2
CONTINUOUS_PCM_CHUNK_SECONDS = 0.02
CONTINUOUS_PCM_PRIME_SECONDS = 0.2
CONTINUOUS_PCM_WRITE_TIMEOUT_SECONDS = 0.5
CONTINUOUS_PCM_PRIME_WRITE_TIMEOUT_SECONDS = 2.0
CONTINUOUS_PCM_WRITE_POLL_SECONDS = 0.05


class AudioInitializationError(RuntimeError):
    """Raised when a requested audio backend cannot start safely."""


class AudioRuntimeError(RuntimeError):
    """Raised when a running audio backend loses its only output sink."""


class _SinkWriteCancelled(Exception):
    """Internal control flow for a playback generation or shutdown change."""


class _SinkWriteTimedOut(TimeoutError):
    """Raised when a live sink stops accepting PCM within its deadline."""


class AudioPlayer:
    """Play one audio file at a time using an external command."""

    def __init__(
        self,
        command: str = "mpg123 -q",
        dry_run: bool = False,
        extensions: Iterable[str] = PLAYABLE_EXTENSIONS,
        volume_getter: Callable[[], int] | None = None,
        amp_gate: AmpGate | None = None,
        amp_unmute_delay: float = 0.12,
        amp_mute_delay: float = 0.05,
        mute_between_tracks: bool = False,
        use_mpg123_remote: bool = False,
        use_continuous_pcm: bool = False,
        sink_command: str | None = None,
        warmup_file: Path | None = None,
        use_remote_volume: bool | None = None,
        max_output_percent: int = DEFAULT_MAX_OUTPUT_VOLUME_PERCENT,
    ) -> None:
        self.command = command
        self.dry_run = dry_run
        self.extensions = {extension.lower() for extension in extensions}
        self.volume_getter = volume_getter
        self.max_output_percent = max_output_percent
        self.amp_gate = amp_gate or NoopAmpGate()
        self.amp_unmute_delay = max(amp_unmute_delay, 0)
        self.amp_mute_delay = max(amp_mute_delay, 0)
        self.mute_between_tracks = mute_between_tracks
        self.use_mpg123_remote = use_mpg123_remote and _is_mpg123_command(command)
        self.use_continuous_pcm = use_continuous_pcm and _is_fixed_pcm_decoder_command(command)
        if self.use_continuous_pcm and self.mute_between_tracks:
            LOGGER.warning("continuous PCM keeps the amp enabled; ignoring per-track amp muting")
            self.mute_between_tracks = False
        self.sink_command = sink_command or "aplay -q"
        self.warmup_file = warmup_file
        self.use_remote_volume = (not _uses_pulse_output(command)) if use_remote_volume is None else use_remote_volume
        self._current_process: subprocess.Popen[bytes] | None = None
        self._remote_process: subprocess.Popen[str] | None = None
        self._remote_is_playing = False
        self._sink_process: subprocess.Popen[bytes] | None = None
        self._sink_thread: threading.Thread | None = None
        self._sink_stop = threading.Event()
        self._sink_writes_enabled = threading.Event()
        self._sink_writes_enabled.set()
        self._sink_write_lock = threading.Lock()
        self._pcm_queue: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=16)
        self._playback_generation = 0
        self._decoder_active = False
        self._queued_pcm_bytes = 0
        self._audible_until = 0.0
        self._sink_failure: str | None = None
        self._pcm_bytes_per_second = (
            CONTINUOUS_PCM_RATE * CONTINUOUS_PCM_CHANNELS * CONTINUOUS_PCM_SAMPLE_BYTES
        )
        self._pcm_chunk_bytes = int(self._pcm_bytes_per_second * CONTINUOUS_PCM_CHUNK_SECONDS)
        self._silence_chunk = bytes(self._pcm_chunk_bytes)
        self._prime_silence = bytes(int(self._pcm_bytes_per_second * CONTINUOUS_PCM_PRIME_SECONDS))
        self._sequence_positions: dict[Path, int] = {}
        self._lock = threading.RLock()
        if use_continuous_pcm and not self.use_continuous_pcm:
            raise AudioInitializationError(
                "continuous PCM backend requires mpg123 raw stdout at fixed 48 kHz stereo s16: "
                f"{command}"
            )
        if self.use_continuous_pcm and not _is_fixed_pcm_sink_command(self.sink_command):
            raise AudioInitializationError(
                "continuous PCM backend requires one direct aplay plughw sink at fixed 48 kHz stereo S16: "
                f"{self.sink_command}"
            )
        if self.use_continuous_pcm and not self.dry_run:
            decoder_executable = shlex.split(self.command)[0]
            if shutil.which(decoder_executable) is None:
                raise AudioInitializationError(
                    f"continuous PCM decoder command is not installed: {decoder_executable}"
                )
        if use_mpg123_remote and not self.use_mpg123_remote:
            LOGGER.warning("mpg123 remote backend requested, but command is not mpg123: %s", command)
        if self.use_continuous_pcm and not self.dry_run:
            if not self._start_continuous_sink():
                raise AudioInitializationError("continuous PCM sink could not be started and primed")
            if not self.mute_between_tracks:
                self.amp_gate.unmute()
        elif self.use_mpg123_remote and not self.dry_run:
            self._start_remote()
            self._warm_up_remote()
            if not self.mute_between_tracks:
                self.amp_gate.unmute()
        elif not self.mute_between_tracks:
            self.amp_gate.unmute()

    def is_playing(self) -> bool:
        if self.use_continuous_pcm:
            with self._lock:
                process = self._current_process
                decoder_active = self._decoder_active
                queued_pcm_bytes = self._queued_pcm_bytes
                audible_until = self._audible_until
            return (
                decoder_active
                or (process is not None and process.poll() is None)
                or queued_pcm_bytes > 0
                or time.monotonic() < audible_until
            )
        if self.use_mpg123_remote:
            with self._lock:
                process = self._remote_process
                return process is not None and process.poll() is None and self._remote_is_playing

        with self._lock:
            process = self._current_process
        return process is not None and process.poll() is None

    def raise_if_unhealthy(self) -> None:
        if not self.use_continuous_pcm or self.dry_run:
            return
        with self._lock:
            failure = self._sink_failure
            sink = self._sink_process
        if failure:
            raise AudioRuntimeError(failure)
        if sink is None:
            message = "continuous PCM sink is not running"
            with self._lock:
                if not self._sink_failure:
                    self._sink_failure = message
                    self._playback_generation += 1
                    decoder = self._current_process
                    self._current_process = None
                    self._decoder_active = False
                    self._queued_pcm_bytes = 0
                    self._audible_until = 0.0
                else:
                    message = self._sink_failure
                    decoder = None
            self._sink_stop.set()
            self._sink_writes_enabled.clear()
            self._clear_pcm_queue()
            with self._sink_write_lock:
                pass
            self.amp_gate.mute()
            self._terminate_process(decoder)
            raise AudioRuntimeError(message)
        if sink.poll() is not None:
            message = "continuous PCM sink exited unexpectedly"
            self._handle_sink_failure(sink, message)
            raise AudioRuntimeError(message)

    def find_files(self, folder: Path) -> list[Path]:
        if not folder.exists() or not folder.is_dir():
            LOGGER.error("Audio folder does not exist: %s", folder)
            return []

        return sorted(
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in self.extensions
        )

    def play_folder(self, folder: Path, mode: str) -> bool:
        files = self.find_files(folder)
        if not files:
            LOGGER.warning("No playable audio files found in %s", folder)
            return False

        selected = self._select_file(folder, files, mode)
        return self.play_file(selected)

    def play_file(self, path: Path) -> bool:
        if self.dry_run:
            LOGGER.info("Dry-run audio: would play %s", path)
            return True

        if self.use_continuous_pcm:
            return self._continuous_play_file(path)

        if self.use_mpg123_remote:
            return self._remote_play_file(path)

        self.stop_current()
        args = shlex.split(self.command)
        if self.volume_getter is not None:
            args = _apply_mpg123_volume(args, self.volume_getter(), self.max_output_percent)
        args.append(str(path))
        try:
            if self.mute_between_tracks:
                self.amp_gate.mute()
            process = subprocess.Popen(args)
        except FileNotFoundError:
            LOGGER.error("Audio command not found: %s", args[0])
            LOGGER.error("Install mpg123 or rerun with --dry-run-audio while testing.")
            return False
        except OSError as exc:
            LOGGER.error("Could not start audio command %s: %s", args[0], exc)
            return False

        with self._lock:
            self._current_process = process
        if self.mute_between_tracks:
            self._unmute_after_start(process)
        self._watch_finish(process)
        return True

    def stop_current(self) -> None:
        if self.use_continuous_pcm:
            self._sink_writes_enabled.clear()
            try:
                with self._lock:
                    self._playback_generation += 1
                    process = self._current_process
                    self._current_process = None
                    self._decoder_active = False
                    self._queued_pcm_bytes = 0
                    self._audible_until = 0.0
                self._clear_pcm_queue()
                with self._sink_write_lock:
                    pass
            finally:
                with self._lock:
                    sink_healthy = not self._sink_failure
                if not self._sink_stop.is_set() and sink_healthy:
                    self._sink_writes_enabled.set()
            self._terminate_process(process)
            return

        if self.use_mpg123_remote:
            with self._lock:
                if not self._remote_is_playing:
                    return
                self._remote_is_playing = False
            self._send_remote("STOP")
            return

        with self._lock:
            process = self._current_process
        if process is None:
            return

        if process.poll() is None:
            LOGGER.debug("Stopping current audio process")
            if self.mute_between_tracks:
                self.amp_gate.mute()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        with self._lock:
            if self._current_process is process:
                self._current_process = None

    def close(self) -> None:
        if self.use_continuous_pcm:
            self._sink_stop.set()
            self._sink_writes_enabled.clear()
        self.stop_current()
        if self.use_continuous_pcm:
            self.amp_gate.mute()
        self._close_continuous_sink()
        self._close_remote()
        self.amp_gate.close()

    def _select_file(self, folder: Path, files: list[Path], mode: str) -> Path:
        if mode == "shuffle":
            return random.choice(files)

        if mode == "sequence":
            position = self._sequence_positions.get(folder, 0)
            selected = files[position % len(files)]
            self._sequence_positions[folder] = position + 1
            return selected

        return files[0]

    def _unmute_after_start(self, process: subprocess.Popen[bytes]) -> None:
        def unmute_if_current() -> None:
            with self._lock:
                is_current = self._current_process is process
            if is_current and process.poll() is None:
                self.amp_gate.unmute()

        timer = threading.Timer(self.amp_unmute_delay, unmute_if_current)
        timer.daemon = True
        timer.start()

    def _watch_finish(self, process: subprocess.Popen[bytes]) -> None:
        def wait_and_update() -> None:
            process.wait()
            if self.mute_between_tracks and self.amp_mute_delay:
                time.sleep(self.amp_mute_delay)
            with self._lock:
                is_current = self._current_process is process
                if is_current:
                    self._current_process = None
            if is_current and self.mute_between_tracks:
                self.amp_gate.mute()

        thread = threading.Thread(target=wait_and_update, daemon=True)
        thread.start()

    def _remote_play_file(self, path: Path) -> bool:
        if self.use_remote_volume and not self._send_remote(f"VOLUME {self._volume_percent():.3f}"):
            return False
        if not self.use_remote_volume and not self._send_remote("VOLUME 100"):
            return False
        if not self._send_remote(f"LOAD {path}"):
            return False
        with self._lock:
            self._remote_is_playing = True
        return True

    def _continuous_play_file(self, path: Path) -> bool:
        self.raise_if_unhealthy()

        self.stop_current()
        with self._lock:
            expected_generation = self._playback_generation
            expected_sink = self._sink_process
        args = shlex.split(self.command)
        if self.volume_getter is not None:
            args = _apply_mpg123_volume(args, self.volume_getter(), self.max_output_percent)
        try:
            with path.open("rb") as source:
                source_fd = source.fileno()
                decoder_args = [*args, f"/proc/self/fd/{source_fd}"]
                process = subprocess.Popen(
                    decoder_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    pass_fds=(source_fd,),
                )
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) and exc.filename == args[0]:
                LOGGER.error("Audio decoder command not found: %s", args[0])
            else:
                LOGGER.error("Could not open or decode the selected audio file: %s", exc.strerror or type(exc).__name__)
            return False
        unhealthy_message = ""
        cancelled = False
        dead_sink: subprocess.Popen[bytes] | None = None
        with self._lock:
            sink = self._sink_process
            if self._sink_failure:
                unhealthy_message = self._sink_failure
            elif self._playback_generation != expected_generation:
                cancelled = True
            elif sink is not expected_sink:
                unhealthy_message = "continuous PCM sink changed before decoder activation"
                dead_sink = sink
            elif sink is None:
                unhealthy_message = "continuous PCM sink disappeared before decoder activation"
            elif sink.poll() is not None:
                unhealthy_message = "continuous PCM sink exited before decoder activation"
                dead_sink = sink
            else:
                self._current_process = process
                self._decoder_active = True
        if cancelled:
            self._terminate_process(process)
            return False
        if unhealthy_message:
            if dead_sink is not None:
                self._handle_sink_failure(dead_sink, unhealthy_message)
            self._terminate_process(process)
            raise AudioRuntimeError(unhealthy_message)
        thread = threading.Thread(
            target=self._decode_into_sink,
            args=(process, expected_generation),
            daemon=True,
        )
        thread.start()
        return True

    def _start_continuous_sink(self) -> bool:
        if self.dry_run or self._sink_is_running():
            return True
        args = shlex.split(self.sink_command)
        if not args:
            LOGGER.error("Continuous PCM sink command is empty")
            return False
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except FileNotFoundError:
            LOGGER.error("Continuous PCM sink command not found: %s", args[0])
            return False
        except OSError as exc:
            LOGGER.error("Could not start continuous PCM sink %s: %s", args[0], exc)
            return False
        if process.stdin is None:
            self._terminate_process(process)
            LOGGER.error("Continuous PCM sink did not expose stdin")
            return False
        self._limit_sink_pipe(process)
        try:
            sink_fd = _prepare_nonblocking_sink_fd(process.stdin)
        except (AttributeError, OSError, ValueError) as exc:
            self._terminate_process(process)
            LOGGER.error(
                "Could not configure continuous PCM sink pipe: %s",
                getattr(exc, "strerror", None) or type(exc).__name__,
            )
            return False
        prime_started = time.monotonic()
        try:
            with self._sink_write_lock:
                _write_nonblocking_fd(
                    sink_fd,
                    self._prime_silence,
                    timeout=CONTINUOUS_PCM_PRIME_WRITE_TIMEOUT_SECONDS,
                    cancelled=lambda: False,
                )
        except (_SinkWriteTimedOut, BrokenPipeError, OSError) as exc:
            self._terminate_process(process)
            LOGGER.error(
                "Could not prime continuous PCM sink: %s",
                getattr(exc, "strerror", None) or type(exc).__name__,
            )
            return False
        if process.poll() is not None:
            self._terminate_process(process)
            LOGGER.error("Continuous PCM sink exited during startup")
            return False

        with self._lock:
            self._sink_process = process
            self._sink_failure = None
        self._sink_stop.clear()
        self._sink_thread = threading.Thread(target=self._feed_continuous_sink, daemon=True)
        self._sink_thread.start()
        prime_seconds = max(CONTINUOUS_PCM_PRIME_SECONDS, self.amp_unmute_delay)
        prime_remaining = prime_seconds - (time.monotonic() - prime_started)
        if prime_remaining > 0:
            time.sleep(prime_remaining)
        with self._lock:
            failed = self._sink_failure or self._sink_process is not process or process.poll() is not None
        if failed:
            self._handle_sink_failure(process, "Continuous PCM sink exited while priming")
            return False
        LOGGER.info("Started continuous PCM audio sink")
        return True

    def _sink_is_running(self) -> bool:
        with self._lock:
            process = self._sink_process
        return process is not None and process.poll() is None

    def _feed_continuous_sink(self) -> None:
        while not self._sink_stop.is_set():
            if not self._sink_writes_enabled.wait(CONTINUOUS_PCM_WRITE_POLL_SECONDS):
                continue
            try:
                generation, chunk = self._pcm_queue.get_nowait()
                is_audio = True
            except queue.Empty:
                generation = -1
                chunk = self._silence_chunk
                is_audio = False

            failure_process: subprocess.Popen[bytes] | None = None
            failure_message = ""
            stale = False
            cancelled = False
            with self._sink_write_lock:
                with self._lock:
                    process = self._sink_process
                    write_generation = self._playback_generation
                    if (
                        self._sink_stop.is_set()
                        or self._sink_failure
                        or not self._sink_writes_enabled.is_set()
                    ):
                        cancelled = True
                    elif is_audio and generation != write_generation:
                        stale = True
                    elif process is None or process.poll() is not None or process.stdin is None:
                        failure_process = process
                        failure_message = "Continuous PCM sink stopped unexpectedly"
                    else:
                        try:
                            sink_fd = process.stdin.fileno()
                        except (AttributeError, OSError, ValueError) as exc:
                            failure_process = process
                            failure_message = (
                                "Continuous PCM sink pipe became unavailable: "
                                f"{getattr(exc, 'strerror', None) or type(exc).__name__}"
                            )
                if not cancelled and not stale and not failure_message and process is not None:
                    try:
                        _write_nonblocking_fd(
                            sink_fd,
                            chunk,
                            timeout=CONTINUOUS_PCM_WRITE_TIMEOUT_SECONDS,
                            cancelled=lambda: self._sink_write_cancelled(
                                process,
                                write_generation,
                            ),
                        )
                    except _SinkWriteCancelled:
                        cancelled = True
                    except _SinkWriteTimedOut:
                        failure_process = process
                        failure_message = "Continuous PCM sink write timed out"
                    except (BrokenPipeError, OSError) as exc:
                        failure_process = process
                        failure_message = (
                            "Continuous PCM sink write failed: "
                            f"{getattr(exc, 'strerror', None) or type(exc).__name__}"
                        )
                    if is_audio and not failure_message and not cancelled:
                        with self._lock:
                            still_current = (
                                self._sink_process is process
                                and not self._sink_failure
                                and write_generation == self._playback_generation
                            )
                            if still_current:
                                self._queued_pcm_bytes = max(
                                    0,
                                    self._queued_pcm_bytes - len(chunk),
                                )
                        if still_current:
                            chunk_seconds = len(chunk) / self._pcm_bytes_per_second
                            with self._lock:
                                if (
                                    self._sink_process is process
                                    and write_generation == self._playback_generation
                                ):
                                    self._audible_until = max(
                                        self._audible_until,
                                        time.monotonic() + chunk_seconds + 0.2,
                                    )
            if stale or cancelled:
                continue
            if failure_message:
                if failure_process is not None:
                    self._handle_sink_failure(failure_process, failure_message)
                return

    def _sink_write_cancelled(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
    ) -> bool:
        if self._sink_stop.is_set() or not self._sink_writes_enabled.is_set():
            return True
        with self._lock:
            return bool(
                self._sink_failure
                or self._sink_process is not process
                or self._playback_generation != generation
            )

    def _decode_into_sink(self, process: subprocess.Popen[bytes], generation: int) -> None:
        stdout = process.stdout
        decoder_failure = ""
        if stdout is None:
            decoder_failure = "Audio decoder exposed no PCM output stream"
        try:
            pending = bytearray()
            if stdout is not None:
                while True:
                    data = stdout.read(self._pcm_chunk_bytes)
                    if not data:
                        break
                    pending.extend(data)
                    aligned = len(pending) - (
                        len(pending) % (CONTINUOUS_PCM_CHANNELS * CONTINUOUS_PCM_SAMPLE_BYTES)
                    )
                    if aligned:
                        chunk = bytes(pending[:aligned])
                        del pending[:aligned]
                        if not self._queue_pcm_chunk(generation, chunk):
                            return
                if pending:
                    frame_bytes = CONTINUOUS_PCM_CHANNELS * CONTINUOUS_PCM_SAMPLE_BYTES
                    pending.extend(bytes((-len(pending)) % frame_bytes))
                    if not self._queue_pcm_chunk(generation, bytes(pending)):
                        return
        except OSError as exc:
            decoder_failure = (
                "Audio decoder PCM read failed: "
                f"{_redacted_os_error(exc)}"
            )
        finally:
            exit_code: int | None = None
            try:
                exit_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    exit_code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    decoder_failure = decoder_failure or "Audio decoder cleanup timed out"
                except OSError as exc:
                    decoder_failure = decoder_failure or (
                        "Audio decoder cleanup failed: "
                        f"{_redacted_os_error(exc)}"
                    )
            except OSError as exc:
                decoder_failure = decoder_failure or (
                    "Audio decoder cleanup failed: "
                    f"{_redacted_os_error(exc)}"
                )
            with self._lock:
                report_failure = (
                    self._current_process is process
                    and generation == self._playback_generation
                )
                if self._current_process is process:
                    self._current_process = None
                    self._decoder_active = False
            if report_failure:
                if decoder_failure:
                    LOGGER.error("%s; selected audio ended early", decoder_failure)
                elif exit_code not in (None, 0):
                    LOGGER.error(
                        "Audio decoder exited with status %s; selected audio ended early",
                        exit_code,
                    )

    def _queue_pcm_chunk(self, generation: int, chunk: bytes) -> bool:
        while True:
            with self._lock:
                if generation != self._playback_generation:
                    return False
                try:
                    self._pcm_queue.put_nowait((generation, chunk))
                except queue.Full:
                    queued = False
                else:
                    self._queued_pcm_bytes += len(chunk)
                    queued = True
            if queued:
                return True
            if self._sink_stop.wait(0.02):
                return False

    def _clear_pcm_queue(self) -> None:
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _terminate_process(
        process: subprocess.Popen[bytes] | subprocess.Popen[str] | None,
        *,
        timeout: float = 2,
    ) -> None:
        if process is None:
            return
        if process.poll() is not None:
            try:
                process.wait(timeout=0)
            except (OSError, subprocess.TimeoutExpired):
                LOGGER.warning("Audio child could not be reaped after exit")
            return
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                LOGGER.error("Audio child did not exit after forced termination")

    def _limit_sink_pipe(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdin is None:
            return
        try:
            import fcntl

            requested = max(4096, self._pcm_chunk_bytes * 2)
            fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, requested)
        except (AttributeError, ImportError, OSError, ValueError):
            LOGGER.debug("Could not reduce continuous PCM pipe buffering")

    def _handle_sink_failure(self, process: subprocess.Popen[bytes], message: str) -> None:
        with self._lock:
            if self._sink_process is not process:
                return
            self._sink_process = None
            self._playback_generation += 1
            decoder = self._current_process
            self._current_process = None
            self._decoder_active = False
            self._queued_pcm_bytes = 0
            self._audible_until = 0.0
            self._sink_failure = message
        LOGGER.error("%s", message)
        self._sink_stop.set()
        self._sink_writes_enabled.clear()
        self._clear_pcm_queue()
        with self._sink_write_lock:
            pass
        self.amp_gate.mute()
        self._terminate_process(decoder)
        self._terminate_process(process)

    def _close_continuous_sink(self) -> None:
        self._sink_stop.set()
        self._sink_writes_enabled.clear()
        with self._sink_write_lock:
            pass
        with self._lock:
            process = self._sink_process
            self._sink_process = None
        self._terminate_process(process)
        thread = self._sink_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._sink_thread = None

    def _warm_up_remote(self) -> None:
        if self.warmup_file is None or not self.warmup_file.exists():
            return
        LOGGER.info("Warming audio output with %s", self.warmup_file)
        self._send_remote("SILENCE")
        self._send_remote("VOLUME 0")
        self._send_remote(f"LOAD {self.warmup_file}")

    def _volume_percent(self) -> float:
        if self.volume_getter is None:
            return float(min(max(self.max_output_percent, 0), 100))
        return effective_output_volume(self.volume_getter(), self.max_output_percent)

    def _start_remote(self) -> bool:
        with self._lock:
            if self._remote_process is not None and self._remote_process.poll() is None:
                return True

            args = _build_mpg123_remote_args(shlex.split(self.command))
            try:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except FileNotFoundError:
                LOGGER.error("Audio command not found: %s", args[0])
                return False
            except OSError as exc:
                LOGGER.error("Could not start mpg123 remote backend: %s", exc)
                return False

            self._remote_process = process
            self._remote_is_playing = False
            self._watch_remote_status(process)
            LOGGER.info("Started mpg123 remote audio backend")
            return True

    def _send_remote(self, command: str) -> bool:
        for attempt in range(2):
            if not self._start_remote():
                return False
            assert self._remote_process is not None
            assert self._remote_process.stdin is not None
            try:
                self._remote_process.stdin.write(f"{command}\n")
                self._remote_process.stdin.flush()
                return True
            except (BrokenPipeError, OSError) as exc:
                LOGGER.warning("mpg123 remote command failed: %s", exc)
                self._close_remote(force=True)
                if attempt:
                    return False
        return False

    def _close_remote(self, force: bool = False) -> None:
        with self._lock:
            process = self._remote_process
            self._remote_process = None
            self._remote_is_playing = False
        if process is None:
            return

        if process.poll() is None:
            try:
                if process.stdin is not None and not force:
                    process.stdin.write("QUIT\n")
                    process.stdin.flush()
                process.wait(timeout=1)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)

    def _watch_remote_status(self, process: subprocess.Popen[str]) -> None:
        def read_status() -> None:
            stdout = process.stdout
            if stdout is None:
                return
            try:
                for line in stdout:
                    playing = _mpg123_status_is_playing(line)
                    if playing is None:
                        continue
                    with self._lock:
                        if self._remote_process is process:
                            self._remote_is_playing = playing
            finally:
                with self._lock:
                    if self._remote_process is process:
                        self._remote_is_playing = False

        thread = threading.Thread(target=read_status, daemon=True)
        thread.start()


def _apply_mpg123_volume(
    args: list[str],
    volume_percent: int,
    max_output_percent: int = DEFAULT_MAX_OUTPUT_VOLUME_PERCENT,
) -> list[str]:
    if not args or Path(args[0]).name != "mpg123":
        return args

    scale = _mpg123_scalefactor(effective_output_volume(volume_percent, max_output_percent))
    cleaned: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "-f":
            index += 2
            continue
        if arg.startswith("-f") and arg != "-f":
            index += 1
            continue
        cleaned.append(arg)
        index += 1
    return [*cleaned, "-f", str(scale)]


def _build_mpg123_remote_args(args: list[str]) -> list[str]:
    cleaned = _remove_mpg123_scalefactor(args)
    cleaned = [arg for arg in cleaned if arg not in {"-R", "--remote", "--keep-open"}]
    return [*cleaned, "-R", "--keep-open"]


def _remove_mpg123_scalefactor(args: list[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "-f":
            index += 2
            continue
        if arg.startswith("-f") and arg != "-f":
            index += 1
            continue
        cleaned.append(arg)
        index += 1
    return cleaned


def _redacted_os_error(exc: OSError) -> str:
    if exc.errno is None:
        return type(exc).__name__
    return f"{type(exc).__name__} errno={exc.errno}"


def _prepare_nonblocking_sink_fd(stream: object) -> int:
    fd = stream.fileno()  # type: ignore[attr-defined]
    if not isinstance(fd, int) or fd < 0:
        raise OSError("PCM sink exposed an invalid pipe descriptor")
    os.set_blocking(fd, False)
    return fd


def _write_nonblocking_fd(
    fd: int,
    data: bytes,
    *,
    timeout: float,
    cancelled: Callable[[], bool],
) -> None:
    deadline = time.monotonic() + max(timeout, 0)
    offset = 0
    view = memoryview(data)
    while offset < len(view):
        if cancelled():
            raise _SinkWriteCancelled
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _SinkWriteTimedOut("PCM sink accepted no data before its deadline")
        try:
            _readable, writable, _exceptional = select.select(
                [],
                [fd],
                [],
                min(CONTINUOUS_PCM_WRITE_POLL_SECONDS, remaining),
            )
        except InterruptedError:
            continue
        except ValueError as exc:
            raise OSError("PCM sink pipe descriptor became invalid") from exc
        if not writable:
            continue
        if cancelled():
            raise _SinkWriteCancelled
        try:
            written = os.write(fd, view[offset:])
        except BlockingIOError:
            continue
        if written <= 0:
            raise BrokenPipeError("PCM sink accepted no data")
        offset += written
    if cancelled():
        raise _SinkWriteCancelled


def _is_mpg123_command(command: str) -> bool:
    args = shlex.split(command)
    return bool(args) and Path(args[0]).name == "mpg123"


def _is_fixed_pcm_decoder_command(command: str) -> bool:
    args = shlex.split(command)
    return bool(args) and Path(args[0]).name == "mpg123" and args[1:] == [
        "-q",
        "-s",
        "--rate",
        "48000",
        "--stereo",
        "-e",
        "s16",
    ]


def _is_fixed_pcm_sink_command(command: str) -> bool:
    args = shlex.split(command)
    if len(args) != 14 or Path(args[0]).name != "aplay":
        return False
    return (
        args[1:3] == ["-q", "-D"]
        and args[3].startswith("plughw:")
        and args[4:]
        == [
            "--file-type",
            "raw",
            "--format",
            "S16_LE",
            "--rate",
            "48000",
            "--channels",
            "2",
            "--buffer-time=100000",
            "--period-time=20000",
        ]
    )


def _uses_pulse_output(command: str) -> bool:
    args = shlex.split(command)
    return any(args[index] in {"-o", "--output"} and index + 1 < len(args) and args[index + 1] == "pulse" for index in range(len(args)))


def _mpg123_status_is_playing(line: str) -> bool | None:
    if not line.startswith("@P "):
        return None
    parts = line.strip().split()
    if len(parts) < 2:
        return None
    return parts[1] == "2"


def _mpg123_scalefactor(volume_percent: float) -> int:
    percent = min(max(volume_percent, 0), 100)
    return round(32768 * (percent / 100))
