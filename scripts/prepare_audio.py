#!/usr/bin/env python3
"""Re-encode existing MP3 files with short fades to reduce clip pops."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import time

from magic_box.audio_prep import prepare_playable_mp3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare existing MP3 files for cleaner playback")
    parser.add_argument("paths", nargs="*", default=["audio"], help="Files or folders to prepare")
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Backup directory. Defaults to audio/.anti-pop-backups/<timestamp>.",
    )
    parser.add_argument("--no-backup", action="store_true", help="Rewrite files without making backups")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    files = _collect_mp3s([Path(path) for path in args.paths])
    if not files:
        print("No MP3 files found.")
        return 0

    backup_root: Path | None = None
    if not args.no_backup:
        backup_root = Path(args.backup_dir) if args.backup_dir else Path("audio") / ".anti-pop-backups" / time.strftime("%Y%m%d-%H%M%S")
        backup_root.mkdir(parents=True, exist_ok=True)

    failed = 0
    for file_path in files:
        if backup_root is not None:
            if file_path.is_absolute():
                try:
                    backup_name = file_path.relative_to(Path.cwd())
                except ValueError:
                    backup_name = Path(file_path.name)
            else:
                backup_name = file_path
            backup_path = backup_root / backup_name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, backup_path)

        if prepare_playable_mp3(file_path, file_path):
            print(f"prepared {file_path}")
        else:
            failed += 1
            print(f"failed {file_path}", file=sys.stderr)

    return 1 if failed else 0


def _collect_mp3s(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                item
                for item in sorted(path.rglob("*.mp3"))
                if item.is_file() and ".anti-pop-backups" not in item.parts
            )
        elif path.is_file() and path.suffix.lower() == ".mp3":
            files.append(path)
    return files


if __name__ == "__main__":
    raise SystemExit(main())
