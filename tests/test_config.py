from pathlib import Path
import tempfile
import unittest

from magic_box.config import (
    CharacterConfig,
    ConfigError,
    normalize_uid,
    slugify_name,
    story_locator_lookup_key,
    unique_character_folder,
)


class ConfigTests(unittest.TestCase):
    def test_normalize_hex_uid(self) -> None:
        self.assertEqual(normalize_uid("04:a1 22-9b"), "04-A1-22-9B")

    def test_normalize_named_fake_uid(self) -> None:
        self.assertEqual(normalize_uid("dinosaur"), "DINOSAUR")

    def test_normalize_preserves_canonical_story_playback_key(self) -> None:
        key = "sdpk1_" + "a" * 64

        self.assertEqual(normalize_uid(key), key)

    def test_malformed_story_playback_key_is_rejected_not_treated_as_named_uid(self) -> None:
        malformed = (
            "sdpk1_" + "a" * 63,
            "sdpk1_" + "g" * 64,
            "SDPK1_" + "a" * 64,
            "sdpk1-not-a-key",
        )

        for key in malformed:
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_uid(key)

    def test_constructs_and_preserves_canonical_story_locator_key(self) -> None:
        key = story_locator_lookup_key("SD03-0001", "ABCD")

        self.assertEqual(key, "sdlk1_SD03-0001_ABCD")
        self.assertEqual(normalize_uid(key), key)

    def test_locator_key_constructor_rejects_noncanonical_components(self) -> None:
        invalid = (
            ("SD3-0001", "ABCD"),
            ("SD03-001", "ABCD"),
            ("sd03-0001", "ABCD"),
            ("SD03-0001", "ABC1"),
            ("SD03-0001", "abcD"),
            ("SD03-0001", "ABCD "),
        )

        for locator, verifier in invalid:
            with self.subTest(locator=locator, verifier=verifier), self.assertRaises(ValueError):
                story_locator_lookup_key(locator, verifier)

    def test_malformed_story_locator_key_is_rejected_not_normalized_as_uid(self) -> None:
        malformed = (
            "SDLK1_SD03-0001_ABCD",
            "sdlk1_SD03-0001_ABC1",
            "sdlk1_sd03-0001_ABCD",
            "sdlk1_SD03-0001_ABCD_extra",
            "sdlk1-SD03-0001-ABCD",
        )

        for key in malformed:
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_uid(key)

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

    def test_loads_and_looks_up_story_playback_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "audio" / "memory").mkdir(parents=True)
            key = "sdpk1_" + "a" * 64
            (root / "config" / "characters.json").write_text(
                '{"' + key + '": {"name": "Memory", "folder": "audio/memory", "mode": "first"}}',
                encoding="utf-8",
            )

            config = CharacterConfig.load(root / "config" / "characters.json")

            character = config.lookup(key)
            assert character is not None
            self.assertEqual(character.uid, key)

    def test_loads_and_looks_up_story_locator_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "audio" / "memory").mkdir(parents=True)
            key = story_locator_lookup_key("SD03-0001", "ABCD")
            (root / "config" / "characters.json").write_text(
                '{"' + key + '": {"name": "Memory", "folder": "audio/memory", "mode": "first"}}',
                encoding="utf-8",
            )

            config = CharacterConfig.load(root / "config" / "characters.json")

            character = config.lookup(key)
            assert character is not None
            self.assertEqual(character.uid, key)

    def test_load_rejects_noncanonical_story_playback_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "audio" / "memory").mkdir(parents=True)
            key = "SDPK1_" + "a" * 64
            (root / "config" / "characters.json").write_text(
                '{"' + key + '": {"name": "Memory", "folder": "audio/memory", "mode": "first"}}',
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                CharacterConfig.load(root / "config" / "characters.json")

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
