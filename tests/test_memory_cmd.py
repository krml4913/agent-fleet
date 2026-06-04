"""Tests for ``fleet-agent memory <list|read|write>`` (Issue #114)."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet-agent"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402

_FIXTURE = """\
---
name: testing-rule
description: integration tests must use real state files
metadata:
  type: feedback
---

Don't test with mocked state.
"""


class MemoryCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        self.memory_dir = self.state_dir / "memory"
        # init_state already created memory/ with MEMORY.md + GUIDE.md.
        (self.memory_dir / "testing-rule.md").write_text(_FIXTURE, encoding="utf-8")
        index = self.memory_dir / "MEMORY.md"
        index.write_text(
            index.read_text(encoding="utf-8")
            + "\n- [testing-rule](testing-rule.md) — integration tests must use real state files\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("FLEET_TASK_ID", None)
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        return subprocess.run(
            [sys.executable, str(FLEET), *args],
            capture_output=True, text=True, cwd=str(self.project), env=env,
            input=stdin,
        )

    def test_list_shows_entry_with_description(self) -> None:
        r = self._run("memory", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("testing-rule — integration tests must use real state files", r.stdout)
        # Reserved index/guide files are not listed as entries.
        self.assertNotIn("MEMORY", r.stdout)
        self.assertNotIn("GUIDE", r.stdout)

    def test_read_prints_file_contents(self) -> None:
        r = self._run("memory", "read", "testing-rule")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Don't test with mocked state.", r.stdout)
        self.assertIn("name: testing-rule", r.stdout)

    def test_read_accepts_dot_md_suffix(self) -> None:
        r = self._run("memory", "read", "testing-rule.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Don't test with mocked state.", r.stdout)

    def test_read_missing_errors(self) -> None:
        r = self._run("memory", "read", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no memory named", r.stderr)

    def test_read_rejects_path_traversal(self) -> None:
        r = self._run("memory", "read", "../project")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid memory name", r.stderr)

    def test_write_creates_file_and_index(self) -> None:
        r = self._run(
            "memory", "write", "release-note",
            "--description", "freeze begins 2026-06-10",
            "--type", "project",
            stdin="Merge freeze for the release cut.",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        written = (self.memory_dir / "release-note.md").read_text(encoding="utf-8")
        self.assertIn("name: release-note", written)
        self.assertIn("description: freeze begins 2026-06-10", written)
        self.assertIn("type: project", written)
        self.assertIn("Merge freeze for the release cut.", written)
        index = (self.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("- [release-note](release-note.md) — freeze begins 2026-06-10", index)

    def test_write_updates_existing_index_line_in_place(self) -> None:
        r = self._run(
            "memory", "write", "testing-rule",
            "--description", "now with a sharper summary",
            "--type", "feedback",
            stdin="Updated body.",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        # Exactly one index line for the slug, carrying the new description.
        matches = [l for l in index.splitlines() if "](testing-rule.md)" in l]
        self.assertEqual(len(matches), 1, index)
        self.assertIn("now with a sharper summary", matches[0])
        self.assertNotIn("integration tests must use real state files", index)

    def test_write_body_from_file(self) -> None:
        body_file = Path(self._tmp.name) / "body.txt"
        body_file.write_text("Body sourced from a file.", encoding="utf-8")
        r = self._run(
            "memory", "write", "from-file",
            "--description", "d", "--body-file", str(body_file),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        written = (self.memory_dir / "from-file.md").read_text(encoding="utf-8")
        self.assertIn("Body sourced from a file.", written)

    def test_write_rejects_unsafe_name(self) -> None:
        r = self._run("memory", "write", "../evil", "--description", "d", stdin="x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid memory name", r.stderr)


if __name__ == "__main__":
    unittest.main()
