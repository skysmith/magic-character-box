from pathlib import Path
import tempfile
import unittest

from magic_box.config import CharacterConfig, normalize_uid, slugify_name, unique_character_folder


class ConfigTests(unittest.TestCase):
    def test_normalize_hex_uid(self) -> None:
        self.assertEqual(normalize_uid("04:a1 22-9b"), "04-A1-22-9B")

    def test_normalize_named_fake_uid(self) -> None:
        self.assertEqual(normalize_uid("dinosaur"), "DINOSAUR")

    def test_slugify_name_removes_apostrophes(self) -> None:
        self.assertEqual(slugify_name("He's got the Whole World in his Hands"), "hes-got-the-whole-world-in-his-hands")

    def test_loads_relative_audio_folder_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "audio" / "dino").mkdir(parents=True)
            (root / "config" / "characters.json").write_text(
                '{"DINOSAUR": {"name": "Dinosaur", "folder": "audio/dino", "mode": "first"}}',
                encoding="utf-8",
            )

            config = CharacterConfig.load(root / "config" / "characters.json")

            character = config.lookup("dinosaur")
            assert character is not None
            self.assertEqual(character.name, "Dinosaur")
            self.assertEqual(character.folder, (root / "audio" / "dino").resolve())

    def test_unique_character_folder_numbers_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "audio" / "grandma-token").mkdir(parents=True)
            data = {
                "04-A1-22-9B": {
                    "name": "Grandma Token",
                    "folder": "audio/grandma-token",
                    "mode": "first",
                }
            }

            folder = unique_character_folder(root, "Grandma Token", data, "04-B8-10-4C")

            self.assertEqual(folder, (root / "audio" / "grandma-token-2").resolve())


if __name__ == "__main__":
    unittest.main()
