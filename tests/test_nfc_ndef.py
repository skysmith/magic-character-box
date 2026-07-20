import math
import unittest
from unittest.mock import patch

from magic_box.nfc import (
    NFCError,
    PN532NDEFReader,
    PN532SPIReader,
    create_reader,
    story_playback_key_from_token,
    story_playback_key_from_url,
)


ORIGIN = "https://tap.getstorydock.com"


class PN532NDEFReaderTests(unittest.TestCase):
    def test_valid_multi_page_ndef_url_returns_domain_separated_opaque_key(self) -> None:
        token = "alpha_token-123"
        fake = _FakePN532(uid=b"\x04\xA1\x22\x9B", memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")))
        reader = _ndef_reader(fake)

        key = reader.read_uid()

        self.assertEqual(
            key,
            "sdpk1_9a1a0b2715b28494d7c368b315a9aaf0d359124421d51a50cdd403f10d98d424",
        )
        self.assertGreater(len(fake.read_pages), 4)
        self.assertEqual(fake.read_pages[0], 3)
        self.assertNotIn(token, key or "")

    def test_no_tag_returns_none_without_reading_memory(self) -> None:
        fake = _FakePN532(uid=None, memory=b"")
        reader = _ndef_reader(fake)

        self.assertIsNone(reader.read_uid())
        self.assertEqual(fake.read_pages, [])

    def test_partial_page_read_fails_closed_without_uid_fallback(self) -> None:
        token = "a-token-long-enough-to-cross-several-pages"
        fake = _FakePN532(uid=b"\x04\xA1\x22\x9B", memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")))
        fake.pages.pop(6)
        reader = _ndef_reader(fake)

        with patch("magic_box.nfc.time.sleep"):
            with self.assertRaisesRegex(NFCError, "could not be verified") as raised:
                reader.read_uid()

        self.assertNotIn("04-A1-22-9B", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))
        self.assertIn("(page-read)", str(raised.exception))

    def test_transient_page_read_failures_retry_without_uid_fallback(self) -> None:
        token = "retry-token"
        fake = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
            transient_page_failures={3: 2, 4: 1},
        )
        reader = _ndef_reader(fake)

        with patch("magic_box.nfc.time.sleep") as sleep:
            key = reader.read_uid()

        self.assertEqual(key, story_playback_key_from_token(token))
        self.assertEqual(fake.page_read_attempts[3], 3)
        self.assertEqual(fake.page_read_attempts[4], 2)
        self.assertGreaterEqual(sleep.call_count, 3)

    def test_truncated_tlv_without_terminator_fails_closed(self) -> None:
        token = "truncated-token"
        message = _uri_record(f"{ORIGIN}/s/{token}")
        memory = bytes([0x03, len(message)]) + message
        fake = _FakePN532(uid=b"\x04\xA1", memory=memory, add_terminator=False)
        reader = _ndef_reader(fake)

        with self.assertRaises(NFCError) as raised:
            reader.read_uid()

        self.assertNotIn(token, str(raised.exception))
        self.assertNotIn(ORIGIN, str(raised.exception))

    def test_malformed_record_flags_fail_closed(self) -> None:
        # A standalone record must set both Message Begin and Message End.
        message = _uri_record(f"{ORIGIN}/s/malformed", header=0x91)
        fake = _FakePN532(uid=b"\x04\xA1", memory=_type2_memory(message))

        with self.assertRaises(NFCError):
            _ndef_reader(fake).read_uid()

    def test_multiple_uri_records_fail_closed(self) -> None:
        first = _uri_record(f"{ORIGIN}/s/first", header=0x91)
        second = _uri_record(f"{ORIGIN}/s/second", header=0x51)
        fake = _FakePN532(uid=b"\x04\xA1", memory=_type2_memory(first + second))

        with self.assertRaises(NFCError):
            _ndef_reader(fake).read_uid()

    def test_multiple_ndef_tlvs_fail_closed(self) -> None:
        first = _uri_record(f"{ORIGIN}/s/first")
        second = _uri_record(f"{ORIGIN}/s/second")
        memory = _tlv(first) + _tlv(second) + b"\xFE"
        fake = _FakePN532(uid=b"\x04\xA1", memory=memory, add_terminator=False)

        with self.assertRaises(NFCError):
            _ndef_reader(fake).read_uid()

    def test_wrong_origin_path_query_and_fragment_fail_closed(self) -> None:
        invalid_urls = (
            "https://example.com/s/token",
            f"{ORIGIN}/story/token",
            f"{ORIGIN}/s/token/extra",
            f"{ORIGIN}/s/token?source=test",
            f"{ORIGIN}/s/token?",
            f"{ORIGIN}/s/token#fragment",
            f"{ORIGIN}:443/s/token",
            "http://tap.getstorydock.com/s/token",
            f"{ORIGIN}/s/token%2Fextra",
            f"{ORIGIN}/s/abc",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                fake = _FakePN532(uid=b"\x04\xA1", memory=_type2_memory(_uri_record(url, prefix_code=0x00)))
                with self.assertRaises(NFCError) as raised:
                    _ndef_reader(fake).read_uid()
                self.assertNotIn(url, str(raised.exception))

    def test_same_uid_different_urls_have_different_identity(self) -> None:
        uid = b"\x04\xA1\x22\x9B"
        first = _ndef_reader(
            _FakePN532(uid=uid, memory=_type2_memory(_uri_record(f"{ORIGIN}/s/first-token")))
        )
        second = _ndef_reader(
            _FakePN532(uid=uid, memory=_type2_memory(_uri_record(f"{ORIGIN}/s/second-token")))
        )

        self.assertNotEqual(first.read_uid(), second.read_uid())

    def test_same_url_different_uids_have_same_identity(self) -> None:
        memory = _type2_memory(_uri_record(f"{ORIGIN}/s/shared-token"))
        first = _ndef_reader(_FakePN532(uid=b"\x04\xA1", memory=memory))
        second = _ndef_reader(_FakePN532(uid=b"\x04\xB2", memory=memory))

        self.assertEqual(first.read_uid(), second.read_uid())

    def test_invalid_url_is_not_logged_or_returned_as_uid(self) -> None:
        token = "private-token-value"
        url = f"https://wrong.example/s/{token}"
        fake = _FakePN532(uid=b"\x04\xA1", memory=_type2_memory(_uri_record(url, prefix_code=0x00)))

        with self.assertNoLogs("magic_box.nfc"):
            with self.assertRaises(NFCError) as raised:
                _ndef_reader(fake).read_uid()

        message = str(raised.exception)
        self.assertNotIn(token, message)
        self.assertNotIn(url, message)
        self.assertNotIn("04-A1", message)
        self.assertIn("(story-url)", message)

    def test_ordinary_pn532_mode_preserves_uid_identity(self) -> None:
        fake = _FakePN532(uid=b"\x04\xA1\x22\x9B", memory=b"")
        with patch("magic_box.nfc._open_pn532_spi", return_value=fake):
            reader = PN532SPIReader()

        self.assertEqual(reader.read_uid(), "04-A1-22-9B")
        self.assertEqual(fake.read_pages, [])

    def test_create_reader_requires_explicit_pn532_ndef_mode(self) -> None:
        fake = _FakePN532(uid=None, memory=b"")
        with patch("magic_box.nfc._open_pn532_spi", return_value=fake):
            hosted_reader = create_reader("pn532-ndef")
            maker_reader = create_reader("pn532")

        self.assertIsInstance(hosted_reader, PN532NDEFReader)
        self.assertIsInstance(maker_reader, PN532SPIReader)


class StoryPlaybackKeyTests(unittest.TestCase):
    def test_token_derivation_has_stable_domain_separated_vector(self) -> None:
        self.assertEqual(
            story_playback_key_from_token("alpha_token-123"),
            "sdpk1_9a1a0b2715b28494d7c368b315a9aaf0d359124421d51a50cdd403f10d98d424",
        )

    def test_url_derivation_accepts_only_canonical_story_path(self) -> None:
        self.assertEqual(
            story_playback_key_from_url(f"{ORIGIN}/s/alpha_token-123"),
            story_playback_key_from_token("alpha_token-123"),
        )


class _FakePN532:
    def __init__(
        self,
        *,
        uid: bytes | None,
        memory: bytes,
        add_terminator: bool = True,
        transient_page_failures: dict[int, int] | None = None,
    ) -> None:
        if add_terminator and (not memory or memory[-1] != 0xFE):
            memory += b"\xFE"
        data_units = max(1, math.ceil(len(memory) / 8))
        padded = memory.ljust(data_units * 8, b"\x00")
        self.uid = uid
        self.pages = {
            3: bytes((0xE1, 0x10, data_units, 0x00)),
            **{
                page: padded[offset : offset + 4]
                for page, offset in enumerate(range(0, len(padded), 4), start=4)
            },
        }
        self.read_pages: list[int] = []
        self.transient_page_failures = dict(transient_page_failures or {})
        self.page_read_attempts: dict[int, int] = {}

    def read_passive_target(self, *, timeout: float) -> bytes | None:
        return self.uid

    def ntag2xx_read_block(self, page: int) -> bytes | None:
        self.read_pages.append(page)
        self.page_read_attempts[page] = self.page_read_attempts.get(page, 0) + 1
        remaining = self.transient_page_failures.get(page, 0)
        if remaining > 0:
            self.transient_page_failures[page] = remaining - 1
            return None
        return self.pages.get(page)


def _ndef_reader(fake: _FakePN532) -> PN532NDEFReader:
    with patch("magic_box.nfc._open_pn532_spi", return_value=fake):
        return PN532NDEFReader(selection_settle_seconds=0)


def _uri_record(url: str, *, header: int = 0xD1, prefix_code: int = 0x04) -> bytes:
    if prefix_code == 0x04:
        assert url.startswith("https://")
        suffix = url.removeprefix("https://")
    else:
        suffix = url
    payload = bytes((prefix_code,)) + suffix.encode("utf-8")
    assert len(payload) <= 0xFF
    return bytes((header, 1, len(payload))) + b"U" + payload


def _tlv(message: bytes) -> bytes:
    assert len(message) < 0xFF
    return bytes((0x03, len(message))) + message


def _type2_memory(message: bytes) -> bytes:
    return _tlv(message) + b"\xFE"


if __name__ == "__main__":
    unittest.main()
