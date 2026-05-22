from pathlib import Path
import tempfile
import unittest

from magic_box.runtime_state import append_event, load_state, record_tag, state_file_for_config


class RuntimeStateTests(unittest.TestCase):
    def test_state_file_defaults_beside_config(self) -> None:
        config_path = Path("/tmp/project/config/characters.json")

        self.assertEqual(state_file_for_config(config_path), Path("/tmp/project/config/device_state.json").resolve())

    def test_record_tag_updates_last_tag_and_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "device_state.json"

            record_tag(path, "04:a1:22:9b", known=False, source="test")
            append_event(path, "audio", "Test event.")
            state = load_state(path)

            self.assertEqual(state["last_tag"]["uid"], "04-A1-22-9B")
            self.assertFalse(state["last_tag"]["known"])
            self.assertEqual(state["events"][0]["message"], "Test event.")
            self.assertIn("Unknown tag found", state["events"][1]["message"])


if __name__ == "__main__":
    unittest.main()
