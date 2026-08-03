import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from magic_box.volume import (
    STORY_DOCK_VOLUME_SCHEMA,
    StoryDockVolumeState,
    VolumeControl,
    VolumeFileError,
    apply_pipewire_volume,
    apply_story_dock_volume_settings,
    effective_output_volume,
)


class VolumeTests(unittest.TestCase):
    def test_volume_defaults_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = VolumeControl(Path(temp_dir) / "volume.json", default_percent=40)

            self.assertEqual(control.get(), 40)

    def test_volume_adjusts_and_clamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = VolumeControl(Path(temp_dir) / "volume.json", default_percent=40)

            self.assertEqual(control.adjust(70), 100)
            self.assertEqual(control.adjust(-150), 0)

    def test_revisioned_volume_write_is_durable_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            control = VolumeControl(path)

            state = control.set_revisioned(60, 3)

            self.assertEqual(state, StoryDockVolumeState(60, 3))
            self.assertEqual(control.story_dock_state(), state)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {
                    "volume_percent": 60,
                    "story_dock": {
                        "schema": STORY_DOCK_VOLUME_SCHEMA,
                        "applied_revision": 3,
                    },
                },
            )

    def test_local_volume_change_preserves_applied_revision_for_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            control = VolumeControl(path)
            control.set_revisioned(60, 3)

            control.set(45)

            self.assertEqual(control.story_dock_state(), StoryDockVolumeState(45, 3))

    def test_story_dock_settings_are_idempotent_and_repair_same_revision_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            control = VolumeControl(path)
            control.set_revisioned(40, 2)

            with patch.object(VolumeControl, "set_revisioned", wraps=control.set_revisioned) as setter:
                report = apply_story_dock_volume_settings(
                    path,
                    {"managed": True, "desired_percent": 40, "revision": 2},
                )
            setter.assert_not_called()
            self.assertEqual(report["volume_apply_status"], "applied")

            report = apply_story_dock_volume_settings(
                path,
                {"managed": True, "desired_percent": 65, "revision": 2},
            )
            self.assertEqual(report["volume_apply_status"], "applied")
            self.assertEqual(control.story_dock_state(), StoryDockVolumeState(65, 2))

    def test_first_managed_setting_creates_revisioned_volume_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"

            report = apply_story_dock_volume_settings(
                path,
                {"managed": True, "desired_percent": 60, "revision": 1},
            )

            self.assertEqual(report["volume_apply_status"], "applied")
            self.assertEqual(report["volume_applied_percent"], 60)
            self.assertEqual(report["volume_applied_revision"], 1)
            self.assertEqual(report["volume_attempted_revision"], 1)
            self.assertEqual(
                VolumeControl(path).story_dock_state(),
                StoryDockVolumeState(60, 1),
            )

    def test_story_dock_settings_reject_revision_regression_without_changing_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            control = VolumeControl(path)
            control.set_revisioned(70, 4)
            previous = path.read_bytes()

            report = apply_story_dock_volume_settings(
                path,
                {"managed": True, "desired_percent": 20, "revision": 3},
            )

            self.assertEqual(report["volume_apply_status"], "failed")
            self.assertEqual(report["volume_apply_reason"], "revision_regression")
            self.assertEqual(report["volume_applied_revision"], 4)
            self.assertEqual(report["volume_attempted_revision"], 3)
            self.assertEqual(path.read_bytes(), previous)

    def test_failed_managed_apply_reports_attempted_and_prior_applied_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"

            with patch.object(
                VolumeControl,
                "set_revisioned",
                side_effect=VolumeFileError("write_failed"),
            ):
                report = apply_story_dock_volume_settings(
                    path,
                    {"managed": True, "desired_percent": 60, "revision": 1},
                )

            self.assertEqual(report["volume_apply_status"], "failed")
            self.assertEqual(report["volume_apply_reason"], "write_failed")
            self.assertEqual(report["volume_applied_revision"], 0)
            self.assertEqual(report["volume_attempted_revision"], 1)

    def test_story_dock_settings_reject_corrupt_and_symlink_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corrupt = root / "corrupt.json"
            corrupt.write_text("{not json", encoding="utf-8")
            previous = corrupt.read_bytes()
            report = apply_story_dock_volume_settings(
                corrupt,
                {"managed": True, "desired_percent": 60, "revision": 1},
            )
            self.assertEqual(report["volume_apply_reason"], "volume_file_unreadable")
            self.assertEqual(corrupt.read_bytes(), previous)

            target = root / "target.json"
            target.write_text('{"volume_percent": 35}\n', encoding="utf-8")
            link = root / "volume.json"
            link.symlink_to(target)
            report = apply_story_dock_volume_settings(
                link,
                {"managed": True, "desired_percent": 60, "revision": 1},
            )
            self.assertEqual(report["volume_apply_reason"], "unsafe_volume_file")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["volume_percent"], 35)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_revisioned_volume_rejects_special_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            os.mkfifo(path)

            with self.assertRaises(VolumeFileError) as raised:
                VolumeControl(path).set_revisioned(60, 1)

            self.assertEqual(raised.exception.reason, "unsafe_volume_file")

    def test_readback_mismatch_restores_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            control = VolumeControl(path)
            control.set_revisioned(30, 1)
            previous = path.read_bytes()

            with patch.object(
                control,
                "story_dock_state",
                side_effect=[
                    StoryDockVolumeState(30, 1),
                    StoryDockVolumeState(31, 2),
                ],
            ):
                with self.assertRaises(VolumeFileError) as raised:
                    control.set_revisioned(60, 2)

            self.assertEqual(raised.exception.reason, "readback_mismatch")
            self.assertEqual(path.read_bytes(), previous)

    def test_unmanaged_settings_report_current_value_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"
            path.write_text('{"volume_percent": 55}\n', encoding="utf-8")
            previous = path.read_bytes()

            report = apply_story_dock_volume_settings(path, {"managed": False})

            self.assertEqual(report["volume_apply_status"], "unmanaged")
            self.assertEqual(report["volume_applied_percent"], 55)
            self.assertEqual(report["volume_applied_revision"], 0)
            self.assertEqual(report["volume_attempted_revision"], 0)
            self.assertEqual(path.read_bytes(), previous)

    def test_first_unmanaged_setting_reports_default_without_creating_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "volume.json"

            report = apply_story_dock_volume_settings(path, {"managed": False})

            self.assertEqual(report["volume_apply_status"], "unmanaged")
            self.assertEqual(report["volume_applied_percent"], 80)
            self.assertEqual(report["volume_applied_revision"], 0)
            self.assertEqual(report["volume_attempted_revision"], 0)
            self.assertFalse(path.exists())

    def test_apply_pipewire_volume_uses_wpctl_percent(self) -> None:
        completed = Mock(returncode=0, stderr=b"")
        with patch("magic_box.volume.shutil.which", return_value="/usr/bin/wpctl"):
            with patch("magic_box.volume.subprocess.run", return_value=completed) as run:
                self.assertTrue(apply_pipewire_volume(65))

        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["/usr/bin/wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@"])
        self.assertEqual(args[3], "0.650")

    def test_effective_output_volume_applies_ceiling(self) -> None:
        self.assertEqual(effective_output_volume(100, 75), 75)
        self.assertEqual(effective_output_volume(60, 75), 45)
        self.assertEqual(effective_output_volume(200, 75), 75)
        self.assertEqual(effective_output_volume(80, 75), 60)
        self.assertEqual(effective_output_volume(120, 75), 75)
        self.assertEqual(effective_output_volume(80, 120), 80)


if __name__ == "__main__":
    unittest.main()
