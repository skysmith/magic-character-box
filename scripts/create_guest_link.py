#!/usr/bin/env python3
"""Create an upload-only guest recording link for a character."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from magic_box.config import load_raw_config, normalize_uid  # noqa: E402
from magic_box.guest_links import create_guest_link, guest_links_file_for_config  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a guest recording link")
    parser.add_argument("--config", default="config/characters.json", help="Path to characters.json")
    parser.add_argument("--guest-links-file", help="Path to guest_links.json")
    parser.add_argument("--base-url", default="", help="Optional admin origin, such as https://example.trycloudflare.com")
    parser.add_argument("--label", default="", help="Human label for the link")
    parser.add_argument("--days", type=int, default=14, help="Days before the link expires")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--uid", help="Character UID to receive the recording")
    target.add_argument("--name", help="Character name to receive the recording")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config).expanduser().resolve()
    guest_links_path = (
        Path(args.guest_links_file).expanduser().resolve()
        if args.guest_links_file
        else guest_links_file_for_config(config_path)
    )

    data = load_raw_config(config_path)
    uid = _resolve_uid(data, uid=args.uid, name=args.name)
    if uid is None:
        print("Could not find that character in the config.", file=sys.stderr)
        return 2

    link = create_guest_link(
        guest_links_path,
        uid=uid,
        label=args.label or f"{_character_name(data, uid)} guest recorder",
        expires_days=args.days,
        base_url=args.base_url,
    )
    path = f"/guest/{link.token}"
    print("Guest recording link created")
    print(f"Character: {_character_name(data, uid)} ({uid})")
    print(f"Expires: {link.expires_at.isoformat() if link.expires_at else 'never'}")
    if link.base_url:
        print(f"URL: {link.base_url}{path}")
    else:
        print(f"Path: {path}")
        print("Tip: pass --base-url with your HTTPS tunnel URL to print a sendable link.")
    return 0


def _resolve_uid(data: dict[str, object], uid: str | None, name: str | None) -> str | None:
    if uid:
        normalized = normalize_uid(uid)
        return normalized if any(normalize_uid(str(key)) == normalized for key in data) else None

    wanted = (name or "").strip().casefold()
    for raw_uid, raw_character in data.items():
        if not isinstance(raw_character, dict):
            continue
        character_name = str(raw_character.get("name", "")).strip().casefold()
        if character_name == wanted:
            return normalize_uid(str(raw_uid))
    return None


def _character_name(data: dict[str, object], uid: str) -> str:
    normalized = normalize_uid(uid)
    for raw_uid, raw_character in data.items():
        if normalize_uid(str(raw_uid)) != normalized or not isinstance(raw_character, dict):
            continue
        return str(raw_character.get("name", normalized))
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
