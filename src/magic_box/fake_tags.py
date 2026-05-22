"""Helpers for software-only fake tag scans."""

from __future__ import annotations

import os
from pathlib import Path

from .config import normalize_uid


DEFAULT_TRIGGER_FILE = "/tmp/magic-character-box-tags.txt"


def trigger_file_from_env() -> Path:
    return Path(os.getenv("MAGIC_BOX_TRIGGER_FILE", DEFAULT_TRIGGER_FILE)).expanduser().resolve()


def queue_fake_tag(uid: str, path: str | Path | None = None) -> tuple[str, Path]:
    normalized_uid = normalize_uid(uid)
    trigger_file = Path(path).expanduser().resolve() if path is not None else trigger_file_from_env()
    trigger_file.parent.mkdir(parents=True, exist_ok=True)
    with trigger_file.open("a", encoding="utf-8") as handle:
        handle.write(normalized_uid + "\n")
    return normalized_uid, trigger_file
