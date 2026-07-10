# Transactional Player-Load Bridge

The player has an optional local request/ack bridge for installers that prepare
complete audio generations outside the player process. It is generic local
filesystem coordination: it does not fetch manifests, know a hosted service,
or contain URLs or credentials.

Maker mode is unchanged. The bridge is enabled only with:

```bash
python -m magic_box.app --transactional-config
```

or `MAGIC_BOX_TRANSACTIONAL_CONFIG=1`.

## Safety model

On the first opt-in start, the player validates the current config, its real
folders, and its selected MP3 files, then durably records that mapping as the
one-time legacy bootstrap. Later starts must prove that the on-disk config still
matches the last confirmed config and inventory. An unrequested or partially
switched config stops transactional startup before the startup sound, NFC
reader, or audio player can serve it.

While enabled:

- ordinary `characters.json` mtime changes are not loaded;
- a request may arrive before its config switch, and a config-SHA mismatch
  remains pending without disturbing the current in-memory mapping;
- the player acknowledges only after this process parses, verifies, and swaps
  the exact candidate in memory;
- an exact candidate with invalid config, selected inventory, or generation
  evidence is rejected once with a fixed reason code, then handled
  idempotently without repeatedly hashing audio;
- malformed, oversized, duplicate-key, path-escaping, symlinked, special, or
  unstable inputs fail closed;
- activation UUID reuse with different proof fields is rejected durably;
- acknowledgement and bridge state writes use fsync plus atomic replacement.

The bridge files live beside `characters.json`:

```text
story-dock-player-load-request.json
story-dock-player-load-ack.json
.magic-character-box-player-load-state.json
```

The state file is player-owned. An installer must not edit or remove it during
normal operation. Back it up together with the config before first enabling
transactional mode.

## V2 request

The request schema is `story-dock-player-load-request-v2`. It contains exactly:

```json
{
  "schema": "story-dock-player-load-request-v2",
  "activation_id": "<fresh lowercase UUID hex>",
  "operation": "activate",
  "target_kind": "manifest-revision",
  "target_label": "<opaque exact revision>",
  "manifest_revision": "<same opaque exact revision>",
  "project_root": "/canonical/project/root",
  "config_path": "/canonical/project/root/config/characters.json",
  "config_sha256": "<canonical parsed-config SHA256>",
  "generation_root": "/canonical/project/root/audio/generations/<revision>",
  "generation_metadata_sha256": "<exact metadata-file SHA256>",
  "selected_inventory_sha256": "<canonical selected-binding inventory SHA256>",
  "selected_binding_count": 1,
  "selected_folder_count": 1,
  "selected_file_count": 1
}
```

Rollback uses `operation: "rollback"`. A versioned rollback keeps
`target_kind: "manifest-revision"` and supplies the previous generation proof.
A one-time legacy rollback uses `target_kind: "legacy-config"`, an opaque
`target_label` such as `legacy:<config-sha>`, and null values for
`manifest_revision`, `generation_root`, and `generation_metadata_sha256`.

All paths are exact canonical absolute real paths. For a manifest target,
`target_label` and `manifest_revision` must match exactly. The player makes no
assumption about the revision's length or naming scheme.

## Canonical fingerprints

Canonical JSON uses UTF-8 with sorted object keys, `ensure_ascii=True`, and
compact `,` / `:` separators.

`config_sha256` hashes the parsed config object serialized that way. Duplicate
JSON keys and duplicate normalized NFC UIDs are rejected.

The selected inventory is a JSON list sorted by normalized UID. Each item is:

```json
{
  "uid": "04-A1",
  "mode": "first",
  "folder": "audio/example",
  "files": [
    {
      "path": "audio/example/memo.mp3",
      "byte_count": 123,
      "sha256": "<exact regular-file SHA256>"
    }
  ]
}
```

Folders and files are relative POSIX paths beneath the fixed real project root.
Files are the sorted immediate regular `.mp3` entries the current player can
actually select. The three counts are the number of bindings, unique folders,
and unique playable paths. The SHA is over the canonical list.
Zero-byte files are included in this whole-config proof so existing maker
placeholder files do not prevent the one-time legacy bootstrap.

For a generation target, the player also hashes the exact
`.story-dock-generation.json` bytes and independently walks the full generation
tree. Its regular-file paths, byte counts, and SHA256 values must exactly match
the metadata `file_inventory`; links, special files, extra files, and missing
files reject the activation. Unlike legacy placeholders, every managed
generation file must be non-empty. Every selected playable inside the managed
generation must belong to that exact inventory.

## V2 acknowledgement

A loaded ack uses `story-dock-player-load-ack-v2`, repeats every request proof
field exactly, sets `status: "loaded"`, and adds:

- `player_instance_id`: fresh lowercase UUID hex for this process;
- `player_pid`: the current positive process ID;
- `load_sequence`: a positive per-process load sequence;
- `loaded_at`: UTC timestamp ending in `Z`.

A valid request whose exact candidate fails permanent validation gets
`status: "rejected"`, the same proof/process fields, a nonnegative load
sequence, and one fixed `reason_code`:

- `candidate-config-invalid`
- `selected-inventory-invalid`
- `generation-invalid`

The machine-readable parity fixture is
[`tests/fixtures/player-load-v2-contract.json`](../tests/fixtures/player-load-v2-contract.json).

## Rollout

Do not enable this flag merely to get ordinary maker hot reload. It is intended
for a coordinated installer/client that implements the exact V2 contract.
Before enabling it on a physical box, back up the runtime, test request-first
activation and rollback, then verify offline playback and a cold restart. The
systemd service remains unmodified and maker installs stay opt-out by default.
