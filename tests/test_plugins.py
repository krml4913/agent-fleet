"""Tests for ``fleet.plugins`` — load_workflow / list / run_hook / custom override."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import plugins, state  # noqa: E402


class PluginsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        self.state_dir = self.project / ".fleet-state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list_builtin_has_bare_and_git_worktree(self) -> None:
        names = set(plugins.list_builtin())
        self.assertIn("bare", names)
        self.assertIn("git_worktree", names)

    def test_default_workflow_is_bare(self) -> None:
        module = plugins.load_workflow(self.state_dir)
        self.assertEqual(getattr(module, "WORKFLOW_NAME", None), "bare")

    def test_workflow_field_picks_plugin(self) -> None:
        project = state.load_project(self.state_dir)
        project["workflow"] = "git_worktree"
        state.save_project(self.state_dir, project)
        module = plugins.load_workflow(self.state_dir)
        self.assertEqual(module.WORKFLOW_NAME, "git_worktree")

    def test_run_hook_no_op_when_missing(self) -> None:
        module = plugins.load_workflow(self.state_dir)
        # bare has on_pre_spawn / on_post_done; nonexistent hook returns None.
        self.assertIsNone(plugins.run_hook(module, "on_does_not_exist", {}))

    def test_run_hook_calls_function(self) -> None:
        module = plugins.load_workflow(self.state_dir)
        # bare hooks accept ctx and return None.
        self.assertIsNone(plugins.run_hook(module, "on_pre_spawn", {"x": 1}))

    def test_custom_plugin_overrides_builtin(self) -> None:
        custom_dir = self.state_dir / plugins.PROJECT_PLUGIN_SUBDIR
        custom_dir.mkdir()
        (custom_dir / "bare.py").write_text(
            'WORKFLOW_NAME = "bare-custom"\n'
            "def on_pre_spawn(ctx):\n"
            "    ctx['seen'] = True\n"
        )
        module = plugins._resolve("bare", self.state_dir)
        self.assertEqual(module.WORKFLOW_NAME, "bare-custom")
        ctx: dict = {}
        plugins.run_hook(module, "on_pre_spawn", ctx)
        self.assertTrue(ctx["seen"])

    def test_list_custom(self) -> None:
        custom_dir = self.state_dir / plugins.PROJECT_PLUGIN_SUBDIR
        custom_dir.mkdir()
        (custom_dir / "alpha.py").write_text("")
        (custom_dir / "beta.py").write_text("")
        (custom_dir / "_skipme.py").write_text("")
        self.assertEqual(plugins.list_custom(self.state_dir), ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
