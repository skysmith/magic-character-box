import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from magic_box.config import story_locator_lookup_key
from magic_box.nfc import (
    NFCError,
    PN532NDEFReader,
    PN532SPIReader,
    _configure_pn532_communication_retries,
    _configure_pn532_type2_receiver_gain,
    create_reader,
    story_playback_key_from_token,
    story_playback_key_from_url,
)


ORIGIN = "https://tap.getstorydock.com"


class PN532NDEFReaderTests(unittest.TestCase):
    def test_configures_three_pn532_native_communication_retries(self) -> None:
        fake = _FakeRFConfiguration(response=b"")

        _configure_pn532_communication_retries(fake)

        self.assertEqual(fake.calls, [(0x32, [0x04, 0x03])])

    def test_rejects_missing_pn532_retry_configuration_response(self) -> None:
        fake = _FakeRFConfiguration(response=None)

        with self.assertRaisesRegex(NFCError, "communication retries"):
            _configure_pn532_communication_retries(fake)

    def test_uid_reader_does_not_call_hosted_rf_retry_configuration(self) -> None:
        fake_hardware = MagicMock()

        with patch.dict(sys.modules, _fake_pn532_modules(fake_hardware)), patch(
            "magic_box.nfc._configure_pn532_communication_retries"
        ) as configure_retries:
            reader = PN532SPIReader()

        self.assertIs(reader._pn532, fake_hardware)
        fake_hardware.SAM_configuration.assert_called_once_with()
        configure_retries.assert_not_called()

    def test_ndef_reader_explicitly_enables_hosted_type2_receiver_profile(self) -> None:
        fake = _FakePN532(uid=None, memory=b"")

        with patch("magic_box.nfc._open_pn532_spi", return_value=fake) as open_reader:
            PN532NDEFReader()

        open_reader.assert_called_once_with(type2_receiver=True)

    def test_type2_receiver_profile_matches_deployed_gain_and_field_cycle(self) -> None:
        fake = _FakeReceiverConfiguration(current=b"\x12")

        with patch("magic_box.nfc.time.sleep") as sleep:
            _configure_pn532_type2_receiver_gain(fake)

        self.assertEqual(
            fake.calls,
            [
                (0x06, [0x63, 0x16], 1),
                (0x32, [0x01, 0x00], None),
                (0x08, [0x63, 0x16, 0x72], None),
                (0x32, [0x01, 0x01], None),
            ],
        )
        sleep.assert_called_once_with(0.10)

    def test_receiver_profile_reenables_field_when_gain_write_fails(self) -> None:
        fake = _FakeReceiverConfiguration(current=b"\x00", fail_write=True)

        with self.assertRaisesRegex(NFCError, "receiver gain"):
            _configure_pn532_type2_receiver_gain(fake)

        self.assertEqual(fake.calls[-1], (0x32, [0x01, 0x01], None))

    def test_valid_multi_page_ndef_url_returns_domain_separated_opaque_key(self) -> None:
        token = "alpha_token-123"
        fake = _FakePN532(uid=b"\x04\xA1\x22\x9B", memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")))
        reader = _ndef_reader(fake)

        key = reader.read_uid()

        self.assertEqual(
            key,
            "sdpk1_9a1a0b2715b28494d7c368b315a9aaf0d359124421d51a50cdd403f10d98d424",
        )
        self.assertEqual(fake.read_pages[:3], [11, 4, 3])
        self.assertLessEqual(len(fake.read_pages), 6)
        self.assertNotIn(token, key or "")

    def test_v2_url_returns_locator_key_from_exactly_one_page_11_window(self) -> None:
        private_token = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        url = f"{ORIGIN}/s/SD03-0001/{private_token}"
        fake = _FakePN532(uid=b"\x04\xA1\x22\x9B", memory=_type2_memory(_uri_record(url)))

        key = _ndef_reader(fake).read_uid()

        self.assertEqual(key, story_locator_lookup_key("SD03-0001", "ABCD"))
        self.assertEqual(fake.read_pages, [11])
        self.assertEqual(fake.selection_attempts, 1)
        self.assertNotIn(private_token, key or "")

    def test_v2_fast_window_uses_exact_case_sensitive_token_prefix(self) -> None:
        for private_token in (
            "WXYZ" + "x" * 28,
            "a-_2" + "y" * 28,
        ):
            with self.subTest(prefix=private_token[:4]):
                url = f"{ORIGIN}/s/SD03-0001/{private_token}"
                fake = _FakePN532(uid=b"\x04\xA1", memory=_type2_memory(_uri_record(url)))

                self.assertEqual(
                    _ndef_reader(fake).read_uid(),
                    story_locator_lookup_key("SD03-0001", private_token[:4]),
                )
                self.assertEqual(fake.read_pages, [11])

    def test_v2_fast_window_retries_are_bounded_and_never_use_uid(self) -> None:
        url = f"{ORIGIN}/s/SD03-0001/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        fake = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(_uri_record(url)),
            transient_page_failures={11: 2},
        )

        with patch("magic_box.nfc.time.sleep") as sleep:
            key = _ndef_reader(fake).read_uid()

        self.assertEqual(key, story_locator_lookup_key("SD03-0001", "ABCD"))
        self.assertEqual(fake.read_pages, [11, 11, 11])
        self.assertEqual(fake.selection_attempts, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_v2_wrong_size_window_fails_closed_with_value_free_reason(self) -> None:
        private_token = "mustneverappearxxxxxxxxxxxxxxxxx"
        fake = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(
                _uri_record(f"{ORIGIN}/s/SD03-0001/{private_token}")
            ),
        )
        fake.mifare_classic_read_block = MagicMock(return_value=b"short")

        with patch("magic_box.nfc.time.sleep"):
            with self.assertRaisesRegex(NFCError, r"\(page-size\)") as raised:
                _ndef_reader(fake).read_uid()

        # Three bounded fast-window attempts, then three bounded full-NDEF
        # attempts before the same value-free page-size rejection.
        self.assertEqual(fake.mifare_classic_read_block.call_count, 6)
        self.assertNotIn("04-A1", str(raised.exception))
        self.assertNotIn(private_token, str(raised.exception))

    def test_unreadable_fast_window_uses_strict_legacy_full_ndef_fallback(self) -> None:
        token = "legacy-url-remains-authoritative"
        fake = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
            transient_page_failures={11: 3},
        )

        with patch("magic_box.nfc.time.sleep"):
            key = _ndef_reader(fake).read_uid()

        self.assertEqual(key, story_playback_key_from_token(token))
        self.assertEqual(fake.read_pages[:4], [11, 11, 11, 4])

    def test_unreadable_fast_window_uses_strict_v2_full_ndef_fallback(self) -> None:
        private_token = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        fake = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(
                _uri_record(f"{ORIGIN}/s/SD03-0001/{private_token}")
            ),
            transient_page_failures={11: 3},
        )

        with patch("magic_box.nfc.time.sleep"):
            key = _ndef_reader(fake).read_uid()

        self.assertEqual(key, story_locator_lookup_key("SD03-0001", "ABCD"))
        self.assertEqual(fake.read_pages[:4], [11, 11, 11, 4])

    def test_readable_non_v2_window_uses_strict_v1_full_ndef_fallback(self) -> None:
        token = "legacy-fallback-token"
        fake = _FakePN532(
            uid=b"\x04\xA1",
            memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
        )

        self.assertEqual(_ndef_reader(fake).read_uid(), story_playback_key_from_token(token))
        self.assertEqual(fake.read_pages[:3], [11, 4, 3])

    def test_legacy_uid_cache_is_learned_only_after_exact_url_verification(self) -> None:
        token = "legacy-cache-token"
        uid = b"\x04\xA1\x22\x9B"
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "ndef-uid-cache.json"
            first_fake = _FakePN532(
                uid=uid,
                memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
            )
            expected_key = _ndef_reader(first_fake, uid_cache_path=cache_path).read_uid()

            self.assertTrue(cache_path.exists())
            cache_text = cache_path.read_text(encoding="utf-8")
            self.assertNotIn(token, cache_text)
            self.assertNotIn("04-A1-22-9B", cache_text)
            self.assertGreater(len(first_fake.read_pages), 1)

            cached_fake = _FakePN532(uid=uid, memory=b"")
            cached_key = _ndef_reader(cached_fake, uid_cache_path=cache_path).read_uid()

            self.assertEqual(cached_key, expected_key)
            self.assertEqual(cached_fake.read_pages, [])

    def test_v2_shortcut_never_enters_uid_cache(self) -> None:
        private_token = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "ndef-uid-cache.json"
            fake = _FakePN532(
                uid=b"\x04\xA1\x22\x9B",
                memory=_type2_memory(
                    _uri_record(f"{ORIGIN}/s/SD03-0001/{private_token}")
                ),
            )

            key = _ndef_reader(fake, uid_cache_path=cache_path).read_uid()

            self.assertEqual(key, story_locator_lookup_key("SD03-0001", "ABCD"))
            self.assertEqual(fake.read_pages, [11])
            self.assertFalse(cache_path.exists())

    def test_invalidating_learned_key_forces_next_legacy_url_read(self) -> None:
        token = "legacy-invalidated-token"
        uid = b"\x04\xA1\x22\x9B"
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "ndef-uid-cache.json"
            first_fake = _FakePN532(
                uid=uid,
                memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
            )
            reader = _ndef_reader(first_fake, uid_cache_path=cache_path)
            playback_key = reader.read_uid()
            reader.invalidate_cached_identity(playback_key or "")

            second_fake = _FakePN532(
                uid=uid,
                memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
            )
            second_key = _ndef_reader(second_fake, uid_cache_path=cache_path).read_uid()

            self.assertEqual(second_key, playback_key)
            self.assertGreater(len(second_fake.read_pages), 1)

    def test_no_tag_returns_none_without_reading_memory(self) -> None:
        fake = _FakePN532(uid=None, memory=b"")
        reader = _ndef_reader(fake)

        self.assertIsNone(reader.read_uid())
        self.assertEqual(fake.read_pages, [])

    def test_successful_tap_has_no_manufacturing_settle_delay(self) -> None:
        token = "quick-tap-token"
        fake = _FakePN532(
            uid=b"\x04\xA1\x22\x9B",
            memory=_type2_memory(_uri_record(f"{ORIGIN}/s/{token}")),
        )

        with patch("magic_box.nfc.time.sleep") as sleep:
            key = _ndef_reader(fake).read_uid()

        self.assertEqual(key, story_playback_key_from_token(token))
        sleep.assert_not_called()

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
        self.assertEqual(fake.selection_attempts, 4)

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

    def test_noncanonical_v2_locator_or_token_prefix_fails_closed(self) -> None:
        invalid_urls = (
            f"{ORIGIN}/s/SD3-0001/ABCD" + "x" * 28,
            f"{ORIGIN}/s/SD03-001/ABCD" + "x" * 28,
            f"{ORIGIN}/s/SD03-0000/ABCD" + "x" * 28,
            f"{ORIGIN}/s/sd03-0001/ABCD" + "x" * 28,
            f"{ORIGIN}/s/SD03-0001/ABC!" + "x" * 28,
            f"{ORIGIN}/s/SD03-0001/ABC",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                fake = _FakePN532(uid=b"\x04\xA1", memory=_type2_memory(_uri_record(url)))
                with self.assertRaises(NFCError) as raised:
                    _ndef_reader(fake).read_uid()
                self.assertNotIn(url, str(raised.exception))
                self.assertNotIn("ABCD", str(raised.exception))

    def test_shifted_v2_bytes_use_complete_url_fallback_not_substring_matching(self) -> None:
        # The no-prefix URI encoding shifts the otherwise canonical text. It
        # must not be accepted through a substring search or variable scan,
        # but the exact complete NDEF URL remains an authoritative fallback.
        url = f"{ORIGIN}/s/SD03-0001/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        fake = _FakePN532(
            uid=b"\x04\xA1",
            memory=_type2_memory(_uri_record(url, prefix_code=0x00)),
        )

        key = _ndef_reader(fake).read_uid()

        self.assertEqual(key, story_locator_lookup_key("SD03-0001", "ABCD"))
        self.assertGreater(len(fake.read_pages), 1)

    def test_v2_identity_uses_both_full_locator_and_token_prefix_not_uid(self) -> None:
        uid = b"\x04\xA1\x22\x9B"
        identities = []
        for locator, token_prefix in (
            ("SD03-0001", "ABCD"),
            ("SD03-0002", "ABCD"),
            ("SD03-0001", "a-_2"),
        ):
            url = f"{ORIGIN}/s/{locator}/{token_prefix}" + "x" * 28
            identities.append(
                _ndef_reader(_FakePN532(uid=uid, memory=_type2_memory(_uri_record(url)))).read_uid()
            )

        self.assertEqual(len(set(identities)), 3)

        shared = _type2_memory(
            _uri_record(f"{ORIGIN}/s/SD03-0001/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
        )
        first = _ndef_reader(_FakePN532(uid=b"\x04\xA1", memory=shared)).read_uid()
        second = _ndef_reader(_FakePN532(uid=b"\x04\xB2", memory=shared)).read_uid()
        self.assertEqual(first, second)

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

    def test_url_derivation_accepts_canonical_v2_path(self) -> None:
        self.assertEqual(
            story_playback_key_from_url(
                f"{ORIGIN}/s/SD03-0001/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
            ),
            story_locator_lookup_key("SD03-0001", "ABCD"),
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
        # Real Story Stickers have at least the NTAG213 144-byte user area,
        # so page 11 remains readable even for a short V1 message.
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
        self.read_pages: list[int] = []
        self.selection_attempts = 0
        self.transient_page_failures = dict(transient_page_failures or {})
        self.page_read_attempts: dict[int, int] = {}

    def read_passive_target(self, *, timeout: float) -> bytes | None:
        self.selection_attempts += 1
        return self.uid

    def mifare_classic_read_block(self, page: int) -> bytes | None:
        self.read_pages.append(page)
        self.page_read_attempts[page] = self.page_read_attempts.get(page, 0) + 1
        remaining = self.transient_page_failures.get(page, 0)
        if remaining > 0:
            self.transient_page_failures[page] = remaining - 1
            return None
        pages = [self.pages.get(page + offset) for offset in range(4)]
        if any(value is None for value in pages):
            return None
        return b"".join(value for value in pages if value is not None)


class _FakeRFConfiguration:
    def __init__(self, *, response: bytes | None) -> None:
        self.response = response
        self.calls: list[tuple[int, list[int]]] = []

    def call_function(self, command: int, *, params: list[int]) -> bytes | None:
        self.calls.append((command, params))
        return self.response


class _FakeReceiverConfiguration:
    def __init__(self, *, current: bytes, fail_write: bool = False) -> None:
        self.current = current
        self.fail_write = fail_write
        self.calls: list[tuple[int, list[int], int | None]] = []

    def call_function(
        self,
        command: int,
        *,
        params: list[int],
        response_length: int | None = None,
    ) -> bytes | None:
        self.calls.append((command, params, response_length))
        if command == 0x06:
            return self.current
        if command == 0x08 and self.fail_write:
            return None
        return b""


def _fake_pn532_modules(fake_hardware: MagicMock) -> dict[str, ModuleType]:
    board = ModuleType("board")
    board.SCK = object()  # type: ignore[attr-defined]
    board.MOSI = object()  # type: ignore[attr-defined]
    board.MISO = object()  # type: ignore[attr-defined]
    board.D8 = object()  # type: ignore[attr-defined]

    busio = ModuleType("busio")
    busio.SPI = MagicMock(return_value=object())  # type: ignore[attr-defined]
    digitalio = ModuleType("digitalio")
    digitalio.DigitalInOut = MagicMock(return_value=object())  # type: ignore[attr-defined]

    package = ModuleType("adafruit_pn532")
    package.__path__ = []  # type: ignore[attr-defined]
    spi = ModuleType("adafruit_pn532.spi")
    spi.PN532_SPI = MagicMock(return_value=fake_hardware)  # type: ignore[attr-defined]
    return {
        "board": board,
        "busio": busio,
        "digitalio": digitalio,
        "adafruit_pn532": package,
        "adafruit_pn532.spi": spi,
    }


def _ndef_reader(
    fake: _FakePN532,
    *,
    uid_cache_path: Path | None = None,
) -> PN532NDEFReader:
    with patch("magic_box.nfc._open_pn532_spi", return_value=fake):
        return PN532NDEFReader(uid_cache_path=uid_cache_path)


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
