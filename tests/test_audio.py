from pathlib import Path
import tempfile
import unittest

from magic_box.audio import (
    AudioPlayer,
    _apply_mpg123_volume,
    _build_mpg123_remote_args,
    _mpg123_status_is_playing,
    _uses_pulse_output,
)


class AudioTests(unittest.TestCase):
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

    def test_mpg123_volume_honors_max_output_ceiling(self) -> None:
        self.assertEqual(
            _apply_mpg123_volume(["mpg123", "-q"], 100, 75),
            ["mpg123", "-q", "-f", "24576"],
        )

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


if __name__ == "__main__":
    unittest.main()
