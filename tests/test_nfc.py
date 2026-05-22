from pathlib import Path
import tempfile
import unittest

from magic_box.nfc import TriggerFileNFCReader


class TriggerFileNFCReaderTests(unittest.TestCase):
    def test_consumes_one_uid_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            trigger_file = Path(temp_dir) / "tags.txt"
            trigger_file.write_text("DINOSAUR\nROCKET\n", encoding="utf-8")
            reader = TriggerFileNFCReader(trigger_file)

            self.assertEqual(reader.read_uid(), "DINOSAUR")
            self.assertEqual(reader.read_uid(), "ROCKET")
            self.assertIsNone(reader.read_uid())
            self.assertFalse(trigger_file.exists())

    def test_normalizes_hex_uid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            trigger_file = Path(temp_dir) / "tags.txt"
            trigger_file.write_text("04:a1 22-9b\n", encoding="utf-8")
            reader = TriggerFileNFCReader(trigger_file)

            self.assertEqual(reader.read_uid(), "04-A1-22-9B")


if __name__ == "__main__":
    unittest.main()
