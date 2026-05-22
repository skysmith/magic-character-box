from pathlib import Path
import tempfile
import unittest

from magic_box.audio import AudioPlayer, _apply_mpg123_volume, _build_mpg123_remote_args, _uses_pulse_output


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

    def test_remote_args_keep_player_open_and_remove_scalefactor(self) -> None:
        self.assertEqual(
            _build_mpg123_remote_args(["mpg123", "-q", "-o", "pulse", "-f", "1000"]),
            ["mpg123", "-q", "-o", "pulse", "-R", "--keep-open"],
        )

    def test_pulse_output_is_detected(self) -> None:
        self.assertTrue(_uses_pulse_output("mpg123 -q -o pulse"))
        self.assertFalse(_uses_pulse_output("mpg123 -q -a plughw:1,0"))

    def test_amp_gate_is_left_enabled_by_default(self) -> None:
        gate = _FakeAmpGate()

        AudioPlayer(dry_run=True, amp_gate=gate)

        self.assertEqual(gate.events, ["unmute"])


class _FakeAmpGate:
    def __init__(self) -> None:
        self.events: list[str] = []

    def mute(self) -> None:
        self.events.append("mute")

    def unmute(self) -> None:
        self.events.append("unmute")

    def close(self) -> None:
        self.events.append("close")


if __name__ == "__main__":
    unittest.main()
