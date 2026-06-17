"""Tests for the global (cross-project) leader-memory layer — Issue #166 Phase 1.

Covers the ``global/`` paths helper and the idempotent scaffolder for
``global/leader-memory/`` (design doc §5.3, §6).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from tests._fleet_test_helpers import run_fleet  # noqa: E402
from fleet import state  # noqa: E402


class _FleetHomeBase(unittest.TestCase):
    """Base: isolates FLEET_HOME to a tempdir for every test."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()


class GlobalPathTests(_FleetHomeBase):
    # fleet_home() resolves symlinks (.resolve()); compare against resolved paths.
    def test_global_dir_under_fleet_home(self) -> None:
        self.assertEqual(state.global_dir(), self.fleet_home.resolve() / "global")

    def test_leader_memory_dir(self) -> None:
        self.assertEqual(
            state.global_leader_memory_dir(),
            self.fleet_home.resolve() / "global" / "leader-memory",
        )

    def test_sessions_dir_reserved(self) -> None:
        # Reserved path constant for later phases — resolvable, not built.
        self.assertEqual(
            state.global_sessions_dir(),
            self.fleet_home.resolve() / "global" / "sessions",
        )

    def test_paths_honor_fleet_home_override(self) -> None:
        # Repoint FLEET_HOME and the helpers must follow it (no caching).
        other = Path(self._tmp.name) / "other-home"
        os.environ["FLEET_HOME"] = str(other)
        try:
            self.assertEqual(
                state.global_leader_memory_dir(),
                other.resolve() / "global" / "leader-memory",
            )
        finally:
            os.environ["FLEET_HOME"] = str(self.fleet_home)


class EnsureScaffoldTests(_FleetHomeBase):
    def test_creates_memory_dir(self) -> None:
        returned = state.ensure_global_leader_memory()
        mem_dir = self.fleet_home.resolve() / "global" / "leader-memory"
        self.assertTrue(mem_dir.is_dir())
        self.assertEqual(returned, mem_dir)

    def test_creates_index_stub(self) -> None:
        state.ensure_global_leader_memory()
        index = self.fleet_home / "global" / "leader-memory" / "MEMORY.md"
        self.assertTrue(index.is_file())
        content = index.read_text(encoding="utf-8")
        self.assertIn("Leader Memory Index", content)

    def test_creates_guide(self) -> None:
        state.ensure_global_leader_memory()
        guide = self.fleet_home / "global" / "leader-memory" / "GUIDE.md"
        self.assertTrue(guide.is_file())
        content = guide.read_text(encoding="utf-8")
        # Global tier carries the user-global / router-rule discipline.
        self.assertIn("Global Leader Memory", content)
        self.assertIn("user", content)
        self.assertIn("Router operating rules", content)

    def test_guide_documents_split_axis(self) -> None:
        # The split axis (global = relate-to-user; per-project = project output)
        # is what keeps the two tiers from colliding — assert it is present.
        state.ensure_global_leader_memory()
        guide = self.fleet_home / "global" / "leader-memory" / "GUIDE.md"
        content = guide.read_text(encoding="utf-8")
        self.assertIn("per-project", content)

    def test_does_not_create_sessions_dir(self) -> None:
        # Phase 1 reserves global/sessions/ but must not build session state.
        state.ensure_global_leader_memory()
        self.assertFalse(state.global_sessions_dir().exists())

    def test_idempotent_second_call_no_clobber(self) -> None:
        state.ensure_global_leader_memory()
        mem_dir = self.fleet_home / "global" / "leader-memory"
        index = mem_dir / "MEMORY.md"
        guide = mem_dir / "GUIDE.md"

        # Simulate operator edits: a saved memory in the index, plus a new file.
        edited_index = "# Leader Memory Index (global)\n\n- [Tone](user_tone.md) — terse\n"
        index.write_text(edited_index, encoding="utf-8")
        guide.write_text("EDITED GUIDE\n", encoding="utf-8")
        (mem_dir / "user_tone.md").write_text("custom memory\n", encoding="utf-8")

        # Second call must not clobber any existing content.
        state.ensure_global_leader_memory()
        self.assertEqual(index.read_text(encoding="utf-8"), edited_index)
        self.assertEqual(guide.read_text(encoding="utf-8"), "EDITED GUIDE\n")
        self.assertEqual(
            (mem_dir / "user_tone.md").read_text(encoding="utf-8"), "custom memory\n"
        )

    def test_idempotent_repeated_calls_dont_raise(self) -> None:
        for _ in range(3):
            state.ensure_global_leader_memory()
        self.assertTrue((self.fleet_home / "global" / "leader-memory" / "GUIDE.md").is_file())


class InitWiringTests(unittest.TestCase):
    """`fleet init` is the project-agnostic wiring point for the global store."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_init_scaffolds_global_store(self) -> None:
        result = run_fleet(
            "init", "--name", "gl-test", str(self.project),
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        mem_dir = self.fleet_home / "global" / "leader-memory"
        self.assertTrue((mem_dir / "MEMORY.md").is_file())
        self.assertTrue((mem_dir / "GUIDE.md").is_file())
        # The global store is a sibling of projects/, not under the project.
        self.assertFalse(
            (self.fleet_home / "projects" / "gl-test" / "global").exists()
        )

    def test_second_init_preserves_global_content(self) -> None:
        run_fleet("init", "--name", "p1", str(self.project), fleet_home=self.fleet_home)
        index = self.fleet_home / "global" / "leader-memory" / "MEMORY.md"
        edited = "# Leader Memory Index (global)\n\n- [Tone](user_tone.md) — terse\n"
        index.write_text(edited, encoding="utf-8")

        proj2 = Path(self._tmp.name) / "proj2"
        proj2.mkdir()
        result = run_fleet("init", "--name", "p2", str(proj2), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        # A later init must not clobber the operator-edited global index.
        self.assertEqual(index.read_text(encoding="utf-8"), edited)


if __name__ == "__main__":
    unittest.main()
