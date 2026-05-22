"""Persistent software volume for mpg123 playback."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


LOGGER = logging.getLogger(__name__)
DEFAULT_VOLUME_PERCENT = 50
MIN_VOLUME_PERCENT = 0
MAX_VOLUME_PERCENT = 100
VOLUME_STEP_PERCENT = 10


class VolumeControl:
    """Store volume as a percentage shared by the app and admin service."""

    def __init__(self, path: Path, default_percent: int = DEFAULT_VOLUME_PERCENT) -> None:
        self.path = path
        self.default_percent = clamp_volume(default_percent)

    def get(self) -> int:
        if not self.path.exists():
            return self.default_percent

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not read volume file %s: %s", self.path, exc)
            return self.default_percent

        try:
            return clamp_volume(int(data.get("volume_percent", self.default_percent)))
        except (TypeError, ValueError):
            return self.default_percent

    def set(self, percent: int) -> int:
        value = clamp_volume(percent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"volume_percent": value}, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(payload)
            temp_path = Path(temp_file.name)
        temp_path.replace(self.path)
        return value

    def adjust(self, delta: int) -> int:
        return self.set(self.get() + delta)


def volume_file_for_config(config_path: Path) -> Path:
    return config_path.expanduser().resolve().parent / "volume.json"


def clamp_volume(percent: int) -> int:
    return min(max(percent, MIN_VOLUME_PERCENT), MAX_VOLUME_PERCENT)


def apply_pipewire_volume(percent: int, target: str = "@DEFAULT_AUDIO_SINK@") -> bool:
    """Apply volume to the PipeWire default sink when wpctl is available."""

    command = shutil.which("wpctl")
    if command is None:
        return False

    value = clamp_volume(percent) / 100
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    completed = subprocess.run(
        [command, "set-volume", target, f"{value:.3f}"],
        check=False,
        capture_output=True,
        env=env,
    )
    if completed.returncode != 0:
        LOGGER.warning("Could not set PipeWire volume: %s", completed.stderr.decode(errors="replace"))
        return False
    return True
