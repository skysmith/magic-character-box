from pathlib import Path
import json
import os
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from magic_box.app import (
    _TagPlaybackState,
    _handle_service_stop,
    build_parser,
    main,
    _config_mtime,
    _play_system_sound,
    _reload_config_if_changed,
    _resolve_optional_audio_path,
)
from magic_box.config import CharacterConfig
from magic_box.audio import AudioInitializationError, AudioRuntimeError
from magic_box.player_load import PlayerLoadError


class AppSystemSoundTests(unittest.TestCase):
    def test_hosted_ndef_reader_mode_is_explicitly_available(self) -> None:
        self.assertEqual(build_parser().parse_args(["--nfc", "pn532-ndef"]).nfc, "pn532-ndef")

    def test_transactional_config_is_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGIC_BOX_TRANSACTIONAL_CONFIG", None)
            self.assertFalse(build_parser().parse_args([]).transactional_config)
            self.assertTrue(build_parser().parse_args(["--transactional-config"]).transactional_config)

    def test_unsafe_transactional_startup_stops_before_reader_or_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "audio" / "first"
            folder.mkdir(parents=True)
            (folder / "memo.mp3").write_bytes(b"memo")
            config_path = root / "config" / "characters.json"
            config_path.parent.mkdir()
            _write_config(
                config_path,
                {"04-A1": {"name": "First", "folder": "audio/first", "mode": "first"}},
            )

            with patch(
                "magic_box.app.PlayerLoadBridge",
                side_effect=PlayerLoadError("unsafe persisted proof"),
            ), patch("magic_box.app.create_reader") as create_reader, patch(
                "magic_box.app.AudioPlayer"
            ) as audio_player, self.assertLogs("magic_box.app", level="ERROR"):
                result = main(
                    [
                        "--config",
                        str(config_path),
                        "--transactional-config",
                        "--startup-sound",
                        "",
                    ]
                )

            self.assertEqual(result, 2)
            create_reader.assert_not_called()
            audio_player.assert_not_called()

    def test_audio_initialization_failure_never_reports_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"
            config_path.parent.mkdir()
            _write_config(config_path, {})
            gate = MagicMock()

            with patch("magic_box.app.create_reader"), patch(
                "magic_box.app.create_amp_gate", return_value=gate
            ), patch(
                "magic_box.app.AudioPlayer",
                side_effect=AudioInitializationError("sink missing"),
            ), self.assertLogs("magic_box.app", level="ERROR") as logs:
                result = main(["--config", str(config_path), "--startup-sound", ""])

            self.assertEqual(result, 2)
            gate.close.assert_called_once_with()
            self.assertFalse(any("ready using" in line for line in logs.output))

    def test_runtime_sink_failure_exits_nonzero_for_systemd_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"
            config_path.parent.mkdir()
            _write_config(config_path, {})
            player = MagicMock()
            player.raise_if_unhealthy.side_effect = AudioRuntimeError("sink exited")

            with patch("magic_box.app.create_reader"), patch(
                "magic_box.app.AudioPlayer", return_value=player
            ), self.assertLogs("magic_box.app", level="ERROR"):
                result = main(["--config", str(config_path), "--startup-sound", ""])

            self.assertEqual(result, 3)
            player.close.assert_called_once_with()

    def test_sigterm_path_closes_player_and_restores_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"
            config_path.parent.mkdir()
            _write_config(config_path, {})
            player = MagicMock()
            previous = signal.getsignal(signal.SIGTERM)

            with patch("magic_box.app.create_reader", return_value=_TerminatingReader()), patch(
                "magic_box.app.AudioPlayer", return_value=player
            ), self.assertLogs("magic_box.app", level="INFO"):
                result = main(["--config", str(config_path), "--startup-sound", ""])

            self.assertEqual(result, 0)
            player.close.assert_called_once_with()
            self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_sigterm_during_audio_initialization_is_deferred_until_player_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"
            config_path.parent.mkdir()
            _write_config(config_path, {})
            player = MagicMock()
            previous = signal.getsignal(signal.SIGTERM)

            def initialize_player(**_kwargs: object) -> MagicMock:
                installed_handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(installed_handler))
                installed_handler(signal.SIGTERM, None)  # type: ignore[operator]
                return player

            with patch("magic_box.app.create_reader"), patch(
                "magic_box.app.AudioPlayer", side_effect=initialize_player
            ), self.assertLogs("magic_box.app", level="INFO"):
                result = main(["--config", str(config_path), "--startup-sound", ""])

            self.assertEqual(result, 0)
            player.close.assert_called_once_with()
            self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_character_event_records_request_or_generic_start_failure_not_played(self) -> None:
        for started, expected_message in (
            (True, "First playback requested."),
            (False, "Selected audio could not start."),
        ):
            with self.subTest(started=started), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                audio_folder = root / "audio" / "first"
                audio_folder.mkdir(parents=True)
                config_path = root / "config" / "characters.json"
                config_path.parent.mkdir()
                _write_config(
                    config_path,
                    {"04-A1": {"name": "First", "folder": "audio/first", "mode": "first"}},
                )
                player = MagicMock()
                player.play_folder.return_value = started

                with patch(
                    "magic_box.app.create_reader",
                    return_value=_OneTagThenTerminateReader("04-A1"),
                ), patch(
                    "magic_box.app.AudioPlayer",
                    return_value=player,
                ), patch(
                    "magic_box.app._safe_record_tag",
                ), patch(
                    "magic_box.app._safe_append_event",
                ) as append_event, self.assertLogs("magic_box.app", level="INFO"):
                    result = main(["--config", str(config_path), "--startup-sound", ""])

                self.assertEqual(result, 0)
                messages = [call.args[2] for call in append_event.call_args_list]
                self.assertIn(expected_message, messages)
                self.assertFalse(any(message.endswith(" played.") for message in messages))

    def test_resolve_optional_audio_path_uses_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"

            self.assertEqual(
                _resolve_optional_audio_path(config_path, "audio/system/startup-chime.mp3"),
                Path(temp_dir).resolve() / "audio" / "system" / "startup-chime.mp3",
            )

    def test_resolve_optional_audio_path_allows_empty_disable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config" / "characters.json"

            self.assertIsNone(_resolve_optional_audio_path(config_path, ""))

    def test_play_system_sound_plays_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sound = Path(temp_dir) / "sound.mp3"
            sound.write_bytes(b"fake mp3")
            player = _FakePlayer()

            _play_system_sound(player, sound, "startup")

            self.assertEqual(player.events, [("stop", None), ("play", sound)])

    def test_play_system_sound_skips_missing_file(self) -> None:
        player = _FakePlayer()

        _play_system_sound(player, Path("/tmp/does-not-exist.mp3"), "startup")

        self.assertEqual(player.events, [])

    def test_reload_config_if_changed_picks_up_new_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config" / "characters.json"
            config_path.parent.mkdir()
            first_audio = root / "audio" / "first"
            second_audio = root / "audio" / "second"
            first_audio.mkdir(parents=True)
            second_audio.mkdir(parents=True)
            _write_config(config_path, {"04-A1": {"name": "First", "folder": "audio/first", "mode": "first"}})
            config = CharacterConfig.load(config_path)
            last_mtime = _config_mtime(config_path)

            _write_config(
                config_path,
                {
                    "04-A1": {"name": "First", "folder": "audio/first", "mode": "first"},
                    "04-B2": {"name": "Second", "folder": "audio/second", "mode": "first"},
                },
                mtime_offset=5,
            )

            updated, updated_mtime, reloaded = _reload_config_if_changed(config_path, config, last_mtime)

            self.assertTrue(reloaded)
            self.assertNotEqual(updated_mtime, last_mtime)
            self.assertEqual(updated.lookup("04-B2").name, "Second")

    def test_reload_config_if_changed_keeps_current_config_when_rewrite_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config" / "characters.json"
            config_path.parent.mkdir()
            first_audio = root / "audio" / "first"
            first_audio.mkdir(parents=True)
            _write_config(config_path, {"04-A1": {"name": "First", "folder": "audio/first", "mode": "first"}})
            config = CharacterConfig.load(config_path)
            last_mtime = _config_mtime(config_path)
            config_path.write_text("{not-json", encoding="utf-8")
            os.utime(config_path, (last_mtime + 5, last_mtime + 5))

            with self.assertLogs("magic_box.app", level="WARNING"):
                updated, updated_mtime, reloaded = _reload_config_if_changed(config_path, config, last_mtime)

            self.assertFalse(reloaded)
            self.assertNotEqual(updated_mtime, last_mtime)
            self.assertEqual(updated.lookup("04-A1").name, "First")


