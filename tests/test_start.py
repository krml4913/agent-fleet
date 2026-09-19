"""Tests for ``fleet-agent start`` (dry-run path + a fake multiplexer backend)."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import prompt_pointer, state  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent, make_project  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_dry_run_creates_task_artifacts(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "7", "Add a new endpoint",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.state_dir / "tasks" / "task-7"
        self.assertTrue(tdir.is_dir())
        self.assertTrue((tdir / "task.yaml").is_file())
        self.assertTrue((tdir / "inbox.md").is_file())
        self.assertTrue((tdir / "outbox.md").is_file())
        self.assertTrue((tdir / "driver-prompt.md").is_file())
        text = (tdir / "task.yaml").read_text(encoding="utf-8")
        task_data = state.load_task(self.state_dir, "7")
        self.assertEqual(str(task_data["id"]), "7")
        self.assertIn("agent: claude:sonnet", text)
        # Task-level status is derived from the stages, not hardcoded: stage[0]
        # is "running" at birth, so the task is "running" — never the stale
        # "spawning" that used to be shown the entire time a solo task ran.
        self.assertIn("status: running", text)
        self.assertNotIn("status: spawning", text)
        self.assertIn("formation: solo", text)
        self.assertIn("stages:", text)
        self.assertIn("current_stage:", text)
        self.assertIsInstance(task_data.get("stages"), list)
        self.assertEqual(len(task_data["stages"]), 1)
        self.assertEqual(task_data["stages"][0]["status"], "running")
        self.assertEqual(
            task_data["stages"][0]["user_approval"],
            {"required": True, "status": "pending"},
        )
        # The persisted task status agrees with derive_task_status(stages).
        self.assertEqual(task_data["status"], "running")
        self.assertEqual(
            task_data["status"], state.derive_task_status(task_data["stages"])
        )
        events_path = self.state_dir / "events.jsonl"
        lines = [
            json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line
        ]
        starts = [e for e in lines if e.get("type") == "start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["task_id"], "7")
        self.assertTrue(starts[0].get("dry_run"))

    def test_prompt_file_reads_description(self) -> None:
        prompt_file = self.project / "task-prompt.md"
        prompt_text = "File prompt first line\nSecond line from file\n"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(prompt_file), "from-file",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.state_dir / "tasks" / "task-from-file"
        task_data = state.load_task(self.state_dir, "from-file")
        self.assertEqual(task_data["description"], prompt_text)
        self.assertEqual(task_data["title"], "File prompt first line")
        prompt = (tdir / "driver-prompt.md").read_text(encoding="utf-8")
        self.assertIn(prompt_text, prompt)
        lines = [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        starts = [e for e in lines if e.get("type") == "start"]
        self.assertEqual(starts[-1]["description"], prompt_text)

    def test_prompt_file_and_description_relegates_to_title(self) -> None:
        # Lenient (#151): both given is a common leader slip, not a hard error.
        # prompt-file wins the body; the positional description becomes the
        # title; a warn is printed.
        prompt_file = self.project / "task-prompt.md"
        prompt_text = "File prompt first line\nSecond line from file\n"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(prompt_file), "both", "Stray inline title",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warn:", result.stderr)
        self.assertIn("treating the positional description as the title", result.stderr)
        task_data = state.load_task(self.state_dir, "both")
        self.assertEqual(task_data["description"], prompt_text)
        self.assertEqual(task_data["title"], "Stray inline title")

    def test_prompt_file_and_description_explicit_title_wins(self) -> None:
        # --title still wins over the relegated positional description.
        prompt_file = self.project / "task-prompt.md"
        prompt_text = "File body\n"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(prompt_file), "--title", "Explicit T",
            "both-t", "Stray inline title",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "both-t")
        self.assertEqual(task_data["description"], prompt_text)
        self.assertEqual(task_data["title"], "Explicit T")

    def test_missing_description_and_prompt_file_is_error(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "missing-prompt",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("either description or --prompt-file", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-missing-prompt").exists())

    def test_missing_prompt_file_is_error(self) -> None:
        missing = self.project / "does-not-exist.md"

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(missing), "missing-file",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read --prompt-file", result.stderr)
        self.assertIn(str(missing), result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-missing-file").exists())

    def test_prompt_omits_git_workflow(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "no-git", "No git workflow here",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = (
            self.state_dir / "tasks" / "task-no-git" / "driver-prompt.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Git workflow", prompt)
        self.assertNotIn("gh pr create", prompt)

    def test_prompt_states_working_directory(self) -> None:
        # dry-run skips worktree creation, so the project root is stated.
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "wd", "Where do I work?",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = (
            self.state_dir / "tasks" / "task-wd" / "driver-prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Working directory:", prompt)
        self.assertIn("Work in the project root `", prompt)
        self.assertIn("Apart from it, never edit anything under", prompt)

    def test_rejects_duplicate_task_id(self) -> None:
        run_fleet_agent("start", "--project", "demo", "--dry-run",
                        "1", "first", fleet_home=self.fleet_home)
        result = run_fleet_agent("start", "--project", "demo", "--dry-run",
                                 "1", "second", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)

    def test_agent_override(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--agent", "codex:o4-mini", "2", "Do the codex thing",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.state_dir / "tasks" / "task-2" / "task.yaml").read_text(encoding="utf-8")
        self.assertIn("agent: codex:o4-mini", text)

    def test_codex_untrusted_repo_stops_before_task_creation(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
            task_id="codex-untrusted",
            description="Do the codex thing",
            formation="solo",
            agent="codex:o4-mini",
            title=None,
            dry_run=True,
            auto_paste=True,
            prompt_delay=0.0,
        )

        with (
            unittest.mock.patch("fleet.commands.start._git_toplevel", return_value=self.project),
            unittest.mock.patch(
                "fleet.commands.start.agents_mod.codex_repo_trusted",
                return_value=False,
            ),
            unittest.mock.patch("fleet.commands.start.workspace_mod.on_pre_start") as on_pre_start,
        ):
            result = start.run(args)

        self.assertEqual(result, 1)
        self.assertFalse(
            (self.state_dir / "tasks" / "task-codex-untrusted").exists()
        )
        on_pre_start.assert_not_called()

    def test_formation_pair_review_starts_first_stage(self) -> None:
        from fleet.commands import start

        global_formations = state.global_formations_dir()
        global_formations.mkdir(parents=True)
        (global_formations / "pair_review.yaml").write_text(
            "name: pair_review\n"
            "description: Implementer with AI peer review; the user has the final say.\n"
            "stages:\n"
            "  - role: implementer\n"
            "    agent: codex:gpt-5.5\n"
            "    peer_review:\n"
            "      role: code-reviewer\n"
            "      agent: claude:opus\n"
            "    user_approval: required\n", encoding="utf-8"
        )
        args = argparse.Namespace(
            project="demo",
            task_id="3",
            description="Pair flow",
            formation="pair_review",
            agent=None,
            title=None,
            dry_run=True,
            auto_paste=True,
            prompt_delay=0.0,
        )
        with unittest.mock.patch(
            "fleet.commands.start.agents_mod.codex_repo_trusted",
            return_value=True,
        ):
            result = start.run(args)
        self.assertEqual(result, 0)
        text = (self.state_dir / "tasks" / "task-3" / "task.yaml").read_text(encoding="utf-8")
        self.assertIn("role: implementer", text)
        self.assertIn("agent: codex:gpt-5.5", text)
        task_data = state.load_task(self.state_dir, "3")
        self.assertEqual(len(task_data["stages"]), 1)
        self.assertEqual(task_data["stages"][0]["role"], "implementer")
        self.assertIn("peer_review", task_data["stages"][0])
        self.assertEqual(task_data["stages"][0]["peer_review"]["role"], "code-reviewer")
        self.assertEqual(task_data["current_stage"], 0)
        self.assertEqual(task_data["stages"][0]["status"], "running")

    def test_role_flag_is_not_accepted(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--formation", "pair_review", "--role", "reviewer",
            "4", "Should fail",
            fleet_home=self.fleet_home,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized", result.stderr.lower())

    def test_missing_later_stage_role_aborts_before_task_creation(self) -> None:
        formation_path = self.state_dir / "formations" / "late-missing.yaml"
        formation_path.write_text(
            "name: late-missing\n"
            "stages:\n"
            "  - role: driver\n"
            "    agent: claude:sonnet\n"
            "  - role: no-such-role\n"
            "    agent: claude:sonnet\n",
            encoding="utf-8",
        )

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--formation", "late-missing",
            "late-role", "Should fail before stage 0 starts",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("no role named 'no-such-role'", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-late-role").exists())

    def test_no_project_found(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "nonexistent", "--dry-run",
            "9", "no state",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no registered project", result.stderr)

    def test_no_auto_paste_flag_is_accepted(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--no-auto-paste", "10", "Manual paste task",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.state_dir / "tasks" / "task-10"
        self.assertTrue(tdir.is_dir())

    def test_spawn_command_is_gone(self) -> None:
        result = run_fleet_agent(
            "spawn", "--project", "demo", "--dry-run",
            "99", "old command",
            fleet_home=self.fleet_home,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_title_with_colon_yaml_safe(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--title", "fix: handle edge case in parser",
            "11", "Fix the parser",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "11")
        self.assertEqual(task_data["title"], "fix: handle edge case in parser")

    def test_title_with_hash_yaml_safe(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--title", "issue #42: fix the bug",
            "12", "Fix it",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "12")
        self.assertEqual(task_data["title"], "issue #42: fix the bug")

    def test_description_with_colon_in_title_yaml_safe(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "13", "refactor: split into modules",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "13")
        self.assertEqual(task_data["title"], "refactor: split into modules")


class InferProjectFromPromptFileTests(unittest.TestCase):
    """Issue #150 (follow-up to #143): when --project is omitted and the
    --prompt-file lives under projects/<name>/, infer <name> and proceed instead
    of erroring on a cwd/prompt-file mismatch. The prompt-file path names the
    project unambiguously, so the #143 cross-project error is pure friction."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.other_repo = Path(self._tmp.name) / "bmweb-repo"
        self.other_repo.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        # cwd-resolves to "demo" (cwd under demo's repo).
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        # bmweb is a *registered* project whose tree differs from cwd.
        self.bmweb_dir = make_project(self.fleet_home, "bmweb", self.other_repo)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _bmweb_prompt(self) -> Path:
        """A prompt-file that physically lives under projects/bmweb/."""
        other_dir = self.fleet_home / "projects" / "bmweb" / "tasks"
        other_dir.mkdir(parents=True, exist_ok=True)
        pf = other_dir / "feature-prompt.md"
        pf.write_text("Build the feature pipeline.\n", encoding="utf-8")
        return pf

    def test_infers_project_from_prompt_file_and_proceeds(self) -> None:
        # Headline case: prompt-file under projects/bmweb/, no --project, run
        # from a cwd that resolves to "demo" → infer bmweb and land there.
        pf = self._bmweb_prompt()
        result = run_fleet_agent(
            "start", "--dry-run", "--prompt-file", str(pf), "feat",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.bmweb_dir / "tasks" / "task-feat").is_dir())
        # Did NOT land in the cwd-resolved "demo" project.
        self.assertFalse((self.state_dir / "tasks" / "task-feat").exists())

    def test_explicit_project_wins_over_inference(self) -> None:
        # Explicit --project foo wins even when the prompt-file is under bar/.
        pf = self._bmweb_prompt()
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(pf), "feat",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state_dir / "tasks" / "task-feat").is_dir())
        self.assertFalse((self.bmweb_dir / "tasks" / "task-feat").exists())

    def test_same_project_prompt_file_passes(self) -> None:
        own_dir = self.fleet_home / "projects" / "demo" / "tasks"
        own_dir.mkdir(parents=True, exist_ok=True)
        pf = own_dir / "own-prompt.md"
        pf.write_text("Do the demo work.\n", encoding="utf-8")
        result = run_fleet_agent(
            "start", "--dry-run", "--prompt-file", str(pf), "ownwork",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state_dir / "tasks" / "task-ownwork").is_dir())

    def test_prompt_file_outside_any_project_tree_uses_cwd(self) -> None:
        pf = self.project / "local-prompt.md"
        pf.write_text("Local prompt, not under any project tree.\n", encoding="utf-8")
        result = run_fleet_agent(
            "start", "--dry-run", "--prompt-file", str(pf), "localwork",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Falls back to cwd resolution → lands in "demo".
        self.assertTrue((self.state_dir / "tasks" / "task-localwork").is_dir())

    def test_inferred_unregistered_project_errors(self) -> None:
        # Prompt-file under projects/learn-xgboost/ that is NOT registered →
        # clear error, not a silent fall-back to the cwd "demo" project.
        unreg_dir = self.fleet_home / "projects" / "learn-xgboost" / "tasks"
        unreg_dir.mkdir(parents=True)
        pf = unreg_dir / "p.md"
        pf.write_text("Unregistered project prompt.\n", encoding="utf-8")
        result = run_fleet_agent(
            "start", "--dry-run", "--prompt-file", str(pf), "feat",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("learn-xgboost", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-feat").exists())

    def test_helper_infers_name_or_none(self) -> None:
        from fleet.commands import start

        projects_root = self.fleet_home / "projects"
        other = projects_root / "bmweb" / "tasks" / "p.md"
        same = projects_root / "demo" / "tasks" / "p.md"
        outside = self.project / "p.md"

        self.assertEqual(start._infer_project_from_promptfile(str(other)), "bmweb")
        self.assertEqual(start._infer_project_from_promptfile(str(same)), "demo")
        self.assertIsNone(start._infer_project_from_promptfile(str(outside)))


class StartAutopasteEnterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_auto_paste_starts_detached_deliverer(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
            task_id="200",
            description="auto-paste enter integration test",
            formation="solo",
            agent=None,
            title=None,
            dry_run=False,
            auto_paste=True,
            prompt_delay=0.0,
        )

        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake,
            unittest.mock.patch("fleet.commands.start.workspace_mod.on_pre_start"),
            unittest.mock.patch(
                "fleet.commands.start.prompt_deliverer.start_detached",
                return_value=self.state_dir / "tasks" / "task-200" / "prompt-deliverer.log",
            ) as mock_deliverer,
        ):
            result = start.run(args)

        self.assertEqual(result, 0)
        # start itself does not paste; the detached deliverer does, once the
        # pane is ready. start only pre-loads the pointer (not the prompt
        # body) into the named buffer for the no-auto-paste manual-paste path.
        self.assertEqual(fake.calls_named("paste"), [])
        (buffer_name, pointer), _kw = fake.calls_named("preload_paste")[0]
        self.assertEqual(buffer_name, "fleet-task-200")
        sidecar = self.state_dir / "tasks" / "task-200" / ".driver-prompt.md.paste-pointer"
        self.assertEqual(sidecar.read_text(encoding="utf-8"), pointer)
        prompt_path = self.state_dir / "tasks" / "task-200" / "driver-prompt.md"
        self.assertIn("Read the prompt file at this path", pointer)
        self.assertTrue(pointer.endswith(str(prompt_path.resolve())))
        self.assertEqual(pointer.count("\n"), 0)
        self.assertNotIn("auto-paste enter integration test", pointer)
        mock_deliverer.assert_called_once()
        self.assertEqual(mock_deliverer.call_args.kwargs["task_id"], "200")
        self.assertEqual(mock_deliverer.call_args.kwargs["agent_spec"], "claude:sonnet")
        self.assertNotIn("buffer_name", mock_deliverer.call_args.kwargs)

    def test_no_auto_paste_skips_paste_and_enter(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
            task_id="201",
            description="no auto-paste test",
            formation="solo",
            agent=None,
            title=None,
            dry_run=False,
            auto_paste=False,
            prompt_delay=0.0,
        )

        buf = io.StringIO()
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake,
            unittest.mock.patch("fleet.commands.start.workspace_mod.on_pre_start"),
            contextlib.redirect_stdout(buf),
        ):
            result = start.run(args)

        self.assertEqual(result, 0)
        self.assertEqual(fake.calls_named("paste"), [])
        self.assertEqual(len(fake.calls_named("preload_paste")), 1)
        prompt_path = self.state_dir / "tasks" / "task-201" / "driver-prompt.md"
        (buffer_name, pointer), _kw = fake.calls_named("preload_paste")[0]
        self.assertEqual(buffer_name, "fleet-task-201")
        self.assertEqual(
            prompt_pointer.pointer_path(prompt_path).read_text(encoding="utf-8"), pointer
        )
        self.assertIn(str(prompt_path.resolve()), pointer)
        out = buf.getvalue()
        # Manual-paste instructions come from the backend (tmux: C-b ]).
        self.assertIn("paste pointer: inside the pane press C-b ], then Enter", out)
        self.assertIn("or: fleet-agent send-prompt 201", out)
        self.assertIn("attach:        tmux attach -t fleet-main:201·driver", out)


class LaunchStageDriverWindowCollisionTests(unittest.TestCase):
    """Verify launch_stage_driver cleans old task windows before creating a new one."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = self.project / ".fleet-state"
        state.init_state(self.state_dir, name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_task_dir(self, task_id: str) -> Path:
        task_dir = self.state_dir / "tasks" / f"task-{task_id}"
        task_dir.mkdir(parents=True)
        (task_dir / "driver-prompt.md").write_text("prompt content", encoding="utf-8")
        return task_dir

    def test_kill_task_windows_called_before_new_window(self) -> None:
        """Existing windows for the task must be killed before new_window."""
        from fleet.commands import start

        task_id = "multiproject"
        task_dir = self._make_task_dir(task_id)
        with use_fake_mux(
            sessions={"fleet-main": ["leader", f"{task_id}·driver"]}
        ) as fake:
            result = start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "driver"},
                project_name="demo",
                owner_session="main",
                auto_paste=False,
                prompt_delay=0.0,
            )

        self.assertEqual(result, 0)
        names = fake.method_names()
        self.assertIn("kill_window", names, "old task window was not killed")
        self.assertIn("new_window", names, "new_window was not called")
        # kill must precede new_window
        self.assertLess(
            names.index("kill_window"),
            names.index("new_window"),
            "task windows must be killed before new_window",
        )
        (_name, pointer), _kw = fake.calls_named("preload_paste")[0]
        loaded_path = prompt_pointer.pointer_path(task_dir / "driver-prompt.md")
        self.assertEqual(loaded_path.read_text(encoding="utf-8"), pointer)
        self.assertIn(str((task_dir / "driver-prompt.md").resolve()), pointer)
        self.assertNotIn("prompt content", pointer)

    def test_launch_uses_task_id_and_role_window_name(self) -> None:
        """new_window uses <task-id>·<role> and cleanup targets the task id."""
        from fleet.commands import start

        task_id = "mytask"
        task_dir = self._make_task_dir(task_id)

        with use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake:
            start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "implementer"},
                project_name="demo",
                owner_session="main",
                auto_paste=False,
                prompt_delay=0.0,
            )

        # The session is the OWNER SESSION (fleet-<owner_session>), not fleet-<project>.
        self.assertEqual(fake.calls_named("list_windows"), [(("fleet-main",), {})])
        (session, window), kwargs = fake.calls_named("new_window")[0]
        self.assertEqual((session, window), ("fleet-main", f"{task_id}·implementer"))
        # argv goes to the backend (no shell quoting at this layer); env + cwd too.
        self.assertEqual(kwargs["argv"][0], "claude")
        self.assertEqual(kwargs["cwd"], str(task_dir))
        self.assertEqual(kwargs["env"]["FLEET_TASK_ID"], task_id)
        self.assertEqual(kwargs["env"]["FLEET_STATE_DIR"], str(self.state_dir))

    def test_codex_launch_disables_update_prompt(self) -> None:
        from fleet.commands import start

        task_id = "codex-update"
        task_dir = self._make_task_dir(task_id)

        with use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake:
            start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "codex:gpt-5.5", "role": "implementer"},
                project_name="demo",
                owner_session="main",
                auto_paste=False,
                prompt_delay=0.0,
            )

        argv = fake.calls_named("new_window")[0][1]["argv"]
        self.assertIn("check_for_update_on_startup=false", argv)

    def test_claude_launch_sets_session_name(self) -> None:
        """claude cli gets --name <project>-<task_id>-<role> at launch."""
        from fleet.commands import start

        task_id = "mytask"
        task_dir = self._make_task_dir(task_id)

        with use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake:
            start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "driver"},
                project_name="demo",
                owner_session="main",
                auto_paste=False,
                prompt_delay=0.0,
            )

        argv = fake.calls_named("new_window")[0][1]["argv"]
        self.assertEqual(argv[-2:], ["--name", "demo-mytask-driver"])

    def test_auto_paste_threads_session_name_to_deliverer(self) -> None:
        """The detached deliverer receives the computed session name."""
        from fleet.commands import start

        task_id = "namethread"
        task_dir = self._make_task_dir(task_id)

        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}),
            unittest.mock.patch(
                "fleet.commands.start.prompt_deliverer.start_detached",
                return_value=task_dir / "prompt-deliverer.log",
            ) as mock_deliverer,
        ):
            start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "codex:gpt-5.5", "role": "implementer"},
                project_name="demo",
                owner_session="main",
                auto_paste=True,
                prompt_delay=0.0,
            )

        self.assertEqual(
            mock_deliverer.call_args.kwargs["session_name"],
            "demo-namethread-implementer",
        )

    def test_launch_succeeds_even_when_window_already_exists(self) -> None:
        """Simulates stage transition: new_window does not fail even if a stale window exists."""
        from fleet.commands import start

        task_id = "stage-transition"
        task_dir = self._make_task_dir(task_id)

        # A stale window from the previous stage is still open.
        with use_fake_mux(
            sessions={"fleet-main": ["leader", f"{task_id}·designer"]}
        ) as fake:
            result = start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=1,
                stage={"agent": "claude:sonnet", "role": "implementer"},
                project_name="demo",
                owner_session="main",
                auto_paste=False,
                prompt_delay=0.0,
            )

        self.assertEqual(result, 0)
        # The session is the OWNER SESSION (fleet-<owner_session>), not fleet-<project>.
        self.assertEqual(
            fake.calls_named("kill_window"), [(("fleet-main", f"{task_id}·designer"), {})]
        )
        self.assertEqual(len(fake.calls_named("new_window")), 1)
        self.assertEqual(fake.sessions["fleet-main"], ["leader", f"{task_id}·implementer"])

    def test_launch_can_preserve_existing_task_windows(self) -> None:
        from fleet.commands import start

        task_id = "keep-live"
        task_dir = self._make_task_dir(task_id)

        with use_fake_mux(
            sessions={"fleet-main": ["leader", f"{task_id}·implementer"]}
        ) as fake:
            result = start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "code-reviewer"},
                project_name="demo",
                owner_session="main",
                auto_paste=False,
                prompt_delay=0.0,
                replace_task_windows=False,
            )

        self.assertEqual(result, 0)
        self.assertEqual(fake.calls_named("kill_window"), [])
        self.assertEqual(fake.calls_named("list_windows"), [])
        self.assertEqual(len(fake.calls_named("new_window")), 1)


class ResolveOwnerSessionTests(unittest.TestCase):
    """Pure ``_resolve_owner_session``: --session > FLEET_SESSION env > 'main'."""

    def _args(self, session=None) -> argparse.Namespace:
        return argparse.Namespace(session=session)

    def test_default_main_when_nothing_set(self) -> None:
        from fleet.commands import start

        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLEET_SESSION", None)
            self.assertEqual(start._resolve_owner_session(self._args()), "main")

    def test_env_fleet_session_used(self) -> None:
        from fleet.commands import start

        with unittest.mock.patch.dict(os.environ, {"FLEET_SESSION": "migration"}, clear=False):
            self.assertEqual(start._resolve_owner_session(self._args()), "migration")

    def test_explicit_session_overrides_env(self) -> None:
        from fleet.commands import start

        with unittest.mock.patch.dict(os.environ, {"FLEET_SESSION": "migration"}, clear=False):
            self.assertEqual(
                start._resolve_owner_session(self._args(session="hotfix")), "hotfix"
            )

    def test_empty_env_falls_back_to_main(self) -> None:
        from fleet.commands import start

        with unittest.mock.patch.dict(os.environ, {"FLEET_SESSION": ""}, clear=False):
            self.assertEqual(start._resolve_owner_session(self._args()), "main")


class StampOwnerSessionTests(unittest.TestCase):
    """``fleet-agent start`` stamps ``owner_session`` onto task.yaml."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_default_main(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "o1", "work",
            fleet_home=self.fleet_home, env_extra={"FLEET_SESSION": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.load_task(self.state_dir, "o1")["owner_session"], "main")

    def test_from_env_fleet_session(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "o2", "work",
            fleet_home=self.fleet_home, env_extra={"FLEET_SESSION": "migration"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            state.load_task(self.state_dir, "o2")["owner_session"], "migration"
        )

    def test_session_flag_overrides_env(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "--session", "hotfix",
            "o3", "work",
            fleet_home=self.fleet_home, env_extra={"FLEET_SESSION": "migration"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.load_task(self.state_dir, "o3")["owner_session"], "hotfix")


class ValidateTaskIdTests(unittest.TestCase):
    """Unit tests for the pure ``_validate_task_id`` slug validator."""

    def test_valid_ids_return_none(self) -> None:
        from fleet.commands import start

        valid = [
            "a",
            "7",
            "task1",
            "validate-task-id",
            "fix-the-bug",
            "abc-123-def",
            "a1b2c3",
            "x" * 24,  # exactly at the max length
        ]
        for task_id in valid:
            with self.subTest(task_id=task_id):
                self.assertIsNone(start._validate_task_id(task_id))

    def test_invalid_shape_returns_kebab_error(self) -> None:
        from fleet.commands import start

        invalid = [
            "",  # empty
            "-leading",
            "trailing-",
            "double--hyphen",
            "Upper",
            "has space",
            "under_score",
            "sym!bol",
            "слон",  # non-ascii
        ]
        for task_id in invalid:
            with self.subTest(task_id=task_id):
                msg = start._validate_task_id(task_id)
                self.assertIsNotNone(msg)
                self.assertIn("kebab-case", msg)
                self.assertIn(task_id, msg)

    def test_too_long_returns_length_error(self) -> None:
        from fleet.commands import start

        task_id = "a" * 25  # one over the max
        msg = start._validate_task_id(task_id)
        self.assertIsNotNone(msg)
        self.assertIn("too long", msg)
        self.assertIn("25 chars", msg)
        self.assertIn("max 24", msg)
        self.assertIn(task_id, msg)

    def test_shape_checked_before_length(self) -> None:
        from fleet.commands import start

        # Too long AND malformed (uppercase) — shape error wins.
        task_id = "A" * 25
        msg = start._validate_task_id(task_id)
        self.assertIsNotNone(msg)
        self.assertIn("kebab-case", msg)


class StartTaskIdValidationTests(unittest.TestCase):
    """``start`` rejects invalid task ids before creating any state."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_rejects_malformed_task_id(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "Bad_ID", "some work",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("kebab-case", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-Bad_ID").exists())

    def test_rejects_too_long_task_id(self) -> None:
        long_id = "a" * 25
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            long_id, "some work",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("too long", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / f"task-{long_id}").exists())

    def test_accepts_valid_kebab_task_id(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "valid-task-id", "some work",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state_dir / "tasks" / "task-valid-task-id").is_dir())

    # --- scope guard ---

    def test_scope_guard_blocks_out_of_scope_project(self) -> None:
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        make_project(self.fleet_home, "other", other)
        # Set scope to only "demo"; dispatch to "other" should fail
        from fleet import state
        state.set_session_scope("main", ["demo"], mode="set")
        result = run_fleet_agent(
            "start", "--project", "other", "--dry-run",
            "t1", "blocked",
            fleet_home=self.fleet_home,
            env_extra={"FLEET_SESSION": "main"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside", result.stderr.lower())

    def test_scope_guard_allows_in_scope_project(self) -> None:
        from fleet import state
        state.set_session_scope("main", ["demo"], mode="set")
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "t2", "allowed",
            fleet_home=self.fleet_home,
            env_extra={"FLEET_SESSION": "main"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_guard_unscoped_no_effect(self) -> None:
        # No scope set; dispatch should pass
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "t3", "no scope check",
            fleet_home=self.fleet_home,
            env_extra={"FLEET_SESSION": "main"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_guard_allow_out_of_scope_flag(self) -> None:
        other = Path(self._tmp.name) / "other2"
        other.mkdir()
        make_project(self.fleet_home, "other2", other)
        from fleet import state
        state.set_session_scope("main", ["demo"], mode="set")
        result = run_fleet_agent(
            "start", "--project", "other2", "--dry-run", "--allow-out-of-scope",
            "t4", "override",
            fleet_home=self.fleet_home,
            env_extra={"FLEET_SESSION": "main"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_guard_runs_in_dry_run(self) -> None:
        other = Path(self._tmp.name) / "other3"
        other.mkdir()
        make_project(self.fleet_home, "other3", other)
        from fleet import state
        state.set_session_scope("main", ["demo"], mode="set")
        result = run_fleet_agent(
            "start", "--project", "other3", "--dry-run",
            "t5", "dry-run guard",
            fleet_home=self.fleet_home,
            env_extra={"FLEET_SESSION": "main"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("scope", result.stderr)


if __name__ == "__main__":
    unittest.main()
