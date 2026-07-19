"""Application loop for the Magic Character Box."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
import time
from typing import Sequence

from .amp import create_amp_gate
from .audio import AudioInitializationError, AudioPlayer, AudioRuntimeError
from .config import CharacterConfig, ConfigError, project_root_for_config
from .control import consume_stop_request, control_file_for_config
from .nfc import NFCError, StopRequested, create_reader
from .player_load import PlayerLoadBridge, PlayerLoadError
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


class _ServiceStopRequested(Exception):
    """Raised by SIGTERM so the player can close its children in order."""


def _handle_service_stop(_signum: int, _frame: object) -> None:
    raise _ServiceStopRequested


class _DeferredServiceStopHandler:
    """Defer SIGTERM until audio construction has a closeable owner."""

    def __init__(self) -> None:
        self.requested = False
        self._armed = False

    def __call__(self, signum: int, frame: object) -> None:
        self.requested = True
        if self._armed:
            _handle_service_stop(signum, frame)

    def arm(self) -> None:
        self._armed = True
        if self.requested:
            raise _ServiceStopRequested

    def defer(self) -> None:
        self._armed = False


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
        choices=[
            "mock",
            "keyboard",
            "dev",
            "file",
            "trigger-file",
            "queue",
            "pn532",
            "pn532-spi",
            "spi",
            "pn532-ndef",
        ],
        help="NFC reader backend",
    )
    parser.add_argument(
        "--audio-command",
        default=os.getenv("MAGIC_BOX_AUDIO_CMD", "mpg123 -q"),
        help="Command used to play one audio file",
    )
    parser.add_argument(
        "--audio-backend",
        choices=["subprocess", "mpg123-remote", "continuous-pcm"],
        default=os.getenv("MAGIC_BOX_AUDIO_BACKEND", "subprocess"),
        help="Use one-shot playback, mpg123 remote mode, or one continuous direct PCM sink.",
    )
    parser.add_argument(
        "--audio-sink-command",
        default=os.getenv("MAGIC_BOX_AUDIO_SINK_CMD", "aplay -q"),
        help="Long-lived raw PCM sink command used by the continuous-pcm backend.",
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
    parser.add_argument(
        "--transactional-config",
        action="store_true",
        default=_env_flag("MAGIC_BOX_TRANSACTIONAL_CONFIG"),
        help=(
            "Opt in to request/ack config activation and block ordinary mtime reloads. "
            "Maker mode leaves this off."
        ),
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
    config_mtime = _config_mtime(config.path)
    player_load_bridge: PlayerLoadBridge | None = None
    if args.transactional_config:
        try:
            player_load_bridge = PlayerLoadBridge(config)
        except PlayerLoadError as exc:
            LOGGER.error("Transactional config setup is unsafe: %s", exc)
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
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    stop_handler = _DeferredServiceStopHandler()
    signal.signal(signal.SIGTERM, stop_handler)
    player: AudioPlayer | None = None
    try:
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
            use_continuous_pcm=args.audio_backend == "continuous-pcm",
            sink_command=args.audio_sink_command,
            warmup_file=Path(args.audio_warmup_file).expanduser().resolve() if args.audio_warmup_file else None,
        )
        stop_handler.arm()
        LOGGER.info("Magic Character Box ready using %s NFC", args.nfc)
        _safe_append_event(state_path, "system", "Box started.")
        _play_system_sound(player, startup_sound, "startup")
        tag_state = _TagPlaybackState()
        while True:
            player.raise_if_unhealthy()
            if player_load_bridge is not None:
                config_reloaded = player_load_bridge.poll()
                config = player_load_bridge.config
            else:
                config, config_mtime, config_reloaded = _reload_config_if_changed(
                    config_path,
                    config,
                    config_mtime,
                )
            if config_reloaded:
                if player_load_bridge is not None:
                    LOGGER.info("Loaded requested character config activation")
                else:
                    LOGGER.info("Reloaded character config after sync update")

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
            if player.play_folder(character.folder, character.mode):
                _safe_append_event(
                    state_path,
                    "audio",
                    f"{character.name} playback requested.",
                    uid=uid,
                    character_name=character.name,
                )
                tag_state.note_audio_started(uid)
            else:
                _safe_append_event(
                    state_path,
                    "audio",
                    "Selected audio could not start.",
                    uid=uid,
                    character_name=character.name,
                )
    except AudioInitializationError as exc:
        if stop_handler.requested:
            LOGGER.info("Service stop requested during audio initialization")
            return 0
        LOGGER.error("Audio initialization failed: %s", exc)
        return 2
    except AudioRuntimeError as exc:
        LOGGER.error("Audio backend failed: %s", exc)
        return 3
    except _ServiceStopRequested:
        LOGGER.info("Service stop requested")
        return 0
    except KeyboardInterrupt:
        LOGGER.info("Interrupted")
        return 130
    finally:
        stop_handler.defer()
        try:
            if player is None:
                amp_gate.close()
            else:
                player.close()
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)


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


def _config_mtime(path: Path) -> float | None:
    try:
        return path.expanduser().resolve().stat().st_mtime
    except OSError:
        return None


def _reload_config_if_changed(
    config_path: Path,
    current_config: CharacterConfig,
    last_mtime: float | None,
) -> tuple[CharacterConfig, float | None, bool]:
    current_mtime = _config_mtime(config_path)
    if current_mtime is None or current_mtime == last_mtime:
        return current_config, last_mtime, False

    try:
        return CharacterConfig.load(config_path), current_mtime, True
    except ConfigError as exc:
        LOGGER.warning("Could not reload changed character config: %s", exc)
        return current_config, current_mtime, False


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
