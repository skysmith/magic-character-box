from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SystemdAudioTests(unittest.TestCase):
    def test_player_services_use_one_fixed_format_continuous_alsa_sink(self) -> None:
        for service_name in ("magic-character-box.service", "magic-character-box-dev.service"):
            with self.subTest(service=service_name):
                text = (ROOT / "systemd" / service_name).read_text(encoding="utf-8")

                self.assertIn("Environment=MAGIC_BOX_AMP_SD_GPIO=16", text)
                self.assertIn("Environment=MAGIC_BOX_AMP_MUTE_BETWEEN_TRACKS=0", text)
                self.assertIn("Environment=MAGIC_BOX_AUDIO_BACKEND=continuous-pcm", text)
                self.assertIn(
                    'Environment="MAGIC_BOX_AUDIO_CMD=mpg123 -q -s --rate 48000 --stereo -e s16"',
                    text,
                )
                self.assertIn(
                    'Environment="MAGIC_BOX_AUDIO_SINK_CMD=aplay -q -D '
                    'plughw:CARD=MAX98357A,DEV=0 --file-type raw --format S16_LE '
                    '--rate 48000 --channels 2 --buffer-time=100000 --period-time=20000"',
                    text,
                )
                self.assertNotIn("MAGIC_BOX_AUDIO_WARMUP_FILE", text)
                self.assertNotIn("MAGIC_BOX_AUDIO_BACKEND=mpg123-remote", text)
                self.assertNotIn("-o alsa", text)
                self.assertIn("KillMode=mixed", text)
                self.assertIn("KillSignal=SIGTERM", text)
                self.assertIn("TimeoutStopSec=15s", text)

    def test_player_services_wait_for_unprivileged_gpio_and_spi_access(self) -> None:
        for service_name in ("magic-character-box.service", "magic-character-box-dev.service"):
            with self.subTest(service=service_name):
                text = (ROOT / "systemd" / service_name).read_text(encoding="utf-8")

                self.assertIn("User=pi", text)
                self.assertIn("SupplementaryGroups=audio gpio spi", text)
                self.assertIn(
                    "ExecStartPre=/home/pi/magic-character-box/.venv/bin/python "
                    "-m magic_box.hardware_ready --timeout 60",
                    text,
                )
                self.assertIn("Restart=on-failure", text)
                self.assertIn("RestartSec=5", text)
                self.assertNotIn("systemd-udev-settle", text)


if __name__ == "__main__":
    unittest.main()
