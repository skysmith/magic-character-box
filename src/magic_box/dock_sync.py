"""Sync a hosted Story Dock manifest into the local Pi playback cache."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from .config import (
    folder_for_config,
    load_raw_config,
    normalize_uid,
    project_root_for_config,
    slugify_name,
    write_raw_config,
)


FetchJson = Callable[[str, str], dict[str, Any]]
FetchBytes = Callable[[str, str], bytes]


class DockSyncError(RuntimeError):
    """Raised when a hosted manifest cannot be synced."""


@dataclass
class DockSyncResult:
    stories_seen: int = 0
    stories_synced: int = 0
    uids_mapped: list[str] = field(default_factory=list)
    files_downloaded: list[Path] = field(default_factory=list)
    files_skipped: list[Path] = field(default_factory=list)
    skipped_stories: list[str] = field(default_factory=list)


class HostedDockSync:
    def __init__(
        self,
        *,
        manifest_url: str,
        dock_secret: str,
        config_path: str | Path,
        audio_root: str | Path | None = None,
        fetch_json: FetchJson | None = None,
        fetch_bytes: FetchBytes | None = None,
    ) -> None:
        self.manifest_url = manifest_url
        self.dock_secret = dock_secret
        self.config_path = Path(config_path).expanduser().resolve()
        project_root = project_root_for_config(self.config_path)
        self.audio_root = Path(audio_root).expanduser().resolve() if audio_root else project_root / "audio" / "hosted"
        self.fetch_json = fetch_json or _fetch_json
        self.fetch_bytes = fetch_bytes or _fetch_bytes

    def sync(self) -> DockSyncResult:
        manifest = self.fetch_json(self.manifest_url, self.dock_secret)
        if not manifest.get("ok", False):
            raise DockSyncError("Hosted manifest did not return ok=true.")
        if manifest.get("schema") != "story-dock-hosted-manifest-v1":
            raise DockSyncError(f"Unsupported hosted manifest schema: {manifest.get('schema')!r}")

        result = DockSyncResult()
        config = load_raw_config(self.config_path)
        project_root = project_root_for_config(self.config_path)
        synced_at = datetime.now().astimezone().isoformat()

        for story in _story_items(manifest):
            result.stories_seen += 1
            story_id = str(story.get("id") or "")
            story_name = str(story.get("name") or "Untitled memory")
            uids = _story_uids(story)
            recordings = _recording_items(story)
            if not uids:
                result.skipped_stories.append(f"{story_name}: no NFC UID")
                continue
            if not recordings:
                result.skipped_stories.append(f"{story_name}: no playable recordings")
                continue

            folder = self.audio_root / _folder_name(story_name, story_id)
            folder.mkdir(parents=True, exist_ok=True)
            for recording in recordings:
                filename = _safe_filename(str(recording.get("filename") or recording.get("id") or "recording.mp3"))
                target = folder / filename
                url = str(recording.get("download_url") or recording.get("url") or "")
                if not url:
                    result.skipped_stories.append(f"{story_name}: recording {filename} has no download URL")
                    continue
                data = self.fetch_bytes(urllib.parse.urljoin(self.manifest_url, url), self.dock_secret)
                if target.exists() and target.stat().st_size == len(data):
                    result.files_skipped.append(target)
                    continue
                target.write_bytes(data)
                result.files_downloaded.append(target)

            if not any(folder.iterdir()):
                result.skipped_stories.append(f"{story_name}: no files cached")
                continue

            folder_value = folder_for_config(folder, project_root)
            for uid in uids:
                existing = config.get(uid)
                entry = existing.copy() if isinstance(existing, dict) else {}
                entry.update(
                    {
                        "name": story_name,
                        "folder": folder_value,
                        "mode": "first",
                        "kind": "photo_story",
                        "source": "hosted",
                        "hosted_story_id": story_id,
                        "hosted_synced_at": synced_at,
                    }
                )
                config[uid] = entry
                result.uids_mapped.append(uid)
            result.stories_synced += 1

        write_raw_config(self.config_path, config)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync a hosted Story Dock manifest into local playback files.")
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--dock-secret", required=True)
    parser.add_argument("--config", default="config/characters.json")
    parser.add_argument("--audio-root", default="")
    args = parser.parse_args(argv)

    sync = HostedDockSync(
        manifest_url=args.manifest_url,
        dock_secret=args.dock_secret,
        config_path=args.config,
        audio_root=args.audio_root or None,
    )
    result = sync.sync()
    print(
        "Synced "
        f"{result.stories_synced}/{result.stories_seen} stories, "
        f"mapped {len(result.uids_mapped)} UIDs, "
        f"downloaded {len(result.files_downloaded)} files."
    )
    return 0


def _fetch_json(url: str, bearer_token: str) -> dict[str, Any]:
    data = _request(url, bearer_token)
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise DockSyncError("Hosted manifest response was not a JSON object.")
    return payload


def _fetch_bytes(url: str, bearer_token: str) -> bytes:
    return _request(url, bearer_token)


def _request(url: str, bearer_token: str) -> bytes:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer_token}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _story_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    stories = manifest.get("stories")
    return [story for story in stories if isinstance(story, dict)] if isinstance(stories, list) else []


def _story_uids(story: dict[str, Any]) -> list[str]:
    values: list[str] = []
    raw_uids = story.get("uids")
    if isinstance(raw_uids, list):
        values.extend(str(uid) for uid in raw_uids if str(uid).strip())
    stickers = story.get("story_stickers")
    if isinstance(stickers, list):
        for sticker in stickers:
            if isinstance(sticker, dict) and str(sticker.get("uid") or "").strip():
                values.append(str(sticker["uid"]))
    normalized: list[str] = []
    for value in values:
        try:
            uid = normalize_uid(value)
        except ValueError:
            continue
        if uid not in normalized:
            normalized.append(uid)
    return normalized


def _recording_items(story: dict[str, Any]) -> list[dict[str, Any]]:
    recordings = story.get("recordings")
    if not isinstance(recordings, list):
        return []
    return [recording for recording in recordings if isinstance(recording, dict) and not recording.get("deleted")]


def _folder_name(story_name: str, story_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", "", story_id)[:8].lower()
    return f"{slugify_name(story_name)}-{suffix}" if suffix else slugify_name(story_name)


def _safe_filename(value: str) -> str:
    name = Path(value or "recording.mp3").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name or "recording.mp3"


if __name__ == "__main__":
    raise SystemExit(main())
