"""Small shared runtime state for the playback service and admin UI."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any

from .config import normalize_uid


LOGGER = logging.getLogger(__name__)
MAX_EVENTS = 80


def state_file_for_config(config_path: str | Path) -> Path:
    """Default to a device state file beside characters.json."""

    return Path(config_path).expanduser().resolve().parent / "device_state.json"


def load_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path).expanduser().resolve()
    try:
        raw = state_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_state()
    except OSError as exc:
        LOGGER.warning("Could not read device state %s: %s", state_path, exc)
        return _empty_state()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning("Ignoring invalid device state file %s", state_path)
        return _empty_state()

    if not isinstance(data, dict):
        return _empty_state()
    last_tag = data.get("last_tag")
    events = data.get("events")
    return {
        "last_tag": last_tag if isinstance(last_tag, dict) else None,
        "events": events if isinstance(events, list) else [],
    }


def record_tag(
    path: str | Path,
    uid: str,
    *,
    known: bool,
    character_name: str | None = None,
    source: str = "reader",
) -> dict[str, Any]:
    normalized_uid = normalize_uid(uid)
    label = character_name or "Unknown tag"
    message = f"{label} {'seen' if known else 'found'} ({normalized_uid})."
    tag = {
        "uid": normalized_uid,
        "known": bool(known),
        "character_name": character_name,
        "source": source,
        "seen_at": _now_iso(),
    }
    data = load_state(path)
    data["last_tag"] = tag
    data["events"] = [_event("tag", message, uid=normalized_uid, character_name=character_name), *data["events"]][
        :MAX_EVENTS
    ]
    write_state(path, data)
    return tag


def append_event(
    path: str | Path,
    event_type: str,
    message: str,
    *,
    uid: str | None = None,
    character_name: str | None = None,
    level: str = "info",
) -> dict[str, Any]:
    event = _event(
        event_type,
        message,
        uid=normalize_uid(uid) if uid else None,
        character_name=character_name,
        level=level,
    )
    data = load_state(path)
    data["events"] = [event, *data["events"]][:MAX_EVENTS]
    write_state(path, data)
    return event


def write_state(path: str | Path, data: dict[str, Any]) -> None:
    state_path = Path(path).expanduser().resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_tag": data.get("last_tag") if isinstance(data.get("last_tag"), dict) else None,
        "events": [item for item in data.get("events", []) if isinstance(item, dict)][:MAX_EVENTS],
    }
    temp_path = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(state_path)


def _empty_state() -> dict[str, Any]:
    return {"last_tag": None, "events": []}


def _event(
    event_type: str,
    message: str,
    *,
    uid: str | None = None,
    character_name: str | None = None,
    level: str = "info",
) -> dict[str, Any]:
    return {
        "type": event_type,
        "level": level,
        "message": message,
        "uid": uid,
        "character_name": character_name,
        "created_at": _now_iso(),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
