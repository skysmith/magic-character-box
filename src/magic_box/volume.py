"""Persistent software volume for mpg123 playback."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Any


LOGGER = logging.getLogger(__name__)
DEFAULT_VOLUME_PERCENT = 50
DEFAULT_MAX_OUTPUT_VOLUME_PERCENT = 100
MIN_VOLUME_PERCENT = 0
MAX_VOLUME_PERCENT = 100
VOLUME_STEP_PERCENT = 10
STORY_DOCK_VOLUME_SCHEMA = "story-dock-volume-applied-v1"
STORY_DOCK_VOLUME_CONTROL_VERSION = 1


class VolumeFileError(RuntimeError):
    """A bounded local volume-file failure safe to report without a path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class StoryDockVolumeState:
    volume_percent: int
    applied_revision: int


class VolumeControl:
    """Store volume as a percentage shared by the app and admin service."""

    def __init__(self, path: Path, default_percent: int = DEFAULT_VOLUME_PERCENT) -> None:
        self.path = path
        self.default_percent = clamp_volume(default_percent)

    def get(self) -> int:
        try:
            data = self._read_payload(strict=False)
        except VolumeFileError as exc:
            LOGGER.warning("Could not read volume setting: %s", exc.reason)
            return self.default_percent

        try:
            return clamp_volume(int(data.get("volume_percent", self.default_percent)))
        except (TypeError, ValueError):
            return self.default_percent

    def set(self, percent: int) -> int:
        value = clamp_volume(percent)
        story_dock = self._existing_story_dock_metadata()
        payload: dict[str, Any] = {"volume_percent": value}
        if story_dock is not None:
            payload["story_dock"] = story_dock
        self._replace_payload(payload)
        return value

    def story_dock_state(self) -> StoryDockVolumeState:
        data = self._read_payload(strict=False)
        if not data:
            return StoryDockVolumeState(self.default_percent, 0)
        raw_percent = data.get("volume_percent", self.default_percent)
        if isinstance(raw_percent, bool) or not isinstance(raw_percent, int):
            raise VolumeFileError("volume_file_unreadable")
        percent = clamp_volume(raw_percent)
        metadata = data.get("story_dock")
        if metadata is None:
            return StoryDockVolumeState(percent, 0)
        if not isinstance(metadata, dict):
            raise VolumeFileError("volume_file_unreadable")
        revision = metadata.get("applied_revision")
        if (
            metadata.get("schema") != STORY_DOCK_VOLUME_SCHEMA
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
        ):
            raise VolumeFileError("volume_file_unreadable")
        return StoryDockVolumeState(percent, revision)

    def set_revisioned(self, percent: int, revision: int) -> StoryDockVolumeState:
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
            raise VolumeFileError("malformed_settings")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise VolumeFileError("malformed_settings")
        previous = self.story_dock_state()
        if revision < previous.applied_revision:
            raise VolumeFileError("revision_regression")
        previous_bytes = self._existing_bytes()
        previous_mode = self._existing_mode()
        try:
            self._replace_payload(
                {
                    "volume_percent": percent,
                    "story_dock": {
                        "schema": STORY_DOCK_VOLUME_SCHEMA,
                        "applied_revision": revision,
                    },
                }
            )
            applied = self.story_dock_state()
            if applied != StoryDockVolumeState(percent, revision):
                raise VolumeFileError("readback_mismatch")
            return applied
        except VolumeFileError:
            self._restore_previous(previous_bytes, previous_mode)
            raise
        except OSError as exc:
            self._restore_previous(previous_bytes, previous_mode)
            raise VolumeFileError("write_failed") from exc

    def adjust(self, delta: int) -> int:
        return self.set(self.get() + delta)

    def _read_payload(self, *, strict: bool) -> dict[str, Any]:
        try:
            target = self.path.lstat()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise VolumeFileError("volume_file_unreadable") from exc
        if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
            raise VolumeFileError("unsafe_volume_file")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VolumeFileError("volume_file_unreadable") from exc
        if not isinstance(data, dict):
            raise VolumeFileError("volume_file_unreadable")
        if strict and not data:
            raise VolumeFileError("volume_file_unreadable")
        return data

    def _existing_story_dock_metadata(self) -> dict[str, Any] | None:
        try:
            data = self._read_payload(strict=False)
        except VolumeFileError as exc:
            if exc.reason == "unsafe_volume_file":
                raise
            return None
        metadata = data.get("story_dock")
        if not isinstance(metadata, dict):
            return None
        revision = metadata.get("applied_revision")
        if (
            metadata.get("schema") != STORY_DOCK_VOLUME_SCHEMA
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
        ):
            return None
        return {
            "schema": STORY_DOCK_VOLUME_SCHEMA,
            "applied_revision": revision,
        }

    def _replace_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._require_safe_target()
        existing_mode = self._existing_mode()
        staged_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                staged_path = Path(temp_file.name)
                json.dump(payload, temp_file, indent=2, sort_keys=True)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            if existing_mode is not None:
                os.chmod(staged_path, existing_mode)
            os.replace(staged_path, self.path)
            staged_path = None
            self._fsync_parent()
        except VolumeFileError:
            raise
        except OSError as exc:
            raise VolumeFileError("write_failed") from exc
        finally:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)

    def _require_safe_target(self) -> None:
        try:
            parent = self.path.parent.lstat()
        except OSError as exc:
            raise VolumeFileError("write_failed") from exc
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            raise VolumeFileError("unsafe_volume_file")
        try:
            target = self.path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise VolumeFileError("write_failed") from exc
        if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
            raise VolumeFileError("unsafe_volume_file")

    def _existing_bytes(self) -> bytes | None:
        try:
            self._require_safe_target()
            return self.path.read_bytes()
        except FileNotFoundError:
            return None
        except VolumeFileError:
            raise
        except OSError as exc:
            raise VolumeFileError("volume_file_unreadable") from exc

    def _existing_mode(self) -> int | None:
        try:
            target = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise VolumeFileError("volume_file_unreadable") from exc
        if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
            raise VolumeFileError("unsafe_volume_file")
        return stat.S_IMODE(target.st_mode)

    def _restore_previous(self, previous: bytes | None, mode: int | None) -> None:
        try:
            if previous is None:
                if self.path.exists() and not self.path.is_symlink():
                    self.path.unlink()
                    self._fsync_parent()
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            staged_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "wb",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.restore.",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    staged_path = Path(temp_file.name)
                    temp_file.write(previous)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                if mode is not None:
                    os.chmod(staged_path, mode)
                os.replace(staged_path, self.path)
                staged_path = None
                self._fsync_parent()
            finally:
                if staged_path is not None:
                    staged_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.error("Could not restore the prior volume file after a failed update.")

    def _fsync_parent(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(self.path.parent, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def apply_story_dock_volume_settings(
    path: Path,
    settings: dict[str, Any],
    *,
    default_percent: int = DEFAULT_VOLUME_PERCENT,
) -> dict[str, Any]:
    """Apply one validated declarative volume block and return a bounded report."""

    control = VolumeControl(path, default_percent=default_percent)
    attempted_revision = _candidate_attempted_revision(settings)
    try:
        current = control.story_dock_state()
    except VolumeFileError as exc:
        return _story_dock_volume_report(
            status="failed",
            percent=control.get(),
            revision=0,
            attempted_revision=attempted_revision,
            reason=exc.reason,
        )

    if not isinstance(settings, dict) or set(settings) not in (
        {"managed"},
        {"managed", "desired_percent", "revision"},
    ):
        return _story_dock_volume_report(
            status="failed",
            percent=current.volume_percent,
            revision=current.applied_revision,
            attempted_revision=attempted_revision,
            reason="malformed_settings",
        )
    managed = settings.get("managed")
    if not isinstance(managed, bool):
        return _story_dock_volume_report(
            status="failed",
            percent=current.volume_percent,
            revision=current.applied_revision,
            attempted_revision=attempted_revision,
            reason="malformed_settings",
        )
    if not managed:
        if set(settings) != {"managed"}:
            return _story_dock_volume_report(
                status="failed",
                percent=current.volume_percent,
                revision=current.applied_revision,
                attempted_revision=attempted_revision,
                reason="malformed_settings",
            )
        return _story_dock_volume_report(
            status="unmanaged",
            percent=current.volume_percent,
            revision=current.applied_revision,
        )

    desired_percent = settings.get("desired_percent")
    desired_revision = settings.get("revision")
    if (
        set(settings) != {"managed", "desired_percent", "revision"}
        or isinstance(desired_percent, bool)
        or not isinstance(desired_percent, int)
        or not 0 <= desired_percent <= 100
        or isinstance(desired_revision, bool)
        or not isinstance(desired_revision, int)
        or desired_revision < 0
    ):
        return _story_dock_volume_report(
            status="failed",
            percent=current.volume_percent,
            revision=current.applied_revision,
            attempted_revision=attempted_revision,
            reason="malformed_settings",
        )
    if desired_revision < current.applied_revision:
        return _story_dock_volume_report(
            status="failed",
            percent=current.volume_percent,
            revision=current.applied_revision,
            attempted_revision=desired_revision,
            reason="revision_regression",
        )
    if (
        desired_revision == current.applied_revision
        and desired_percent == current.volume_percent
    ):
        return _story_dock_volume_report(
            status="applied",
            percent=current.volume_percent,
            revision=current.applied_revision,
        )
    try:
        applied = control.set_revisioned(desired_percent, desired_revision)
    except VolumeFileError as exc:
        return _story_dock_volume_report(
            status="failed",
            percent=current.volume_percent,
            revision=current.applied_revision,
            attempted_revision=desired_revision,
            reason=exc.reason,
        )
    return _story_dock_volume_report(
        status="applied",
        percent=applied.volume_percent,
        revision=applied.applied_revision,
    )


def _story_dock_volume_report(
    *,
    status: str,
    percent: int,
    revision: int,
    attempted_revision: int | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if attempted_revision is None and status in {"applied", "unmanaged"}:
        attempted_revision = revision
    return {
        "volume_control_version": STORY_DOCK_VOLUME_CONTROL_VERSION,
        "volume_apply_status": status,
        "volume_applied_percent": clamp_volume(percent),
        "volume_applied_revision": max(0, int(revision)),
        "volume_attempted_revision": (
            max(0, int(attempted_revision))
            if attempted_revision is not None
            else None
        ),
        "volume_apply_reason": reason,
    }


def _candidate_attempted_revision(settings: Any) -> int | None:
    if not isinstance(settings, dict):
        return None
    revision = settings.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return None
    return revision


def volume_file_for_config(config_path: Path) -> Path:
    return config_path.expanduser().resolve().parent / "volume.json"


def clamp_volume(percent: int) -> int:
    return min(max(percent, MIN_VOLUME_PERCENT), MAX_VOLUME_PERCENT)


def effective_output_volume(volume_percent: int, max_output_percent: int = DEFAULT_MAX_OUTPUT_VOLUME_PERCENT) -> float:
    """Map user-facing volume through a speaker/amp-specific output ceiling."""

    return clamp_volume(volume_percent) * (clamp_volume(max_output_percent) / 100)


def apply_pipewire_volume(percent: float, target: str = "@DEFAULT_AUDIO_SINK@") -> bool:
    """Apply volume to the PipeWire default sink when wpctl is available."""

    command = shutil.which("wpctl")
    if command is None:
        return False

    value = min(max(percent, MIN_VOLUME_PERCENT), MAX_VOLUME_PERCENT) / 100
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
