#!/usr/bin/env python3
"""Print one NFC tag config lookup key."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from magic_box.nfc import NFCError, StopRequested, create_reader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan one NFC tag and print its config lookup key")
    parser.add_argument(
        "--nfc",
        default="pn532",
        help="Reader backend: pn532, pn532-ndef, or mock",
    )
    parser.add_argument("--poll-interval", type=float, default=0.2)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    try:
        reader = create_reader(args.nfc)
    except NFCError as exc:
        print(exc, file=sys.stderr)
        return 2

    print("Waiting for tag. Press Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            uid = reader.read_uid()
            if uid:
                print(uid)
                return 0
            time.sleep(args.poll_interval)
    except (KeyboardInterrupt, StopRequested):
        return 130
    except NFCError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
