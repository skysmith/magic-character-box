from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "magic-character-box-wifi-control"


def load_helper():
    loader = importlib.machinery.SourceFileLoader("magic_character_box_wifi_control_test", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("Could not load Wi-Fi helper test module.")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class WifiControlHelperTests(unittest.TestCase):
    def test_connect_reads_secret_from_stdin_not_command_arguments(self) -> None:
        helper = load_helper()
        payload = json.dumps({"ssid": "Test Network", "password": "private pass"})

        with (
            patch.object(sys, "argv", [str(HELPER_PATH), "connect-stdin"]),
            patch.object(sys, "stdin", io.StringIO(payload)),
            patch.object(helper.shutil, "which", return_value="/usr/bin/nmcli"),
            patch.object(helper.os.path, "exists", return_value=True),
            patch.object(helper, "run_command", return_value=0) as run_command,
        ):
            self.assertEqual(helper.main(), 0)

        command = run_command.call_args.args[0]
        self.assertNotIn("private pass", command)
        self.assertEqual(
            command,
            [
                "/usr/bin/nmcli",
                "--ask",
                "device",
                "wifi",
                "connect",
                "Test Network",
                "name",
                "story-dock-provisioned-wifi",
            ],
        )
        self.assertEqual(run_command.call_args.kwargs["input_text"], "private pass\n")

    def test_recovery_password_reads_secret_from_stdin_not_command_arguments(self) -> None:
        helper = load_helper()
        payload = json.dumps({"password": "new private pass"})

        with (
            patch.object(sys, "argv", [str(HELPER_PATH), "set-recovery-password-stdin"]),
            patch.object(sys, "stdin", io.StringIO(payload)),
            patch.object(helper.shutil, "which", return_value="/usr/bin/nmcli"),
            patch.object(helper.os.path, "exists", return_value=True),
            patch.object(helper, "set_recovery_password", return_value=0) as set_recovery_password,
        ):
            self.assertEqual(helper.main(), 0)
            invocation_argv = list(sys.argv)

        self.assertNotIn("new private pass", invocation_argv)
        set_recovery_password.assert_called_once_with("new private pass")

    def test_secret_payload_must_be_json_object(self) -> None:
        helper = load_helper()
        with patch.object(sys, "stdin", io.StringIO("[]")):
            with self.assertRaisesRegex(SystemExit, "invalid"):
                helper.read_secret_payload()

    def test_secret_payload_is_bounded(self) -> None:
        helper = load_helper()
        with patch.object(sys, "stdin", io.StringIO("x" * (helper.MAX_SECRET_PAYLOAD_BYTES + 1))):
            with self.assertRaisesRegex(SystemExit, "too large"):
                helper.read_secret_payload()


if __name__ == "__main__":
    unittest.main()
