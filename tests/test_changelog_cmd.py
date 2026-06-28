"""Tests for ``fleet changelog``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet.commands.changelog import assemble_changelog  # noqa: E402
from tests._fleet_test_helpers import run_fleet  # noqa: E402


class ChangelogCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.fragments = self.repo / "changelog.d"
        self.fragments.mkdir()
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n"
            "Intro.\n\n"
            "## [Unreleased]\n\n"
            "### fix: existing unreleased entry\n\n"
            "Keep me.\n\n"
            "## [0.1.0] - 2026-06-01\n\n"
            "Initial release.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_fragment(self, name: str, body: str) -> None:
        (self.fragments / name).write_text(body, encoding="utf-8")

    def read_changelog(self) -> str:
        return (self.repo / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_assembles_fragments_under_unreleased_and_deletes_them(self) -> None:
        self.write_fragment("b-task.md", "### fix: beta\n\nBeta details.\n")
        self.write_fragment("a-task.md", "### feat: alpha\n\nAlpha details.\n")

        result = assemble_changelog(repo=self.repo)

        self.assertEqual(result.fragment_count, 2)
        self.assertEqual(result.heading, "## [Unreleased]")
        text = self.read_changelog()
        self.assertLess(text.index("### feat: alpha"), text.index("### fix: beta"))
        self.assertLess(
            text.index("### fix: beta"),
            text.index("### fix: existing unreleased entry"),
        )
        self.assertIn("## [0.1.0] - 2026-06-01", text)
        self.assertEqual(list(self.fragments.glob("*.md")), [])

    def test_assembles_fragments_into_versioned_section_and_deletes_them(self) -> None:
        self.write_fragment("task.md", "### change: release note\n\nDetails.\n")

        result = assemble_changelog(
            repo=self.repo,
            version="0.2.0",
            date="2026-06-29",
        )

        self.assertEqual(result.fragment_count, 1)
        self.assertEqual(result.heading, "## [0.2.0] - 2026-06-29")
        text = self.read_changelog()
        self.assertLess(
            text.index("### fix: existing unreleased entry"),
            text.index("## [0.2.0] - 2026-06-29"),
        )
        self.assertLess(
            text.index("## [0.2.0] - 2026-06-29"),
            text.index("## [0.1.0] - 2026-06-01"),
        )
        self.assertIn("### change: release note", text)
        self.assertEqual(list(self.fragments.glob("*.md")), [])

    def test_cli_reports_no_fragments_without_modifying_changelog(self) -> None:
        before = self.read_changelog()

        r = run_fleet("changelog", "--repo", str(self.repo), cwd=self.repo)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no changelog fragments found", r.stdout)
        self.assertEqual(self.read_changelog(), before)

    def test_cli_assembles_fragment(self) -> None:
        self.write_fragment("task.md", "### test: cli path\n\nDetails.\n")

        r = run_fleet("changelog", "--repo", str(self.repo), cwd=self.repo)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("assembled 1 fragment", r.stdout)
        self.assertIn("### test: cli path", self.read_changelog())
        self.assertEqual(list(self.fragments.glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
