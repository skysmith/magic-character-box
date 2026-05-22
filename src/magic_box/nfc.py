"""NFC reader implementations."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import time
from typing import Protocol

from .config import normalize_uid


LOGGER = logging.getLogger(__name__)


class NFCError(Exception):
    """Raised when an NFC reader cannot be initialized or read."""


class StopRequested(Exception):
    """Raised by interactive readers when the operator asks to quit."""


class NFCReader(Protocol):
    def read_uid(self) -> str | None:
        """Return a normalized UID, or None when no tag is present."""


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


class PN532SPIReader:
    """PN532 reader using SPI via Adafruit Blinka/CircuitPython."""

    def __init__(self, timeout: float = 0.1) -> None:
        self.timeout = timeout
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
            self._pn532 = PN532_SPI(spi, cs_pin, debug=False)
            self._pn532.SAM_configuration()
        except Exception as exc:  # Hardware libraries raise board-specific errors.
            raise NFCError(f"Could not initialize PN532 over SPI: {exc}") from exc

    def read_uid(self) -> str | None:
        try:
            uid = self._pn532.read_passive_target(timeout=self.timeout)
        except Exception as exc:
            raise NFCError(f"Could not read PN532 tag: {exc}") from exc

        if uid is None:
            return None
        return "-".join(f"{byte:02X}" for byte in uid)


def create_reader(kind: str, prompt: str | None = None) -> NFCReader:
    normalized = kind.strip().lower()
    if normalized in {"mock", "keyboard", "dev"}:
        return KeyboardNFCReader(prompt=prompt or KeyboardNFCReader.prompt)
    if normalized in {"file", "trigger-file", "queue"}:
        path = Path(os.getenv("MAGIC_BOX_TRIGGER_FILE", "/tmp/magic-character-box-tags.txt"))
        return TriggerFileNFCReader(path=path.expanduser().resolve())
    if normalized in {"pn532", "pn532-spi", "spi"}:
        return PN532SPIReader()
    raise NFCError(f"Unknown NFC reader {kind!r}; use mock, file, or pn532")


def wait_for_uid(reader: NFCReader, poll_interval: float = 0.2) -> str:
    """Block until the reader returns a UID."""
    while True:
        uid = reader.read_uid()
        if uid:
            return uid
        time.sleep(poll_interval)
