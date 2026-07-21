import json
import math
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from magic_box.app import _handle_service_stop, main
from magic_box.config import story_locator_lookup_key
from magic_box.nfc import PN532NDEFReader


ORIGIN = "https://tap.getstorydock.com"


class StoryStickerPlaybackPipelineTests(unittest.TestCase):
    def test_v2_locator_window_selects_manifest_audio_after_transient_read_failures(self) -> None:
        """Exercise V2 one-window identity, Manifest lookup, and playback selection."""
        playback_key = story_locator_lookup_key("SD03-0001", "ABCD")
        fake_pn532 = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(
                _uri_record(f"{ORIGIN}/s/SD03-0001#ABCD.synthetic-private-token")
            ),
            transient_page_failures={11: 2},
        )
        with patch("magic_box.nfc._open_pn532_spi", return_value=fake_pn532):
            ndef_reader = PN532NDEFReader()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected_folder = root / "audio" / "url-selected"
            legacy_uid_folder = root / "audio" / "wrong-uid-fallback"
            expected_folder.mkdir(parents=True)
            legacy_uid_folder.mkdir(parents=True)
            (expected_folder / "memory.mp3").write_bytes(b"synthetic audio fixture")
            (legacy_uid_folder / "wrong.mp3").write_bytes(b"must not be selected")

            config_path = root / "config" / "characters.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps(
                    {
                        playback_key: {
                            "name": "URL-selected memory",
                            "folder": "audio/url-selected",
                            "mode": "first",
                        },
                        # A conflicting legacy UID entry makes UID fallback observable.
                        "04-A1-22-9B": {
                            "name": "Wrong UID fallback",
                            "folder": "audio/wrong-uid-fallback",
                            "mode": "first",
                        },
                    }
                ),
                encoding="utf-8",
            )

            player = MagicMock()
            player.play_folder.return_value = True
            with patch(
                "magic_box.app.create_reader",
                return_value=_OneNDEFTagThenTerminateReader(ndef_reader),
            ), patch(
                "magic_box.app.AudioPlayer",
                return_value=player,
            ), patch(
                "magic_box.app._safe_record_tag",
            ) as record_tag, patch(
                "magic_box.app._safe_append_event",
            ), patch(
                "magic_box.nfc.time.sleep",
            ), self.assertLogs("magic_box.app", level="INFO"):
                result = main(
                    [
                        "--config",
                        str(config_path),
                        "--nfc",
                        "pn532-ndef",
                        "--startup-sound",
                        "",
                        "--unknown-sound",
                        "",
                    ]
                )

        self.assertEqual(result, 0)
        player.play_folder.assert_called_once_with(expected_folder.resolve(), "first")
        record_tag.assert_called_once_with(
            unittest.mock.ANY,
            playback_key,
            known=True,
            character_name="URL-selected memory",
            source="playback",
        )
        self.assertEqual(fake_pn532.page_read_attempts, {11: 3})
        self.assertEqual(fake_pn532.selection_attempts, 3)


class _OneNDEFTagThenTerminateReader:
    def __init__(self, reader: PN532NDEFReader) -> None:
        self.reader = reader
        self.delivered = False

    def read_uid(self) -> str | None:
        if not self.delivered:
            self.delivered = True
            return self.reader.read_uid()
        _handle_service_stop(signal.SIGTERM, None)
        return None


class _FakePN532:
    def __init__(
        self,
        *,
        uid: bytes,
        memory: bytes,
        transient_page_failures: dict[int, int],
    ) -> None:
        data_units = max(18, math.ceil(len(memory) / 8))
        padded = memory.ljust(data_units * 8, b"\x00")
        self.uid = uid
        self.pages = {
            3: bytes((0xE1, 0x10, data_units, 0x00)),
            **{
                page: padded[offset : offset + 4]
                for page, offset in enumerate(range(0, len(padded), 4), start=4)
            },
        }
        self.transient_page_failures = dict(transient_page_failures)
        self.page_read_attempts: dict[int, int] = {}
        self.selection_attempts = 0

    def read_passive_target(self, *, timeout: float) -> bytes:
        self.selection_attempts += 1
        return self.uid

    def mifare_classic_read_block(self, page: int) -> bytes | None:
        self.page_read_attempts[page] = self.page_read_attempts.get(page, 0) + 1
        remaining = self.transient_page_failures.get(page, 0)
        if remaining:
            self.transient_page_failures[page] = remaining - 1
            return None
        pages = [self.pages.get(page + offset) for offset in range(4)]
        if any(value is None for value in pages):
            return None
        return b"".join(value for value in pages if value is not None)


def _uri_record(url: str) -> bytes:
    suffix = url.removeprefix("https://")
    payload = b"\x04" + suffix.encode("utf-8")
    return bytes((0xD1, 1, len(payload))) + b"U" + payload


def _type2_memory(message: bytes) -> bytes:
    return bytes((0x03, len(message))) + message + b"\xFE"


if __name__ == "__main__":
    unittest.main()
