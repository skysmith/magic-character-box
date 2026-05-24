import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from magic_box.dock_sync import HostedDockSync


class HostedDockSyncTests(unittest.TestCase):
    def test_sync_downloads_audio_and_writes_character_mapping(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config" / "characters.json"
            config_path.parent.mkdir()
            config_path.write_text(json.dumps({"DINOSAUR": {"name": "Dinosaur", "folder": "audio/dinosaur", "mode": "first"}}))
            (root / "audio" / "dinosaur").mkdir(parents=True)
            downloads: list[str] = []

            def fetch_json(url: str, token: str):
                self.assertEqual(url, "https://tap.test/api/hosted/docks/dock_1/manifest")
                self.assertEqual(token, "dock-secret")
                return {
                    "ok": True,
                    "schema": "story-dock-hosted-manifest-v1",
                    "stories": [
                        {
                            "id": "story_123456789",
                            "name": "Grandma at Yellowstone",
                            "uids": ["04:a1:22:9b"],
                            "recordings": [
                                {
                                    "id": "rec_1",
                                    "filename": "voice memo.mp3",
                                    "download_url": "/api/hosted/docks/dock_1/recordings/rec_1",
                                }
                            ],
                        }
                    ],
                }

            def fetch_bytes(url: str, token: str) -> bytes:
                downloads.append(url)
                self.assertEqual(token, "dock-secret")
                return b"downloaded audio"

            result = HostedDockSync(
                manifest_url="https://tap.test/api/hosted/docks/dock_1/manifest",
                dock_secret="dock-secret",
                config_path=config_path,
                fetch_json=fetch_json,
                fetch_bytes=fetch_bytes,
            ).sync()

            data = json.loads(config_path.read_text())
            mapped = data["04-A1-22-9B"]
            audio_path = root / mapped["folder"] / "voice-memo.mp3"

            self.assertEqual(result.stories_synced, 1)
            self.assertEqual(result.uids_mapped, ["04-A1-22-9B"])
            self.assertEqual(downloads, ["https://tap.test/api/hosted/docks/dock_1/recordings/rec_1"])
            self.assertEqual(mapped["name"], "Grandma at Yellowstone")
            self.assertEqual(mapped["kind"], "photo_story")
            self.assertEqual(mapped["source"], "hosted")
            self.assertEqual(mapped["hosted_story_id"], "story_123456789")
            self.assertEqual(audio_path.read_bytes(), b"downloaded audio")
            self.assertIn("DINOSAUR", data)

    def test_sync_skips_unplayable_stories_without_erasing_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config" / "characters.json"
            config_path.parent.mkdir()
            config_path.write_text("{}")

            result = HostedDockSync(
                manifest_url="https://tap.test/manifest",
                dock_secret="dock-secret",
                config_path=config_path,
                fetch_json=lambda _url, _token: {
                    "ok": True,
                    "schema": "story-dock-hosted-manifest-v1",
                    "stories": [
                        {"id": "story_no_uid", "name": "No UID", "recordings": [{"filename": "a.mp3"}]},
                        {"id": "story_no_audio", "name": "No Audio", "uids": ["DAD"], "recordings": []},
                    ],
                },
                fetch_bytes=lambda _url, _token: b"",
            ).sync()

            self.assertEqual(result.stories_seen, 2)
            self.assertEqual(result.stories_synced, 0)
            self.assertEqual(json.loads(config_path.read_text()), {})
            self.assertIn("No UID: no NFC UID", result.skipped_stories)
            self.assertIn("No Audio: no playable recordings", result.skipped_stories)


if __name__ == "__main__":
    unittest.main()
