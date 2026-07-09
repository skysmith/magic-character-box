from __future__ import annotations

import subprocess
import unittest

from magic_box.wifi import WifiController


class WifiControllerTests(unittest.TestCase):
    def test_status_uses_privileged_helper_when_available(self) -> None:
        runner = _FakeRunner(
            {
                ("/usr/local/bin/magic-character-box-wifi-control", "radio-status"): subprocess.CompletedProcess(
                    [], 0, "enabled\n", ""
                ),
                ("/usr/local/bin/magic-character-box-wifi-control", "device-status"): subprocess.CompletedProcess(
                    [], 0, "wlan0:wifi:connected:Mini Cottage\n", ""
                ),
            }
        )
        controller = WifiController(
            nmcli=None,
            helper="/usr/local/bin/magic-character-box-wifi-control",
            sudo="",
            runner=runner,
        )

        status = controller.status()

        self.assertTrue(status.available)
        self.assertTrue(status.connected)
        self.assertEqual(status.ssid, "Mini Cottage")
        self.assertEqual(
            runner.commands,
            [
                ("/usr/local/bin/magic-character-box-wifi-control", "radio-status"),
                ("/usr/local/bin/magic-character-box-wifi-control", "device-status"),
            ],
        )

    def test_connect_uses_helper_without_leaking_password_in_result(self) -> None:
        runner = _FakeRunner(
            {
                (
                    "/usr/local/bin/magic-character-box-wifi-control",
                    "connect",
                    "Mini Cottage",
                    "private pass",
                ): subprocess.CompletedProcess([], 0, "connected\n", ""),
                ("/usr/local/bin/magic-character-box-wifi-control", "radio-status"): subprocess.CompletedProcess(
                    [], 0, "enabled\n", ""
                ),
                ("/usr/local/bin/magic-character-box-wifi-control", "device-status"): subprocess.CompletedProcess(
                    [], 0, "wlan0:wifi:connected:Mini Cottage\n", ""
                ),
            }
        )
        controller = WifiController(
            nmcli=None,
            helper="/usr/local/bin/magic-character-box-wifi-control",
            sudo="",
            runner=runner,
        )

        result = controller.connect("Mini Cottage", "private pass")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Connected to Mini Cottage.")
        self.assertNotIn("private pass", result.to_dict()["message"])
        self.assertEqual(
            runner.commands[0],
            ("/usr/local/bin/magic-character-box-wifi-control", "connect", "Mini Cottage", "private pass"),
        )


class _FakeRunner:
    def __init__(self, results: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.results = results
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        key = tuple(command)
        self.commands.append(key)
        return self.results.get(key, subprocess.CompletedProcess(command, 1, "", f"unexpected command: {key}"))


if __name__ == "__main__":
    unittest.main()
