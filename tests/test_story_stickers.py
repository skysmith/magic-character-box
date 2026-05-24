from pathlib import Path
import tempfile
import unittest

from magic_box.story_stickers import (
    StoryStickerError,
    bind_story_sticker_uid,
    claim_story_sticker,
    create_story_sticker,
    get_story_sticker,
    load_story_stickers,
    story_stickers_file_for_config,
)


class StoryStickerTests(unittest.TestCase):
    def test_story_stickers_file_defaults_beside_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"

            self.assertEqual(story_stickers_file_for_config(config_path), config_path.parent.resolve() / "story_stickers.json")

    def test_create_bind_and_claim_story_sticker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "story_stickers.json"

            created = create_story_sticker(path, token="story-token-123", support_code="SD-0001")
            bound = bind_story_sticker_uid(path, "story-token-123", "04:a1:22:9b")
            claimed = claim_story_sticker(
                path,
                "story-token-123",
                name="Grandma at Yellowstone",
                folder="audio/grandma-at-yellowstone",
            )
            loaded = load_story_stickers(path)

            self.assertEqual(created.token, "story-token-123")
            self.assertEqual(bound.uid, "04-A1-22-9B")
            self.assertTrue(claimed.claimed)
            self.assertEqual(claimed.uid, "04-A1-22-9B")
            self.assertEqual(loaded["story-token-123"].support_code, "SD-0001")

    def test_invalid_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "story_stickers.json"

            with self.assertRaises(StoryStickerError):
                create_story_sticker(path, token="bad token")

            with self.assertRaises(StoryStickerError):
                get_story_sticker(path, "bad token")

    def test_duplicate_bound_uid_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "story_stickers.json"
            create_story_sticker(path, token="story-token-123")
            create_story_sticker(path, token="story-token-456")
            bind_story_sticker_uid(path, "story-token-123", "04:a1:22:9b")

            with self.assertRaises(StoryStickerError):
                bind_story_sticker_uid(path, "story-token-456", "04-A1-22-9B")


if __name__ == "__main__":
    unittest.main()
