from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from magic_box.hardware_ready import main, wait_for_player_hardware


class HardwareReadyTests(unittest.TestCase):
    def test_returns_immediately_when_all_devices_are_accessible(self) -> None:
        paths = (Path("/test/gpiomem"), Path("/test/spidev0.0"))
        sleeps: list[float] = []

        result = wait_for_player_hardware(
            paths,
            access_check=lambda _path: True,
            monotonic=lambda: 10.0,
            sleep=sleeps.append,
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.unavailable, ())
        self.assertEqual(sleeps, [])

    def test_waits_for_delayed_permissions(self) -> None:
        paths = (Path("/test/gpiomem"), Path("/test/spidev0.0"))
        now = [0.0]

        def advance(seconds: float) -> None:
            now[0] += seconds

        result = wait_for_player_hardware(
            paths,
            timeout_seconds=1.0,
            poll_interval_seconds=0.25,
            access_check=lambda _path: now[0] >= 0.5,
            monotonic=lambda: now[0],
            sleep=advance,
        )

        self.assertTrue(result.ready)
        self.assertEqual(now[0], 0.5)

    def test_timeout_reports_each_inaccessible_device(self) -> None:
        paths = (Path("/test/gpiomem"), Path("/test/spidev0.0"))
        now = [0.0]

        def advance(seconds: float) -> None:
            now[0] += seconds

        result = wait_for_player_hardware(
            paths,
            timeout_seconds=0.5,
            poll_interval_seconds=0.25,
            access_check=lambda _path: False,
            monotonic=lambda: now[0],
            sleep=advance,
        )

        self.assertFalse(result.ready)
        self.assertEqual(result.unavailable, paths)
        self.assertEqual(now[0], 0.5)

    def test_missing_device_fails_zero_timeout_cli_check(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing-device"
            stderr = StringIO()

            with redirect_stderr(stderr):
                status = main(["--timeout", "0", str(missing)])

        self.assertEqual(status, 1)
        self.assertIn(str(missing), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
