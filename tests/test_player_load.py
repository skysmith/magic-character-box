from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from magic_box.config import CharacterConfig, ConfigError
from magic_box.player_load import (
    ACK_PROOF_FIELDS,
    ACK_FILENAME,
    ACK_SCHEMA,
    MAX_REQUEST_BYTES,
    PlayerLoadBridge,
    PlayerLoadError,
    REQUEST_FILENAME,
    REQUEST_FIELDS,
    REQUEST_SCHEMA,
    REJECTION_REASON_CODES,
    build_selected_inventory,
    canonical_config_sha256,
)


class PlayerLoadBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config_path = self.root / "config" / "characters.json"
        self.config_path.parent.mkdir()
        self.current_folder = self.root / "audio" / "current"
        self.current_folder.mkdir(parents=True)
        (self.current_folder / "current.mp3").write_bytes(b"current audio")
        self.current_raw = {
            "04-A1": {"name": "Current", "folder": "audio/current", "mode": "first"}
        }
        _write_json(self.config_path, self.current_raw)
        self.current = CharacterConfig.load(self.config_path)
        self.request_path = self.config_path.parent / REQUEST_FILENAME
        self.ack_path = self.config_path.parent / ACK_FILENAME

    def test_request_first_waits_then_swaps_and_acks_exact_loaded_proof(self) -> None:
        candidate_raw, request = self._manifest_candidate("a" * 64)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
        self.assertFalse(self.ack_path.exists())

        _write_json(self.config_path, candidate_raw)
        self.assertTrue(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-B2").name, "Candidate")
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["schema"], ACK_SCHEMA)
        for field, value in request.items():
            if field != "schema":
                self.assertEqual(ack[field], value)
        self.assertEqual(ack["status"], "loaded")
        self.assertEqual(ack["player_instance_id"], bridge.player_instance_id)
        self.assertEqual(ack["player_pid"], os.getpid())
        self.assertEqual(ack["load_sequence"], 1)
        self.assertRegex(ack["loaded_at"], r"Z$")

    def test_startup_pending_request_is_parsed_and_acknowledged_by_new_process(self) -> None:
        candidate_raw, request = self._manifest_candidate("b" * 64)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        bridge = PlayerLoadBridge(CharacterConfig.load(self.config_path))

        self.assertFalse(bridge.poll())
        self.assertEqual(json.loads(self.ack_path.read_text())["player_instance_id"], bridge.player_instance_id)

    def test_first_transactional_bootstrap_includes_zero_byte_maker_placeholders(self) -> None:
        (self.current_folder / "placeholder.mp3").write_bytes(b"")

        bridge = PlayerLoadBridge(CharacterConfig.load(self.config_path))

        state = json.loads(bridge.state_path.read_text())
        self.assertEqual(state["active_proof"]["selected_file_count"], 2)
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")

    def test_same_active_request_is_idempotent_and_repairs_missing_ack(self) -> None:
        candidate_raw, request = self._manifest_candidate("c" * 64)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)
        self.assertTrue(bridge.poll())
        first_ack = self.ack_path.read_bytes()

        self.assertFalse(bridge.poll())
        self.assertEqual(self.ack_path.read_bytes(), first_ack)
        self.ack_path.unlink()
        self.assertFalse(bridge.poll())
        self.assertEqual(self.ack_path.read_bytes(), first_ack)

    def test_loaded_ack_is_published_only_after_active_startup_proof_is_durable(self) -> None:
        candidate_raw, request = self._manifest_candidate("c1" * 32)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)
        from magic_box import player_load as player_load_module

        original_write = player_load_module._atomic_write_json
        observed_active_proof: list[dict[str, object]] = []

        def inspect_before_write(path: Path, value: dict[str, object]) -> None:
            if path == self.ack_path:
                state = json.loads(bridge.state_path.read_text())
                observed_active_proof.append(state["active_proof"])
            original_write(path, value)

        with patch("magic_box.player_load._atomic_write_json", side_effect=inspect_before_write):
            self.assertTrue(bridge.poll())

        self.assertEqual(observed_active_proof[0]["config_sha256"], request["config_sha256"])

    def test_activation_id_reuse_with_different_proof_is_rejected(self) -> None:
        candidate_raw, request = self._manifest_candidate("d" * 64)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)
        self.assertTrue(bridge.poll())
        first_ack = self.ack_path.read_bytes()

        request["target_label"] = "e" * 64
        request["manifest_revision"] = "e" * 64
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(self.ack_path.read_bytes(), first_ack)

    def test_completed_activation_cannot_be_replayed_after_a_later_load(self) -> None:
        first_raw, first_request = self._manifest_candidate("1" * 64, uid="04-B2", label="First")
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, first_raw)
        _write_json(self.request_path, first_request)
        self.assertTrue(bridge.poll())

        second_raw, second_request = self._manifest_candidate("2" * 64, uid="04-C3", label="Second")
        _write_json(self.config_path, second_raw)
        _write_json(self.request_path, second_request)
        self.assertTrue(bridge.poll())
        second_ack = self.ack_path.read_bytes()

        _write_json(self.config_path, first_raw)
        _write_json(self.request_path, first_request)
        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-C3").name, "Second")
        self.assertEqual(self.ack_path.read_bytes(), second_ack)

    def test_request_fingerprint_mismatch_stays_pending_and_preserves_current(self) -> None:
        _candidate_raw, request = self._manifest_candidate("f" * 64)
        _write_json(self.request_path, request)
        bridge = PlayerLoadBridge(self.current)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
        self.assertFalse(self.ack_path.exists())

    def test_exact_candidate_parse_failure_preserves_prior_mapping(self) -> None:
        broken = {
            "04-B2": {"name": "Broken", "folder": "audio/missing", "mode": "first"}
        }
        request = self._legacy_request(broken)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, broken)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["reason_code"], "candidate-config-invalid")

    def test_transactional_bridge_ignores_unrequested_config_changes(self) -> None:
        candidate_raw, request = self._manifest_candidate("3" * 64)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)
        self.assertTrue(bridge.poll())

        unrequested = {
            "04-D4": {"name": "Unrequested", "folder": "audio/current", "mode": "first"}
        }
        _write_json(self.config_path, unrequested)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-B2").name, "Candidate")
        self.assertIsNone(bridge.config.lookup("04-D4"))

    def test_restart_rejects_unrequested_disk_config_before_serving(self) -> None:
        bridge = PlayerLoadBridge(self.current)
        unrequested = {
            "04-D4": {"name": "Unrequested", "folder": "audio/current", "mode": "first"}
        }
        _write_json(self.config_path, unrequested)

        with self.assertRaisesRegex(PlayerLoadError, "last confirmed activation"):
            PlayerLoadBridge(CharacterConfig.load(self.config_path))

        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")

    def test_restart_can_finish_valid_request_first_activation(self) -> None:
        first_process = PlayerLoadBridge(self.current)
        candidate_raw, request = self._manifest_candidate("6" * 64)
        _write_json(self.request_path, request)
        self.assertFalse(first_process.poll())
        _write_json(self.config_path, candidate_raw)

        second_process = PlayerLoadBridge(CharacterConfig.load(self.config_path))

        self.assertEqual(second_process.config.lookup("04-B2").name, "Candidate")
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["status"], "loaded")
        self.assertEqual(ack["player_instance_id"], second_process.player_instance_id)

    def test_legacy_rollback_uses_nullable_generation_fields(self) -> None:
        rollback_raw = {
            "04-A1": {"name": "Rollback", "folder": "audio/current", "mode": "sequence"}
        }
        request = self._legacy_request(rollback_raw)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, rollback_raw)
        _write_json(self.request_path, request)

        self.assertTrue(bridge.poll())
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["operation"], "rollback")
        self.assertEqual(ack["target_kind"], "legacy-config")
        self.assertIsNone(ack["manifest_revision"])
        self.assertIsNone(ack["generation_root"])

    def test_generation_metadata_or_file_mismatch_prevents_ack(self) -> None:
        candidate_raw, request = self._manifest_candidate("4" * 64)
        generation = Path(request["generation_root"])
        (generation / "story" / "memo.mp3").write_bytes(b"changed after metadata")
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["reason_code"], "selected-inventory-invalid")

    def test_manifest_activation_preserves_proven_local_bindings_outside_generation(self) -> None:
        candidate_raw, request = self._manifest_candidate("9" * 64)
        candidate_raw.update(self.current_raw)
        candidate = CharacterConfig.from_mapping(self.config_path, candidate_raw)
        selected = build_selected_inventory(candidate, project_root=self.root)
        request.update(
            {
                "config_sha256": canonical_config_sha256(candidate_raw),
                "selected_inventory_sha256": selected.sha256,
                "selected_binding_count": selected.binding_count,
                "selected_folder_count": selected.folder_count,
                "selected_file_count": selected.file_count,
            }
        )
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.request_path, request)
        self.assertFalse(bridge.poll())
        _write_json(self.config_path, candidate_raw)

        self.assertTrue(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
        self.assertEqual(bridge.config.lookup("04-B2").name, "Candidate")
        self.assertEqual(json.loads(self.ack_path.read_text())["status"], "loaded")

    def test_extra_unselected_generation_file_is_rejected_as_generation_invalid(self) -> None:
        candidate_raw, request = self._manifest_candidate("0" * 64)
        generation = Path(request["generation_root"])
        (generation / "extra.mp3").write_bytes(b"undeclared")
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["reason_code"], "generation-invalid")
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")

    def test_zero_byte_managed_generation_file_is_rejected(self) -> None:
        candidate_raw, request = self._manifest_candidate("01" * 32)
        generation = Path(request["generation_root"])
        empty = generation / "story" / "empty.mp3"
        empty.write_bytes(b"")
        metadata_path = generation / ".story-dock-generation.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["file_inventory"].append(
            {
                "path": empty.relative_to(generation).as_posix(),
                "byte_count": 0,
                "sha256": hashlib.sha256(b"").hexdigest(),
                "codec_name": "mp3",
            }
        )
        _write_json(metadata_path, metadata)
        candidate = CharacterConfig.from_mapping(self.config_path, candidate_raw)
        selected = build_selected_inventory(candidate, project_root=self.root)
        request.update(
            {
                "generation_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                "selected_inventory_sha256": selected.sha256,
                "selected_binding_count": selected.binding_count,
                "selected_folder_count": selected.folder_count,
                "selected_file_count": selected.file_count,
            }
        )
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        ack = json.loads(self.ack_path.read_text())
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["reason_code"], "generation-invalid")
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")

    def test_generation_total_byte_bound_counts_each_file_once_per_validation_pass(self) -> None:
        candidate_raw, request = self._manifest_candidate("a1" * 32)
        playable = Path(request["generation_root"]) / "story" / "memo.mp3"
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        with patch("magic_box.player_load.MAX_TOTAL_AUDIO_BYTES", playable.stat().st_size + 1):
            self.assertTrue(bridge.poll())

        self.assertEqual(json.loads(self.ack_path.read_text())["status"], "loaded")

    def test_identical_exact_rejection_is_cached_and_idempotent(self) -> None:
        candidate_raw, request = self._manifest_candidate("7" * 64)
        request["selected_inventory_sha256"] = "0" * 64
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        with patch(
            "magic_box.player_load.build_selected_inventory",
            wraps=build_selected_inventory,
        ) as inventory_builder:
            self.assertFalse(bridge.poll())
            first_ack = self.ack_path.read_bytes()
            self.assertFalse(bridge.poll())
            self.assertEqual(inventory_builder.call_count, 1)
            self.assertEqual(self.ack_path.read_bytes(), first_ack)

        ack = json.loads(first_ack)
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["reason_code"], "selected-inventory-invalid")

    def test_identical_request_first_mismatch_is_not_reparsed_until_config_changes(self) -> None:
        _candidate_raw, request = self._manifest_candidate("8" * 64)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.request_path, request)

        with patch.object(bridge, "_read_candidate_raw", wraps=bridge._read_candidate_raw) as reader:
            self.assertFalse(bridge.poll())
            self.assertFalse(bridge.poll())
            self.assertEqual(reader.call_count, 1)

    def test_selected_folder_symlink_is_rejected(self) -> None:
        real_folder = self.root / "audio" / "real"
        real_folder.mkdir()
        (real_folder / "memo.mp3").write_bytes(b"memo")
        link = self.root / "audio" / "linked"
        link.symlink_to(real_folder, target_is_directory=True)
        raw = {"04-B2": {"name": "Linked", "folder": "audio/linked", "mode": "first"}}
        request = self._legacy_request(raw, inventory_raw={})
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, raw)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")

    def test_symlink_request_is_rejected_without_touching_target(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("{}")
        bridge = PlayerLoadBridge(self.current)
        self.request_path.symlink_to(outside)

        self.assertFalse(bridge.poll())
        self.assertEqual(outside.read_text(), "{}")
        self.assertFalse(self.ack_path.exists())

    def test_symlink_ack_fails_closed_and_is_not_replaced(self) -> None:
        candidate_raw, request = self._manifest_candidate("5" * 64)
        outside = self.root / "outside-ack.json"
        outside.write_text("untouched")
        bridge = PlayerLoadBridge(self.current)
        self.ack_path.symlink_to(outside)
        _write_json(self.config_path, candidate_raw)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
        self.assertEqual(outside.read_text(), "untouched")
        state = json.loads(bridge.state_path.read_text())
        self.assertEqual(state["active_proof"]["target_kind"], "legacy-config")
        self.assertEqual(state["pending_activation_id"], request["activation_id"])

    def test_malformed_partial_and_oversized_requests_fail_closed(self) -> None:
        bridge = PlayerLoadBridge(self.current)
        for payload in (b'{"schema":', b"[]", b"x" * (MAX_REQUEST_BYTES + 1)):
            self.request_path.write_bytes(payload)
            self.assertFalse(bridge.poll())
            self.assertEqual(bridge.config.lookup("04-A1").name, "Current")
            self.assertFalse(self.ack_path.exists())

    def test_duplicate_json_request_key_is_rejected(self) -> None:
        self.request_path.write_text('{"schema":"one","schema":"two"}')
        bridge = PlayerLoadBridge(self.current)

        self.assertFalse(bridge.poll())
        self.assertFalse(self.ack_path.exists())

    def test_duplicate_normalized_uids_are_rejected(self) -> None:
        raw = {
            "04:A1": {"name": "One", "folder": "audio/current", "mode": "first"},
            "04-A1": {"name": "Two", "folder": "audio/current", "mode": "first"},
        }
        request = self._legacy_request(raw)
        bridge = PlayerLoadBridge(self.current)
        _write_json(self.config_path, raw)
        _write_json(self.request_path, request)

        self.assertFalse(bridge.poll())
        self.assertEqual(bridge.config.lookup("04-A1").name, "Current")

    def test_invalid_persisted_state_stops_transactional_startup(self) -> None:
        state = self.config_path.parent / ".magic-character-box-player-load-state.json"
        state.write_text("{partial")

        with self.assertRaises(PlayerLoadError):
            PlayerLoadBridge(self.current)

    def test_machine_readable_v2_contract_matches_implementation(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "player-load-v2-contract.json"
        fixture = json.loads(fixture_path.read_text())

        self.assertEqual(fixture["request_schema"], REQUEST_SCHEMA)
        self.assertEqual(fixture["ack_schema"], ACK_SCHEMA)
        self.assertEqual(fixture["request_fields"], sorted(REQUEST_FIELDS))
        self.assertEqual(fixture["ack_proof_fields"], list(ACK_PROOF_FIELDS))
        self.assertEqual(fixture["rejection_reason_codes"], sorted(REJECTION_REASON_CODES))

    def _manifest_candidate(
        self,
        revision: str,
        *,
        uid: str = "04-B2",
        label: str = "Candidate",
    ) -> tuple[dict[str, object], dict[str, object]]:
        generation = self.root / "audio" / "hosted" / "generations" / revision
        story_folder = generation / "story"
        story_folder.mkdir(parents=True, exist_ok=True)
        playable = story_folder / "memo.mp3"
        playable.write_bytes(f"audio:{revision}:{uid}".encode())
        relative_playable = playable.relative_to(generation).as_posix()
        metadata = {
            "schema": "test-generation-v1",
            "manifest_revision": revision,
            "file_inventory": [
                {
                    "path": relative_playable,
                    "byte_count": playable.stat().st_size,
                    "sha256": hashlib.sha256(playable.read_bytes()).hexdigest(),
                    "codec_name": "mp3",
                }
            ],
        }
        metadata_path = generation / ".story-dock-generation.json"
        _write_json(metadata_path, metadata)
        raw = {
            uid: {
                "name": label,
                "folder": story_folder.relative_to(self.root).as_posix(),
                "mode": "first",
            }
        }
        candidate = CharacterConfig.from_mapping(self.config_path, raw)
        selected = build_selected_inventory(candidate, project_root=self.root)
        request = {
            "schema": REQUEST_SCHEMA,
            "activation_id": uuid.uuid4().hex,
            "operation": "activate",
            "target_kind": "manifest-revision",
            "target_label": revision,
            "manifest_revision": revision,
            "project_root": str(self.root),
            "config_path": str(self.config_path),
            "config_sha256": canonical_config_sha256(raw),
            "generation_root": str(generation),
            "generation_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            "selected_inventory_sha256": selected.sha256,
            "selected_binding_count": selected.binding_count,
            "selected_folder_count": selected.folder_count,
            "selected_file_count": selected.file_count,
        }
        return raw, request

    def _legacy_request(
        self,
        raw: dict[str, object],
        *,
        inventory_raw: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if inventory_raw is None:
            try:
                candidate = CharacterConfig.from_mapping(self.config_path, raw)
                selected = build_selected_inventory(candidate, project_root=self.root)
                inventory_sha = selected.sha256
                binding_count = selected.binding_count
                folder_count = selected.folder_count
                file_count = selected.file_count
            except (ConfigError, PlayerLoadError, OSError):
                inventory_sha = "0" * 64
                binding_count = folder_count = file_count = 0
        else:
            inventory_sha = "0" * 64
            binding_count = folder_count = file_count = 0
        config_sha = canonical_config_sha256(raw)
        return {
            "schema": REQUEST_SCHEMA,
            "activation_id": uuid.uuid4().hex,
            "operation": "rollback",
            "target_kind": "legacy-config",
            "target_label": f"legacy:{config_sha}",
            "manifest_revision": None,
            "project_root": str(self.root),
            "config_path": str(self.config_path),
            "config_sha256": config_sha,
            "generation_root": None,
            "generation_metadata_sha256": None,
            "selected_inventory_sha256": inventory_sha,
            "selected_binding_count": binding_count,
            "selected_folder_count": folder_count,
            "selected_file_count": file_count,
        }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    unittest.main()
