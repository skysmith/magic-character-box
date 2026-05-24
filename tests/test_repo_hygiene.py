import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_STORY_DOCK_PATHS = [
    "api/index.py",
    "hosted/README.md",
    "site/index.html",
    "docs/story-album/README.md",
    "docs/marketing/README.md",
    "docs/manufacturing/README.md",
    "docs/store-pilot/README.md",
    "src/story_dock_hosted/app.py",
    "tests/test_hosted_factory.py",
    "tests/test_supabase_adapters.py",
    "tests/test_sticker_manifest.py",
    "tests/test_vercel_deploy_script.py",
    "cad/story-dock-concept.scad",
    "cad/build_story_dock_concept.sh",
    "vercel.json",
    ".vercel/project.json",
    ".vercelignore",
]

PRIVATE_TRACKED_PREFIXES = [
    "api/",
    "hosted/",
    "site/",
    "docs/story-album/",
    "docs/marketing/",
    "docs/manufacturing/",
    "docs/store-pilot/",
    "src/story_dock_hosted/",
]

PRIVATE_TRACKED_FILES = {
    "cad/story-dock-concept.scad",
    "cad/build_story_dock_concept.sh",
    "vercel.json",
    ".vercelignore",
}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class RepoHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = _git("rev-parse", "--is-inside-work-tree")
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise unittest.SkipTest("repo hygiene checks require a git worktree")

    def test_private_story_dock_paths_are_gitignored(self) -> None:
        for rel_path in PRIVATE_STORY_DOCK_PATHS:
            with self.subTest(path=rel_path):
                result = _git("check-ignore", "--quiet", rel_path)
                self.assertEqual(result.returncode, 0, msg=f"{rel_path} should be gitignored")

    def test_private_story_dock_paths_are_not_tracked(self) -> None:
        result = _git("ls-files")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        offenders = []
        for rel_path in result.stdout.splitlines():
            if rel_path in PRIVATE_TRACKED_FILES:
                offenders.append(rel_path)
                continue
            if rel_path.startswith(tuple(PRIVATE_TRACKED_PREFIXES)):
                offenders.append(rel_path)

        self.assertEqual(offenders, [], msg="Private Story Dock paths are tracked: " + ", ".join(offenders))
