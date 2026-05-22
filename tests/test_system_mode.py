import subprocess
import unittest

from magic_box.system_mode import ServiceModeController


class ServiceModeControllerTests(unittest.TestCase):
    def test_status_reports_playback_when_service_is_active(self) -> None:
        controller = ServiceModeController(systemctl="/bin/systemctl", sudo=None, runner=_active_runner)

        status = controller.status()

        self.assertTrue(status.available)
        self.assertEqual(status.mode, "playback")
        self.assertTrue(status.playback_active)

    def test_enter_setup_stops_playback_service(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if "is-active" in args:
                return subprocess.CompletedProcess(args, 3, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        controller = ServiceModeController(systemctl="/bin/systemctl", sudo=None, runner=runner)

        result = controller.enter_setup()

        self.assertTrue(result.ok)
        self.assertEqual(result.status.mode, "setup")
        self.assertTrue(any("stop" in call for call in calls))


def _active_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, "", "")


if __name__ == "__main__":
    unittest.main()
