from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SystemdAudioTests(unittest.TestCase):
    def test_player_services_keep_amp_enabled_between_clips(self) -> None:
        for service_name in ("magic-character-box.service", "magic-character-box-dev.service"):
            with self.subTest(service=service_name):
                text = (ROOT / "systemd" / service_name).read_text(encoding="utf-8")

                self.assertIn("Environment=MAGIC_BOX_AMP_SD_GPIO=16", text)
                self.assertIn("Environment=MAGIC_BOX_AMP_MUTE_BETWEEN_TRACKS=0", text)
                self.assertIn("Environment=MAGIC_BOX_AUDIO_BACKEND=mpg123-remote", text)
                self.assertIn("MAGIC_BOX_AUDIO_WARMUP_FILE=/home/pi/magic-character-box/audio/system/silence.mp3", text)


if __name__ == "__main__":
    unittest.main()
