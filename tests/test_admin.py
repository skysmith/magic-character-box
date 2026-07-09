from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from magic_box.admin import create_app, _load_characters, _safe_character_file
from magic_box.bluetooth import BluetoothActionResult, BluetoothDevice, BluetoothStatus
from magic_box.control import consume_stop_request
from magic_box.guest_links import create_guest_link, load_guest_links
from magic_box.runtime_state import load_state, record_tag, state_file_for_config
from magic_box.story_stickers import create_story_sticker, load_story_stickers, story_stickers_file_for_config
from magic_box.system_mode import ModeActionResult, ModeStatus
from magic_box.wifi import WifiActionResult, WifiNetwork, WifiStatus


class AdminTests(unittest.TestCase):
    def test_index_renders_characters(self) -> None:
        with _temp_project() as root:
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Magic Character Box", response.data)
            self.assertIn(b"Dinosaur", response.data)
            self.assertIn(b"Photo stories", response.data)
            self.assertIn(b"Teach a character", response.data)
            self.assertIn(b"Last seen tag", response.data)
            self.assertIn(b"Box tools", response.data)
            self.assertIn(b"Wi-Fi", response.data)
            self.assertIn(b"Bluetooth experiments", response.data)
            self.assertIn(b"Setup scan", response.data)

    def test_recovery_host_redirects_to_reconnect_page(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                wifi_controller=_FakeWifiController(),
            )
            client = app.test_client()

            response = client.get("/", base_url="http://10.42.0.1:8080")

            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/reconnect"))

    def test_recovery_setup_hostname_redirects_to_reconnect_page(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                wifi_controller=_FakeWifiController(),
            )
            client = app.test_client()

            response = client.get("/", base_url="http://storydock.setup")

            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/reconnect"))

    def test_captive_portal_probe_urls_open_reconnect_page(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                wifi_controller=_FakeWifiController(),
            )
            client = app.test_client()

            for path in ("/hotspot-detect.html", "/generate_204", "/connecttest.txt", "/ncsi.txt"):
                with self.subTest(path=path):
                    response = client.get(path, base_url="http://captive.apple.com")

                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.headers["Location"].endswith("/reconnect"))

    def test_reconnect_page_is_simple_wifi_recovery_flow(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                wifi_controller=_FakeWifiController(),
            )
            client = app.test_client()

            response = client.get("/reconnect")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Let's reconnect your Story Dock.", response.data)
            self.assertIn(b"Find Wi-Fi", response.data)
            self.assertIn(b"Reconnect Story Dock", response.data)
            self.assertIn(b"story-dock-logo.svg", response.data)
            self.assertIn(b"story-dock-app-icon-192.png", response.data)
            self.assertIn(b"Device status: setup mode", response.data)
            self.assertIn(b"Open full admin", response.data)
            self.assertIn(b"Your stories and stickers stay saved.", response.data)
            self.assertNotIn(b"favicon.svg", response.data)
            self.assertNotIn(b"I've got you", response.data)
            self.assertNotIn(b"Photo stories", response.data)
            self.assertNotIn(b"Teach character", response.data)
            self.assertNotIn(b"Bluetooth experiments", response.data)

    def test_full_admin_still_available_from_recovery_page(self) -> None:
        with _temp_project() as root:
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/admin", base_url="http://10.42.0.1:8080")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Magic Character Box", response.data)
            self.assertIn(b"Photo stories", response.data)
            self.assertIn(b"Bluetooth experiments", response.data)

    def test_index_renders_last_seen_tag(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            record_tag(state_file_for_config(config_path), "04:a1:22:9b", known=False, source="test")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"04-A1-22-9B", response.data)
            self.assertIn(b"Use this tag", response.data)

    def test_load_characters_groups_aliases_for_same_character(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            data = json.loads(config_path.read_text(encoding="utf-8"))
            data["04-A1-22-9B"] = {
                "name": "Dinosaur",
                "folder": "audio/dinosaur",
                "mode": "shuffle",
            }
            config_path.write_text(json.dumps(data), encoding="utf-8")

            characters = _load_characters(config_path)

            self.assertEqual(len(characters), 1)
            self.assertEqual(characters[0].uid, "04-A1-22-9B")
            self.assertEqual(characters[0].uids, ["04-A1-22-9B", "DINOSAUR"])

    def test_register_character_updates_config(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post(
                "/characters",
                data={
                    "uid": "04:a1:22:9b",
                    "name": "Grandma Token",
                    "mode": "first",
                },
            )

            self.assertEqual(response.status_code, 302)
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("04-A1-22-9B", data)
            self.assertEqual(data["04-A1-22-9B"]["name"], "Grandma Token")
            self.assertEqual(data["04-A1-22-9B"]["folder"], "audio/grandma-token")
            self.assertTrue((root / "audio" / "grandma-token").exists())

    def test_register_character_uses_clean_slug_from_name(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post(
                "/characters",
                data={
                    "uid": "04:a1:22:9b",
                    "name": "He's got the Whole World in his Hands",
                    "mode": "first",
                },
            )

            self.assertEqual(response.status_code, 302)
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                data["04-A1-22-9B"]["folder"],
                "audio/hes-got-the-whole-world-in-his-hands",
            )
            self.assertTrue((root / "audio" / "hes-got-the-whole-world-in-his-hands").exists())

    def test_duplicate_character_names_get_numbered_folders(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            first = client.post(
                "/characters",
                data={"uid": "04:a1:22:9b", "name": "Grandma Token", "mode": "first"},
            )
            second = client.post(
                "/characters",
                data={"uid": "04:b8:10:4c", "name": "Grandma Token", "mode": "first"},
            )

            self.assertEqual(first.status_code, 302)
            self.assertEqual(second.status_code, 302)
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["04-A1-22-9B"]["folder"], "audio/grandma-token")
            self.assertEqual(data["04-B8-10-4C"]["folder"], "audio/grandma-token-2")
            self.assertTrue((root / "audio" / "grandma-token").exists())
            self.assertTrue((root / "audio" / "grandma-token-2").exists())

    def test_upload_mp3_saves_file(self) -> None:
        with _temp_project() as root:
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            with patch("magic_box.admin.prepare_playable_mp3", return_value=False):
                response = client.post(
                    "/characters/DINOSAUR/upload",
                    data={"audio": (BytesIO(b"fake mp3 data"), "roar.mp3")},
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 302)
            uploaded = list((root / "audio" / "dinosaur").glob("*roar.mp3"))
            self.assertEqual(len(uploaded), 1)

    def test_upload_mp3_ajax_returns_redirect_payload(self) -> None:
        with _temp_project() as root:
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            with patch("magic_box.admin.prepare_playable_mp3", return_value=True):
                response = client.post(
                    "/characters/DINOSAUR/upload",
                    data={"audio": (BytesIO(b"fake mp3 data"), "roar.mp3")},
                    content_type="multipart/form-data",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["redirect"], "/#character-DINOSAUR")
            uploaded = list((root / "audio" / "dinosaur").glob("*roar.mp3"))
            self.assertEqual(len(uploaded), 1)

    def test_fake_scan_queues_uid(self) -> None:
        with _temp_project() as root:
            trigger_file = root / "tags.txt"
            with _temporary_env("MAGIC_BOX_TRIGGER_FILE", str(trigger_file)):
                app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
                client = app.test_client()

                response = client.post("/characters/DINOSAUR/fake-scan")

            self.assertEqual(response.status_code, 302)
            self.assertEqual(trigger_file.read_text(encoding="utf-8"), "DINOSAUR\n")

    def test_delete_audio_file_removes_character_file(self) -> None:
        with _temp_project() as root:
            file_path = root / "audio" / "dinosaur" / "old-message.mp3"
            file_path.write_bytes(b"old mp3 data")
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post("/characters/DINOSAUR/files/old-message.mp3/delete")

            self.assertEqual(response.status_code, 302)
            self.assertFalse(file_path.exists())

    def test_delete_character_removes_all_uid_aliases_but_keeps_audio_folder(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            data = json.loads(config_path.read_text(encoding="utf-8"))
            data["04-A1-22-9B"] = {
                "name": "Dinosaur",
                "folder": "audio/dinosaur",
                "mode": "shuffle",
            }
            config_path.write_text(json.dumps(data), encoding="utf-8")
            audio_file = root / "audio" / "dinosaur" / "roar.mp3"
            audio_file.write_bytes(b"fake mp3 data")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post("/characters/DINOSAUR/delete")

            self.assertEqual(response.status_code, 302)
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("DINOSAUR", updated)
            self.assertNotIn("04-A1-22-9B", updated)
            self.assertTrue(audio_file.exists())

    def test_safe_character_file_rejects_paths_outside_folder(self) -> None:
        with _temp_project() as root:
            folder = root / "audio" / "dinosaur"
            outside_file = root / "audio" / "outside.mp3"
            outside_file.write_bytes(b"outside data")

            with self.assertRaises(ValueError):
                _safe_character_file(folder, "../outside.mp3")

            self.assertTrue(outside_file.exists())

    def test_volume_buttons_update_volume_file(self) -> None:
        with _temp_project() as root:
            volume_path = root / "config" / "volume.json"
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                volume_file=volume_path,
                default_volume=50,
            )
            client = app.test_client()

            with patch("magic_box.admin.apply_pipewire_volume", return_value=True) as apply_volume:
                response = client.post("/volume", data={"action": "up"})

            self.assertEqual(response.status_code, 302)
            self.assertEqual(json.loads(volume_path.read_text(encoding="utf-8"))["volume_percent"], 60)
            apply_volume.assert_called_with(60)

    def test_stop_audio_requests_playback_service_stop(self) -> None:
        with _temp_project() as root:
            control_path = root / "config" / "control.json"
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post("/stop")

            self.assertEqual(response.status_code, 302)
            self.assertTrue(control_path.exists())
            self.assertTrue(consume_stop_request(control_path))

    def test_create_guest_recording_link_from_admin(self) -> None:
        with _temp_project() as root:
            guest_links_path = root / "config" / "guest_links.json"
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post(
                "/guest-links",
                data={
                    "uid": "DINOSAUR",
                    "label": "Grandma roar",
                    "expires_days": "5",
                    "base_url": "https://example.trycloudflare.com",
                },
            )

            self.assertEqual(response.status_code, 302)
            links = load_guest_links(guest_links_path)
            self.assertEqual(len(links), 1)
            link = next(iter(links.values()))
            self.assertEqual(link.uid, "DINOSAUR")
            self.assertEqual(link.label, "Grandma roar")
            self.assertEqual(link.base_url, "https://example.trycloudflare.com")

            page = client.get("/")
            self.assertIn(b"https://example.trycloudflare.com/guest/", page.data)
            self.assertIn(b"/guest/", page.data)
            self.assertIn(b"/qr.svg", page.data)

            qr = client.get(f"/guest/{link.token}/qr.svg")
            self.assertEqual(qr.status_code, 200)
            self.assertEqual(qr.mimetype, "image/svg+xml")
            self.assertIn(b"<svg", qr.data)

    def test_create_photo_story_creates_character_and_guest_link(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            guest_links_path = root / "config" / "guest_links.json"
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post(
                "/photo-stories",
                data={
                    "uid": "04:a1:22:9b",
                    "name": "Grandma at Yellowstone, 1974",
                    "label": "Grandma tells this story",
                    "expires_days": "7",
                    "base_url": "https://example.trycloudflare.com",
                },
            )

            self.assertEqual(response.status_code, 302)
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["04-A1-22-9B"]["name"], "Grandma at Yellowstone, 1974")
            self.assertEqual(data["04-A1-22-9B"]["folder"], "audio/grandma-at-yellowstone-1974")
            self.assertEqual(data["04-A1-22-9B"]["mode"], "first")
            self.assertEqual(data["04-A1-22-9B"]["kind"], "photo_story")
            self.assertTrue((root / "audio" / "grandma-at-yellowstone-1974").exists())

            links = load_guest_links(guest_links_path)
            self.assertEqual(len(links), 1)
            link = next(iter(links.values()))
            self.assertEqual(link.uid, "04-A1-22-9B")
            self.assertEqual(link.label, "Grandma tells this story")
            self.assertEqual(link.base_url, "https://example.trycloudflare.com")

    def test_create_story_sticker_link_from_admin(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post(
                "/story-stickers",
                data={
                    "uid": "04:a1:22:9b",
                    "support_code": "SD-0001",
                },
            )

            self.assertEqual(response.status_code, 302)
            stickers = load_story_stickers(stickers_path)
            self.assertEqual(len(stickers), 1)
            sticker = next(iter(stickers.values()))
            self.assertEqual(sticker.uid, "04-A1-22-9B")
            self.assertEqual(sticker.support_code, "SD-0001")

            page = client.get("/")
            self.assertIn(b"Story stickers", page.data)
            self.assertIn(b"SD-0001", page.data)
            self.assertIn(b"/story/", page.data)
            self.assertIn(b"/qr.svg", page.data)

            qr = client.get(f"/story/{sticker.token}/qr.svg")
            self.assertEqual(qr.status_code, 200)
            self.assertEqual(qr.mimetype, "image/svg+xml")
            self.assertIn(b"<svg", qr.data)

    def test_story_sticker_phone_link_claims_story_and_saves_audio(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", uid="04:a1:22:9b", support_code="SD-0001")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            page = client.get("/story/story-token-123")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Record this memory", page.data)

            with patch("magic_box.admin.prepare_playable_mp3", return_value=False):
                response = client.post(
                    "/story/story-token-123/recordings",
                    data={
                        "story_name": "Grandma at Yellowstone, 1974",
                        "title": "first story",
                        "recording": (BytesIO(b"fake mp3 data"), "story.mp3"),
                    },
                    content_type="multipart/form-data",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["can_play_on_box"])

            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["04-A1-22-9B"]["name"], "Grandma at Yellowstone, 1974")
            self.assertEqual(data["04-A1-22-9B"]["folder"], "audio/grandma-at-yellowstone-1974")
            self.assertEqual(data["04-A1-22-9B"]["kind"], "photo_story")
            self.assertEqual(data["04-A1-22-9B"]["story_token"], "story-token-123")
            uploaded = list((root / "audio" / "grandma-at-yellowstone-1974").glob("*story.mp3"))
            self.assertEqual(len(uploaded), 1)

    def test_mobile_story_sticker_api_returns_app_contract(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", support_code="SD-0001")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/api/mobile/story-stickers/story-token-123")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["story_sticker"]["support_code"], "SD-0001")
            self.assertEqual(payload["story_sticker"]["status"], "unclaimed")
            self.assertEqual(payload["story_sticker"]["next_action"], "name_and_record")
            self.assertEqual(payload["story_sticker"]["recordings"], [])
            self.assertIn("/api/mobile/story-stickers/story-token-123/recordings", payload["links"]["upload"])
            self.assertIn("/story/story-token-123", payload["links"]["web"])

    def test_mobile_story_sticker_api_includes_recordings(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", uid="04:a1:22:9b")
            audio_folder = root / "audio" / "grandma-at-yellowstone"
            audio_folder.mkdir()
            from magic_box.story_stickers import claim_story_sticker

            claim_story_sticker(
                stickers_path,
                "story-token-123",
                uid="04:a1:22:9b",
                name="Grandma at Yellowstone",
                folder="audio/grandma-at-yellowstone",
            )
            (audio_folder / "story.mp3").write_bytes(b"fake mp3 data")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/api/mobile/story-stickers/story-token-123")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            recordings = payload["story_sticker"]["recordings"]
            self.assertEqual(len(recordings), 1)
            self.assertEqual(recordings[0]["filename"], "story.mp3")
            self.assertIn("/api/mobile/story-stickers/story-token-123/recordings/story.mp3", recordings[0]["url"])

            audio = client.get(recordings[0]["url"])
            self.assertEqual(audio.status_code, 200)
            self.assertEqual(audio.data, b"fake mp3 data")
            audio.close()

    def test_mobile_story_sticker_upload_returns_json_without_ajax_header(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", uid="04:a1:22:9b")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            with patch("magic_box.admin.prepare_playable_mp3", return_value=False):
                response = client.post(
                    "/api/mobile/story-stickers/story-token-123/recordings",
                    data={
                        "story_name": "Grandma at Yellowstone",
                        "recording": (BytesIO(b"fake mp3 data"), "story.mp3"),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["story"], "Grandma at Yellowstone")
            self.assertTrue(payload["can_play_on_box"])
            self.assertEqual(payload["story_sticker"]["playable_count"], 1)
            self.assertEqual(payload["story_sticker"]["status"], "ready_for_dock")

    def test_story_sticker_links_can_use_public_story_base_url(self) -> None:
        with _temp_project() as root, _temporary_env("MAGIC_BOX_PUBLIC_STORY_BASE_URL", "https://tap.getstorydock.com"):
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", support_code="SD-0001")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            page = client.get("/")
            api = client.get("/api/mobile/story-stickers/story-token-123")

            self.assertIn(b"https://tap.getstorydock.com/story/story-token-123", page.data)
            payload = api.get_json()
            assert payload is not None
            self.assertEqual(payload["links"]["web"], "https://tap.getstorydock.com/story/story-token-123")
            self.assertEqual(
                payload["links"]["upload"],
                "https://tap.getstorydock.com/api/mobile/story-stickers/story-token-123/recordings",
            )

    def test_guest_only_mode_allows_story_sticker_mobile_api(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", support_code="SD-0001")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True, guest_only=True)
            client = app.test_client()

            dashboard = client.get("/")
            api = client.get("/api/mobile/story-stickers/story-token-123")

            self.assertEqual(dashboard.status_code, 404)
            self.assertEqual(api.status_code, 200)
            payload = api.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])

    def test_dock_manifest_includes_story_sticker_mapping(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            stickers_path = story_stickers_file_for_config(config_path)
            create_story_sticker(stickers_path, token="story-token-123", uid="04:a1:22:9b")
            data = json.loads(config_path.read_text(encoding="utf-8"))
            data["04-A1-22-9B"] = {
                "name": "Grandma at Yellowstone",
                "folder": "audio/grandma-at-yellowstone",
                "mode": "first",
                "kind": "photo_story",
                "story_token": "story-token-123",
            }
            (root / "audio" / "grandma-at-yellowstone").mkdir()
            (root / "audio" / "grandma-at-yellowstone" / "story.mp3").write_bytes(b"fake mp3 data")
            config_path.write_text(json.dumps(data), encoding="utf-8")
            app = create_app(config_path, nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/api/dock/manifest")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertEqual(payload["schema"], "story-dock-manifest-v1")
            story = next(item for item in payload["stories"] if item["name"] == "Grandma at Yellowstone")
            self.assertEqual(story["story_tokens"], ["story-token-123"])
            self.assertEqual(story["files"][0]["filename"], "story.mp3")

    def test_guest_link_external_url_respects_proxy_headers(self) -> None:
        with _temp_project() as root, _temporary_env("MAGIC_BOX_TRUST_PROXY_HEADERS", "1"):
            guest_links_path = root / "config" / "guest_links.json"
            create_guest_link(guest_links_path, uid="DINOSAUR", label="Grandma roar", token="guest-token-123")
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get(
                "/",
                headers={
                    "Host": "your-box.your-tailnet.ts.net",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "your-box.your-tailnet.ts.net",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"https://your-box.your-tailnet.ts.net/guest/guest-token-123", response.data)
            self.assertIn(b"Private Tailscale HTTPS", response.data)

    def test_guest_link_form_defaults_to_preferred_base_url(self) -> None:
        with _temp_project() as root, _temporary_env(
            "MAGIC_BOX_PREFERRED_GUEST_BASE_URL",
            "https://your-box.your-tailnet.ts.net/",
        ):
            guest_links_path = root / "config" / "guest_links.json"
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            page = client.get("/")
            self.assertIn(b'value="https://your-box.your-tailnet.ts.net"', page.data)

            response = client.post(
                "/guest-links",
                data={"uid": "DINOSAUR", "label": "Grandma roar", "expires_days": "5"},
            )

            self.assertEqual(response.status_code, 302)
            link = next(iter(load_guest_links(guest_links_path).values()))
            self.assertEqual(link.base_url, "https://your-box.your-tailnet.ts.net")

    def test_guest_recorder_page_renders_for_valid_token(self) -> None:
        with _temp_project() as root:
            guest_links_path = root / "config" / "guest_links.json"
            create_guest_link(guest_links_path, uid="DINOSAUR", label="Grandma roar", token="guest-token-123")
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/guest/guest-token-123")

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Send a message for Dinosaur", response.data)
            self.assertIn(b"Upload voice memo", response.data)

    def test_guest_recorder_upload_saves_audio(self) -> None:
        with _temp_project() as root:
            guest_links_path = root / "config" / "guest_links.json"
            create_guest_link(guest_links_path, uid="DINOSAUR", label="Grandma roar", token="guest-token-123")
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            with patch("magic_box.admin.prepare_playable_mp3", return_value=False):
                response = client.post(
                    "/guest/guest-token-123/recordings",
                    data={"title": "hello", "recording": (BytesIO(b"fake mp3 data"), "hello.mp3")},
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["character"], "Dinosaur")
            uploaded = list((root / "audio" / "dinosaur").glob("*hello.mp3"))
            self.assertEqual(len(uploaded), 1)

    def test_expired_guest_recorder_page_returns_410(self) -> None:
        with _temp_project() as root:
            guest_links_path = root / "config" / "guest_links.json"
            create_guest_link(guest_links_path, uid="DINOSAUR", label="Grandma roar", token="guest-token-123")
            data = json.loads(guest_links_path.read_text(encoding="utf-8"))
            data["guest-token-123"]["expires_at"] = "2000-01-01T00:00:00Z"
            guest_links_path.write_text(json.dumps(data), encoding="utf-8")
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/guest/guest-token-123")

            self.assertEqual(response.status_code, 410)
            self.assertIn(b"This link is not available", response.data)

    def test_guest_only_mode_blocks_admin_dashboard(self) -> None:
        with _temp_project() as root:
            guest_links_path = root / "config" / "guest_links.json"
            create_guest_link(guest_links_path, uid="DINOSAUR", label="Grandma roar", token="guest-token-123")
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                guest_only=True,
            )
            client = app.test_client()

            dashboard = client.get("/")
            guest = client.get("/guest/guest-token-123")

            self.assertEqual(dashboard.status_code, 404)
            self.assertIn(b"temporary recording doorway", dashboard.data)
            self.assertEqual(guest.status_code, 200)
            self.assertIn(b"Send a message for Dinosaur", guest.data)

    def test_bluetooth_status_endpoint_returns_devices(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                bluetooth_controller=_FakeBluetoothController(),
            )
            client = app.test_client()

            response = client.get("/api/bluetooth/status")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["available"])
            self.assertEqual(payload["devices"][0]["name"], "Sony Speaker")

    def test_wifi_status_endpoint_returns_redacted_network_state(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                wifi_controller=_FakeWifiController(),
            )
            client = app.test_client()

            response = client.get("/api/wifi/status")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["available"])
            self.assertEqual(payload["ssid"], "Cottage")
            self.assertNotIn("password", json.dumps(payload).lower())

    def test_wifi_connect_endpoint_sends_credentials_to_controller_only(self) -> None:
        with _temp_project() as root:
            wifi = _FakeWifiController()
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                wifi_controller=wifi,
            )
            client = app.test_client()

            response = client.post("/api/wifi/connect", json={"ssid": "Mini Cottage", "password": "private pass"})

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertEqual(wifi.connect_calls, [("Mini Cottage", "private pass")])
            self.assertNotIn("private pass", json.dumps(payload))

    def test_bluetooth_device_action_validates_action(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                bluetooth_controller=_FakeBluetoothController(),
            )
            client = app.test_client()

            response = client.post("/api/bluetooth/devices/AA:BB:CC:DD:EE:FF/use-audio")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["message"], "Selected Sony Speaker.")

    def test_mode_setup_endpoint_stops_playback_service(self) -> None:
        with _temp_project() as root:
            mode = _FakeModeController(playback_active=True)
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="mock",
                dry_run_audio=True,
                mode_controller=mode,
            )
            client = app.test_client()

            response = client.post("/api/mode/setup")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertEqual(payload["mode"], "setup")
            self.assertFalse(mode.playback_active)

    def test_scan_requires_setup_mode_when_playback_service_is_active(self) -> None:
        with _temp_project() as root:
            app = create_app(
                root / "config" / "characters.json",
                nfc_backend="pn532",
                dry_run_audio=True,
                mode_controller=_FakeModeController(playback_active=True),
            )
            client = app.test_client()

            response = client.get("/api/scan?timeout=1")

            self.assertEqual(response.status_code, 409)
            payload = response.get_json()
            assert payload is not None
            self.assertIn("Switch to setup mode", payload["message"])

    def test_scan_records_last_seen_tag(self) -> None:
        with _temp_project() as root:
            config_path = root / "config" / "characters.json"
            trigger_file = root / "tags.txt"
            trigger_file.write_text("04:a1:22:9b\n", encoding="utf-8")
            with _temporary_env("MAGIC_BOX_TRIGGER_FILE", str(trigger_file)):
                app = create_app(
                    config_path,
                    nfc_backend="file",
                    dry_run_audio=True,
                    mode_controller=_FakeModeController(playback_active=False),
                )
                client = app.test_client()

                response = client.get("/api/scan?timeout=1")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertEqual(payload["uid"], "04-A1-22-9B")
            self.assertEqual(payload["last_tag"]["uid"], "04-A1-22-9B")
            state = load_state(state_file_for_config(config_path))
            self.assertEqual(state["last_tag"]["uid"], "04-A1-22-9B")

    def test_diagnostics_endpoint_returns_checks(self) -> None:
        with _temp_project() as root:
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post("/api/diagnostics")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            assert payload is not None
            self.assertTrue(payload["ok"])
            labels = {item["label"] for item in payload["checks"]}
            self.assertIn("NFC scan", labels)
            self.assertIn("Audio", labels)

    def test_backup_zip_includes_config_and_audio(self) -> None:
        with _temp_project() as root:
            audio_file = root / "audio" / "dinosaur" / "roar.mp3"
            audio_file.write_bytes(b"fake mp3 data")
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.get("/backup.zip")

            self.assertEqual(response.status_code, 200)
            with ZipFile(BytesIO(response.data)) as archive:
                names = set(archive.namelist())
            self.assertIn("config/characters.json", names)
            self.assertIn("audio/dinosaur/roar.mp3", names)

    def test_shutdown_uses_configured_command(self) -> None:
        with _temp_project() as root, _temporary_env("MAGIC_BOX_SHUTDOWN_COMMAND", f"{sys.executable} -c pass"):
            app = create_app(root / "config" / "characters.json", nfc_backend="mock", dry_run_audio=True)
            client = app.test_client()

            response = client.post("/shutdown")

            self.assertEqual(response.status_code, 302)
            state = load_state(state_file_for_config(root / "config" / "characters.json"))
            self.assertIn("Shutdown requested", state["events"][0]["message"])


