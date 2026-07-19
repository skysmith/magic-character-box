"""NFC reader implementations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from .config import normalize_uid


LOGGER = logging.getLogger(__name__)


STORY_STICKER_ORIGIN = "https://tap.getstorydock.com"
STORY_PLAYBACK_KEY_DOMAIN = b"story-dock-playback-v1\0"

_NTAG_CC_PAGE = 3
_NTAG_USER_START_PAGE = 4
_NTAG_PAGE_BYTES = 4
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


def _open_pn532_spi() -> Any:
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
        return pn532
    except Exception as exc:  # Hardware libraries raise board-specific errors.
        raise NFCError(f"Could not initialize PN532 over SPI: {exc}") from exc


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
    """Hosted Story Sticker reader whose identity comes only from its NDEF URL.

    The physical UID is used only by the PN532 to select a present tag. It is
    never returned as identity and is never a fallback when URL parsing fails.
    """

    def __init__(
        self,
        timeout: float = 0.1,
        *,
        expected_origin: str = STORY_STICKER_ORIGIN,
    ) -> None:
        _require_https_origin(expected_origin)
        self.timeout = timeout
        self.expected_origin = expected_origin
        self._pn532 = _open_pn532_spi()

    def read_uid(self) -> str | None:
        try:
            tag_present = self._pn532.read_passive_target(timeout=self.timeout)
        except Exception:
            raise NFCError("Could not read PN532 tag.") from None

        if tag_present is None:
            return None

        try:
            type2_memory = _read_type2_tlv_memory(self._pn532)
            ndef_message = _single_ndef_message(type2_memory)
            story_url = _single_uri_record(ndef_message)
            return story_playback_key_from_url(
                story_url,
                expected_origin=self.expected_origin,
            )
        except Exception:
            # Do not include the underlying parse error, URL, or token in an
            # exception that the app will log. Invalid NDEF must fail closed.
            raise NFCError("Story Sticker URL data could not be verified.") from None


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

    match = _STORY_PATH_RE.fullmatch(parsed.path)
    if match is None:
        raise ValueError("Story Sticker URL was invalid")
    return story_playback_key_from_token(match.group(1))


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


def _read_type2_tlv_memory(pn532: Any) -> bytes:
    cc = _read_ntag_page(pn532, _NTAG_CC_PAGE)
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
    memory = bytearray()
    for page in range(_NTAG_USER_START_PAGE, _NTAG_USER_START_PAGE + page_count):
        memory.extend(_read_ntag_page(pn532, page))
        terminator_end = _complete_tlv_prefix_end(memory)
        if terminator_end is not None:
            return bytes(memory[:terminator_end])
    raise ValueError("Type 2 data did not contain a complete TLV stream")


def _read_ntag_page(pn532: Any, page: int) -> bytes:
    block = pn532.ntag2xx_read_block(page)
    if block is None:
        raise ValueError("NTAG page could not be read")
    value = bytes(block)
    if len(value) != _NTAG_PAGE_BYTES:
        raise ValueError("NTAG page had the wrong size")
    return value


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
