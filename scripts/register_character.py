#!/usr/bin/env python3
"""Register one NFC tag in config/characters.json."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from magic_box.config import (  # noqa: E402
    VALID_MODES,
    folder_for_config,
    load_raw_config,
    normalize_uid,
    unique_character_folder,
    write_raw_config,
)
from magic_box.nfc import NFCError, StopRequested, create_reader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a character NFC tag")
    parser.add_argument("--config", default=str(ROOT / "config" / "characters.json"))
    parser.add_argument("--nfc", default="pn532", help="Reader backend: pn532 or mock")
    parser.add_argument("--name", required=True)
    parser.add_argument("--folder", help="Optional audio folder override. Defaults to audio/<character-name>.")
    parser.add_argument("--mode", default="first", choices=sorted(VALID_MODES))
    parser.add_argument("--force", action="store_true", help="Overwrite an existing UID")
    parser.add_argument("--create-folder", action="store_true", help="Create a supplied --folder if it is missing.")
    parser.add_argument("--poll-interval", type=float, default=0.2)
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    project_root = config_path.parent.parent

    try:
        reader = create_reader(args.nfc)
    except NFCError as exc:
        print(exc, file=sys.stderr)
        return 2

    print("Place the character/tag on the reader.", file=sys.stderr)
    try:
        uid = _read_one_uid(reader, args.poll_interval)
    except (KeyboardInterrupt, StopRequested):
        return 130
    except NFCError as exc:
        print(exc, file=sys.stderr)
        return 2

    data = load_raw_config(config_path)
    normalized_existing = {normalize_uid(key): key for key in data}
    existing_key = normalized_existing.get(uid)
    if existing_key and not args.force:
        print(f"UID {uid} already exists as {existing_key}. Use --force to overwrite.", file=sys.stderr)
        return 2

    if args.folder:
        folder = Path(args.folder).expanduser()
        folder_abs = folder if folder.is_absolute() else project_root / folder
        if args.create_folder:
            folder_abs.mkdir(parents=True, exist_ok=True)
        elif not folder_abs.exists():
            print(f"Audio folder does not exist: {folder_abs}", file=sys.stderr)
            print("Omit --folder to create one automatically, or rerun with --create-folder.", file=sys.stderr)
            return 2
    else:
        folder_abs = unique_character_folder(project_root, args.name, data, uid)
        folder_abs.mkdir(parents=True, exist_ok=True)

    key = existing_key or uid
    data[key] = {
        "name": args.name,
        "folder": folder_for_config(folder_abs, project_root),
        "mode": args.mode,
    }
    write_raw_config(config_path, data)

    print(f"Registered {args.name} as {uid}")
    print(f"Audio folder: {folder_for_config(folder_abs, project_root)}")
    return 0


def _read_one_uid(reader: Any, poll_interval: float) -> str:
    while True:
        uid = reader.read_uid()
        if uid:
            return uid
        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
