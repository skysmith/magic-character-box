"""Local web admin UI for the Magic Character Box."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import threading
import time
from typing import Any, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.datastructures import FileStorage
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from .amp import create_amp_gate
from .audio_prep import prepare_playable_mp3
from .audio import AudioPlayer
from .bluetooth import BluetoothController
from .config import (
    VALID_MODES,
    folder_for_config,
    folder_from_config_value,
    load_raw_config,
    normalize_uid,
    project_root_for_config,
    slugify_name,
    unique_character_folder,
    write_raw_config,
)
from .control import control_file_for_config, request_stop
from .fake_tags import queue_fake_tag, trigger_file_from_env
from .guest_links import (
    GuestLink,
    GuestLinkError,
    create_guest_link as make_guest_link,
    get_guest_link,
    guest_links_file_for_config,
    load_guest_links,
    revoke_guest_link,
)
from .nfc import NFCError, NFCReader, StopRequested, create_reader
from .runtime_state import append_event, load_state, record_tag, state_file_for_config
from .system_mode import ServiceModeController
from .volume import (
    DEFAULT_VOLUME_PERCENT,
    VOLUME_STEP_PERCENT,
    VolumeControl,
    apply_pipewire_volume,
    volume_file_for_config,
)


LOGGER = logging.getLogger(__name__)
UPLOAD_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4"}
PLAYABLE_EXTENSIONS = {".mp3"}


@dataclass(frozen=True)
class AdminCharacter:
    uid: str
    uids: list[str]
    name: str
    folder: Path
    folder_label: str
    mode: str
    playable_files: list[Path]
    other_files: list[Path]


@dataclass(frozen=True)
class SaveResult:
    filename: str
    playable: bool
    converted: bool
    message: str


@dataclass(frozen=True)
class GuestLinkView:
    token: str
    uid: str
    label: str
    character_name: str
    path: str
    url: str
    access_label: str
    access_hint: str
    is_secure: bool
    is_public_hint: bool
    expires_label: str
    expired: bool


class ReaderState:
    """Lazily create the PN532 reader only when the browser actually scans."""

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self.normalized_backend = backend.strip().lower()
        self.reader: NFCReader | None = None

    def browser_scan_error(self) -> str | None:
        if self.normalized_backend in {"mock", "keyboard", "dev"}:
            return "Mock mode cannot scan from the browser; type the UID manually."
        return None

    def get_reader(self) -> NFCReader:
        if self.reader is None:
            self.reader = create_reader(self.backend)
        return self.reader

    def reset(self) -> None:
        self.reader = None


def create_app(
    config_path: str | Path = "config/characters.json",
    nfc_backend: str = "mock",
    audio_command: str = "mpg123 -q",
    dry_run_audio: bool = False,
    volume_file: str | Path | None = None,
    default_volume: int = DEFAULT_VOLUME_PERCENT,
    amp_sd_gpio: int | None = None,
    amp_unmute_delay: float = 0.12,
    amp_mute_delay: float = 0.05,
    amp_mute_between_tracks: bool = False,
    audio_backend: str = "subprocess",
    audio_warmup_file: str | Path | None = None,
    control_file: str | Path | None = None,
    guest_links_file: str | Path | None = None,
    state_file: str | Path | None = None,
    guest_only: bool = False,
    bluetooth_controller: BluetoothController | None = None,
    mode_controller: ServiceModeController | None = None,
) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("MAGIC_BOX_ADMIN_SECRET", "magic-character-box-local-dev")
    if _env_flag("MAGIC_BOX_TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    resolved_config = Path(config_path).expanduser().resolve()
    project_root = project_root_for_config(resolved_config)
    resolved_volume = Path(volume_file).expanduser().resolve() if volume_file else volume_file_for_config(resolved_config)
    resolved_control = Path(control_file).expanduser().resolve() if control_file else control_file_for_config(resolved_config)
    resolved_guest_links = (
        Path(guest_links_file).expanduser().resolve()
        if guest_links_file
        else guest_links_file_for_config(resolved_config)
    )
    resolved_state = Path(state_file).expanduser().resolve() if state_file else state_file_for_config(resolved_config)
    volume = VolumeControl(resolved_volume, default_percent=default_volume)
    apply_pipewire_volume(volume.get())
    amp_gate = create_amp_gate(amp_sd_gpio)
    audio_player = AudioPlayer(
        command=audio_command,
        dry_run=dry_run_audio,
        volume_getter=volume.get,
        amp_gate=amp_gate,
        amp_unmute_delay=amp_unmute_delay,
        amp_mute_delay=amp_mute_delay,
        mute_between_tracks=amp_mute_between_tracks,
        use_mpg123_remote=audio_backend == "mpg123-remote",
        warmup_file=Path(audio_warmup_file).expanduser().resolve() if audio_warmup_file else None,
    )
    trigger_file = trigger_file_from_env()
    bluetooth = bluetooth_controller or BluetoothController()
    mode = mode_controller or ServiceModeController()
    reader_state = ReaderState(nfc_backend)
    reader_lock = threading.Lock()
    player_lock = threading.Lock()
    nfc_error = reader_state.browser_scan_error()

    app.config.update(
        MAGIC_BOX_CONFIG_PATH=resolved_config,
        MAGIC_BOX_PROJECT_ROOT=project_root,
        MAGIC_BOX_NFC_BACKEND=nfc_backend,
        MAGIC_BOX_NFC_ERROR=nfc_error,
        MAGIC_BOX_AUDIO_COMMAND=audio_command,
        MAGIC_BOX_DRY_RUN_AUDIO=dry_run_audio,
        MAGIC_BOX_TRIGGER_FILE=trigger_file,
        MAGIC_BOX_VOLUME_FILE=resolved_volume,
        MAGIC_BOX_CONTROL_FILE=resolved_control,
        MAGIC_BOX_GUEST_LINKS_FILE=resolved_guest_links,
        MAGIC_BOX_STATE_FILE=resolved_state,
        MAGIC_BOX_GUEST_ONLY=guest_only,
        MAGIC_BOX_PREFERRED_GUEST_BASE_URL=_clean_url_base(os.getenv("MAGIC_BOX_PREFERRED_GUEST_BASE_URL", "")),
    )

    def note_event(
        event_type: str,
        message: str,
        *,
        uid: str | None = None,
        character_name: str | None = None,
        level: str = "info",
    ) -> None:
        try:
            append_event(
                resolved_state,
                event_type,
                message,
                uid=uid,
                character_name=character_name,
                level=level,
            )
        except OSError as exc:
            LOGGER.warning("Could not write device event: %s", exc)

    def note_tag(uid: str, *, source: str) -> dict[str, Any]:
        character = _find_character(resolved_config, uid)
        try:
            tag = record_tag(
                resolved_state,
                uid,
                known=character is not None,
                character_name=character.name if character else None,
                source=source,
            )
        except OSError as exc:
            LOGGER.warning("Could not write last-seen tag state: %s", exc)
            tag = {"uid": normalize_uid(uid), "known": character is not None, "character_name": character.name if character else None}
        return _last_tag_view_from_raw(tag, resolved_config)

    def suggest_guest_base_url() -> str:
        if request.is_secure:
            return _clean_url_base(request.url_root)
        preferred = app.config.get("MAGIC_BOX_PREFERRED_GUEST_BASE_URL", "")
        return str(preferred or "")

    def guest_link_base_url(form_value: str) -> str:
        explicit = _clean_url_base(form_value)
        if explicit:
            return explicit
        return suggest_guest_base_url()

    if guest_only:
        @app.before_request
        def restrict_guest_only():
            if request.endpoint in {"guest_recorder", "save_guest_recording", "static"}:
                return None
            return (
                render_template(
                    "guest.html",
                    error="This temporary recording doorway only accepts guest recording links.",
                    token="",
                    character=None,
                    link=None,
                    ffmpeg_available=shutil.which("ffmpeg") is not None,
                ),
                404,
            )

    @app.get("/")
    def index() -> str:
        characters = _load_characters(resolved_config)
        return render_template(
            "admin.html",
            characters=characters,
            modes=sorted(VALID_MODES),
            nfc_backend=nfc_backend,
            nfc_error=nfc_error,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
            audio_command=audio_command,
            dry_run_audio=dry_run_audio,
            trigger_file=trigger_file,
            volume_percent=volume.get(),
            volume_step=VOLUME_STEP_PERCENT,
            guest_links=_load_guest_link_views(resolved_guest_links, resolved_config),
            suggested_guest_base_url=suggest_guest_base_url(),
            last_tag=_last_tag_view(resolved_state, resolved_config),
            recent_events=_event_views(resolved_state),
            bluetooth_status=bluetooth.status().to_dict(),
            mode_status=mode.status().to_dict(),
        )

    @app.post("/characters")
    def save_character():
        uid_value = request.form.get("uid", "")
        name = request.form.get("name", "").strip()
        folder_slug = request.form.get("folder", "").strip()
        mode = request.form.get("mode", "first").strip().lower()

        try:
            uid = normalize_uid(uid_value)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("index") + "#program")

        if not name:
            flash("Character name is required.", "error")
            return redirect(url_for("index") + "#program")
        if mode not in VALID_MODES:
            flash("Playlist mode must be first, shuffle, or sequence.", "error")
            return redirect(url_for("index") + "#program")

        data = load_raw_config(resolved_config)
        if folder_slug:
            folder = _resolve_character_folder(project_root, folder_slug)
        else:
            folder = unique_character_folder(project_root, name, data, uid)
        folder.mkdir(parents=True, exist_ok=True)

        data[uid] = {
            "name": name,
            "folder": folder_for_config(folder, project_root),
            "mode": mode,
        }
        write_raw_config(resolved_config, data)
        note_event("character", f"{name} added.", uid=uid, character_name=name)
        note_tag(uid, source="admin")
        flash(f"Saved {name} as {uid}. Next: add its first sound.", "success")
        return redirect(url_for("index") + f"#character-{uid}")

    @app.post("/characters/<uid>/upload")
    def upload_audio(uid: str):
        character = _get_character_or_404(resolved_config, uid)
        files = request.files.getlist("audio")
        if not files or all(not item.filename for item in files):
            return _upload_response(
                character.uid,
                [{"category": "error", "message": "Choose at least one audio file to upload."}],
                status=400,
            )

        try:
            results = [_save_audio_file(character.folder, item) for item in files if item.filename]
        except ValueError as exc:
            return _upload_response(character.uid, [{"category": "error", "message": str(exc)}], status=400)

        messages = [
            {"category": "success" if result.playable else "warning", "message": result.message}
            for result in results
        ]
        note_event(
            "audio",
            f"Uploaded {len(results)} audio file{'s' if len(results) != 1 else ''} for {character.name}.",
            uid=character.uid,
            character_name=character.name,
        )
        return _upload_response(character.uid, messages)

    @app.post("/characters/<uid>/recordings")
    def save_recording(uid: str):
        character = _get_character_or_404(resolved_config, uid)
        recording = request.files.get("recording")
        title = request.form.get("title", "").strip()
        if recording is None or not recording.filename:
            return jsonify({"ok": False, "message": "No recording received."}), 400

        try:
            result = _save_audio_file(character.folder, recording, title=title or "voice-message")
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        note_event("audio", f"Recorded {result.filename} for {character.name}.", uid=character.uid, character_name=character.name)
        return jsonify(
            {
                "ok": result.playable,
                "filename": result.filename,
                "converted": result.converted,
                "message": result.message,
            }
        ), 200 if result.playable else 202

    @app.post("/characters/<uid>/play")
    def play_character(uid: str):
        character = _get_character_or_404(resolved_config, uid)
        with player_lock:
            audio_player.stop_current()
            played = audio_player.play_folder(character.folder, character.mode)
        note_event(
            "audio",
            f"{character.name} played from dashboard." if played else f"{character.name} has no playable MP3 files.",
            uid=character.uid,
            character_name=character.name,
            level="info" if played else "warning",
        )
        flash(
            f"Playing {character.name}." if played else f"No playable MP3 files found for {character.name}.",
            "success" if played else "warning",
        )
        return redirect(url_for("index") + f"#character-{character.uid}")

    @app.post("/characters/<uid>/delete")
    def delete_character(uid: str):
        character = _get_character_or_404(resolved_config, uid)
        data = load_raw_config(resolved_config)
        removed_uids = [
            raw_uid
            for raw_uid in list(data)
            if normalize_uid(str(raw_uid)) in character.uids
        ]
        for raw_uid in removed_uids:
            data.pop(raw_uid, None)

        write_raw_config(resolved_config, data)
        note_event("character", f"{character.name} deleted from character list.", character_name=character.name)
        flash(
            f"Deleted {character.name} from the character list. Audio files were left in {character.folder_label}.",
            "success",
        )
        return redirect(url_for("index") + "#characters")

    @app.post("/characters/<uid>/files/<path:filename>/delete")
    def delete_audio_file(uid: str, filename: str):
        character = _get_character_or_404(resolved_config, uid)
        try:
            path = _safe_character_file(character.folder, filename)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("index") + f"#character-{character.uid}")

        try:
            path.unlink()
        except OSError as exc:
            flash(f"Could not delete {path.name}: {exc}", "error")
        else:
            note_event("audio", f"Deleted {path.name} from {character.name}.", uid=character.uid, character_name=character.name)
            flash(f"Deleted {path.name}.", "success")
        return redirect(url_for("index") + f"#character-{character.uid}")

    @app.post("/characters/<uid>/fake-scan")
    def fake_scan_character(uid: str):
        character = _get_character_or_404(resolved_config, uid)
        queued_uid, path = queue_fake_tag(character.uid, trigger_file)
        note_event("tag", f"Queued fake scan for {character.name}.", uid=queued_uid, character_name=character.name)
        flash(f"Queued fake scan for {character.name} ({queued_uid}) in {path}.", "success")
        return redirect(url_for("index") + f"#character-{character.uid}")

    @app.post("/stop")
    def stop_audio():
        with player_lock:
            audio_player.stop_current()
        try:
            request_stop(resolved_control)
        except OSError as exc:
            LOGGER.warning("Could not request playback-service stop: %s", exc)
            note_event("audio", "Stopped admin playback; playback service stop failed.", level="warning")
            flash("Stopped admin playback, but could not reach the playback service.", "warning")
        else:
            note_event("audio", "Audio stopped from dashboard.")
            flash("Stop requested for device playback.", "success")
        return redirect(url_for("index"))

    @app.post("/guest-links")
    def create_guest_recording_link():
        try:
            character = _get_character_or_404(resolved_config, request.form.get("uid", ""))
            expires_days = _bounded_int(request.form.get("expires_days"), default=14, minimum=1, maximum=90)
            label = request.form.get("label", "").strip() or f"{character.name} guest recorder"
            link = make_guest_link(
                resolved_guest_links,
                uid=character.uid,
                label=label,
                expires_days=expires_days,
                base_url=guest_link_base_url(request.form.get("base_url", "")),
            )
        except (GuestLinkError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("index") + "#guest-links")

        guest_path = url_for("guest_recorder", token=link.token)
        guest_url = f"{link.base_url}{guest_path}" if link.base_url else url_for("guest_recorder", token=link.token, _external=True)
        note_event("guest", f"Guest recording link created for {character.name}.", uid=character.uid, character_name=character.name)
        flash(f"Guest recording link created: {guest_url}", "success")
        return redirect(url_for("index") + "#guest-links")

    @app.post("/guest-links/<token>/delete")
    def delete_guest_recording_link(token: str):
        try:
            removed = revoke_guest_link(resolved_guest_links, token)
        except GuestLinkError as exc:
            flash(str(exc), "error")
        else:
            note_event("guest", "Guest recording link revoked.")
            flash("Guest recording link revoked." if removed else "Guest recording link was already gone.", "success")
        return redirect(url_for("index") + "#guest-links")

    @app.get("/guest/<token>")
    def guest_recorder(token: str):
        try:
            link = get_guest_link(resolved_guest_links, token)
            character = _get_character_or_404(resolved_config, link.uid)
        except GuestLinkError as exc:
            return (
                render_template(
                    "guest.html",
                    error=str(exc),
                    token=token,
                    character=None,
                    link=None,
                    ffmpeg_available=shutil.which("ffmpeg") is not None,
                ),
                410 if "expired" in str(exc).lower() else 404,
            )

        return render_template(
            "guest.html",
            error=None,
            token=token,
            character=character,
            link=link,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
        )

    @app.post("/guest/<token>/recordings")
    def save_guest_recording(token: str):
        try:
            link = get_guest_link(resolved_guest_links, token)
            character = _get_character_or_404(resolved_config, link.uid)
        except GuestLinkError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 403

        recording = request.files.get("recording") or request.files.get("audio")
        title = request.form.get("title", "").strip() or link.label or "guest-message"
        if recording is None or not recording.filename:
            return jsonify({"ok": False, "message": "No audio file received."}), 400

        try:
            result = _save_audio_file(character.folder, recording, title=title)
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400

        note_event("guest", f"Guest message saved for {character.name}.", uid=character.uid, character_name=character.name)
        return jsonify(
            {
                "ok": result.playable,
                "filename": result.filename,
                "converted": result.converted,
                "message": result.message,
                "character": character.name,
            }
        ), 200 if result.playable else 202

    @app.post("/volume")
    def change_volume():
        action = request.form.get("action", "").strip().lower()
        if action == "up":
            value = volume.adjust(VOLUME_STEP_PERCENT)
        elif action == "down":
            value = volume.adjust(-VOLUME_STEP_PERCENT)
        else:
            try:
                requested = int(request.form.get("volume", ""))
            except ValueError:
                flash("Volume must be a number from 0 to 100.", "error")
                return redirect(url_for("index"))
            value = volume.set(requested)

        if apply_pipewire_volume(value):
            flash(f"Volume set to {value}%.", "success")
        else:
            flash(f"Volume set to {value}% for app playback.", "success")
        note_event("settings", f"Volume set to {value}%.")
        return redirect(url_for("index"))

    @app.get("/api/scan")
    def scan_tag():
        browser_scan_error = reader_state.browser_scan_error()
        if browser_scan_error is not None:
            return jsonify({"ok": False, "message": browser_scan_error}), 503

        mode_status = mode.status()
        if mode_status.available and mode_status.playback_active:
            return jsonify(
                {
                    "ok": False,
                    "message": "Switch to setup mode first. Playback mode is using the NFC reader.",
                    "mode_status": mode_status.to_dict(),
                }
            ), 409

        timeout = _bounded_float(request.args.get("timeout"), default=15.0, minimum=1.0, maximum=60.0)
        deadline = time.monotonic() + timeout
        try:
            with reader_lock:
                reader = reader_state.get_reader()
                while time.monotonic() < deadline:
                    uid = reader.read_uid()
                    if uid:
                        return jsonify({"ok": True, "uid": uid, "last_tag": note_tag(uid, source="browser scan")})
                    time.sleep(0.2)
        except StopRequested:
            return jsonify({"ok": False, "message": "Scan stopped."}), 499
        except NFCError as exc:
            reader_state.reset()
            return jsonify({"ok": False, "message": f"{exc} Try setup mode, then scan again."}), 503

        return jsonify({"ok": False, "message": "No tag found before the scan timed out."}), 408

    @app.get("/api/device-state")
    def device_state():
        return jsonify(
            {
                "ok": True,
                "last_tag": _last_tag_view(resolved_state, resolved_config),
                "events": _event_views(resolved_state),
            }
        )

    @app.post("/api/diagnostics")
    def run_diagnostics():
        checks: list[dict[str, str]] = []

        chime_path = project_root / "audio" / "system" / "startup-chime.mp3"
        if chime_path.exists():
            with player_lock:
                played = audio_player.play_file(chime_path)
            checks.append(
                _diagnostic_check(
                    "Test chime",
                    "good" if played else "bad",
                    "Played startup chime." if played else "Could not start the test chime.",
                )
            )
            note_event("diagnostic", "Test chime played." if played else "Test chime failed.", level="info" if played else "warning")
        else:
            checks.append(_diagnostic_check("Test chime", "warn", "Startup chime file is missing."))

        checks.append(_diagnose_scan(reader_state, reader_lock, mode, nfc_error, resolved_config, note_tag))
        checks.append(_diagnose_audio(audio_command, dry_run_audio, volume.get(), _load_characters(resolved_config)))
        checks.append(_diagnose_service(mode.status()))
        checks.append(_diagnose_storage(project_root))

        note_event("diagnostic", "Box test finished.")
        return jsonify(
            {
                "ok": True,
                "checks": checks,
                "last_tag": _last_tag_view(resolved_state, resolved_config),
                "events": _event_views(resolved_state),
            }
        )

    @app.get("/backup.zip")
    def download_backup():
        buffer = _create_backup_zip(
            project_root=project_root,
            config_path=resolved_config,
            guest_links_path=resolved_guest_links,
            volume_path=resolved_volume,
            control_path=resolved_control,
            state_path=resolved_state,
        )
        note_event("backup", "Backup downloaded.")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return send_file(
            buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"magic-character-box-backup-{timestamp}.zip",
        )

    @app.post("/shutdown")
    def shutdown_device():
        ok, message = _request_shutdown()
        note_event("system", "Shutdown requested from dashboard." if ok else f"Shutdown failed: {message}", level="info" if ok else "warning")
        flash(message, "success" if ok else "error")
        return redirect(url_for("index") + "#details")

    @app.get("/api/mode/status")
    def mode_status():
        return jsonify({"ok": True, **mode.status().to_dict()})

    @app.post("/api/mode/setup")
    def enter_setup_mode():
        result = mode.enter_setup()
        reader_state.reset()
        return jsonify(result.to_dict()), 200 if result.ok else 503

    @app.post("/api/mode/playback")
    def enter_playback_mode():
        reader_state.reset()
        result = mode.enter_playback()
        return jsonify(result.to_dict()), 200 if result.ok else 503

    @app.get("/api/bluetooth/status")
    def bluetooth_status():
        return jsonify({"ok": True, **bluetooth.status().to_dict()})

    @app.post("/api/bluetooth/scan")
    def bluetooth_scan():
        timeout = int(_bounded_float(request.form.get("timeout"), default=8, minimum=3, maximum=30))
        result = bluetooth.scan(timeout=timeout)
        return jsonify(result.to_dict()), 200 if result.ok else 503

    @app.post("/api/bluetooth/power")
    def bluetooth_power():
        payload = _request_data()
        enabled = str(payload.get("enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
        result = bluetooth.power(enabled)
        return jsonify(result.to_dict()), 200 if result.ok else 503

    @app.post("/api/bluetooth/devices/<address>/<action>")
    def bluetooth_device_action(address: str, action: str):
        try:
            if action == "pair":
                result = bluetooth.pair(address)
            elif action == "trust":
                result = bluetooth.trust(address)
            elif action == "connect":
                result = bluetooth.connect(address)
            elif action == "disconnect":
                result = bluetooth.disconnect(address)
            elif action == "use-audio":
                result = bluetooth.use_for_audio(address)
            else:
                return jsonify({"ok": False, "message": f"Unknown Bluetooth action: {action}"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400

        return jsonify(result.to_dict()), 200 if result.ok else 503

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Magic Character Box web admin")
    parser.add_argument("--config", default=os.getenv("MAGIC_BOX_CONFIG", "config/characters.json"))
    parser.add_argument("--nfc", default=os.getenv("MAGIC_BOX_NFC", "mock"))
    parser.add_argument("--host", default=os.getenv("MAGIC_BOX_ADMIN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MAGIC_BOX_ADMIN_PORT", "8080")))
    parser.add_argument("--audio-command", default=os.getenv("MAGIC_BOX_AUDIO_CMD", "mpg123 -q"))
    parser.add_argument(
        "--audio-backend",
        choices=["subprocess", "mpg123-remote"],
        default=os.getenv("MAGIC_BOX_AUDIO_BACKEND", "subprocess"),
    )
    parser.add_argument("--audio-warmup-file", default=os.getenv("MAGIC_BOX_AUDIO_WARMUP_FILE"))
    parser.add_argument("--volume-file", default=os.getenv("MAGIC_BOX_VOLUME_FILE"))
    parser.add_argument("--control-file", default=os.getenv("MAGIC_BOX_CONTROL_FILE"))
    parser.add_argument("--guest-links-file", default=os.getenv("MAGIC_BOX_GUEST_LINKS_FILE"))
    parser.add_argument("--state-file", default=os.getenv("MAGIC_BOX_STATE_FILE"))
    parser.add_argument(
        "--default-volume",
        type=int,
        default=int(os.getenv("MAGIC_BOX_DEFAULT_VOLUME", str(DEFAULT_VOLUME_PERCENT))),
    )
    parser.add_argument("--amp-sd-gpio", type=int, default=_optional_int(os.getenv("MAGIC_BOX_AMP_SD_GPIO")))
    parser.add_argument(
        "--amp-unmute-delay",
        type=float,
        default=float(os.getenv("MAGIC_BOX_AMP_UNMUTE_DELAY", "0.12")),
    )
    parser.add_argument(
        "--amp-mute-delay",
        type=float,
        default=float(os.getenv("MAGIC_BOX_AMP_MUTE_DELAY", "0.05")),
    )
    parser.add_argument(
        "--amp-mute-between-tracks",
        action="store_true",
        default=_env_flag("MAGIC_BOX_AMP_MUTE_BETWEEN_TRACKS"),
    )
    parser.add_argument(
        "--dry-run-audio",
        action="store_true",
        default=os.getenv("MAGIC_BOX_DRY_RUN_AUDIO", "").lower() in {"1", "true", "yes"},
    )
    parser.add_argument("--cert-file", default=os.getenv("MAGIC_BOX_ADMIN_CERT_FILE"))
    parser.add_argument("--key-file", default=os.getenv("MAGIC_BOX_ADMIN_KEY_FILE"))
    parser.add_argument(
        "--guest-only",
        action="store_true",
        default=_env_flag("MAGIC_BOX_GUEST_ONLY"),
        help="Serve only guest recording links. Use this behind a temporary public tunnel.",
    )
    parser.add_argument(
        "--ssl-adhoc",
        action="store_true",
        default=os.getenv("MAGIC_BOX_ADMIN_SSL_ADHOC", "").lower() in {"1", "true", "yes"},
        help="Use Flask/Werkzeug's temporary HTTPS certificate for local testing",
    )
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    app = create_app(
        config_path=args.config,
        nfc_backend=args.nfc,
        audio_command=args.audio_command,
        dry_run_audio=args.dry_run_audio,
        volume_file=args.volume_file,
        control_file=args.control_file,
        guest_links_file=args.guest_links_file,
        state_file=args.state_file,
        guest_only=args.guest_only,
        default_volume=args.default_volume,
        amp_sd_gpio=args.amp_sd_gpio,
        amp_unmute_delay=args.amp_unmute_delay,
        amp_mute_delay=args.amp_mute_delay,
        amp_mute_between_tracks=args.amp_mute_between_tracks,
        audio_backend=args.audio_backend,
        audio_warmup_file=args.audio_warmup_file,
    )
    ssl_context: str | tuple[str, str] | None = None
    if args.cert_file or args.key_file:
        if not args.cert_file or not args.key_file:
            LOGGER.error("--cert-file and --key-file must be provided together")
            return 2
        ssl_context = (args.cert_file, args.key_file)
    elif args.ssl_adhoc:
        ssl_context = "adhoc"

    app.run(host=args.host, port=args.port, debug=args.debug, ssl_context=ssl_context)
    return 0


def _load_characters(config_path: Path) -> list[AdminCharacter]:
    project_root = project_root_for_config(config_path)
    data = load_raw_config(config_path)
    grouped: dict[tuple[str, Path, str], AdminCharacter] = {}
    for raw_uid, raw_character in data.items():
        if not isinstance(raw_character, dict):
            continue
        uid = normalize_uid(str(raw_uid))
        name = str(raw_character.get("name", uid)).strip() or uid
        folder_value = str(raw_character.get("folder", f"audio/{slugify_name(name)}"))
        folder = folder_from_config_value(folder_value, project_root)
        folder.mkdir(parents=True, exist_ok=True)
        mode = str(raw_character.get("mode", "first")).strip().lower()
        if mode not in VALID_MODES:
            mode = "first"
        playable, other = _list_audio_files(folder)
        key = (name, folder.resolve(), mode)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = AdminCharacter(
                uid=uid,
                uids=[uid],
                name=name,
                folder=folder,
                folder_label=folder_for_config(folder, project_root),
                mode=mode,
                playable_files=playable,
                other_files=other,
            )
            continue

        uids = sorted([*existing.uids, uid], key=_uid_sort_key)
        grouped[key] = AdminCharacter(
            uid=uids[0],
            uids=uids,
            name=existing.name,
            folder=existing.folder,
            folder_label=existing.folder_label,
            mode=existing.mode,
            playable_files=playable,
            other_files=other,
        )
    characters = list(grouped.values())
    return sorted(characters, key=lambda character: character.name.lower())


def _get_character_or_404(config_path: Path, uid: str) -> AdminCharacter:
    normalized_uid = normalize_uid(uid)
    for character in _load_characters(config_path):
        if normalized_uid in character.uids:
            return character
    from flask import abort

    abort(404)


def _load_guest_link_views(guest_links_path: Path, config_path: Path) -> list[GuestLinkView]:
    characters = _load_characters(config_path)
    character_by_uid = {
        uid: character
        for character in characters
        for uid in character.uids
    }
    views: list[GuestLinkView] = []
    for link in sorted(load_guest_links(guest_links_path).values(), key=lambda item: item.created_at, reverse=True):
        character = character_by_uid.get(link.uid)
        character_name = character.name if character is not None else link.uid
        path = url_for("guest_recorder", token=link.token)
        link_url = f"{link.base_url}{path}" if link.base_url else url_for("guest_recorder", token=link.token, _external=True)
        access_label, access_hint, is_secure, is_public_hint = _guest_link_access_details(link_url)
        views.append(
            GuestLinkView(
                token=link.token,
                uid=link.uid,
                label=link.label,
                character_name=character_name,
                path=path,
                url=link_url,
                access_label=access_label,
                access_hint=access_hint,
                is_secure=is_secure,
                is_public_hint=is_public_hint,
                expires_label=_expires_label(link),
                expired=link.is_expired(),
            )
        )
    return views


def _expires_label(link: GuestLink) -> str:
    if link.expires_at is None:
        return "no expiration"
    local = link.expires_at.astimezone()
    return f"expires {local.strftime('%Y-%m-%d %H:%M')}"


def _clean_url_base(value: str) -> str:
    return value.strip().rstrip("/")


def _guest_link_access_details(url: str) -> tuple[str, str, bool, bool]:
    if url.startswith("https://") and ".ts.net/" in url:
        return (
            "Private Tailscale HTTPS",
            "Works from your Tailscale devices. For Grandma outside your tailnet, use a public guest-only tunnel.",
            True,
            False,
        )
    if url.startswith("https://"):
        return (
            "Public/secure guest link",
            "Send this to a remote guest when the tunnel is intentionally running.",
            True,
            True,
        )
    if _is_local_http_url(url):
        return (
            "Local Wi-Fi link",
            "Works at home for upload. Browser recording needs HTTPS; use the Tailscale dashboard or a public guest-only tunnel.",
            False,
            False,
        )
    return (
        "Guest link",
        "Check that this link is reachable from the device that will upload the message.",
        url.startswith("https://"),
        False,
    )


def _is_local_http_url(url: str) -> bool:
    return url.startswith(("http://192.168.", "http://10.", "http://172.", "http://127.", "http://localhost"))


def _last_tag_view(state_path: Path, config_path: Path) -> dict[str, Any]:
    state = load_state(state_path)
    last_tag = state.get("last_tag")
    if not isinstance(last_tag, dict):
        return {"available": False}
    return _last_tag_view_from_raw(last_tag, config_path)


def _last_tag_view_from_raw(last_tag: dict[str, Any], config_path: Path) -> dict[str, Any]:
    uid_value = str(last_tag.get("uid", "")).strip()
    if not uid_value:
        return {"available": False}

    try:
        uid = normalize_uid(uid_value)
    except ValueError:
        return {"available": False}

    character = _find_character(config_path, uid)
    known = character is not None
    character_name = character.name if character else str(last_tag.get("character_name") or "")
    return {
        "available": True,
        "uid": uid,
        "known": known,
        "character_name": character_name,
        "status_label": character_name if known else "New tag",
        "hint": "Already registered." if known else "Ready to teach.",
        "source": str(last_tag.get("source") or "reader"),
        "seen_label": _time_label(last_tag.get("seen_at")),
        "can_add": not known,
    }


def _event_views(state_path: Path, limit: int = 12) -> list[dict[str, Any]]:
    state = load_state(state_path)
    views: list[dict[str, Any]] = []
    for raw_event in state.get("events", [])[:limit]:
        if not isinstance(raw_event, dict):
            continue
        message = str(raw_event.get("message") or "").strip()
        if not message:
            continue
        views.append(
            {
                "message": message,
                "level": str(raw_event.get("level") or "info"),
                "type": str(raw_event.get("type") or "event"),
                "created_label": _time_label(raw_event.get("created_at")),
            }
        )
    return views


def _find_character(config_path: Path, uid: str) -> AdminCharacter | None:
    try:
        normalized_uid = normalize_uid(uid)
    except ValueError:
        return None
    for character in _load_characters(config_path):
        if normalized_uid in character.uids:
            return character
    return None


def _time_label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "just now"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    try:
        return parsed.astimezone().strftime("%b %-d, %-I:%M %p")
    except ValueError:
        return parsed.astimezone().strftime("%b %d, %I:%M %p")


def _diagnostic_check(label: str, state: str, message: str) -> dict[str, str]:
    return {"label": label, "state": state, "message": message}


def _diagnose_scan(
    reader_state: ReaderState,
    reader_lock: threading.Lock,
    mode: ServiceModeController,
    nfc_error: str | None,
    config_path: Path,
    note_tag,
) -> dict[str, str]:
    if nfc_error:
        return _diagnostic_check("NFC scan", "warn", nfc_error)

    mode_status = mode.status()
    if mode_status.available and mode_status.playback_active:
        return _diagnostic_check("NFC scan", "warn", "Switch to Setup scan, then run the box test again.")

    deadline = time.monotonic() + 10
    try:
        with reader_lock:
            reader = reader_state.get_reader()
            while time.monotonic() < deadline:
                uid = reader.read_uid()
                if uid:
                    tag = note_tag(uid, source="diagnostic")
                    character = _find_character(config_path, uid)
                    status = character.name if character else "new tag"
                    return _diagnostic_check("NFC scan", "good", f"Found {tag['uid']} ({status}).")
                time.sleep(0.2)
    except StopRequested:
        return _diagnostic_check("NFC scan", "warn", "Scan stopped.")
    except NFCError as exc:
        reader_state.reset()
        return _diagnostic_check("NFC scan", "bad", f"{exc}")

    return _diagnostic_check("NFC scan", "warn", "No tag found in 10 seconds.")


def _diagnose_audio(
    audio_command: str,
    dry_run_audio: bool,
    volume_percent: int,
    characters: list[AdminCharacter],
) -> dict[str, str]:
    playable_count = sum(len(character.playable_files) for character in characters)
    tool = _audio_tool_name(audio_command)
    available = dry_run_audio or (bool(tool) and shutil.which(tool) is not None)
    if available:
        mode = "dry run" if dry_run_audio else tool or "audio command"
        message = f"{mode} ready. {playable_count} playable MP3 file{'s' if playable_count != 1 else ''}. Volume {volume_percent}%."
    else:
        message = f"{tool or 'Audio command'} was not found. Install mpg123 or use dry-run mode."
    return _diagnostic_check("Audio", "good" if available else "bad", message)


def _diagnose_service(status: Any) -> dict[str, str]:
    if not status.available:
        return _diagnostic_check("Playback service", "warn", status.message)
    if status.playback_active is True:
        return _diagnostic_check("Playback service", "good", "Playback mode is running.")
    if status.playback_active is False:
        return _diagnostic_check("Playback service", "good", "Setup scan mode is active.")
    return _diagnostic_check("Playback service", "warn", status.message)


def _diagnose_storage(project_root: Path) -> dict[str, str]:
    free_gb = shutil.disk_usage(project_root).free / (1024 ** 3)
    return _diagnostic_check(
        "Storage",
        "good" if free_gb >= 1 else "warn",
        f"{free_gb:.1f} GB free for songs and voice clips.",
    )


def _audio_tool_name(audio_command: str) -> str:
    try:
        parts = shlex.split(audio_command)
    except ValueError:
        return ""
    return Path(parts[0]).name if parts else ""


def _create_backup_zip(
    *,
    project_root: Path,
    config_path: Path,
    guest_links_path: Path,
    volume_path: Path,
    control_path: Path,
    state_path: Path,
) -> BytesIO:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for path in [config_path, guest_links_path, volume_path, control_path, state_path]:
            _zip_file_if_exists(archive, path, project_root)

        audio_root = project_root / "audio"
        if audio_root.exists():
            for path in sorted(audio_root.rglob("*")):
                if path.is_file() and not path.name.startswith("."):
                    _zip_file_if_exists(archive, path, project_root)

        archive.writestr(
            "README-backup.txt",
            "Magic Character Box backup. Restore config/ and audio/ into the project folder on the Pi.\n",
        )
    buffer.seek(0)
    return buffer


def _zip_file_if_exists(archive: ZipFile, path: Path, project_root: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        arcname = path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        arcname = path.name
    archive.write(path, arcname)


def _request_shutdown() -> tuple[bool, str]:
    command = os.getenv("MAGIC_BOX_SHUTDOWN_COMMAND", "sudo -n shutdown -h now")
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return False, f"Shutdown command is invalid: {exc}"
    if not args:
        return False, "Shutdown command is empty."

    try:
        completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return False, "Shutdown command timed out."
    except OSError as exc:
        return False, f"Could not request shutdown: {exc}"

    if completed.returncode == 0:
        return True, "Shutdown requested. Wait for the Pi activity light to settle before unplugging."

    message = (completed.stderr or completed.stdout or "").strip() or "Shutdown command failed."
    return False, message.splitlines()[-1].strip()




def _list_audio_files(folder: Path) -> tuple[list[Path], list[Path]]:
    playable: list[Path] = []
    other: list[Path] = []
    if not folder.exists():
        return playable, other

    for item in sorted(folder.iterdir()):
        if not item.is_file() or item.name.startswith("."):
            continue
        if item.suffix.lower() in PLAYABLE_EXTENSIONS:
            playable.append(item)
        elif item.suffix.lower() in UPLOAD_EXTENSIONS:
            other.append(item)
    return playable, other


def _resolve_character_folder(project_root: Path, folder_slug: str) -> Path:
    value = folder_slug.strip()
    if "/" in value or value.startswith("audio/"):
        folder = Path(value)
    else:
        folder = Path("audio") / slugify_name(value)
    if not folder.is_absolute():
        folder = project_root / folder
    return folder.resolve()


def _safe_character_file(folder: Path, filename: str) -> Path:
    if not filename or "/" in filename or "\\" in filename:
        raise ValueError("Invalid audio filename.")

    path = (folder / filename).resolve()
    if path.parent != folder.resolve():
        raise ValueError("Invalid audio filename.")
    if not path.exists() or not path.is_file():
        raise ValueError(f"Audio file not found: {filename}")
    if path.suffix.lower() not in UPLOAD_EXTENSIONS:
        raise ValueError(f"Unsupported audio file type: {filename}")
    return path


def _upload_response(uid: str, messages: list[dict[str, str]], status: int = 200):
    redirect_url = url_for("index") + f"#character-{uid}"
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return (
            jsonify(
                {
                    "ok": status < 400,
                    "message": "\n".join(item["message"] for item in messages),
                    "messages": messages,
                    "redirect": redirect_url,
                }
            ),
            status,
        )

    for item in messages:
        flash(item["message"], item["category"])
    return redirect(redirect_url)


def _save_audio_file(folder: Path, storage: FileStorage, title: str | None = None) -> SaveResult:
    folder.mkdir(parents=True, exist_ok=True)
    original_name = secure_filename(storage.filename or "recording")
    suffix = Path(original_name).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValueError(f"Unsupported audio extension: {suffix or '(none)'}")

    stem = secure_filename(title or Path(original_name).stem or "audio")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    source_path = _unique_path(folder / f"{timestamp}-{stem}{suffix}")
    storage.save(source_path)

    playable_path = source_path.with_suffix(".mp3")
    if prepare_playable_mp3(source_path, playable_path):
        if source_path != playable_path:
            try:
                source_path.unlink()
            except OSError:
                pass
        return SaveResult(
            filename=playable_path.name,
            playable=True,
            converted=suffix != ".mp3",
            message=f"Prepared {playable_path.name} with soft fades and normalized volume.",
        )

    if suffix == ".mp3":
        return SaveResult(
            filename=source_path.name,
            playable=True,
            converted=False,
            message=f"Saved {source_path.name}. Install ffmpeg to add automatic fades and volume normalization.",
        )

    return SaveResult(
        filename=source_path.name,
        playable=False,
        converted=False,
        message=f"Saved {source_path.name}, but ffmpeg is missing or conversion failed so it is not playable by mpg123 yet.",
    )


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find an available filename near {path}")


def _uid_sort_key(uid: str) -> tuple[int, str]:
    is_named_dev_uid = uid.isalpha()
    return (1 if is_named_dev_uid else 0, uid)


def _bounded_float(value: str | None, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except ValueError:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None and value.strip() else default
    except ValueError:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _request_data() -> dict[str, Any]:
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
