"""Opt-in, fail-closed config activation handshake for the player process.

The bridge is deliberately local and transport-agnostic.  Another local
process may prepare audio and request a config activation, but the player only
acknowledges after this exact process has parsed the fingerprinted config,
verified its selected files, and swapped the resulting mapping in memory.

Normal maker mode does not instantiate this bridge and keeps the existing
mtime-based config reload behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Iterable
import uuid

from .audio import PLAYABLE_EXTENSIONS
from .config import CharacterConfig, ConfigError, normalize_uid


REQUEST_SCHEMA = "story-dock-player-load-request-v2"
ACK_SCHEMA = "story-dock-player-load-ack-v2"
STATE_SCHEMA = "magic-character-box-player-load-state-v2"
REQUEST_FILENAME = "story-dock-player-load-request.json"
ACK_FILENAME = "story-dock-player-load-ack.json"
STATE_FILENAME = ".magic-character-box-player-load-state.json"

MAX_REQUEST_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_GENERATION_METADATA_BYTES = 8 * 1024 * 1024
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_ACTIVATION_HISTORY = 100_000
MAX_BINDINGS = 8_192
MAX_GENERATION_FILES = 65_536
MAX_PLAYABLE_FILES = 65_536
MAX_SINGLE_AUDIO_BYTES = 512 * 1024 * 1024
MAX_TOTAL_AUDIO_BYTES = 16 * 1024 * 1024 * 1024
MAX_TARGET_LABEL_CHARS = 512
MAX_PATH_CHARS = 4_096
REJECTION_REASON_CODES = {
    "candidate-config-invalid",
    "selected-inventory-invalid",
    "generation-invalid",
}

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUEST_FIELDS = {
    "schema",
    "activation_id",
    "operation",
    "target_kind",
    "target_label",
    "manifest_revision",
    "project_root",
    "config_path",
    "config_sha256",
    "generation_root",
    "generation_metadata_sha256",
    "selected_inventory_sha256",
    "selected_binding_count",
    "selected_folder_count",
    "selected_file_count",
}
ACK_PROOF_FIELDS = tuple(sorted(REQUEST_FIELDS - {"schema"}))
STATE_PROOF_FIELDS = tuple(
    sorted(
        {
            "operation",
            "target_kind",
            "target_label",
            "manifest_revision",
            "project_root",
            "config_path",
            "config_sha256",
            "generation_root",
            "generation_metadata_sha256",
            "selected_inventory_sha256",
            "selected_binding_count",
            "selected_folder_count",
            "selected_file_count",
        }
    )
)


class PlayerLoadError(RuntimeError):
    """A player-load request or its candidate failed closed."""


@dataclass(frozen=True)
class SelectedInventory:
    """Canonical proof of the exact files selectable by loaded bindings."""

    sha256: str
    binding_count: int
    folder_count: int
    file_count: int
    paths: frozenset[str]


@dataclass(frozen=True)
class PlayerLoadRequest:
    """Validated request fields echoed by a successful acknowledgement."""

    payload: dict[str, Any]
    fingerprint: str

    @property
    def activation_id(self) -> str:
        return str(self.payload["activation_id"])


class PlayerLoadBridge:
    """Own the transactional player's active in-memory config.

    ``poll`` is intentionally non-throwing for candidate/request failures: the
    currently loaded mapping remains available and no acknowledgement is
    emitted.  Construction errors indicate an unsafe local setup and do raise.
    """

    def __init__(
        self,
        config: CharacterConfig,
        *,
        project_root: str | Path | None = None,
        request_path: str | Path | None = None,
        ack_path: str | Path | None = None,
        state_path: str | Path | None = None,
        playable_extensions: Iterable[str] = PLAYABLE_EXTENSIONS,
    ) -> None:
        configured_path = Path(config.path).expanduser()
        if configured_path.is_symlink():
            raise PlayerLoadError("Player config path must not be a symlink")
        self.config_path = configured_path.resolve(strict=True)
        if not self.config_path.is_file():
            raise PlayerLoadError("Player config path must be a regular file")

        inferred_root = self.config_path.parent.parent
        requested_root = Path(project_root).expanduser() if project_root is not None else inferred_root
        if requested_root.is_symlink():
            raise PlayerLoadError("Player project root must not be a symlink")
        self.project_root = requested_root.resolve(strict=True)
        if not self.project_root.is_dir():
            raise PlayerLoadError("Player project root must be a directory")
        _relative_to(self.config_path, self.project_root, label="Player config")

        self.request_path = _bridge_path(request_path, self.config_path.parent / REQUEST_FILENAME)
        self.ack_path = _bridge_path(ack_path, self.config_path.parent / ACK_FILENAME)
        self.state_path = _bridge_path(state_path, self.config_path.parent / STATE_FILENAME)
        for path in (self.request_path, self.ack_path, self.state_path):
            if path.parent != self.config_path.parent:
                raise PlayerLoadError("Player-load bridge files must stay beside the config")

        self.playable_extensions = frozenset(_normalize_extensions(playable_extensions))
        if not self.playable_extensions:
            raise PlayerLoadError("At least one playable extension is required")
        self.config = config
        self.player_instance_id = uuid.uuid4().hex
        self._load_sequence = 0
        self._active_fingerprint: str | None = None
        self._active_ack: dict[str, Any] | None = None
        self._invalid_request_signature: tuple[int, int, int, int, int] | None = None
        self._pending_observation: tuple[str, tuple[int, int, int, int, int] | None] | None = None
        self._rejected_fingerprint: str | None = None
        self._rejected_ack: dict[str, Any] | None = None
        self._state_existed = self.state_path.exists()
        self._state = self._read_state()
        self._initialize_active_config()

    def poll(self) -> bool:
        """Process one pending request and return whether config was swapped."""
        try:
            request_signature = _file_signature(self.request_path)
            if request_signature is None:
                return False
            if request_signature == self._invalid_request_signature:
                return False
            try:
                raw_request = _read_regular_bytes(
                    self.request_path,
                    maximum_bytes=MAX_REQUEST_BYTES,
                    missing_ok=False,
                )
                assert raw_request is not None
                request = self._parse_request(raw_request)
            except (PlayerLoadError, OSError, UnicodeError, ValueError):
                self._invalid_request_signature = request_signature
                return False
            self._invalid_request_signature = None

            history = self._state["history"]
            previous_fingerprint = history.get(request.activation_id)
            if previous_fingerprint is not None and previous_fingerprint != request.fingerprint:
                raise PlayerLoadError("Activation id was reused with different proof fields")
            if (
                previous_fingerprint == request.fingerprint
                and self._state.get("active_activation_id") != request.activation_id
                and self._state.get("pending_activation_id") != request.activation_id
                and self._state.get("rejected_activation_id") != request.activation_id
            ):
                raise PlayerLoadError("A completed activation id cannot be replayed after another load")

            if self._active_fingerprint == request.fingerprint and self._active_ack is not None:
                if not _ack_matches(self.ack_path, self._active_ack):
                    _atomic_write_json(self.ack_path, self._active_ack)
                return False
            if self._rejected_fingerprint == request.fingerprint and self._rejected_ack is not None:
                if not _ack_matches(self.ack_path, self._rejected_ack):
                    _atomic_write_json(self.ack_path, self._rejected_ack)
                return False
            if (
                self._state.get("rejected_activation_id") == request.activation_id
                and self._state.get("rejected_fingerprint") == request.fingerprint
            ):
                self._emit_rejection(
                    request,
                    str(self._state.get("rejected_reason_code") or "candidate-invalid"),
                )
                return False

            config_signature = _file_signature(self.config_path)
            observation = (request.fingerprint, config_signature)
            if self._pending_observation == observation:
                return False
            try:
                raw_config, canonical_config_sha = self._read_candidate_raw()
            except (ConfigError, PlayerLoadError, OSError, UnicodeError, ValueError):
                self._pending_observation = observation
                return False
            if canonical_config_sha != request.payload["config_sha256"]:
                self._pending_observation = observation
                return False
            self._pending_observation = None

            try:
                candidate = self._candidate_from_raw(raw_config)
            except (ConfigError, PlayerLoadError, OSError, UnicodeError, ValueError):
                self._emit_rejection(request, "candidate-config-invalid")
                return False
            try:
                selected = build_selected_inventory(
                    candidate,
                    project_root=self.project_root,
                    playable_extensions=self.playable_extensions,
                )
                _require_selected_proof(request.payload, selected)
            except (PlayerLoadError, OSError, ValueError):
                self._emit_rejection(request, "selected-inventory-invalid")
                return False
            try:
                self._verify_generation(request.payload, selected)
            except (PlayerLoadError, OSError, UnicodeError, ValueError):
                self._emit_rejection(request, "generation-invalid")
                return False

            if previous_fingerprint is None:
                if len(history) >= MAX_ACTIVATION_HISTORY:
                    raise PlayerLoadError("Player activation history reached its safe bound")
                history[request.activation_id] = request.fingerprint
                self._state["pending_activation_id"] = request.activation_id
                self._state["pending_fingerprint"] = request.fingerprint
                self._write_state()

            previous_config = self.config
            previous_state_fields = {
                field: self._state.get(field)
                for field in (
                    "active_activation_id",
                    "active_fingerprint",
                    "active_proof",
                    "rejected_activation_id",
                    "rejected_fingerprint",
                    "rejected_reason_code",
                )
            }
            self.config = candidate
            self._load_sequence += 1
            ack = self._make_ack(request, status="loaded")
            self._state["active_activation_id"] = request.activation_id
            self._state["active_fingerprint"] = request.fingerprint
            self._state["active_proof"] = {
                field: request.payload[field] for field in STATE_PROOF_FIELDS
            }
            self._state["pending_activation_id"] = None
            self._state["pending_fingerprint"] = None
            self._state["rejected_activation_id"] = None
            self._state["rejected_fingerprint"] = None
            self._state["rejected_reason_code"] = None
            try:
                # Startup proof is durable before an external process can
                # observe the loaded ack.  A crash between these writes is
                # recovered by replaying the still-present exact request.
                self._write_state()
                _atomic_write_json(self.ack_path, ack)
            except (OSError, PlayerLoadError):
                self.config = previous_config
                self._state.update(previous_state_fields)
                self._state["pending_activation_id"] = request.activation_id
                self._state["pending_fingerprint"] = request.fingerprint
                try:
                    self._write_state()
                except (OSError, PlayerLoadError):
                    pass
                raise
            self._active_fingerprint = request.fingerprint
            self._active_ack = ack
            return True
        except (ConfigError, PlayerLoadError, OSError, UnicodeError, ValueError):
            return False

    def _initialize_active_config(self) -> None:
        """Establish a proven startup mapping before the app can serve taps."""
        if not self._state_existed:
            # A valid exact request already present at the first opt-in start
            # takes precedence over bootstrap.  A future request whose config
            # has not arrived yet leaves history empty and permits bootstrap of
            # the current pre-transactional maker config.
            if self.poll():
                return
            if self._state["history"]:
                raise PlayerLoadError("Initial transactional request could not be loaded safely")
            raw_config, config_sha = self._read_candidate_raw()
            candidate = self._candidate_from_raw(raw_config)
            selected = build_selected_inventory(
                candidate,
                project_root=self.project_root,
                playable_extensions=self.playable_extensions,
            )
            proof = {
                "operation": "bootstrap",
                "target_kind": "legacy-config",
                "target_label": f"bootstrap:{config_sha}",
                "manifest_revision": None,
                "project_root": str(self.project_root),
                "config_path": str(self.config_path),
                "config_sha256": config_sha,
                "generation_root": None,
                "generation_metadata_sha256": None,
                "selected_inventory_sha256": selected.sha256,
                "selected_binding_count": selected.binding_count,
                "selected_folder_count": selected.folder_count,
                "selected_file_count": selected.file_count,
            }
            self.config = candidate
            self._state["active_proof"] = proof
            self._write_state()
            return

        proof = self._state.get("active_proof")
        if not isinstance(proof, dict):
            if self._state.get("pending_activation_id") is not None and self.poll():
                return
            raise PlayerLoadError("Transactional startup lacked a confirmed active proof")
        try:
            candidate = self._load_proven_candidate(proof)
        except (ConfigError, PlayerLoadError, OSError, UnicodeError, ValueError):
            if self.poll():
                return
            raise PlayerLoadError("On-disk config did not match the last confirmed activation")
        self.config = candidate

        # If a request survived a process restart, answer it before any startup
        # sound or NFC work.  A request-first config mismatch remains pending
        # while the confirmed mapping stays active.
        self.poll()

    def _load_proven_candidate(self, proof: dict[str, Any]) -> CharacterConfig:
        _validate_state_proof(proof, project_root=self.project_root, config_path=self.config_path)
        raw_config, config_sha = self._read_candidate_raw()
        if config_sha != proof["config_sha256"]:
            raise PlayerLoadError("Startup config fingerprint changed without activation")
        candidate = self._candidate_from_raw(raw_config)
        selected = build_selected_inventory(
            candidate,
            project_root=self.project_root,
            playable_extensions=self.playable_extensions,
        )
        _require_selected_proof(proof, selected)
        self._verify_generation(proof, selected)
        return candidate

    def _emit_rejection(self, request: PlayerLoadRequest, reason_code: str) -> None:
        history = self._state["history"]
        previous = history.get(request.activation_id)
        if previous is not None and previous != request.fingerprint:
            raise PlayerLoadError("Activation id was reused with different proof fields")
        if previous is None:
            if len(history) >= MAX_ACTIVATION_HISTORY:
                raise PlayerLoadError("Player activation history reached its safe bound")
            history[request.activation_id] = request.fingerprint
        self._state["rejected_activation_id"] = request.activation_id
        self._state["rejected_fingerprint"] = request.fingerprint
        self._state["rejected_reason_code"] = reason_code
        if self._state.get("pending_activation_id") == request.activation_id:
            self._state["pending_activation_id"] = None
            self._state["pending_fingerprint"] = None
        self._write_state()
        ack = self._make_ack(request, status="rejected", reason_code=reason_code)
        _atomic_write_json(self.ack_path, ack)
        self._rejected_fingerprint = request.fingerprint
        self._rejected_ack = ack

    def _make_ack(
        self,
        request: PlayerLoadRequest,
        *,
        status: str,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        ack = {
            "schema": ACK_SCHEMA,
            **{field: request.payload[field] for field in ACK_PROOF_FIELDS},
            "status": status,
            "player_instance_id": self.player_instance_id,
            "player_pid": os.getpid(),
            "load_sequence": self._load_sequence,
            "loaded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }
        if reason_code is not None:
            ack["reason_code"] = reason_code
        return ack

    def _parse_request(self, raw_request: bytes) -> PlayerLoadRequest:
        payload = _decode_json_object(raw_request, label="Player-load request")
        if set(payload) != REQUEST_FIELDS:
            raise PlayerLoadError("Player-load request fields were not exact")
        if payload.get("schema") != REQUEST_SCHEMA:
            raise PlayerLoadError("Player-load request schema was not supported")
        _require_uuid(payload.get("activation_id"))
        operation = payload.get("operation")
        target_kind = payload.get("target_kind")
        if operation not in {"activate", "rollback"}:
            raise PlayerLoadError("Player-load operation was invalid")
        if target_kind not in {"manifest-revision", "legacy-config"}:
            raise PlayerLoadError("Player-load target kind was invalid")
        if operation == "activate" and target_kind != "manifest-revision":
            raise PlayerLoadError("Activate requests require a manifest target")

        target_label = _bounded_nonempty_string(
            payload.get("target_label"),
            maximum_chars=MAX_TARGET_LABEL_CHARS,
            label="Target label",
        )
        manifest_revision = payload.get("manifest_revision")
        if target_kind == "manifest-revision":
            if not isinstance(manifest_revision, str) or manifest_revision != target_label:
                raise PlayerLoadError("Manifest target label and revision must match exactly")
        elif manifest_revision is not None:
            raise PlayerLoadError("Legacy config targets must not claim a manifest revision")

        if payload.get("project_root") != str(self.project_root):
            raise PlayerLoadError("Player-load project root did not match this process")
        if payload.get("config_path") != str(self.config_path):
            raise PlayerLoadError("Player-load config path did not match this process")
        for field in ("config_sha256", "selected_inventory_sha256"):
            _require_sha256(payload.get(field), label=field)
        for field in (
            "selected_binding_count",
            "selected_folder_count",
            "selected_file_count",
        ):
            _require_count(payload.get(field), maximum=MAX_PLAYABLE_FILES, label=field)

        generation_root = payload.get("generation_root")
        metadata_sha = payload.get("generation_metadata_sha256")
        if target_kind == "manifest-revision":
            if not isinstance(generation_root, str) or len(generation_root) > MAX_PATH_CHARS:
                raise PlayerLoadError("Manifest targets require a bounded generation root")
            root = Path(generation_root)
            if not root.is_absolute() or str(root) != generation_root:
                raise PlayerLoadError("Generation root must be an exact absolute path")
            _require_sha256(metadata_sha, label="generation_metadata_sha256")
        elif generation_root is not None or metadata_sha is not None:
            raise PlayerLoadError("Legacy config targets must not claim generation metadata")

        fingerprint = _canonical_sha256(payload)
        return PlayerLoadRequest(payload=payload, fingerprint=fingerprint)

    def _read_candidate_raw(self) -> tuple[dict[str, Any], str]:
        raw_bytes = _read_regular_bytes(
            self.config_path,
            maximum_bytes=MAX_CONFIG_BYTES,
            missing_ok=False,
        )
        assert raw_bytes is not None
        raw_config = _decode_json_object(raw_bytes, label="Character config")
        if len(raw_config) > MAX_BINDINGS:
            raise PlayerLoadError("Character config exceeded the binding limit")
        return raw_config, _canonical_sha256(raw_config)

    def _candidate_from_raw(self, raw_config: dict[str, Any]) -> CharacterConfig:
        seen: set[str] = set()
        for raw_uid, raw_character in raw_config.items():
            uid = normalize_uid(str(raw_uid))
            if uid in seen:
                raise PlayerLoadError("Character config contained duplicate normalized UIDs")
            seen.add(uid)
            if not isinstance(raw_character, dict):
                raise PlayerLoadError("Character config binding was invalid")
            folder_value = raw_character.get("folder")
            if not isinstance(folder_value, str) or not folder_value.strip():
                raise PlayerLoadError("Character config folder was invalid")
            _validate_raw_folder(folder_value, self.project_root)
        return CharacterConfig.from_mapping(self.config_path, raw_config)

    def _verify_generation(self, payload: dict[str, Any], selected: SelectedInventory) -> None:
        generation_value = payload["generation_root"]
        if generation_value is None:
            return
        generation_root = Path(str(generation_value))
        _require_real_path(generation_root, root=self.project_root, expect_directory=True)
        metadata_path = generation_root / ".story-dock-generation.json"
        metadata_bytes = _read_regular_bytes(
            metadata_path,
            maximum_bytes=MAX_GENERATION_METADATA_BYTES,
            missing_ok=False,
        )
        assert metadata_bytes is not None
        if hashlib.sha256(metadata_bytes).hexdigest() != payload["generation_metadata_sha256"]:
            raise PlayerLoadError("Generation metadata fingerprint did not match")
        metadata = _decode_json_object(metadata_bytes, label="Generation metadata")
        if metadata.get("manifest_revision") != payload["manifest_revision"]:
            raise PlayerLoadError("Generation metadata revision did not match the request")
        declared = _declared_generation_inventory(metadata)
        actual = _actual_generation_inventory(generation_root)
        if declared != actual:
            raise PlayerLoadError("Generation file inventory did not match metadata")

        generation_prefix = _relative_to(generation_root, self.project_root, label="Generation root")
        for selected_path in selected.paths:
            relative = PurePosixPath(selected_path)
            try:
                inside_generation = relative.relative_to(PurePosixPath(generation_prefix))
            except ValueError:
                # A transactional candidate may intentionally preserve maker
                # bindings outside the managed generation.  Their exact files
                # are already covered by the whole-config selected inventory.
                continue
            if inside_generation.as_posix() not in actual:
                raise PlayerLoadError("Selected playback was absent from generation metadata")

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = _read_regular_bytes(self.state_path, maximum_bytes=MAX_STATE_BYTES, missing_ok=True)
            if raw is None:
                return _empty_state()
            payload = _decode_json_object(raw, label="Player-load state")
            if payload.get("schema") != STATE_SCHEMA:
                raise PlayerLoadError("Player-load state schema was invalid")
            if set(payload) != {
                "schema",
                "history",
                "active_activation_id",
                "active_fingerprint",
                "active_proof",
                "pending_activation_id",
                "pending_fingerprint",
                "rejected_activation_id",
                "rejected_fingerprint",
                "rejected_reason_code",
            }:
                raise PlayerLoadError("Player-load state fields were not exact")
            history = payload.get("history")
            if not isinstance(history, dict) or len(history) > MAX_ACTIVATION_HISTORY:
                raise PlayerLoadError("Player-load state history was invalid")
            for activation_id, fingerprint in history.items():
                _require_uuid(activation_id)
                _require_sha256(fingerprint, label="Activation fingerprint")
            active_id = payload.get("active_activation_id")
            active_fingerprint = payload.get("active_fingerprint")
            if active_id is not None:
                _require_uuid(active_id)
                _require_sha256(active_fingerprint, label="Active fingerprint")
                if history.get(active_id) != active_fingerprint:
                    raise PlayerLoadError("Player-load active state lacked matching history")
            elif active_fingerprint is not None:
                raise PlayerLoadError("Player-load state had an orphan active fingerprint")
            active_proof = payload.get("active_proof")
            if active_proof is not None:
                if not isinstance(active_proof, dict):
                    raise PlayerLoadError("Player-load active proof was invalid")
                _validate_state_proof(
                    active_proof,
                    project_root=self.project_root,
                    config_path=self.config_path,
                )
            pending_id = payload.get("pending_activation_id")
            pending_fingerprint = payload.get("pending_fingerprint")
            if pending_id is not None:
                _require_uuid(pending_id)
                _require_sha256(pending_fingerprint, label="Pending fingerprint")
                if history.get(pending_id) != pending_fingerprint:
                    raise PlayerLoadError("Player-load pending state lacked matching history")
            elif pending_fingerprint is not None:
                raise PlayerLoadError("Player-load state had an orphan pending fingerprint")
            rejected_id = payload.get("rejected_activation_id")
            rejected_fingerprint = payload.get("rejected_fingerprint")
            rejected_reason = payload.get("rejected_reason_code")
            if rejected_id is not None:
                _require_uuid(rejected_id)
                _require_sha256(rejected_fingerprint, label="Rejected fingerprint")
                if history.get(rejected_id) != rejected_fingerprint:
                    raise PlayerLoadError("Player-load rejected state lacked matching history")
                if rejected_reason not in REJECTION_REASON_CODES:
                    raise PlayerLoadError("Player-load rejection reason was invalid")
            elif rejected_fingerprint is not None or rejected_reason is not None:
                raise PlayerLoadError("Player-load state had orphan rejection fields")
            return payload
        except (OSError, UnicodeError, ValueError):
            raise PlayerLoadError("Player-load state could not be read safely")

    def _write_state(self) -> None:
        _atomic_write_json(self.state_path, self._state)


def build_selected_inventory(
    config: CharacterConfig,
    *,
    project_root: str | Path,
    playable_extensions: Iterable[str] = PLAYABLE_EXTENSIONS,
) -> SelectedInventory:
    """Fingerprint the exact immediate files that the player can select."""
    root = Path(project_root).resolve(strict=True)
    extensions = frozenset(_normalize_extensions(playable_extensions))
    bindings: list[dict[str, Any]] = []
    unique_folders: set[str] = set()
    unique_files: set[str] = set()
    folder_files: dict[str, list[dict[str, Any]]] = {}
    total_bytes = 0

    if len(config.characters) > MAX_BINDINGS:
        raise PlayerLoadError("Character config exceeded the binding limit")
    for uid in sorted(config.characters):
        character = config.characters[uid]
        folder = character.folder
        _require_real_path(folder, root=root, expect_directory=True)
        relative_folder = _relative_to(folder, root, label="Character folder")
        unique_folders.add(relative_folder)
        files = folder_files.get(relative_folder)
        if files is None:
            before = folder.stat(follow_symlinks=False)
            files = []
            with os.scandir(folder) as entries:
                for entry in sorted(entries, key=lambda item: item.name):
                    details = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(details.st_mode):
                        raise PlayerLoadError("Character folder contained a symlink")
                    if stat.S_ISDIR(details.st_mode):
                        continue
                    if not stat.S_ISREG(details.st_mode):
                        raise PlayerLoadError("Character folder contained a special entry")
                    path = folder / entry.name
                    if path.suffix.lower() not in extensions:
                        continue
                    relative_file = _relative_to(path, root, label="Playable file")
                    byte_count, digest = _hash_regular_file(
                        path,
                        maximum_bytes=MAX_SINGLE_AUDIO_BYTES,
                        allow_empty=True,
                    )
                    total_bytes += byte_count
                    if total_bytes > MAX_TOTAL_AUDIO_BYTES:
                        raise PlayerLoadError("Selected audio exceeded the total byte limit")
                    unique_files.add(relative_file)
                    if len(unique_files) > MAX_PLAYABLE_FILES:
                        raise PlayerLoadError("Selected audio exceeded the file limit")
                    files.append(
                        {
                            "path": relative_file,
                            "byte_count": byte_count,
                            "sha256": digest,
                        }
                    )
            after = folder.stat(follow_symlinks=False)
            if not _same_stat(before, after):
                raise PlayerLoadError("Character folder changed during inventory")
            folder_files[relative_folder] = files
        bindings.append(
            {
                "uid": character.uid,
                "mode": character.mode,
                "folder": relative_folder,
                "files": files,
            }
        )

    return SelectedInventory(
        sha256=_canonical_sha256(bindings),
        binding_count=len(bindings),
        folder_count=len(unique_folders),
        file_count=len(unique_files),
        paths=frozenset(unique_files),
    )


def canonical_config_sha256(raw_config: dict[str, Any]) -> str:
    """Public helper used by local bridge producers and tests."""
    return _canonical_sha256(raw_config)


def _require_selected_proof(payload: dict[str, Any], selected: SelectedInventory) -> None:
    expected = (
        payload["selected_inventory_sha256"],
        payload["selected_binding_count"],
        payload["selected_folder_count"],
        payload["selected_file_count"],
    )
    actual = (
        selected.sha256,
        selected.binding_count,
        selected.folder_count,
        selected.file_count,
    )
    if expected != actual:
        raise PlayerLoadError("Selected playback inventory did not match the request")


def _validate_state_proof(
    proof: dict[str, Any],
    *,
    project_root: Path,
    config_path: Path,
) -> None:
    if set(proof) != set(STATE_PROOF_FIELDS):
        raise PlayerLoadError("Active proof fields were not exact")
    operation = proof.get("operation")
    if operation not in {"bootstrap", "activate", "rollback"}:
        raise PlayerLoadError("Active proof operation was invalid")
    target_kind = proof.get("target_kind")
    if target_kind not in {"manifest-revision", "legacy-config"}:
        raise PlayerLoadError("Active proof target kind was invalid")
    if operation == "activate" and target_kind != "manifest-revision":
        raise PlayerLoadError("Active activation proof required a manifest target")
    if operation == "bootstrap" and target_kind != "legacy-config":
        raise PlayerLoadError("Active bootstrap proof required a legacy target")
    target_label = _bounded_nonempty_string(
        proof.get("target_label"),
        maximum_chars=MAX_TARGET_LABEL_CHARS,
        label="Active target label",
    )
    manifest_revision = proof.get("manifest_revision")
    if target_kind == "manifest-revision":
        if not isinstance(manifest_revision, str) or manifest_revision != target_label:
            raise PlayerLoadError("Active manifest target was inconsistent")
        generation_root = proof.get("generation_root")
        if not isinstance(generation_root, str) or not Path(generation_root).is_absolute():
            raise PlayerLoadError("Active generation root was invalid")
        _require_sha256(proof.get("generation_metadata_sha256"), label="Active metadata SHA256")
    elif (
        manifest_revision is not None
        or proof.get("generation_root") is not None
        or proof.get("generation_metadata_sha256") is not None
    ):
        raise PlayerLoadError("Active legacy proof claimed generation state")
    if proof.get("project_root") != str(project_root) or proof.get("config_path") != str(config_path):
        raise PlayerLoadError("Active proof paths did not match this player")
    _require_sha256(proof.get("config_sha256"), label="Active config SHA256")
    _require_sha256(proof.get("selected_inventory_sha256"), label="Active inventory SHA256")
    for field in ("selected_binding_count", "selected_folder_count", "selected_file_count"):
        _require_count(proof.get(field), maximum=MAX_PLAYABLE_FILES, label=field)


def _declared_generation_inventory(metadata: dict[str, Any]) -> dict[str, tuple[int, str]]:
    raw_inventory = metadata.get("file_inventory")
    if not isinstance(raw_inventory, list) or len(raw_inventory) > MAX_GENERATION_FILES:
        raise PlayerLoadError("Generation metadata inventory was invalid")
    declared: dict[str, tuple[int, str]] = {}
    total_bytes = 0
    for raw in raw_inventory:
        if not isinstance(raw, dict):
            raise PlayerLoadError("Generation metadata entry was invalid")
        relative = _safe_relative_posix(raw.get("path"), label="Generation file")
        if relative == ".story-dock-generation.json" or relative in declared:
            raise PlayerLoadError("Generation metadata paths were duplicated or reserved")
        if PurePosixPath(relative).suffix.lower() != ".mp3":
            raise PlayerLoadError("Generation metadata contained a non-MP3 path")
        byte_count = raw.get("byte_count")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or not 0 < byte_count <= MAX_SINGLE_AUDIO_BYTES:
            raise PlayerLoadError("Generation metadata byte count was invalid")
        digest = _require_sha256(raw.get("sha256"), label="Generation file SHA256")
        if raw.get("codec_name") != "mp3":
            raise PlayerLoadError("Generation metadata contained a non-MP3 playable")
        total_bytes += byte_count
        if total_bytes > MAX_TOTAL_AUDIO_BYTES:
            raise PlayerLoadError("Generation metadata exceeded the total byte limit")
        declared[relative] = (byte_count, digest)
    return declared


def _actual_generation_inventory(root: Path) -> dict[str, tuple[int, str]]:
    inventory: dict[str, tuple[int, str]] = {}
    total_bytes = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        _require_real_path(directory, root=root, expect_directory=True)
        before = directory.stat(follow_symlinks=False)
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name, reverse=True):
                path = directory / entry.name
                details = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(details.st_mode):
                    raise PlayerLoadError("Generation contained a symlink")
                if stat.S_ISDIR(details.st_mode):
                    stack.append(path)
                    continue
                if not stat.S_ISREG(details.st_mode):
                    raise PlayerLoadError("Generation contained a special file")
                relative = path.relative_to(root).as_posix()
                if relative == ".story-dock-generation.json":
                    continue
                byte_count, digest = _hash_regular_file(path, maximum_bytes=MAX_SINGLE_AUDIO_BYTES)
                total_bytes += byte_count
                if total_bytes > MAX_TOTAL_AUDIO_BYTES:
                    raise PlayerLoadError("Generation exceeded the total byte limit")
                inventory[relative] = (byte_count, digest)
                if len(inventory) > MAX_GENERATION_FILES:
                    raise PlayerLoadError("Generation exceeded the file limit")
        after = directory.stat(follow_symlinks=False)
        if not _same_stat(before, after):
            raise PlayerLoadError("Generation directory changed during inventory")
    return inventory


def _read_regular_bytes(path: Path, *, maximum_bytes: int, missing_ok: bool) -> bytes | None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise PlayerLoadError("Required bridge input was missing")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PlayerLoadError("Bridge input was not a regular non-symlink file")
    if details.st_size > maximum_bytes:
        raise PlayerLoadError("Bridge input exceeded its byte limit")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            raise PlayerLoadError("Bridge input changed before read")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise PlayerLoadError("Bridge input exceeded its byte limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not _same_stat(before, after) or consumed != after.st_size:
        raise PlayerLoadError("Bridge input changed during read")
    return b"".join(chunks)


def _hash_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> tuple[int, str]:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PlayerLoadError("Playable path was not a regular non-symlink file")
    minimum_size = 0 if allow_empty else 1
    if not minimum_size <= details.st_size <= maximum_bytes:
        raise PlayerLoadError("Playable file size was outside the safe bound")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    consumed = 0
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not minimum_size <= before.st_size <= maximum_bytes
        ):
            raise PlayerLoadError("Playable file changed before hashing")
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise PlayerLoadError("Playable file exceeded its byte limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not _same_stat(before, after) or consumed != after.st_size:
        raise PlayerLoadError("Playable file changed during hashing")
    return consumed, digest.hexdigest()


def _decode_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PlayerLoadError(f"{label} was not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PlayerLoadError(f"{label} must be a JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PlayerLoadError("JSON object contained a duplicate key")
        value[key] = item
    return value


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_relative_posix(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_CHARS or "\\" in value:
        raise PlayerLoadError(f"{label} path was invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise PlayerLoadError(f"{label} path was not canonical and relative")
    return value


def _relative_to(path: Path, root: Path, *, label: str) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PlayerLoadError(f"{label} escaped the fixed project root") from exc
    value = relative.as_posix()
    if not value or value == ".":
        raise PlayerLoadError(f"{label} must be below the fixed project root")
    return _safe_relative_posix(value, label=label)


def _validate_raw_folder(value: str, project_root: Path) -> None:
    raw = Path(value).expanduser()
    if any(part == ".." for part in raw.parts):
        raise PlayerLoadError("Character config folder was not canonical")
    candidate = raw if raw.is_absolute() else project_root / raw
    candidate = candidate.absolute()
    _require_real_path(candidate, root=project_root, expect_directory=True)


def _require_real_path(path: Path, *, root: Path, expect_directory: bool) -> None:
    if not path.is_absolute():
        raise PlayerLoadError("Managed path must be absolute")
    if path != root:
        _relative_to(path, root, label="Managed path")
    relative = path.relative_to(root)
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        details = cursor.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise PlayerLoadError("Managed path contained a symlink")
    details = path.lstat()
    expected = stat.S_ISDIR(details.st_mode) if expect_directory else stat.S_ISREG(details.st_mode)
    if not expected:
        raise PlayerLoadError("Managed path had the wrong file type")
    if path.resolve(strict=True) != path:
        raise PlayerLoadError("Managed path was not canonical")


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise PlayerLoadError("Bridge output directory was unsafe")
    if path.exists() or path.is_symlink():
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise PlayerLoadError("Bridge output path was unsafe")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _ack_matches(path: Path, expected: dict[str, Any]) -> bool:
    try:
        raw = _read_regular_bytes(path, maximum_bytes=MAX_REQUEST_BYTES, missing_ok=True)
        return raw is not None and _decode_json_object(raw, label="Player-load acknowledgement") == expected
    except (OSError, PlayerLoadError, UnicodeError, ValueError):
        return False


def _bridge_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value).expanduser() if value is not None else default
    if path.is_symlink():
        raise PlayerLoadError("Player-load bridge path must not be a symlink")
    return path.absolute()


def _normalize_extensions(values: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        extension = str(value).strip().lower()
        if not extension:
            continue
        normalized.add(extension if extension.startswith(".") else f".{extension}")
    return normalized


def _bounded_nonempty_string(value: Any, *, maximum_chars: int, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum_chars or any(ord(char) < 32 for char in value):
        raise PlayerLoadError(f"{label} was invalid")
    return value


def _require_uuid(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 32 or value.lower() != value:
        raise PlayerLoadError("Activation id was not a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise PlayerLoadError("Activation id was not a UUID") from exc
    if parsed.int == 0 or value != parsed.hex:
        raise PlayerLoadError("Activation id was not canonical")
    return value


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise PlayerLoadError(f"{label} was not a canonical SHA256")
    return value


def _require_count(value: Any, *, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise PlayerLoadError(f"{label} was invalid")
    return value


def _same_stat(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_mode,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_mode,
        second.st_size,
        second.st_mtime_ns,
    )


def _file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
    )


def _empty_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "history": {},
        "active_activation_id": None,
        "active_fingerprint": None,
        "active_proof": None,
        "pending_activation_id": None,
        "pending_fingerprint": None,
        "rejected_activation_id": None,
        "rejected_fingerprint": None,
        "rejected_reason_code": None,
    }