class TagPlaybackStateTests(unittest.TestCase):
    def test_same_uid_cannot_interrupt_itself_while_audio_is_active(self) -> None:
        state = _TagPlaybackState()

        self.assertTrue(
            state.should_handle("DINOSAUR", now=0.0, removal_debounce=3.0, active_audio_playing=False)
        )
        state.note_audio_started("DINOSAUR")
        self.assertFalse(state.should_handle(None, now=1.0, removal_debounce=3.0, active_audio_playing=True))
        self.assertFalse(state.should_handle(None, now=4.1, removal_debounce=3.0, active_audio_playing=True))

        self.assertFalse(
            state.should_handle("DINOSAUR", now=4.2, removal_debounce=3.0, active_audio_playing=True)
        )

    def test_different_uid_can_interrupt_active_audio(self) -> None:
        state = _TagPlaybackState()

        self.assertTrue(state.should_handle("DINOSAUR", now=0.0, removal_debounce=3.0, active_audio_playing=False))
        state.note_audio_started("DINOSAUR")
        self.assertFalse(state.should_handle(None, now=4.0, removal_debounce=3.0, active_audio_playing=True))

        self.assertTrue(state.should_handle("ROCKET", now=4.1, removal_debounce=3.0, active_audio_playing=True))

    def test_same_uid_can_replay_after_audio_is_finished_and_tag_was_removed(self) -> None:
        state = _TagPlaybackState()

        self.assertTrue(
            state.should_handle("DINOSAUR", now=0.0, removal_debounce=3.0, active_audio_playing=False)
        )
        state.note_audio_started("DINOSAUR")
        self.assertFalse(state.should_handle(None, now=1.0, removal_debounce=3.0, active_audio_playing=False))
        self.assertFalse(state.should_handle(None, now=4.1, removal_debounce=3.0, active_audio_playing=False))

        self.assertTrue(
            state.should_handle("DINOSAUR", now=4.2, removal_debounce=3.0, active_audio_playing=False)
        )


class _FakePlayer:
    def __init__(self) -> None:
        self.events: list[tuple[str, Path | None]] = []

    def stop_current(self) -> None:
        self.events.append(("stop", None))

    def play_file(self, path: Path) -> bool:
        self.events.append(("play", path))
        return True


class _TerminatingReader:
    def read_uid(self) -> str | None:
        _handle_service_stop(signal.SIGTERM, None)
        return None


class _OneTagThenTerminateReader:
    def __init__(self, uid: str) -> None:
        self.uid = uid
        self.delivered = False

    def read_uid(self) -> str | None:
        if not self.delivered:
            self.delivered = True
            return self.uid
        _handle_service_stop(signal.SIGTERM, None)
        return None


def _write_config(path: Path, data: dict[str, object], *, mtime_offset: float = 0.0) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    if mtime_offset:
        current = path.stat().st_mtime
        os.utime(path, (current + mtime_offset, current + mtime_offset))


if __name__ == "__main__":
    unittest.main()
