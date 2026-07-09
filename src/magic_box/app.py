"""Application loop for the Magic Character Box."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import time
from typing import Sequence

from .amp import create_amp_gate
from .audio import AudioPlayer
from .config import CharacterConfig, ConfigError, project_root_for_config
from .control import consume_stop_request, control_file_for_config
from .nfc import NFCError, StopRequested, create_reader
from .runtime_state import append_event, record_tag, state_file_for_config
from .volume import (
    DEFAULT_MAX_OUTPUT_VOLUME_PERCENT,
    DEFAULT_VOLUME_PERCENT,
    VolumeControl,
    apply_pipewire_volume,
    effective_output_volume,
    volume_file_for_config,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_STARTUP_SOUND = "audio/system/startup-chime.mp3"
DEFAULT_UNKNOWN_SOUND = "audio/system/unknown-tag.mp3"


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Magic Character Box")
    parser.add_argument(
        "--config",
        default=os.getenv("MAGIC_BOX_CONFIG", "config/characters.json"),
        help="Path to characters.json",
    )
    parser.add_argument(
        "--nfc",
        default=os.getenv("MAGIC_BOX_NFC", "mock"),
        choices=["mock", "keyboard", "dev", "file", "trigger-file", "queue", "pn532", "pn532-spi", "spi"],
        help="NFC reader backend",
    )
    parser.add_argument(
        "--audio-command",
        default=os.getenv("MAGIC_BOX_AUDIO_CMD", "mpg123 -q"),
        help="Command used to play one audio file",
    )
    parser.add_argument(
        "--audio-backend",
        choices=["subprocess", "mpg123-remote"],
        default=os.getenv("MAGIC_BOX_AUDIO_BACKEND", "subprocess"),
        help="Use a one-shot subprocess per clip or a persistent mpg123 remote backend.",
    )
    parser.add_argument(
        "--audio-warmup-file",
        default=os.getenv("MAGIC_BOX_AUDIO_WARMUP_FILE"),
        help="Optional silent MP3 to load at startup when using mpg123-remote.",
    )
    parser.add_argument(
        "--startup-sound",
        default=os.getenv("MAGIC_BOX_STARTUP_SOUND", DEFAULT_STARTUP_SOUND),
        help="Optional sound to play once when the app starts. Set to an empty string to disable.",
    )
    parser.add_argument(
        "--unknown-sound",
        default=os.getenv("MAGIC_BOX_UNKNOWN_SOUND", DEFAULT_UNKNOWN_SOUND),
        help="Optional sound to play when an unregistered tag is found. Set to an empty string to disable.",
    )
    parser.add_argument(
        "--dry-run-audio",
        action="store_true",
        default=os.getenv("MAGIC_BOX_DRY_RUN_AUDIO", "").lower() in {"1", "true", "yes"},
        help="Log selected audio files without launching a player",
    )
    parser.add_argument(
        "--volume-file",
        default=os.getenv("MAGIC_BOX_VOLUME_FILE"),
        help="Path to shared software volume JSON. Defaults to config/volume.json beside characters.json.",
    )
    parser.add_argument(
        "--control-file",
        default=os.getenv("MAGIC_BOX_CONTROL_FILE"),
        help="Path to shared admin control JSON. Defaults to config/control.json beside characters.json.",
    )
    parser.add_argument(
        "--state-file",
        default=os.getenv("MAGIC_BOX_STATE_FILE"),
        help="Path to shared runtime state JSON. Defaults to config/device_state.json beside characters.json.",
    )
    parser.add_argument(
        "--default-volume",
        type=int,
        default=int(os.getenv("MAGIC_BOX_DEFAULT_VOLUME", str(DEFAULT_VOLUME_PERCENT))),
        help="Default software volume percentage when no volume file exists.",
    )
    parser.add_argument(
        "--max-output-volume",
        type=int,
        default=int(os.getenv("MAGIC_BOX_MAX_OUTPUT_VOLUME", str(DEFAULT_MAX_OUTPUT_VOLUME_PERCENT))),
        help="Maximum output volume percentage after applying the user-facing volume.",
    )
    parser.add_argument(
        "--amp-sd-gpio",
        type=int,
        default=_optional_int(os.getenv("MAGIC_BOX_AMP_SD_GPIO")),
        help="Optional BCM GPIO connected to MAX98357A SD/shutdown. Example: GPIO16 is physical pin 36.",
    )
    parser.add_argument(
        "--amp-unmute-delay",
        type=float,
        default=float(os.getenv("MAGIC_BOX_AMP_UNMUTE_DELAY", "0.12")),
        help="Seconds to wait after starting playback before enabling the amp.",
    )
    parser.add_argument(
        "--amp-mute-delay",
        type=float,
        default=float(os.getenv("MAGIC_BOX_AMP_MUTE_DELAY", "0.05")),
        help="Seconds to wait after playback exits before muting the amp.",
    )
    parser.add_argument(
        "--amp-mute-between-tracks",
        action="store_true",
        default=_env_flag("MAGIC_BOX_AMP_MUTE_BETWEEN_TRACKS"),
        help="Power-cycle the amp around each clip. Usually off because SD wake can pop.",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=float(os.getenv("MAGIC_BOX_COOLDOWN", "3.0")),
        help="Legacy repeat cooldown in seconds. Tag-present repeats are ignored until removal.",
    )
    parser.add_argument(
        "--removal-debounce",
        type=float,
        default=float(os.getenv("MAGIC_BOX_REMOVAL_DEBOUNCE", "0.75")),
        help="Seconds a tag must be absent before the same UID can trigger again",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("MAGIC_BOX_POLL_INTERVAL", "0.2")),
        help="Seconds to sleep when no tag is present",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("MAGIC_BOX_LOG_LEVEL", "INFO"),
        help="Python logging level",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = Path(args.config)
    try:
        config = CharacterConfig.load(config_path)
    except ConfigError as exc:
        LOGGER.error("%s", exc)
        return 2

    try:
        reader = create_reader(args.nfc)
    except NFCError as exc:
        LOGGER.error("%s", exc)
        return 2

    volume_path = Path(args.volume_file).expanduser().resolve() if args.volume_file else volume_file_for_config(config_path)
    control_path = Path(args.control_file).expanduser().resolve() if args.control_file else control_file_for_config(config_path)
    state_path = Path(args.state_file).expanduser().resolve() if args.state_file else state_file_for_config(config_path)
    startup_sound = _resolve_optional_audio_path(config_path, args.startup_sound)
    unknown_sound = _resolve_optional_audio_path(config_path, args.unknown_sound)
    volume = VolumeControl(volume_path, default_percent=args.default_volume)
    apply_pipewire_volume(effective_output_volume(volume.get(), args.max_output_volume))
    amp_gate = create_amp_gate(args.amp_sd_gpio)
    player = AudioPlayer(
        command=args.audio_command,
        dry_run=args.dry_run_audio,
        volume_getter=volume.get,
        max_output_percent=args.max_output_volume,
        amp_gate=amp_gate,
        amp_unmute_delay=args.amp_unmute_delay,
        amp_mute_delay=args.amp_mute_delay,
        mute_between_tracks=args.amp_mute_between_tracks,
        use_mpg123_remote=args.audio_backend == "mpg123-remote",
        warmup_file=Path(args.audio_warmup_file).expanduser().resolve() if args.audio_warmup_file else None,
    )
    LOGGER.info("Magic Character Box ready using %s NFC", args.nfc)
    _safe_append_event(state_path, "system", "Box started.")
    _play_system_sound(player, startup_sound, "startup")

    tag_state = _TagPlaybackState()

    try:
        while True:
            if consume_stop_request(control_path):
                LOGGER.info("Admin stop audio requested")
                player.stop_current()
                _safe_append_event(state_path, "audio", "Audio stopped from dashboard.")

            try:
                uid = reader.read_uid()
            except StopRequested:
                LOGGER.info("Stop requested")
                return 0
            except NFCError as exc:
                LOGGER.warning("%s", exc)
                time.sleep(max(args.poll_interval, 1.0))
                continue

            now = time.monotonic()
            if not tag_state.should_handle(
                uid,
                now=now,
                removal_debounce=args.removal_debounce,
                active_audio_playing=player.is_playing(),
            ):
                if not uid:
                    time.sleep(args.poll_interval)
                else:
                    LOGGER.debug("Ignoring still-present or self-interrupting tag %s", uid)
                continue

            character = config.lookup(uid)
            if character is None:
                LOGGER.warning("Unknown tag %s", uid)
                _safe_record_tag(state_path, uid, known=False, source="playback")
                if _play_system_sound(player, unknown_sound, "unknown tag"):
                    tag_state.note_audio_started(uid)
                continue

            LOGGER.info("Playing %s (%s)", character.name, character.uid)
            _safe_record_tag(state_path, uid, known=True, character_name=character.name, source="playback")
            _safe_append_event(state_path, "audio", f"{character.name} played.", uid=uid, character_name=character.name)
            player.stop_current()
            if player.play_folder(character.folder, character.mode):
                tag_state.note_audio_started(uid)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted")
        return 130
    finally:
        player.close()


def _resolve_optional_audio_path(config_path: Path, value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root_for_config(config_path) / path
    return path.resolve()


@dataclass
class _TagPlaybackState:
    active_uid: str | None = None
    absent_since: float | None = None
    playing_uid: str | None = None

    def should_handle(
        self,
        uid: str | None,
        *,
        now: float,
        removal_debounce: float,
        active_audio_playing: bool,
    ) -> bool:
        if not uid:
            if self.active_uid is not None:
                if self.absent_since is None:
                    self.absent_since = now
                elif now - self.absent_since >= removal_debounce:
                    LOGGER.debug("Tag %s removed", self.active_uid)
                    self.active_uid = None
                    self.absent_since = None
            return False

        self.absent_since = None
        if uid == self.active_uid:
            return False
        if uid == self.playing_uid and active_audio_playing:
            self.active_uid = uid
            return False

        self.active_uid = uid
        return True

    def note_audio_started(self, uid: str) -> None:
        self.playing_uid = uid


def _play_system_sound(player: AudioPlayer, path: Path | None, label: str) -> bool:
    if path is None:
        return False
    if not path.exists():
        LOGGER.info("Skipping %s sound; file not found: %s", label, path)
        return False
    player.stop_current()
    return player.play_file(path)


def _safe_record_tag(
    state_path: Path,
    uid: str,
    *,
    known: bool,
    character_name: str | None = None,
    source: str,
) -> None:
    try:
        record_tag(state_path, uid, known=known, character_name=character_name, source=source)
    except OSError as exc:
        LOGGER.warning("Could not write last-seen tag state: %s", exc)


def _safe_append_event(
    state_path: Path,
    event_type: str,
    message: str,
    *,
    uid: str | None = None,
    character_name: str | None = None,
) -> None:
    try:
        append_event(state_path, event_type, message, uid=uid, character_name=character_name)
    except OSError as exc:
        LOGGER.warning("Could not write device event: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())
