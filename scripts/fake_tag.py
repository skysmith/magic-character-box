#!/usr/bin/env python3
"""Queue a fake NFC UID for the file-backed development reader."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from magic_box.config import ConfigError  # noqa: E402
from magic_box.fake_tags import DEFAULT_TRIGGER_FILE, queue_fake_tag  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue a fake NFC tag UID for dev testing")
    parser.add_argument("uid", help="Fake UID, such as DINOSAUR, ROCKET, DAD, or 04-A1-22-9B")
    parser.add_argument(
        "--file",
        default=None,
        help="Trigger queue file used by MAGIC_BOX_NFC=file",
    )
    args = parser.parse_args()

    try:
        uid, path = queue_fake_tag(args.uid, args.file)
    except (ConfigError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"Queued fake tag {uid} in {path}")
    if args.file is None:
        print(f"Set MAGIC_BOX_TRIGGER_FILE to override the default {DEFAULT_TRIGGER_FILE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
