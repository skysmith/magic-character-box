from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from magic_box.guest_links import (
    GuestLinkError,
    create_guest_link,
    get_guest_link,
    guest_links_file_for_config,
    load_guest_links,
    revoke_guest_link,
)


class GuestLinkTests(unittest.TestCase):
    def test_guest_links_file_defaults_beside_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"

            self.assertEqual(guest_links_file_for_config(config_path), config_path.parent.resolve() / "guest_links.json")

    def test_create_and_load_guest_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guest_links.json"

            created = create_guest_link(
                path,
                uid="dad",
                label="Grandma",
                expires_days=7,
                token="guest-token-123",
                base_url="https://example.trycloudflare.com/",
            )
            loaded = load_guest_links(path)

            self.assertIn("guest-token-123", loaded)
            self.assertEqual(created.uid, "DAD")
            self.assertEqual(loaded["guest-token-123"].label, "Grandma")
            self.assertEqual(loaded["guest-token-123"].base_url, "https://example.trycloudflare.com")
            self.assertFalse(loaded["guest-token-123"].is_expired())

    def test_invalid_guest_link_base_url_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guest_links.json"

            with self.assertRaises(GuestLinkError):
                create_guest_link(path, uid="dad", token="guest-token-123", base_url="example.com")

    def test_expired_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guest_links.json"
            create_guest_link(path, uid="dad", label="Grandma", expires_days=1, token="guest-token-123")
            data = path.read_text(encoding="utf-8")
            path.write_text(data.replace(datetime.now(timezone.utc).strftime("%Y"), "2000"), encoding="utf-8")

            with self.assertRaises(GuestLinkError):
                get_guest_link(path, "guest-token-123")

    def test_revoke_guest_link_removes_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guest_links.json"
            create_guest_link(path, uid="dad", token="guest-token-123")

            self.assertTrue(revoke_guest_link(path, "guest-token-123"))
            self.assertFalse(revoke_guest_link(path, "guest-token-123"))
            self.assertNotIn("guest-token-123", load_guest_links(path))


if __name__ == "__main__":
    unittest.main()
