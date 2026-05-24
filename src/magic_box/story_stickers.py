"""Preassigned Story Sticker URLs for Story Dock / Story Album flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from typing import Any

from .config import normalize_uid


TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,128}$")


class StoryStickerError(Exception):
    """Raised when a Story Sticker cannot be created or used."""


@dataclass(frozen=True)
class StorySticker:
    token: str
    created_at: datetime
    updated_at: datetime
    uid: str = ""
    support_code: str = ""
    name: str = ""
    folder: str = ""
    claimed_at: datetime | None = None

    @property
    def claimed(self) -> bool:
        return bool(self.name and self.folder)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "uid": self.uid or None,
            "support_code": self.support_code or None,
            "name": self.name or None,
            "folder": self.folder or None,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
            "claimed_at": _format_datetime(self.claimed_at),
        }


def story_stickers_file_for_config(config_path: str | Path) -> Path:
    return Path(config_path).expanduser().resolve().parent / "story_stickers.json"


def create_story_sticker(
    path: str | Path,
    *,
    uid: str = "",
    support_code: str = "",
    token: str | None = None,
) -> StorySticker:
    stickers_path = Path(path).expanduser().resolve()
    data = load_story_sticker_data(stickers_path)
    sticker_token = token or secrets.token_urlsafe(14)
    _validate_token(sticker_token)
    if sticker_token in data:
        raise StoryStickerError("Story Sticker token already exists.")
    normalized_uid = _clean_uid(uid)
    if normalized_uid:
        _require_unique_uid(data, normalized_uid, token=sticker_token)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    sticker = StorySticker(
        token=sticker_token,
        uid=normalized_uid,
        support_code=support_code.strip(),
        created_at=now,
        updated_at=now,
    )
    data[sticker.token] = sticker.to_dict()
    write_story_sticker_data(stickers_path, data)
    return sticker


def load_story_stickers(path: str | Path) -> dict[str, StorySticker]:
    stickers: dict[str, StorySticker] = {}
    for token, raw_sticker in load_story_sticker_data(path).items():
        if not isinstance(raw_sticker, dict):
            continue
        try:
            _validate_token(token)
            created_at = _parse_datetime(raw_sticker.get("created_at")) or datetime.fromtimestamp(0, timezone.utc)
            updated_at = _parse_datetime(raw_sticker.get("updated_at")) or created_at
            claimed_at = _parse_datetime(raw_sticker.get("claimed_at"))
            uid = _clean_uid(str(raw_sticker.get("uid") or ""))
        except (StoryStickerError, ValueError):
            continue

        stickers[token] = StorySticker(
            token=token,
            uid=uid,
            support_code=str(raw_sticker.get("support_code") or "").strip(),
            name=str(raw_sticker.get("name") or "").strip(),
            folder=str(raw_sticker.get("folder") or "").strip(),
            created_at=created_at,
            updated_at=updated_at,
            claimed_at=claimed_at,
        )
    return stickers


def get_story_sticker(path: str | Path, token: str) -> StorySticker:
    _validate_token(token)
    sticker = load_story_stickers(path).get(token)
    if sticker is None:
        raise StoryStickerError("This Story Sticker link was not found.")
    return sticker


def claim_story_sticker(
    path: str | Path,
    token: str,
    *,
    name: str,
    folder: str,
    uid: str = "",
) -> StorySticker:
    _validate_token(token)
    story_name = name.strip()
    folder_value = folder.strip()
    if not story_name:
        raise StoryStickerError("Story name is required.")
    if not folder_value:
        raise StoryStickerError("Story folder is required.")

    stickers_path = Path(path).expanduser().resolve()
    data = load_story_sticker_data(stickers_path)
    raw = data.get(token)
    if not isinstance(raw, dict):
        raise StoryStickerError("This Story Sticker link was not found.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    existing_claimed_at = _parse_datetime(raw.get("claimed_at"))
    merged_uid = _clean_uid(uid) or _clean_uid(str(raw.get("uid") or ""))
    raw.update(
        {
            "uid": merged_uid or None,
            "name": story_name,
            "folder": folder_value,
            "updated_at": _format_datetime(now),
            "claimed_at": _format_datetime(existing_claimed_at or now),
        }
    )
    data[token] = raw
    write_story_sticker_data(stickers_path, data)
    return get_story_sticker(stickers_path, token)


def bind_story_sticker_uid(path: str | Path, token: str, uid: str) -> StorySticker:
    _validate_token(token)
    normalized_uid = _clean_uid(uid)
    if not normalized_uid:
        raise StoryStickerError("UID is required.")

    stickers_path = Path(path).expanduser().resolve()
    data = load_story_sticker_data(stickers_path)
    raw = data.get(token)
    if not isinstance(raw, dict):
        raise StoryStickerError("This Story Sticker link was not found.")
    _require_unique_uid(data, normalized_uid, token=token)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw["uid"] = normalized_uid
    raw["updated_at"] = _format_datetime(now)
    data[token] = raw
    write_story_sticker_data(stickers_path, data)
    return get_story_sticker(stickers_path, token)


def load_story_sticker_data(path: str | Path) -> dict[str, Any]:
    stickers_path = Path(path).expanduser().resolve()
    if not stickers_path.exists():
        return {}
    try:
        raw = json.loads(stickers_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoryStickerError(f"Invalid JSON in {stickers_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StoryStickerError("Story Stickers file must be a JSON object keyed by token")
    return raw


def write_story_sticker_data(path: str | Path, data: dict[str, Any]) -> None:
    stickers_path = Path(path).expanduser().resolve()
    stickers_path.parent.mkdir(parents=True, exist_ok=True)
    stickers_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_token(token: str) -> None:
    if not TOKEN_PATTERN.fullmatch(token):
        raise StoryStickerError("Invalid Story Sticker token.")


def _clean_uid(uid: str) -> str:
    value = uid.strip()
    if not value:
        return ""
    return normalize_uid(value)


def _require_unique_uid(data: dict[str, Any], uid: str, *, token: str) -> None:
    for existing_token, raw_sticker in data.items():
        if existing_token == token or not isinstance(raw_sticker, dict):
            continue
        try:
            existing_uid = _clean_uid(str(raw_sticker.get("uid") or ""))
        except ValueError:
            continue
        if existing_uid == uid:
            raise StoryStickerError("NFC UID is already bound to another Story Sticker.")


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
