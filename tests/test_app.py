from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from magic_box.app import (
    _TagPlaybackState,
    build_parser,
    main,
    _config_mtime,
    _play_system_sound,
    _reload_config_if_changed,
    _resolve_optional_audio_path,
)
from magic_box.config import CharacterConfig
from magic_box.player_load import PlayerLoadError


class AppSystemSoundTests(unittest.TestCase):
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


def _write_config(path: Path, data: dict[str, object], *, mtime_offset: float = 0.0) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    if mtime_offset:
        current = path.stat().st_mtime
        os.utime(path, (current + mtime_offset, current + mtime_offset))


if __name__ == "__main__":
    unittest.main()
