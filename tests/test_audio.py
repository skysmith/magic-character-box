from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from magic_box.audio import (
    AudioInitializationError,
    AudioPlayer,
    AudioRuntimeError,
    _apply_mpg123_volume,
    _build_mpg123_remote_args,
    _is_fixed_pcm_decoder_command,
    _is_fixed_pcm_sink_command,
    _mpg123_status_is_playing,
    _prepare_nonblocking_sink_fd,
    _SinkWriteCancelled,
    _SinkWriteTimedOut,
    _uses_pulse_output,
    _write_nonblocking_fd,
)

TEST_SINK_COMMAND = (
    "aplay -q -D plughw:1,0 --file-type raw --format S16_LE --rate 48000 --channels 2 "
    "--buffer-time=100000 --period-time=20000"
)
_FAKE_PIPES: dict[int, object] = {}


def _fake_nonblocking_write(
    fd: int,
    data: bytes,
    *,
    timeout: float,
    cancelled: object,
) -> None:
    del timeout
    stream = _FAKE_PIPES[fd]
    offset = 0
    while offset < len(data):
        if cancelled():  # type: ignore[operator]
            raise _SinkWriteCancelled
        written = stream.write(data[offset:])  # type: ignore[attr-defined]
        if written is None or written <= 0:
            raise BrokenPipeError("fake sink accepted no data")
        offset += written
    if cancelled():  # type: ignore[operator]
        raise _SinkWriteCancelled


def _saturated_nonblocking_pipe() -> tuple[int, object]:
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    payload = bytes(65_536)
    while True:
        try:
            os.write(write_fd, payload)
        except BlockingIOError:
            break
    return read_fd, os.fdopen(write_fd, "wb", buffering=0)


def _lock_is_held(lock: threading.Lock) -> bool:
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
        return False
    return True


class AudioTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_patch = patch(
            "magic_box.audio._prepare_nonblocking_sink_fd",
            side_effect=lambda stream: stream.fileno(),
        )
        write_patch = patch(
            "magic_box.audio._write_nonblocking_fd",
            side_effect=_fake_nonblocking_write,
        )
        prepare_patch.start()
        write_patch.start()
        self.addCleanup(prepare_patch.stop)
        self.addCleanup(write_patch.stop)

    def test_finds_mp3_files_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "02-second.mp3").touch()
            (folder / "01-first.mp3").touch()
            (folder / "notes.txt").touch()

            player = AudioPlayer(dry_run=True)

            self.assertEqual(
                [path.name for path in player.find_files(folder)],
                ["01-first.mp3", "02-second.mp3"],
            )

    def test_sequence_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "01-first.mp3").touch()
            (folder / "02-second.mp3").touch()
            player = AudioPlayer(dry_run=True)

            self.assertTrue(player.play_folder(folder, "sequence"))
            self.assertTrue(player.play_folder(folder, "sequence"))

    def test_mpg123_volume_is_added_as_scalefactor(self) -> None:
        self.assertEqual(
            _apply_mpg123_volume(["mpg123", "-q", "-a", "plughw:1,0"], 50),
            ["mpg123", "-q", "-a", "plughw:1,0", "-f", "16384"],
        )

    def test_mpg123_volume_replaces_existing_scalefactor(self) -> None:
        self.assertEqual(
            _apply_mpg123_volume(["mpg123", "-q", "-f", "32768"], 25),
            ["mpg123", "-q", "-f", "8192"],
        )

    def test_mpg123_volume_honors_output_ceiling(self) -> None:
        self.assertEqual(
            _apply_mpg123_volume(["mpg123", "-q"], 100, max_output_percent=75),
            ["mpg123", "-q", "-f", "24576"],
        )

    def test_remote_volume_honors_output_ceiling(self) -> None:
        player = AudioPlayer(dry_run=True, volume_getter=lambda: 80, max_output_percent=75)

        self.assertEqual(player._volume_percent(), 60.0)

    def test_remote_args_keep_player_open_and_remove_scalefactor(self) -> None:
        self.assertEqual(
            _build_mpg123_remote_args(["mpg123", "-q", "-o", "pulse", "-f", "1000"]),
            ["mpg123", "-q", "-o", "pulse", "-R", "--keep-open"],
        )

    def test_mpg123_remote_status_detects_playing_state(self) -> None:
        self.assertIsNone(_mpg123_status_is_playing("@R MPG123"))
        self.assertTrue(_mpg123_status_is_playing("@P 2"))
        self.assertFalse(_mpg123_status_is_playing("@P 0"))

    def test_pulse_output_is_detected(self) -> None:
        self.assertTrue(_uses_pulse_output("mpg123 -q -o pulse"))
        self.assertFalse(_uses_pulse_output("mpg123 -q -a plughw:1,0"))

    def test_amp_gate_is_left_enabled_by_default(self) -> None:
        gate = _FakeAmpGate()

        AudioPlayer(dry_run=True, amp_gate=gate)

        self.assertEqual(gate.events, ["unmute"])

    def test_remote_stop_is_noop_when_idle(self) -> None:
        player = _RemoteCommandAudioPlayer()

        player.stop_current()

        self.assertEqual(player.commands, [])

    def test_remote_stop_still_interrupts_active_audio(self) -> None:
        player = _RemoteCommandAudioPlayer()
        player._remote_is_playing = True

        player.stop_current()

        self.assertEqual(player.commands, ["STOP"])
        self.assertFalse(player._remote_is_playing)

    def test_continuous_pcm_requires_fixed_raw_stdout_decoder(self) -> None:
        self.assertTrue(
            _is_fixed_pcm_decoder_command(
                "mpg123 -q -s --rate 48000 --stereo -e s16"
            )
        )
        self.assertTrue(
            _is_fixed_pcm_decoder_command(
                "/usr/bin/mpg123 -q -s --rate 48000 --stereo -e s16"
            )
        )

    def test_continuous_pcm_requires_one_fixed_direct_aplay_sink(self) -> None:
        self.assertTrue(_is_fixed_pcm_sink_command(TEST_SINK_COMMAND))
        invalid_sinks = (
            "pw-cat --playback --raw -",
            "aplay -q -D default --file-type raw --format S16_LE --rate 48000 --channels 2 --buffer-time=100000 --period-time=20000",
            "aplay -q -D plughw:1,0 --file-type raw --format S16_LE --rate 48000 --channels 2",
            f"{TEST_SINK_COMMAND} --rate 44100",
            f"{TEST_SINK_COMMAND} extra.raw",
            TEST_SINK_COMMAND.replace("--buffer-time=100000", "--buffer-time=500000"),
            TEST_SINK_COMMAND.replace("--period-time=20000", "--period-time=50000"),
        )
        for command in invalid_sinks:
            with self.subTest(command=command):
                self.assertFalse(_is_fixed_pcm_sink_command(command))
        self.assertFalse(
            _is_fixed_pcm_decoder_command(
                "mpg123 -q -o alsa -a plughw:CARD=MAX98357A,DEV=0 --rate 48000 --stereo -e s16"
            )
        )
        invalid_decoders = (
            "mpg123 -q -s -s --rate 48000 --stereo -e s16",
            "mpg123 -q --stdout --rate 48000 --stereo -e s16",
            "mpg123 -q -s --rate 44100 --stereo -e s16",
            "mpg123 -q -s --rate 48000 --stereo -e s32",
            "mpg123 -s -q --rate 48000 --stereo -e s16",
            "mpg123 -q -s --rate 48000 --stereo -e s16 story.mp3",
            "mpg123 -q -s --rate 48000 --stereo -e s16 -o alsa",
        )
        for command in invalid_decoders:
            with self.subTest(command=command):
                self.assertFalse(_is_fixed_pcm_decoder_command(command))

    def test_invalid_continuous_sink_fails_closed(self) -> None:
        with self.assertRaises(AudioInitializationError):
            AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command="pw-cat --playback --raw -",
                dry_run=True,
                use_continuous_pcm=True,
            )

    def test_continuous_backend_ignores_per_track_amp_muting(self) -> None:
        gate = _FakeAmpGate()
        with self.assertLogs("magic_box.audio", level="WARNING"):
            player = AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                dry_run=True,
                amp_gate=gate,
                mute_between_tracks=True,
                use_continuous_pcm=True,
            )

        self.assertFalse(player.mute_between_tracks)
        self.assertEqual(gate.events, ["unmute"])

    def test_invalid_continuous_decoder_fails_closed(self) -> None:
        with self.assertRaises(AudioInitializationError):
            AudioPlayer(
                command="mpg123 -q -o alsa -a plughw:1,0",
                dry_run=True,
                use_continuous_pcm=True,
            )

    def test_continuous_sink_is_primed_before_amp_unmute(self) -> None:
        events: list[object] = []
        sink = _FakeProcess(stdin=_RecordingPipe(events, max_write=4096))
        gate = _RecordingAmpGate(events)

        with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
            "magic_box.audio.subprocess.Popen", return_value=sink
        ), patch(
            "magic_box.audio.threading.Thread", side_effect=lambda **_kwargs: _FakeThread(events)
        ), patch(
            "magic_box.audio.time.sleep", side_effect=lambda seconds: events.append(("prime-wait", seconds))
        ):
            player = AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                amp_gate=gate,
                use_continuous_pcm=True,
            )

        self.assertLess(events.index("sink-write"), events.index("amp-unmute"))
        self.assertLess(events.index("thread-start"), events.index("amp-unmute"))
        self.assertEqual(sum(len(chunk) for chunk in sink.stdin.writes), 48_000 * 2 * 2 // 5)
        prime_wait = next(event for event in events if isinstance(event, tuple))
        self.assertGreater(prime_wait[1], 0.19)
        player.close()

    def test_missing_continuous_sink_fails_initialization_before_unmute(self) -> None:
        gate = _FakeAmpGate()
        with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
            "magic_box.audio.subprocess.Popen", side_effect=FileNotFoundError
        ), self.assertLogs(
            "magic_box.audio", level="ERROR"
        ), self.assertRaises(AudioInitializationError):
            AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                amp_gate=gate,
                use_continuous_pcm=True,
            )

        self.assertNotIn("unmute", gate.events)

    def test_missing_continuous_decoder_fails_initialization(self) -> None:
        with patch("magic_box.audio.shutil.which", return_value=None), self.assertRaises(
            AudioInitializationError
        ):
            AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                use_continuous_pcm=True,
            )

    def test_continuous_pcm_reuses_one_sink_and_interrupts_decoder(self) -> None:
        events: list[object] = []
        sink = _FakeProcess(stdin=_RecordingPipe(events))
        first_decoder = _FakeProcess(stdout=BytesIO())
        second_decoder = _FakeProcess(stdout=BytesIO())
        processes = iter((sink, first_decoder, second_decoder))
        commands: list[list[str]] = []

        def popen(args: list[str], **_kwargs: object) -> _FakeProcess:
            commands.append(args)
            return next(processes)

        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "first.mp3"
            second_path = Path(temp_dir) / "second.mp3"
            first_path.write_bytes(b"first")
            second_path.write_bytes(b"second")
            with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
                "magic_box.audio.subprocess.Popen", side_effect=popen
            ), patch(
                "magic_box.audio.threading.Thread", side_effect=lambda **_kwargs: _FakeThread(events)
            ), patch("magic_box.audio.time.sleep"):
                player = AudioPlayer(
                    command="mpg123 -q -s --rate 48000 --stereo -e s16",
                    sink_command=TEST_SINK_COMMAND,
                    volume_getter=lambda: 50,
                    use_continuous_pcm=True,
                )
                self.assertTrue(player.play_file(first_path))
                self.assertTrue(player.is_playing())
                self.assertTrue(player.play_file(second_path))

        self.assertEqual([command[0] for command in commands], ["aplay", "mpg123", "mpg123"])
        self.assertTrue(first_decoder.terminated)
        self.assertFalse(sink.terminated)
        decoder_command = commands[1]
        self.assertIn("-s", decoder_command)
        self.assertEqual(decoder_command[decoder_command.index("--rate") + 1], "48000")
        self.assertIn("--stereo", decoder_command)
        self.assertEqual(decoder_command[decoder_command.index("-e") + 1], "s16")
        self.assertNotIn("-o", decoder_command)
        self.assertNotIn("-a", decoder_command)
        player.close()

    def test_decoder_argv_uses_inherited_fd_not_private_story_path(self) -> None:
        events: list[object] = []
        sink = _FakeProcess(stdin=_RecordingPipe(events))
        decoder = _FakeProcess(stdout=BytesIO())
        processes = iter((sink, decoder))
        decoder_args: list[str] = []
        inherited_fd = -1

        def popen(args: list[str], **kwargs: object) -> _FakeProcess:
            nonlocal inherited_fd
            process = next(processes)
            if args[0] == "mpg123":
                decoder_args.extend(args)
                inherited_fd = kwargs["pass_fds"][0]  # type: ignore[index]
                os.fstat(inherited_fd)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            story = Path(temp_dir) / "private-family-story-name.mp3"
            story.write_bytes(b"private audio")
            with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
                "magic_box.audio.subprocess.Popen", side_effect=popen
            ), patch(
                "magic_box.audio.threading.Thread", side_effect=lambda **_kwargs: _FakeThread(events)
            ), patch("magic_box.audio.time.sleep"):
                player = AudioPlayer(
                    command="mpg123 -q -s --rate 48000 --stereo -e s16",
                    sink_command=TEST_SINK_COMMAND,
                    use_continuous_pcm=True,
                )
                self.assertTrue(player.play_file(story))

            self.assertNotIn(str(story), " ".join(decoder_args))
            self.assertEqual(decoder_args[-1], f"/proc/self/fd/{inherited_fd}")
            with self.assertRaises(OSError):
                os.fstat(inherited_fd)
            player.close()

    def test_sink_failure_during_decoder_launch_prevents_decoder_publish(self) -> None:
        events: list[object] = []
        sink = _FakeProcess(stdin=_RecordingPipe(events))
        decoder = _FakeProcess(stdout=BytesIO())
        with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
            "magic_box.audio.subprocess.Popen", return_value=sink
        ), patch(
            "magic_box.audio.threading.Thread", side_effect=lambda **_kwargs: _FakeThread(events)
        ), patch("magic_box.audio.time.sleep"):
            player = AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                use_continuous_pcm=True,
            )

        def fail_during_launch(_args: list[str], **_kwargs: object) -> _FakeProcess:
            player._handle_sink_failure(sink, "sink failed during decoder launch")
            return decoder

        with tempfile.TemporaryDirectory() as temp_dir:
            story = Path(temp_dir) / "story.mp3"
            story.write_bytes(b"story")
            with patch("magic_box.audio.subprocess.Popen", side_effect=fail_during_launch), self.assertRaises(
                AudioRuntimeError
            ):
                player.play_file(story)

        self.assertIsNone(player._current_process)
        self.assertTrue(decoder.terminated)
        self.assertGreaterEqual(decoder.wait_calls, 1)
        self.assertEqual(events.count("thread-start"), 1)
        player.close()

    def test_stop_during_decoder_launch_prevents_stale_decoder_publish(self) -> None:
        events: list[object] = []
        sink = _FakeProcess(stdin=_RecordingPipe(events))
        decoder = _FakeProcess(stdout=BytesIO())
        with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
            "magic_box.audio.subprocess.Popen", return_value=sink
        ), patch(
            "magic_box.audio.threading.Thread", side_effect=lambda **_kwargs: _FakeThread(events)
        ), patch("magic_box.audio.time.sleep"):
            player = AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                use_continuous_pcm=True,
            )

        def stop_during_launch(_args: list[str], **_kwargs: object) -> _FakeProcess:
            player.stop_current()
            return decoder

        with tempfile.TemporaryDirectory() as temp_dir:
            story = Path(temp_dir) / "story.mp3"
            story.write_bytes(b"story")
            with patch("magic_box.audio.subprocess.Popen", side_effect=stop_during_launch):
                self.assertFalse(player.play_file(story))

        self.assertIsNone(player._current_process)
        self.assertTrue(decoder.terminated)
        self.assertGreaterEqual(decoder.wait_calls, 1)
        self.assertEqual(events.count("thread-start"), 1)
        player.close()

    def test_continuous_idle_silence_is_not_reported_as_playing(self) -> None:
        gate = _FakeAmpGate()
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            amp_gate=gate,
            use_continuous_pcm=True,
        )
        events: list[object] = []
        pipe = _RecordingPipe(events, on_write=player._sink_stop.set)
        player._sink_process = _FakeProcess(stdin=pipe)

        player._feed_continuous_sink()

        self.assertEqual(pipe.writes, [player._silence_chunk])
        self.assertFalse(player.is_playing())

    def test_final_pcm_is_padded_to_stereo_s16_frame(self) -> None:
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        decoder = _FakeProcess(stdout=BytesIO(b"\x01\x02\x03"))
        player._current_process = decoder
        player._decoder_active = True

        player._decode_into_sink(decoder, player._playback_generation)

        _generation, pcm = player._pcm_queue.get_nowait()
        self.assertEqual(pcm, b"\x01\x02\x03\x00")

    def test_sink_failure_blocks_new_play_until_systemd_restarts_service(self) -> None:
        events: list[object] = []
        first_sink = _FakeProcess(stdin=_RecordingPipe(events))
        commands: list[list[str]] = []

        def popen(args: list[str], **_kwargs: object) -> _FakeProcess:
            commands.append(args)
            return first_sink

        with patch("magic_box.audio.shutil.which", return_value="/usr/bin/mpg123"), patch(
            "magic_box.audio.subprocess.Popen", side_effect=popen
        ), patch(
            "magic_box.audio.threading.Thread", side_effect=lambda **_kwargs: _FakeThread(events)
        ), patch("magic_box.audio.time.sleep"):
            player = AudioPlayer(
                command="mpg123 -q -s --rate 48000 --stereo -e s16",
                sink_command=TEST_SINK_COMMAND,
                use_continuous_pcm=True,
            )
            with self.assertLogs("magic_box.audio", level="ERROR"):
                player._handle_sink_failure(first_sink, "simulated sink exit")
            with self.assertRaises(AudioRuntimeError):
                player.play_file(Path("retry.mp3"))

        self.assertEqual([command[0] for command in commands], ["aplay"])
        self.assertTrue(first_sink.terminated)
        player.close()

    def test_stale_pcm_queue_does_not_report_current_playback(self) -> None:
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        player._pcm_queue.put((player._playback_generation - 1, b"old"))

        self.assertFalse(player.is_playing())
        player._queued_pcm_bytes = 4
        self.assertTrue(player.is_playing())

    def test_stop_waits_for_in_progress_pcm_write(self) -> None:
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        pipe = _BlockingPipe([])
        player._sink_process = _FakeProcess(stdin=pipe)
        audio = bytes([1]) * player._pcm_chunk_bytes
        player._pcm_queue.put((player._playback_generation, audio))
        player._queued_pcm_bytes = len(audio)
        feeder = threading.Thread(target=player._feed_continuous_sink)
        feeder.start()
        self.assertTrue(pipe.entered.wait(1))

        stopped = threading.Event()
        stopper = threading.Thread(target=lambda: (player.stop_current(), stopped.set()))
        stopper.start()
        self.assertFalse(stopped.wait(0.05))
        pipe.release.set()
        player._sink_stop.set()
        self.assertTrue(stopped.wait(1))
        stopper.join(1)
        feeder.join(1)

        self.assertEqual([chunk for chunk in pipe.writes if any(chunk)], [audio])
        player.close()

    def test_stop_cancels_stalled_real_pipe_write_and_crosses_barrier(self) -> None:
        read_fd, write_stream = _saturated_nonblocking_pipe()
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        sink = _FakeProcess(stdin=write_stream)
        player._sink_process = sink
        audio = bytes([7]) * player._pcm_chunk_bytes
        player._pcm_queue.put((player._playback_generation, audio))
        player._queued_pcm_bytes = len(audio)
        feeder = threading.Thread(target=player._feed_continuous_sink)
        try:
            with patch(
                "magic_box.audio._write_nonblocking_fd",
                new=_write_nonblocking_fd,
            ):
                feeder.start()
                self._wait_until(lambda: _lock_is_held(player._sink_write_lock))
                started = time.monotonic()
                player.stop_current()
                elapsed = time.monotonic() - started
                player._sink_stop.set()
                player._sink_writes_enabled.clear()
                feeder.join(1)

            self.assertLess(elapsed, 0.3)
            self.assertFalse(feeder.is_alive())
            self.assertFalse(_lock_is_held(player._sink_write_lock))
            self.assertIsNone(player._sink_failure)
            self.assertFalse(player.is_playing())
        finally:
            player.close()
            write_stream.close()
            os.close(read_fd)

    def test_permanently_stalled_real_sink_fails_closed_and_mutes_amp(self) -> None:
        read_fd, write_stream = _saturated_nonblocking_pipe()
        gate = _FakeAmpGate()
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            amp_gate=gate,
            use_continuous_pcm=True,
        )
        sink = _FakeProcess(stdin=write_stream)
        player._sink_process = sink
        player.dry_run = False
        feeder = threading.Thread(target=player._feed_continuous_sink)
        try:
            with patch(
                "magic_box.audio._write_nonblocking_fd",
                new=_write_nonblocking_fd,
            ), patch(
                "magic_box.audio.CONTINUOUS_PCM_WRITE_TIMEOUT_SECONDS",
                0.1,
            ), self.assertLogs("magic_box.audio", level="ERROR"):
                feeder.start()
                self._wait_until(lambda: player._sink_failure is not None)
                feeder.join(1)

            self.assertFalse(feeder.is_alive())
            self.assertEqual(player._sink_failure, "Continuous PCM sink write timed out")
            self.assertTrue(sink.terminated)
            self.assertEqual(gate.events[-1], "mute")
            with self.assertRaises(AudioRuntimeError):
                player.raise_if_unhealthy()
        finally:
            player.close()
            write_stream.close()
            os.close(read_fd)

    def test_cancelled_generation_cannot_start_a_stale_sink_write(self) -> None:
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        pipe = _RecordingPipe([])
        player._sink_process = _FakeProcess(stdin=pipe)
        audio = bytes([2]) * player._pcm_chunk_bytes
        generation = player._playback_generation
        player._pcm_queue.put((generation, audio))
        player._queued_pcm_bytes = len(audio)
        player._sink_write_lock.acquire()
        feeder = threading.Thread(target=player._feed_continuous_sink)
        feeder.start()
        self._wait_until(player._pcm_queue.empty)

        stopped = threading.Event()
        stopper = threading.Thread(target=lambda: (player.stop_current(), stopped.set()))
        stopper.start()
        self._wait_until(lambda: player._playback_generation != generation)
        player._sink_write_lock.release()
        player._sink_stop.set()
        self.assertTrue(stopped.wait(1))
        stopper.join(1)
        feeder.join(1)

        self.assertFalse(any(any(chunk) for chunk in pipe.writes))
        player.close()

    def _wait_until(self, predicate: object, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():  # type: ignore[operator]
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for threaded audio test condition")

    def test_sink_failure_mutes_amp_and_terminates_decoder(self) -> None:
        gate = _FakeAmpGate()
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            amp_gate=gate,
            use_continuous_pcm=True,
        )
        sink = _FakeProcess(stdin=_RecordingPipe([]))
        decoder = _FakeProcess(stdout=BytesIO())
        player._sink_process = sink
        player._current_process = decoder
        player._decoder_active = True

        with self.assertLogs("magic_box.audio", level="ERROR"):
            player._handle_sink_failure(sink, "test sink failure")

        self.assertTrue(sink.terminated)
        self.assertTrue(decoder.terminated)
        self.assertGreaterEqual(sink.wait_calls, 1)
        self.assertGreaterEqual(decoder.wait_calls, 1)
        self.assertEqual(gate.events[-1], "mute")
        self.assertFalse(player.is_playing())
        player.dry_run = False
        with self.assertRaises(AudioRuntimeError):
            player.raise_if_unhealthy()

    def test_process_cleanup_kills_and_reaps_after_terminate_timeout(self) -> None:
        process = _TimeoutProcess()

        AudioPlayer._terminate_process(process, timeout=0.01)

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_calls, 2)

    def test_process_cleanup_returns_after_child_stays_stalled_past_kill(self) -> None:
        process = _NeverExitProcess()

        with self.assertLogs("magic_box.audio", level="ERROR"):
            AudioPlayer._terminate_process(process, timeout=0.01)

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_calls, 2)

    def test_close_mutes_amp_before_terminating_continuous_sink(self) -> None:
        events: list[object] = []
        gate = _RecordingAmpGate(events)
        sink = _RecordingProcess(events, stdin=_RecordingPipe(events))
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            amp_gate=gate,
            use_continuous_pcm=True,
        )
        player._sink_process = sink

        player.close()

        self.assertLess(events.index("amp-mute"), events.index("sink-terminate"))
        self.assertLess(events.index("sink-wait"), events.index("amp-close"))

    def test_prepare_sink_fd_sets_real_pipe_nonblocking(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(write_fd, "wb", buffering=0, closefd=False) as stream:
                self.assertEqual(_prepare_nonblocking_sink_fd(stream), write_fd)
                self.assertFalse(os.get_blocking(write_fd))
        finally:
            os.close(write_fd)
            os.close(read_fd)

    def test_decoder_read_error_is_redacted_and_clears_playing_state(self) -> None:
        secret = "/private/family/alex-story.mp3"
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        decoder = _ExitProcess(
            stdout=_ReadErrorStream(secret),
            exit_code=1,
        )
        player._current_process = decoder
        player._decoder_active = True

        with self.assertLogs("magic_box.audio", level="ERROR") as logs:
            player._decode_into_sink(decoder, player._playback_generation)

        message = " ".join(logs.output)
        self.assertIn("Audio decoder PCM read failed", message)
        self.assertIn("selected audio ended early", message)
        self.assertNotIn(secret, message)
        self.assertFalse(player.is_playing())

    def test_decoder_nonzero_exit_is_actionable_without_poisoning_sink(self) -> None:
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        decoder = _ExitProcess(stdout=BytesIO(), exit_code=7)
        player._current_process = decoder
        player._decoder_active = True

        with self.assertLogs("magic_box.audio", level="ERROR") as logs:
            player._decode_into_sink(decoder, player._playback_generation)

        self.assertIn("exited with status 7", " ".join(logs.output))
        self.assertFalse(player.is_playing())
        self.assertIsNone(player._sink_failure)

    def test_decoder_cleanup_timeout_does_not_leave_track_playing(self) -> None:
        player = AudioPlayer(
            command="mpg123 -q -s --rate 48000 --stereo -e s16",
            sink_command=TEST_SINK_COMMAND,
            dry_run=True,
            use_continuous_pcm=True,
        )
        decoder = _NeverExitProcess(stdout=BytesIO())
        player._current_process = decoder
        player._decoder_active = True

        with self.assertLogs("magic_box.audio", level="ERROR") as logs:
            player._decode_into_sink(decoder, player._playback_generation)

        self.assertIn("cleanup timed out", " ".join(logs.output))
        self.assertFalse(player.is_playing())


class _FakeAmpGate:
    def __init__(self) -> None:
        self.events: list[str] = []

    def mute(self) -> None:
        self.events.append("mute")

    def unmute(self) -> None:
        self.events.append("unmute")

    def close(self) -> None:
        self.events.append("close")


class _RemoteCommandAudioPlayer(AudioPlayer):
    def __init__(self) -> None:
        super().__init__(command="mpg123 -q", dry_run=True, use_mpg123_remote=True)
        self.commands: list[str] = []

    def _send_remote(self, command: str) -> bool:
        self.commands.append(command)
        return True


class _RecordingPipe:
    def __init__(
        self,
        events: list[object],
        on_write: object | None = None,
        max_write: int | None = None,
    ) -> None:
        self.events = events
        self.on_write = on_write
        self.max_write = max_write
        self.writes: list[bytes] = []
        self.fd = id(self)
        _FAKE_PIPES[self.fd] = self

    def fileno(self) -> int:
        return self.fd

    def write(self, data: bytes) -> int:
        self.events.append("sink-write")
        accepted = len(data) if self.max_write is None else min(len(data), self.max_write)
        self.writes.append(bytes(data[:accepted]))
        if callable(self.on_write):
            self.on_write()
        return accepted

    def flush(self) -> None:
        self.events.append("sink-flush")


class _BlockingPipe(_RecordingPipe):
    def __init__(self, events: list[object]) -> None:
        super().__init__(events)
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, data: bytes) -> int:
        self.entered.set()
        if not self.release.wait(2):
            raise BrokenPipeError("test writer stayed blocked")
        return super().write(data)


