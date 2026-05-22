"""Guest recording links for upload-only family voice messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
from typing import Any

from .config import normalize_uid


TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12,128}$")


class GuestLinkError(Exception):
    """Raised when a guest recording link cannot be used."""


@dataclass(frozen=True)
class GuestLink:
    token: str
    uid: str
    label: str
    created_at: datetime
    expires_at: datetime | None
    base_url: str = ""

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        return current >= self.expires_at

    def to_dict(self) -> dict[str, str | None]:
        return {
            "uid": self.uid,
            "label": self.label,
            "created_at": _format_datetime(self.created_at),
            "expires_at": _format_datetime(self.expires_at),
            "base_url": self.base_url or None,
        }


def guest_links_file_for_config(config_path: str | Path) -> Path:
    return Path(config_path).expanduser().resolve().parent / "guest_links.json"


def create_guest_link(
    path: str | Path,
    uid: str,
    label: str = "",
    expires_days: int | None = 14,
    token: str | None = None,
    base_url: str = "",
) -> GuestLink:
    links_path = Path(path).expanduser().resolve()
    data = load_guest_link_data(links_path)
    link_token = token or secrets.token_urlsafe(18)
    _validate_token(link_token)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = None
    if expires_days is not None and expires_days > 0:
        expires_at = now + timedelta(days=expires_days)

    link = GuestLink(
        token=link_token,
        uid=normalize_uid(uid),
        label=label.strip() or "Guest recording",
        created_at=now,
        expires_at=expires_at,
        base_url=_clean_base_url(base_url),
    )
    data[link.token] = link.to_dict()
    write_guest_link_data(links_path, data)
    return link


def load_guest_links(path: str | Path) -> dict[str, GuestLink]:
    links: dict[str, GuestLink] = {}
    for token, raw_link in load_guest_link_data(path).items():
        if not isinstance(raw_link, dict):
            continue
        try:
            _validate_token(token)
            uid = normalize_uid(str(raw_link.get("uid", "")))
            created_at = _parse_datetime(raw_link.get("created_at")) or datetime.fromtimestamp(0, timezone.utc)
            expires_at = _parse_datetime(raw_link.get("expires_at"))
            base_url = _clean_base_url(str(raw_link.get("base_url") or ""))
        except (GuestLinkError, ValueError):
            continue

        label = str(raw_link.get("label", "")).strip() or "Guest recording"
        links[token] = GuestLink(
            token=token,
            uid=uid,
            label=label,
            created_at=created_at,
            expires_at=expires_at,
            base_url=base_url,
        )
    return links


def get_guest_link(path: str | Path, token: str) -> GuestLink:
    _validate_token(token)
    link = load_guest_links(path).get(token)
    if link is None:
        raise GuestLinkError("This recording link was not found.")
    if link.is_expired():
        raise GuestLinkError("This recording link has expired.")
    return link


def revoke_guest_link(path: str | Path, token: str) -> bool:
    _validate_token(token)
    data = load_guest_link_data(path)
    if token not in data:
        return False
    del data[token]
    write_guest_link_data(path, data)
    return True


def load_guest_link_data(path: str | Path) -> dict[str, Any]:
    links_path = Path(path).expanduser().resolve()
    if not links_path.exists():
        return {}
    try:
        raw = json.loads(links_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GuestLinkError(f"Invalid JSON in {links_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise GuestLinkError("Guest links file must be a JSON object keyed by token")
    return raw


def write_guest_link_data(path: str | Path, data: dict[str, Any]) -> None:
    links_path = Path(path).expanduser().resolve()
    links_path.parent.mkdir(parents=True, exist_ok=True)
    links_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_token(token: str) -> None:
    if not TOKEN_PATTERN.fullmatch(token):
        raise GuestLinkError("Invalid guest recording token.")


def _clean_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        raise GuestLinkError("Guest link base URL must start with http:// or https://.")
    return cleaned


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
