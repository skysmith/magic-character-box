"""Prepare audio files for cleaner playback on small I2S amps."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import subprocess


LOGGER = logging.getLogger(__name__)
AUDIO_PREP_FILTER = "loudnorm=I=-18:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.08,areverse,afade=t=in:st=0:d=0.05,areverse"


def prepare_playable_mp3(source_path: Path, target_path: Path) -> bool:
    """Convert/re-encode to a mono MP3 with steady loudness and short edge fades."""

    if shutil.which("ffmpeg") is None:
        return False

    source_path = source_path.resolve()
    target_path = target_path.resolve()
    output_path = target_path
    replace_original = source_path == target_path
    if replace_original:
        output_path = _unique_path(target_path.with_name(f"{target_path.stem}.prepared{target_path.suffix}"))

    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-af",
        AUDIO_PREP_FILTER,
        "-ar",
        "44100",
        "-ac",
        "1",
        "-b:a",
        "128k",
        str(output_path),
    ]
    try:
        completed = subprocess.run(args, check=False, capture_output=True)
    except OSError as exc:
        LOGGER.warning("Could not run ffmpeg: %s", exc)
        return False

    if completed.returncode != 0:
        LOGGER.warning("ffmpeg conversion failed: %s", completed.stderr.decode(errors="replace"))
        try:
            if output_path.exists():
                output_path.unlink()
        except OSError:
            pass
        return False

    if replace_original:
        output_path.replace(target_path)
    return True


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find an available filename near {path}")
