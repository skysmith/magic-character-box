import json
from pathlib import Path
import tempfile
import unittest

from magic_box.control import consume_stop_request, control_file_for_config, request_stop


class ControlTests(unittest.TestCase):
    def test_control_file_defaults_beside_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"

            self.assertEqual(control_file_for_config(config_path), config_path.parent.resolve() / "control.json")

    def test_stop_request_is_consumed_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_path = Path(temp_dir) / "config" / "control.json"

            request_stop(control_path)

            payload = json.loads(control_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["command"], "stop")
            self.assertTrue(consume_stop_request(control_path))
            self.assertFalse(consume_stop_request(control_path))
            self.assertFalse(control_path.exists())

    def test_invalid_control_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_path = Path(temp_dir) / "control.json"
            control_path.write_text("not json", encoding="utf-8")

            self.assertFalse(consume_stop_request(control_path))
            self.assertFalse(control_path.exists())


if __name__ == "__main__":
    unittest.main()
