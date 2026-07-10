"""Configuration loading for character-to-audio mappings."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
import unicodedata


VALID_MODES = {"first", "shuffle", "sequence"}


class ConfigError(Exception):
    """Raised when the character configuration cannot be loaded."""


@dataclass(frozen=True)
class Character:
    uid: str
    name: str
    folder: Path
    mode: str


def normalize_uid(uid: str) -> str:
    """Normalize a typed or NFC-read UID into the app's canonical form."""
    value = uid.strip().upper()
    if not value:
        raise ValueError("UID cannot be empty")

    compact = re.sub(r"[-_\s:]+", "", value)
    is_hex_uid = bool(re.fullmatch(r"[0-9A-F]+", compact))
    if compact and is_hex_uid and len(compact) % 2 == 0:
        return "-".join(compact[index : index + 2] for index in range(0, len(compact), 2))

    return re.sub(r"[-_\s:]+", "-", value).strip("-")


class CharacterConfig:
    """Loaded character map."""

    def __init__(self, path: Path, characters: dict[str, Character]) -> None:
        self.path = path
        self.characters = characters

    @classmethod
    def load(cls, path: str | Path) -> "CharacterConfig":
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")

        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in {config_path}: {exc}") from exc

        return cls.from_mapping(config_path, raw)

    @classmethod
    def from_mapping(cls, path: str | Path, raw: Any) -> "CharacterConfig":
        """Build a config from an already-read JSON value.

        The transactional player-load bridge uses this entry point so the
        bytes it fingerprints are the same bytes it parses.  Normal maker
        mode continues to use :meth:`load`.
        """
        config_path = Path(path).expanduser().resolve()

        if not isinstance(raw, dict):
            raise ConfigError("Character config must be a JSON object keyed by UID")

        project_root = config_path.parent.parent
        characters: dict[str, Character] = {}
        for raw_uid, raw_character in raw.items():
            uid = normalize_uid(str(raw_uid))
            characters[uid] = _parse_character(uid, raw_character, project_root)

        return cls(config_path, characters)

    def lookup(self, uid: str) -> Character | None:
        return self.characters.get(normalize_uid(uid))


def project_root_for_config(path: str | Path) -> Path:
    return Path(path).expanduser().resolve().parent.parent


def load_raw_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        return {}

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Character config must be a JSON object keyed by UID")
    return raw


def write_raw_config(path: str | Path, data: dict[str, Any]) -> None:
    config_path = Path(path).expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def folder_from_config_value(folder_value: str, project_root: Path) -> Path:
    folder = Path(folder_value).expanduser()
    if not folder.is_absolute():
        folder = project_root / folder
    return folder.resolve()


def folder_for_config(folder: str | Path, project_root: Path) -> str:
    folder_path = Path(folder).expanduser().resolve()
    try:
        return folder_path.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(folder_path)


def slugify_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip()).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("'", "").replace("’", "")
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in normalized)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts) or "character"


def unique_character_folder(project_root: Path, name: str, data: dict[str, Any], uid: str) -> Path:
    base = (project_root / "audio" / slugify_name(name)).resolve()
    used_folders: set[Path] = set()
    for raw_uid, raw_character in data.items():
        try:
            existing_uid = normalize_uid(str(raw_uid))
        except ValueError:
            continue
        if existing_uid == uid and isinstance(raw_character, dict):
            folder_value = raw_character.get("folder")
            if isinstance(folder_value, str) and folder_value.strip():
                return folder_from_config_value(folder_value, project_root)
            continue
        if existing_uid == uid:
            continue
        if not isinstance(raw_character, dict):
            continue
        folder_value = raw_character.get("folder")
        if not isinstance(folder_value, str) or not folder_value.strip():
            continue
        used_folders.add(folder_from_config_value(folder_value, project_root))

    if base not in used_folders and not base.exists():
        return base

    for index in range(2, 1000):
        candidate = base.with_name(f"{base.name}-{index}")
        if candidate not in used_folders and not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find an available audio folder for {name}")


def _parse_character(uid: str, raw: Any, project_root: Path) -> Character:
    if not isinstance(raw, dict):
        raise ConfigError(f"Character {uid} must be a JSON object")

    name = str(raw.get("name", uid)).strip() or uid
    folder_value = raw.get("folder")
    if not isinstance(folder_value, str) or not folder_value.strip():
        raise ConfigError(f"Character {uid} needs a non-empty folder")

    folder = folder_from_config_value(folder_value, project_root)
    if not folder.exists() or not folder.is_dir():
        raise ConfigError(f"Audio folder for {uid} does not exist: {folder}")

    mode = str(raw.get("mode", "first")).strip().lower()
    if mode not in VALID_MODES:
        raise ConfigError(f"Character {uid} has invalid mode {mode!r}; use one of {sorted(VALID_MODES)}")

    return Character(uid=uid, name=name, folder=folder, mode=mode)
