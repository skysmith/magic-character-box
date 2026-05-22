from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from magic_box.volume import VolumeControl, apply_pipewire_volume


class VolumeTests(unittest.TestCase):
    def test_volume_defaults_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = VolumeControl(Path(temp_dir) / "volume.json", default_percent=40)

            self.assertEqual(control.get(), 40)

    def test_volume_adjusts_and_clamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = VolumeControl(Path(temp_dir) / "volume.json", default_percent=40)

            self.assertEqual(control.adjust(70), 100)
            self.assertEqual(control.adjust(-150), 0)

    def test_apply_pipewire_volume_uses_wpctl_percent(self) -> None:
        completed = Mock(returncode=0, stderr=b"")
        with patch("magic_box.volume.shutil.which", return_value="/usr/bin/wpctl"):
            with patch("magic_box.volume.subprocess.run", return_value=completed) as run:
                self.assertTrue(apply_pipewire_volume(65))

        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["/usr/bin/wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@"])
        self.assertEqual(args[3], "0.650")


if __name__ == "__main__":
    unittest.main()
