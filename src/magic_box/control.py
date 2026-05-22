"""Small file-based controls shared by the admin UI and playback service."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any


LOGGER = logging.getLogger(__name__)
STOP_COMMAND = "stop"


def control_file_for_config(config_path: str | Path) -> Path:
    """Default to a control file beside characters.json."""

    return Path(config_path).expanduser().resolve().parent / "control.json"


def request_stop(control_path: str | Path) -> None:
    """Ask the running playback service to stop its current audio."""

    path = Path(control_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"command": STOP_COMMAND, "requested_at": time.time()}
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def consume_stop_request(control_path: str | Path) -> bool:
    """Return True once for a pending stop request, then remove it."""

    path = Path(control_path).expanduser().resolve()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError as exc:
        LOGGER.warning("Could not read control file %s: %s", path, exc)
        return False

    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning("Ignoring invalid control file %s", path)
        _unlink_control_file(path)
        return False

    _unlink_control_file(path)
    return isinstance(payload, dict) and str(payload.get("command", "")).strip().lower() == STOP_COMMAND


def _unlink_control_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("Could not remove control file %s: %s", path, exc)
