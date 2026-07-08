from pathlib import Path
import tempfile
import unittest

from magic_box.app import _TagPlaybackState, _play_system_sound, _resolve_optional_audio_path


class AppSystemSoundTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