class _temp_project:
    def __enter__(self) -> Path:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        (root / "config").mkdir()
        (root / "audio" / "dinosaur").mkdir(parents=True)
        (root / "config" / "characters.json").write_text(
            json.dumps(
                {
                    "DINOSAUR": {
                        "name": "Dinosaur",
                        "folder": "audio/dinosaur",
                        "mode": "shuffle",
                    }
                }
            ),
            encoding="utf-8",
        )
        return root

    def __exit__(self, *_exc: object) -> None:
        self._temp_dir.cleanup()


class _temporary_env:
    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value
        self.original: str | None = None

    def __enter__(self) -> None:
        import os

        self.original = os.environ.get(self.key)
        os.environ[self.key] = self.value

    def __exit__(self, *_exc: object) -> None:
        import os

        if self.original is None:
            os.environ.pop(self.key, None)
        else:
            os.environ[self.key] = self.original


class _FakeBluetoothController:
    def status(self) -> BluetoothStatus:
        return BluetoothStatus(
            available=True,
            message="Bluetooth adapter ready.",
            powered=True,
            discovering=False,
            default_sink="Sony Speaker",
            devices=(
                BluetoothDevice(
                    address="AA:BB:CC:DD:EE:FF",
                    name="Sony Speaker",
                    paired=True,
                    trusted=True,
                    connected=True,
                    audio=True,
                ),
            ),
        )

    def scan(self, timeout: int = 8) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message=f"Scanned for {timeout}s.", status=self.status())

    def power(self, enabled: bool) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message=f"Power {'on' if enabled else 'off'}.", status=self.status())

    def pair(self, address: str) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message=f"Paired {address}.", status=self.status())

    def trust(self, address: str) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message=f"Trusted {address}.", status=self.status())

    def connect(self, address: str) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message=f"Connected {address}.", status=self.status())

    def disconnect(self, address: str) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message=f"Disconnected {address}.", status=self.status())

    def use_for_audio(self, address: str) -> BluetoothActionResult:
        return BluetoothActionResult(ok=True, message="Selected Sony Speaker.", status=self.status())