class _FakeProcess:
    def __init__(self, *, stdin: object | None = None, stdout: object | None = None) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self) -> int | None:
        return -15 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return 0


class _TimeoutProcess(_FakeProcess):
    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if not self.killed:
            raise subprocess.TimeoutExpired("fake-process", timeout)
        return 0


class _NeverExitProcess(_FakeProcess):
    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        raise subprocess.TimeoutExpired("fake-process", timeout)


class _ExitProcess(_FakeProcess):
    def __init__(self, *, stdout: object, exit_code: int) -> None:
        super().__init__(stdout=stdout)
        self.exit_code = exit_code
        self.finished = False

    def poll(self) -> int | None:
        if self.terminated or self.killed:
            return -15
        return self.exit_code if self.finished else None

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.finished = True
        return self.exit_code


class _ReadErrorStream:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def read(self, _size: int) -> bytes:
        raise OSError(5, f"simulated read failure for {self.secret}")


class _RecordingProcess(_FakeProcess):
    def __init__(self, events: list[object], *, stdin: _RecordingPipe | None = None) -> None:
        super().__init__(stdin=stdin)
        self.events = events

    def terminate(self) -> None:
        self.events.append("sink-terminate")
        super().terminate()

    def wait(self, timeout: float | None = None) -> int:
        self.events.append("sink-wait")
        return super().wait(timeout)


class _FakeThread:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    def start(self) -> None:
        self.events.append("thread-start")

    def join(self, timeout: float | None = None) -> None:
        self.events.append("thread-join")


class _RecordingAmpGate:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    def mute(self) -> None:
        self.events.append("amp-mute")

    def unmute(self) -> None:
        self.events.append("amp-unmute")

    def close(self) -> None:
        self.events.append("amp-close")


if __name__ == "__main__":
    unittest.main()
