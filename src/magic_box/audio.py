"""Small subprocess-based audio player."""

from __future__ import annotations

import logging
from pathlib import Path
import random
import shlex
import subprocess
import threading
import time
from typing import Callable, Iterable

from .amp import AmpGate, NoopAmpGate
from .volume import DEFAULT_MAX_OUTPUT_VOLUME_PERCENT, effective_output_volume


LOGGER = logging.getLogger(__name__)
PLAYABLE_EXTENSIONS = {".mp3"}


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
        self.warmup_file = warmup_file
        self.use_remote_volume = (not _uses_pulse_output(command)) if use_remote_volume is None else use_remote_volume
        self._current_process: subprocess.Popen[bytes] | None = None
        self._remote_process: subprocess.Popen[str] | None = None
        self._remote_is_playing = False
        self._sequence_positions: dict[Path, int] = {}
        self._lock = threading.RLock()
        if not self.mute_between_tracks:
            self.amp_gate.unmute()
        if use_mpg123_remote and not self.use_mpg123_remote:
            LOGGER.warning("mpg123 remote backend requested, but command is not mpg123: %s", command)
        if self.use_mpg123_remote and not self.dry_run:
            self._start_remote()
            self._warm_up_remote()

    def is_playing(self) -> bool:
        if self.use_mpg123_remote:
            with self._lock:
                process = self._remote_process
                return process is not None and process.poll() is None and self._remote_is_playing

        with self._lock:
            process = self._current_process
        return process is not None and process.poll() is None

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
        self.stop_current()
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


def _is_mpg123_command(command: str) -> bool:
    args = shlex.split(command)
    return bool(args) and Path(args[0]).name == "mpg123"


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
