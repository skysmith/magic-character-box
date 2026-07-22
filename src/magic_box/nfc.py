"""NFC reader implementations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from .config import normalize_uid, story_locator_lookup_key


LOGGER = logging.getLogger(__name__)


STORY_STICKER_ORIGIN = "https://tap.getstorydock.com"
STORY_PLAYBACK_KEY_DOMAIN = b"story-dock-playback-v1\0"
STORY_UID_CACHE_KEY_DOMAIN = b"story-dock-physical-uid-cache-v1\0"

_NTAG_CC_PAGE = 3
_NTAG_USER_START_PAGE = 4
_NTAG_PAGE_BYTES = 4
_NTAG_READ_WINDOW_BYTES = 16
_NTAG_READ_WINDOW_PAGES = _NTAG_READ_WINDOW_BYTES // _NTAG_PAGE_BYTES
_MAX_NTAG_DATA_AREA_BYTES = 872  # NTAG216; NTAG213/215 are smaller.
_TYPE2_MAGIC = 0xE1
_TYPE2_NDEF_TLV = 0x03
_TYPE2_TERMINATOR_TLV = 0xFE
_TYPE2_NULL_TLV = 0x00
_TYPE2_LOCK_CONTROL_TLV = 0x01
_TYPE2_MEMORY_CONTROL_TLV = 0x02
_NDEF_TNF_WELL_KNOWN = 0x01
_NDEF_URI_TYPE = b"U"
_STORY_PATH_RE = re.compile(r"/s/([A-Za-z0-9_-]{4,256})\Z")
_STORY_V2_PATH_RE = re.compile(
    r"/s/([A-Z0-9]{4}-(?!0000/)[0-9]{4})/([A-Za-z0-9_-]{32})\Z"
)
_STORY_V2_FAST_PATH_PAGE = 11
_STORY_V2_FAST_WINDOW_RE = re.compile(
    rb"s/([A-Z0-9]{4}-(?!0000/)[0-9]{4})/([A-Za-z0-9_-]{4})\Z"
)
_PLAYBACK_KEY_RE = re.compile(r"sdpk1_[0-9a-f]{64}\Z")
_UID_CACHE_KEY_RE = re.compile(r"sduid1_[0-9a-f]{64}\Z")
_UID_CACHE_MAX_ENTRIES = 10_000
_NTAG_PAGE_READ_ATTEMPTS = 3
_NTAG_PAGE_RETRY_DELAY_SECONDS = 0.03
_NTAG_PAGE_RESELECT_TIMEOUT_SECONDS = 0.25
_PN532_COMMAND_RF_CONFIGURATION = 0x32
_PN532_COMMAND_READ_REGISTER = 0x06
_PN532_COMMAND_WRITE_REGISTER = 0x08
_PN532_RF_CONFIG_RF_FIELD = 0x01
_PN532_RF_CONFIG_MAX_COMMUNICATION_RETRIES = 0x04
_PN532_TYPE2_COMMUNICATION_RETRIES = 3
_PN532_CIU_RF_CONFIG_REGISTER = (0x63, 0x16)
_PN532_RECEIVER_GAIN_MASK = 0x70
_PN532_TYPE2_CLOSE_CONTACT_GAIN = 0x70

# NFC Forum URI RTD identifier codes. Story Sticker URLs only accept the
# no-prefix form or HTTPS forms; HTTP and every non-web scheme fail closed.
_HTTPS_URI_PREFIXES = {
    0x00: "",
    0x02: "https://www.",
    0x04: "https://",
}


class NFCError(Exception):
    """Raised when an NFC reader cannot be initialized or read."""


class StopRequested(Exception):
    """Raised by interactive readers when the operator asks to quit."""


class NFCReader(Protocol):
    def read_uid(self) -> str | None:
        """Return a normalized config lookup key, or None when no tag is present."""


@dataclass
class KeyboardNFCReader:
    """Development reader that accepts UIDs through stdin."""

    prompt: str = "Enter tag UID (blank for no tag, q to quit): "

    def read_uid(self) -> str | None:
        try:
            value = input(self.prompt).strip()
        except EOFError as exc:
            raise StopRequested from exc

        if value.lower() in {"q", "quit", "exit"}:
            raise StopRequested
        if not value:
            return None
        return normalize_uid(value)


@dataclass
class TriggerFileNFCReader:
    """Development reader that consumes one UID per line from a queue file.

    This is useful on a headless Raspberry Pi before the PN532 reader arrives:
    run the app as a service with this backend, then append fake tag IDs with
    scripts/fake_tag.py.
    """

    path: Path

    def read_uid(self) -> str | None:
        if not self.path.exists():
            return None

        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise NFCError(f"Could not read trigger file {self.path}: {exc}") from exc

        uid: str | None = None
        remaining: list[str] = []
        for line in lines:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            if uid is None:
                uid = value
            else:
                remaining.append(value)

        try:
            if remaining:
                self.path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            else:
                self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise NFCError(f"Could not update trigger file {self.path}: {exc}") from exc

        return normalize_uid(uid) if uid else None


def _open_pn532_spi(*, type2_receiver: bool = False) -> Any:
    try:
        import board
        import busio
        import digitalio
        from adafruit_pn532.spi import PN532_SPI
    except ImportError as exc:
        raise NFCError(
            "PN532 dependencies are missing. Install requirements.txt on the Pi first."
        ) from exc

    try:
        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        cs_pin = digitalio.DigitalInOut(board.D8)
        pn532 = PN532_SPI(spi, cs_pin, debug=False)
        pn532.SAM_configuration()
        if type2_receiver:
            _configure_pn532_communication_retries(pn532)
            _configure_pn532_type2_receiver_gain(pn532)
        return pn532
    except Exception as exc:  # Hardware libraries raise board-specific errors.
        raise NFCError(f"Could not initialize PN532 over SPI: {exc}") from exc


def _configure_pn532_communication_retries(pn532: Any) -> None:
    """Retry a short RF exchange inside the PN532 before it drops the target."""

    response = pn532.call_function(
        _PN532_COMMAND_RF_CONFIGURATION,
        params=[
            _PN532_RF_CONFIG_MAX_COMMUNICATION_RETRIES,
            _PN532_TYPE2_COMMUNICATION_RETRIES,
        ],
    )
    if response is None:
        raise NFCError("Could not configure PN532 communication retries.")


def _configure_pn532_type2_receiver_gain(pn532: Any) -> None:
    """Match the close-contact receiver profile proven on the founder dock."""

    current = pn532.call_function(
        _PN532_COMMAND_READ_REGISTER,
        params=list(_PN532_CIU_RF_CONFIG_REGISTER),
        response_length=1,
    )
    if current is None or len(current) != 1:
        raise NFCError("Could not read PN532 receiver configuration.")
    close_contact = (
        current[0] & ~_PN532_RECEIVER_GAIN_MASK
    ) | _PN532_TYPE2_CLOSE_CONTACT_GAIN

    field_disabled = pn532.call_function(
        _PN532_COMMAND_RF_CONFIGURATION,
        params=[_PN532_RF_CONFIG_RF_FIELD, 0x00],
    )
    if field_disabled is None:
        raise NFCError("Could not disable the PN532 RF field for receiver tuning.")

    write_response: bytes | bytearray | None = None
    try:
        write_response = pn532.call_function(
            _PN532_COMMAND_WRITE_REGISTER,
            params=[*_PN532_CIU_RF_CONFIG_REGISTER, close_contact],
        )
    finally:
        field_enabled = pn532.call_function(
            _PN532_COMMAND_RF_CONFIGURATION,
            params=[_PN532_RF_CONFIG_RF_FIELD, 0x01],
        )

    if write_response is None:
        raise NFCError("Could not configure PN532 receiver gain.")
    if field_enabled is None:
        raise NFCError("Could not re-enable the PN532 RF field after receiver tuning.")

    time.sleep(0.10)


class PN532SPIReader:
    """PN532 UID reader using SPI via Adafruit Blinka/CircuitPython."""

    def __init__(self, timeout: float = 0.1) -> None:
        self.timeout = timeout
        self._pn532 = _open_pn532_spi()

    def read_uid(self) -> str | None:
        try:
            uid = self._pn532.read_passive_target(timeout=self.timeout)
        except Exception as exc:
            raise NFCError(f"Could not read PN532 tag: {exc}") from exc

        if uid is None:
            return None
        return "-".join(f"{byte:02X}" for byte in uid)


class PN532NDEFReader:
    """Hosted reader whose playback key originates in Story Sticker URL data.

    Shortcut tags expose their locator and first four token characters in one
    fixed Type 2 READ window. A readable non-V2 window falls back to strict
    full-NDEF V1 parsing. Only after an exact legacy URL has been verified may
    a hashed local UID binding accelerate later taps. The physical UID is never
    returned as playback identity, and V2 shortcut tags never enter that cache.
    """

    def __init__(
        self,
        timeout: float = 0.1,
        *,
        expected_origin: str = STORY_STICKER_ORIGIN,
        uid_cache_path: Path | None = None,
    ) -> None:
        _require_https_origin(expected_origin)
        self.timeout = timeout
        self.expected_origin = expected_origin
        if uid_cache_path is None:
            configured_cache = os.getenv("MAGIC_BOX_NDEF_UID_CACHE", "").strip()
            uid_cache_path = Path(configured_cache).expanduser() if configured_cache else None
        self._uid_cache = _NDEFUIDCache(uid_cache_path)
        self._pn532 = _open_pn532_spi(type2_receiver=True)

    def read_uid(self) -> str | None:
        try:
            tag_present = self._pn532.read_passive_target(timeout=self.timeout)
        except Exception:
            raise NFCError("Could not read PN532 tag.") from None

        if tag_present is None:
            return None

        cached_key = self._uid_cache.lookup(tag_present)
        if cached_key is not None:
            return cached_key

        try:
            try:
                fast_window = _read_ntag_window(self._pn532, _STORY_V2_FAST_PATH_PAGE)
            except ValueError:
                # The shortcut is an optimization, not a new authority model.
                # A transient failure at page 11 must still allow the exact
                # complete NDEF URL to prove either a legacy or V2 identity.
                fast_window = None
            if fast_window is not None:
                fast_key = _story_v2_key_from_fast_window(fast_window)
                if fast_key is not None:
                    return fast_key

            # A window that was readable but does not exactly match the
            # shortcut contract may be a legacy V1 sticker. An unreadable fast
            # window may also be a transient transport failure. In either case,
            # only a strict complete NDEF parse is allowed to recover.
            type2_memory = _read_type2_tlv_memory(self._pn532)
            ndef_message = _single_ndef_message(type2_memory)
            story_url = _single_uri_record(ndef_message)
            playback_key = story_playback_key_from_url(
                story_url,
                expected_origin=self.expected_origin,
            )
            self._uid_cache.remember(tag_present, playback_key)
            return playback_key
        except Exception as exc:
            # Do not include the underlying parse error, URL, or token in an
            # exception that the app will log. The bounded reason code is
            # intentionally value-free so physical QA can distinguish tag
            # formatting failures without exposing the tag UID or URL.
            reason = _safe_ndef_rejection_reason(exc)
            raise NFCError(
                f"Story Sticker URL data could not be verified ({reason})."
            ) from None

    def invalidate_cached_identity(self, playback_key: str) -> None:
        """Forget learned legacy bindings that no longer exist in the manifest."""

        self._uid_cache.forget_playback_key(playback_key)


class _NDEFUIDCache:
    """Bounded UID fingerprints learned only after an exact legacy URL read."""

    def __init__(self, path: Path | None) -> None:
        self.path = path.expanduser().resolve() if path is not None else None
        self.entries: dict[str, str] = {}
        self._load()

    def lookup(self, uid: bytes | bytearray) -> str | None:
        return self.entries.get(_uid_cache_key(uid))

    def remember(self, uid: bytes | bytearray, playback_key: str) -> None:
        # V2 shortcut keys deliberately do not enter the UID cache. They are
        # already one-read values and remain derived from sticker URL bytes.
        if not _PLAYBACK_KEY_RE.fullmatch(playback_key):
            return
        cache_key = _uid_cache_key(uid)
        if self.entries.get(cache_key) == playback_key:
            return
        if cache_key not in self.entries and len(self.entries) >= _UID_CACHE_MAX_ENTRIES:
            self.entries.pop(next(iter(self.entries)))
        self.entries[cache_key] = playback_key
        self._persist()

    def forget_playback_key(self, playback_key: str) -> None:
        filtered = {key: value for key, value in self.entries.items() if value != playback_key}
        if len(filtered) == len(self.entries):
            return
        self.entries = filtered
        self._persist()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(document, dict):
            return
        mappings = document.get("mappings")
        if document.get("version") != 1 or not isinstance(mappings, dict):
            return
        for key, value in list(mappings.items())[:_UID_CACHE_MAX_ENTRIES]:
            if (
                isinstance(key, str)
                and isinstance(value, str)
                and _UID_CACHE_KEY_RE.fullmatch(key)
                and _PLAYBACK_KEY_RE.fullmatch(value)
            ):
                self.entries[key] = value

    def _persist(self) -> None:
        if self.path is None:
            return
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(temporary_path, 0o600)
                json.dump({"version": 1, "mappings": self.entries}, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError:
            LOGGER.warning("Could not persist the learned Story Sticker binding cache.")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _uid_cache_key(uid: bytes | bytearray) -> str:
    digest = hashlib.sha256(STORY_UID_CACHE_KEY_DOMAIN + bytes(uid)).hexdigest()
    return f"sduid1_{digest}"


def _story_v2_key_from_fast_window(window: bytes) -> str | None:
    """Return a shortcut key only for the exact canonical 16-byte window."""

    match = _STORY_V2_FAST_WINDOW_RE.fullmatch(window)
    if match is None:
        return None
    try:
        locator = match.group(1).decode("ascii", errors="strict")
        token_prefix = match.group(2).decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None
    return story_locator_lookup_key(locator, token_prefix)


def story_playback_key_from_token(token: str) -> str:
    """Derive the account-neutral config key for one opaque Story Sticker token."""
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,256}", token):
        raise ValueError("Story Sticker token was invalid")
    digest = hashlib.sha256(STORY_PLAYBACK_KEY_DOMAIN + token.encode("ascii")).hexdigest()
    return f"sdpk1_{digest}"


def story_playback_key_from_url(
    story_url: str,
    *,
    expected_origin: str = STORY_STICKER_ORIGIN,
) -> str:
    """Validate a canonical hosted Story Sticker URL and derive its lookup key."""
    _require_https_origin(expected_origin)
    if not isinstance(story_url, str) or not story_url or len(story_url) > 1024:
        raise ValueError("Story Sticker URL was invalid")
    if story_url != story_url.strip() or any(ord(character) < 0x20 for character in story_url):
        raise ValueError("Story Sticker URL was invalid")
    if "?" in story_url or "#" in story_url:
        raise ValueError("Story Sticker URL was invalid")

    try:
        parsed = urlsplit(story_url)
    except ValueError:
        raise ValueError("Story Sticker URL was invalid") from None
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        raise ValueError("Story Sticker URL was invalid")
    if parsed.netloc.lower() != urlsplit(expected_origin).netloc.lower():
        raise ValueError("Story Sticker URL was invalid")
    if parsed.query or parsed.fragment:
        raise ValueError("Story Sticker URL was invalid")

    v2_match = _STORY_V2_PATH_RE.fullmatch(parsed.path)
    if v2_match is not None:
        return story_locator_lookup_key(v2_match.group(1), v2_match.group(2)[:4])

    v1_match = _STORY_PATH_RE.fullmatch(parsed.path)
    if v1_match is not None:
        return story_playback_key_from_token(v1_match.group(1))
    raise ValueError("Story Sticker URL was invalid")


def _require_https_origin(origin: str) -> None:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("Story Sticker origin was invalid") from None
    if (
        not isinstance(origin, str)
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or origin.endswith("/")
    ):
        raise ValueError("Story Sticker origin was invalid")


def _safe_ndef_rejection_reason(exc: Exception) -> str:
    """Return a bounded, value-free reason for a rejected physical tag."""

    return {
        "Type 2 capability container was invalid": "capability-container",
        "Type 2 data area exceeded supported NTAG capacity": "tag-capacity",
        "Type 2 data did not contain a complete TLV stream": "tlv-stream",
        "NTAG page could not be read": "page-read",
        "NTAG page had the wrong size": "page-size",
        "TLV payload was incomplete": "tlv-payload",
        "Type 2 control TLV was invalid": "control-tlv",
        "Type 2 TLV stream was not a single NDEF message": "ndef-tlv-count",
        "NDEF message was empty": "ndef-empty",
        "Type 2 TLV stream was incomplete": "tlv-stream",
        "TLV length was missing": "tlv-length",
        "Extended TLV length was incomplete": "tlv-length",
        "NDEF record was incomplete": "ndef-record",
        "NDEF message was not one well-known record": "ndef-record-shape",
        "NDEF payload length was missing": "ndef-payload-length",
        "NDEF payload length was incomplete": "ndef-payload-length",
        "NDEF message contained extra or partial records": "ndef-record-count",
        "NDEF record was not a URI": "ndef-record-type",
        "NDEF URI was not HTTPS": "uri-scheme",
        "NDEF URI was invalid": "uri-encoding",
        "Story Sticker URL was invalid": "story-url",
    }.get(str(exc), "unclassified")


def _read_type2_tlv_memory(pn532: Any) -> bytes:
    # Read the first NDEF user window immediately after selection. This mirrors
    # the proven writer/readback order and avoids spending the customer's
    # short physical tap on a manufacturing-only settle delay. One Type 2 READ
    # returns four pages; retain all 16 bytes instead of discarding 12 of them.
    first_user_window = _read_ntag_window(pn532, _NTAG_USER_START_PAGE)
    cc = _read_ntag_window(pn532, _NTAG_CC_PAGE)[:_NTAG_PAGE_BYTES]
    if (
        cc[0] != _TYPE2_MAGIC
        or cc[1] >> 4 != 1
        or cc[2] == 0
        or cc[3] >> 4 != 0
    ):
        raise ValueError("Type 2 capability container was invalid")

    data_area_bytes = cc[2] * 8
    if data_area_bytes > _MAX_NTAG_DATA_AREA_BYTES:
        raise ValueError("Type 2 data area exceeded supported NTAG capacity")
    page_count = (data_area_bytes + _NTAG_PAGE_BYTES - 1) // _NTAG_PAGE_BYTES
    memory = bytearray(first_user_window[:data_area_bytes])
    terminator_end = _complete_tlv_prefix_end(memory)
    if terminator_end is not None:
        return bytes(memory[:terminator_end])
    for page in range(
        _NTAG_USER_START_PAGE + _NTAG_READ_WINDOW_PAGES,
        _NTAG_USER_START_PAGE + page_count,
        _NTAG_READ_WINDOW_PAGES,
    ):
        remaining = data_area_bytes - len(memory)
        memory.extend(_read_ntag_window(pn532, page)[:remaining])
        terminator_end = _complete_tlv_prefix_end(memory)
        if terminator_end is not None:
            return bytes(memory[:terminator_end])
    raise ValueError("Type 2 data did not contain a complete TLV stream")


def _read_ntag_window(pn532: Any, page: int) -> bytes:
    wrong_size = False
    for attempt in range(_NTAG_PAGE_READ_ATTEMPTS):
        try:
            block = pn532.mifare_classic_read_block(page)
        except Exception:
            block = None
        if block is not None:
            value = bytes(block)
            if len(value) == _NTAG_READ_WINDOW_BYTES:
                return value
            wrong_size = True
        if attempt + 1 < _NTAG_PAGE_READ_ATTEMPTS:
            # A failed Type 2 command can leave the PN532 without an active
            # target. Re-select the still-present sticker before retrying;
            # the returned UID is deliberately ignored and is never identity.
            try:
                pn532.read_passive_target(timeout=_NTAG_PAGE_RESELECT_TIMEOUT_SECONDS)
            except Exception:
                pass
            time.sleep(_NTAG_PAGE_RETRY_DELAY_SECONDS)
    if wrong_size:
        raise ValueError("NTAG page had the wrong size")
    raise ValueError("NTAG page could not be read")


def _complete_tlv_prefix_end(memory: bytes | bytearray) -> int | None:
    """Return the end of a complete Type 2 TLV stream, without parsing payload bytes."""
    index = 0
    while index < len(memory):
        tag = memory[index]
        index += 1
        if tag == _TYPE2_NULL_TLV:
            continue
        if tag == _TYPE2_TERMINATOR_TLV:
            return index
        length, index = _tlv_length(memory, index, incomplete_ok=True)
        if length is None:
            return None
        if index + length > len(memory):
            return None
        index += length
    return None


def _single_ndef_message(memory: bytes) -> bytes:
    index = 0
    message: bytes | None = None
    terminated = False
    while index < len(memory):
        tag = memory[index]
        index += 1
        if tag == _TYPE2_NULL_TLV:
            continue
        if tag == _TYPE2_TERMINATOR_TLV:
            terminated = True
            break

        length, index = _tlv_length(memory, index, incomplete_ok=False)
        assert length is not None
        end = index + length
        if end > len(memory):
            raise ValueError("TLV payload was incomplete")
        value = memory[index:end]
        index = end

        if tag in {_TYPE2_LOCK_CONTROL_TLV, _TYPE2_MEMORY_CONTROL_TLV}:
            if message is not None or length != 3:
                raise ValueError("Type 2 control TLV was invalid")
            continue
        if tag != _TYPE2_NDEF_TLV or message is not None:
            raise ValueError("Type 2 TLV stream was not a single NDEF message")
        if not value:
            raise ValueError("NDEF message was empty")
        message = bytes(value)

    if not terminated or message is None:
        raise ValueError("Type 2 TLV stream was incomplete")
    return message


def _tlv_length(
    memory: bytes | bytearray,
    index: int,
    *,
    incomplete_ok: bool,
) -> tuple[int | None, int]:
    if index >= len(memory):
        if incomplete_ok:
            return None, index
        raise ValueError("TLV length was missing")
    length = memory[index]
    index += 1
    if length != 0xFF:
        return length, index
    if index + 2 > len(memory):
        if incomplete_ok:
            return None, index
        raise ValueError("Extended TLV length was incomplete")
    return int.from_bytes(memory[index : index + 2], "big"), index + 2


def _single_uri_record(message: bytes) -> str:
    if len(message) < 4:
        raise ValueError("NDEF record was incomplete")

    header = message[0]
    message_begin = bool(header & 0x80)
    message_end = bool(header & 0x40)
    chunked = bool(header & 0x20)
    short_record = bool(header & 0x10)
    has_id = bool(header & 0x08)
    tnf = header & 0x07
    if not message_begin or not message_end or chunked or has_id or tnf != _NDEF_TNF_WELL_KNOWN:
        raise ValueError("NDEF message was not one well-known record")

    index = 1
    type_length = message[index]
    index += 1
    if short_record:
        if index >= len(message):
            raise ValueError("NDEF payload length was missing")
        payload_length = message[index]
        index += 1
    else:
        if index + 4 > len(message):
            raise ValueError("NDEF payload length was incomplete")
        payload_length = int.from_bytes(message[index : index + 4], "big")
        index += 4

    end = index + type_length + payload_length
    if end != len(message):
        raise ValueError("NDEF message contained extra or partial records")
    record_type = message[index : index + type_length]
    payload = message[index + type_length : end]
    if record_type != _NDEF_URI_TYPE or len(payload) < 2:
        raise ValueError("NDEF record was not a URI")

    prefix = _HTTPS_URI_PREFIXES.get(payload[0])
    if prefix is None:
        raise ValueError("NDEF URI was not HTTPS")
    try:
        suffix = payload[1:].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("NDEF URI was invalid") from None
    return prefix + suffix


def create_reader(kind: str, prompt: str | None = None) -> NFCReader:
    normalized = kind.strip().lower()
    if normalized in {"mock", "keyboard", "dev"}:
        return KeyboardNFCReader(prompt=prompt or KeyboardNFCReader.prompt)
    if normalized in {"file", "trigger-file", "queue"}:
        path = Path(os.getenv("MAGIC_BOX_TRIGGER_FILE", "/tmp/magic-character-box-tags.txt"))
        return TriggerFileNFCReader(path=path.expanduser().resolve())
    if normalized in {"pn532", "pn532-spi", "spi"}:
        return PN532SPIReader()
    if normalized == "pn532-ndef":
        return PN532NDEFReader()
    raise NFCError(f"Unknown NFC reader {kind!r}; use mock, file, pn532, or pn532-ndef")


def wait_for_uid(reader: NFCReader, poll_interval: float = 0.2) -> str:
    """Block until the reader returns a UID."""
    while True:
        uid = reader.read_uid()
        if uid:
            return uid
        time.sleep(poll_interval)