class _FakeWifiController:
    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, str]] = []

    def status(self) -> WifiStatus:
        return WifiStatus(
            available=True,
            message="Connected to Cottage.",
            powered=True,
            connected=True,
            ssid="Cottage",
            device="wlan0",
            networks=(),
        )

    def scan(self) -> WifiActionResult:
        return WifiActionResult(
            ok=True,
            message="Wi-Fi scan complete.",
            status=WifiStatus(
                available=True,
                message="Wi-Fi scan complete.",
                powered=True,
                connected=True,
                ssid="Cottage",
                device="wlan0",
                networks=(
                    WifiNetwork(ssid="Cottage", signal=88, security="WPA2", active=True),
                    WifiNetwork(ssid="Mini Cottage", signal=74, security="WPA2"),
                ),
            ),
        )

    def connect(self, ssid: str, password: str = "") -> WifiActionResult:
        self.connect_calls.append((ssid, password))
        return WifiActionResult(
            ok=True,
            message=f"Connected to {ssid}.",
            status=WifiStatus(
                available=True,
                message=f"Connected to {ssid}.",
                powered=True,
                connected=True,
                ssid=ssid,
                device="wlan0",
            ),
        )


class _FakeModeController:
    def __init__(self, playback_active: bool) -> None:
        self.playback_active = playback_active

    def status(self) -> ModeStatus:
        return ModeStatus(
            available=True,
            playback_active=self.playback_active,
            message="Playback mode." if self.playback_active else "Setup mode.",
        )

    def enter_setup(self) -> ModeActionResult:
        self.playback_active = False
        return ModeActionResult(ok=True, message="Setup mode is on.", status=self.status())

    def enter_playback(self) -> ModeActionResult:
        self.playback_active = True
        return ModeActionResult(ok=True, message="Playback mode is on.", status=self.status())


if __name__ == "__main__":
    unittest.main()
