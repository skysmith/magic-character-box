import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
import ssl
import urllib.error
import urllib.request

from magic_box.dock_sync import DockSyncError, HostedDockSync, _NoRedirect, _request


class HostedDockSyncTests(unittest.TestCase):
    def test_transport_uses_maintained_trust_despite_stale_environment(self) -> None:
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b"audio"
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/nonexistent/stale-ca.pem"}):
            with patch("magic_box.dock_sync.urllib.request.build_opener", return_value=opener) as build:
                self.assertEqual(_request("https://tap.test/manifest", "synthetic-token"), b"audio")
        handlers = build.call_args.args
        context = next(h._context for h in handlers if isinstance(h, urllib.request.HTTPSHandler))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        names = {value for cert in context.get_ca_certs() for rdn in cert["subject"] for key, value in rdn if key == "commonName"}
        self.assertFalse(names & {"AAA Certificate Services", "emSign ECC Root CA - G3", "emSign Root TLS CA - G3"})
        self.assertTrue(any(isinstance(h, _NoRedirect) for h in handlers))
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 60})

    def test_transport_refuses_plain_http_before_sending_credentials(self) -> None:
        with patch("magic_box.dock_sync.urllib.request.build_opener") as build:
            with self.assertRaises(DockSyncError):
                _request("http://tap.test/manifest", "synthetic-token")
            build.assert_not_called()

    def test_transport_refuses_redirects(self) -> None:
        request = urllib.request.Request("https://tap.test/manifest")
        with self.assertRaises(urllib.error.HTTPError):
            _NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://other.test/")

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
