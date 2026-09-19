"""Tests for ``fleet leader`` — project-agnostic session entrypoint (Issue #166)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import prompt_pointer, state  # noqa: E402
from fleet.mux.base import Key  # noqa: E402
from fleet.mux.tmux import TmuxMux  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402
from tests._fleet_test_helpers import run_fleet, requires_live_tmux  # noqa: E402


class LeaderCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        # Use an isolated, randomized label so real-tmux tests never collide
        # with a live leader session. The production default label is "main"
        # → session "fleet-main"; if a test used it literally, tearDown's
        # kill_session would terminate the running leader (the strict default
        # is verified separately in test_default_session_label_is_main).
        self.label = "test-" + os.urandom(3).hex()
        self.session = f"fleet-{self.label}"
        self.tmux = TmuxMux()

    def tearDown(self) -> None:
        if shutil.which("tmux") and self.tmux.session_exists(self.session):
            self.tmux.kill_session(self.session)
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_default_session_label_is_main(self) -> None:
        # The production default (no --name) is label "main" → session
        # "fleet-main". Verified here without spawning a real session so the
        # live-tmux tests can stay on isolated, randomized labels.
        from fleet.commands import leader

        self.assertEqual(leader.DEFAULT_SESSION_LABEL, "main")

    @requires_live_tmux
    def test_launch_creates_session_and_emits_event(self) -> None:
        r = run_fleet("leader", "--name", self.label, fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.tmux.session_exists(self.session))
        events_path = state.session_dir(self.label) / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l]
        self.assertTrue(any(e["type"] == "leader_start" for e in events))
        self.assertTrue(any(e.get("label") == self.label for e in events))

    @requires_live_tmux
    def test_launch_writes_session_record(self) -> None:
        r = run_fleet("leader", "--name", self.label, "--agent", "claude:opus",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        record_path = state.session_record_path(self.label)
        self.assertTrue(record_path.exists(), "session.json was not written")
        data = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(data["agent"], "claude:opus")
        self.assertEqual(data["label"], self.label)
        self.assertIn("started_at", data)
        self.assertEqual(data["pane"], f"{self.session}:leader")
        # Relocated: nothing left under a per-project leader-session.json.
        self.assertFalse((self.fleet_home / "projects").exists())

    @requires_live_tmux
    def test_existing_session_is_idempotent(self) -> None:
        r1 = run_fleet("leader", "--name", self.label, fleet_home=self.fleet_home)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = run_fleet("leader", "--name", self.label, fleet_home=self.fleet_home)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("already exists", r2.stdout)

    @requires_live_tmux
    def test_launch_writes_leader_prompt(self) -> None:
        r = run_fleet("leader", "--name", self.label, "--prompt-delay", "0",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        prompt_path = state.session_dir(self.label) / "leader-prompt.md"
        self.assertTrue(prompt_path.exists(), "leader-prompt.md was not written")
        content = prompt_path.read_text(encoding="utf-8")
        self.assertIn("You are a fleet leader session", content)

    @requires_live_tmux
    def test_custom_label_session(self) -> None:
        label = "test-" + os.urandom(3).hex()
        session = f"fleet-{label}"
        try:
            r = run_fleet("leader", "--name", label, fleet_home=self.fleet_home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(self.tmux.session_exists(session))
            self.assertTrue(state.session_record_path(label).exists())
        finally:
            if self.tmux.session_exists(session):
                self.tmux.kill_session(session)

    def test_no_auto_paste_skips_prompt_file(self) -> None:
        run_fleet("leader", "--name", self.label, "--no-auto-paste",
                  fleet_home=self.fleet_home)
        prompt_path = state.session_dir(self.label) / "leader-prompt.md"
        self.assertFalse(prompt_path.exists(), "leader-prompt.md should not be written with --no-auto-paste")

    def test_auto_paste_loads_leader_prompt_pointer(self) -> None:
        from fleet.commands import leader

        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "claude:opus"
        args.attach = False
        args.auto_paste = True
        args.prompt_delay = 0.0

        with (
            use_fake_mux() as fake,
            # paste → Enter settle delay (0.8 s) is irrelevant to a fake mux.
            unittest.mock.patch("fleet.commands.leader.time.sleep"),
        ):
            result = leader.run(args)

        self.assertEqual(result, 0)
        prompt_path = state.session_dir(self.label) / "leader-prompt.md"
        pastes = fake.calls_named("paste")
        self.assertEqual(len(pastes), 1)
        (session, window, pointer), _kw = pastes[0]
        self.assertEqual((session, window), (self.session, "leader"))
        loaded_path = prompt_pointer.pointer_path(prompt_path)
        self.assertEqual(loaded_path.read_text(encoding="utf-8"), pointer)
        self.assertIn(str(prompt_path.resolve()), pointer)
        self.assertNotIn("You are a fleet leader session", pointer)
        # paste, then the submit Enter (claude: no rename keystrokes).
        self.assertEqual(
            fake.sent(),
            [("paste", "leader", pointer), ("key", "leader", "Enter")],
        )

    def test_codex_leader_renames_with_explicit_ctrl_u_key(self) -> None:
        from fleet.commands import leader

        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "codex:gpt-5.5"
        args.attach = False
        args.auto_paste = True
        args.prompt_delay = 0.0
        args.scope = None

        with (
            use_fake_mux() as fake,
            unittest.mock.patch("fleet.commands.leader.time.sleep"),
        ):
            result = leader.run(args)

        self.assertEqual(result, 0)
        sent = [(kind, payload) for kind, _w, payload in fake.sent()]
        self.assertEqual(
            sent[:5],
            [
                ("text", "/rename"),
                ("key", "Enter"),
                ("key", "Ctrl-u"),
                ("text", f"{self.label}-leader"),
                ("key", "Enter"),
            ],
        )
        self.assertEqual([k for k, _p in sent[5:]], ["paste", "key"])
        # Ctrl-u is sent as a Key, never typed as text.
        keys = [a[2] for a, _k in fake.calls_named("send_key")]
        self.assertIn(Key("Ctrl-u"), keys)
        self.assertNotIn("C-u", [a[2] for a, _k in fake.calls_named("send_text")])

    def test_claude_leader_sets_session_name(self) -> None:
        from fleet.commands import leader

        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "claude:opus"
        args.attach = False
        args.auto_paste = False
        args.prompt_delay = 0.0

        with use_fake_mux() as fake:
            result = leader.run(args)

        self.assertEqual(result, 0)
        (session,), kwargs = fake.calls_named("new_session")[0]
        self.assertEqual(session, self.session)
        self.assertEqual(kwargs["window"], "leader")
        # argv goes to the backend unquoted; the tmux backend types it.
        self.assertEqual(kwargs["argv"][-2:], ["--name", f"{self.label}-leader"])

    def test_invalid_scope_fails_before_session_creation(self) -> None:
        """An unknown --scope project errors before any tmux/session.json side effect."""
        from fleet.commands import leader

        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "claude:opus"
        args.attach = False
        args.auto_paste = False
        args.prompt_delay = 0.0
        args.scope = "no-such-project"

        with use_fake_mux() as fake:
            result = leader.run(args)

        self.assertEqual(result, 1)
        # Nothing was created: no mux session, no session.json record.
        self.assertEqual(fake.calls_named("new_session"), [])
        self.assertFalse(state.session_record_path(self.label).exists())

    def test_valid_scope_is_applied_to_session_record(self) -> None:
        """A registered --scope project is validated and persisted onto session.json."""
        from tests._fleet_test_helpers import make_project
        from fleet.commands import leader

        repo = Path(self._tmp.name) / "alpha-repo"
        repo.mkdir()
        make_project(self.fleet_home, "alpha", repo)

        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "claude:opus"
        args.attach = False
        args.auto_paste = False
        args.prompt_delay = 0.0
        args.scope = "alpha"

        with use_fake_mux() as fake:
            result = leader.run(args)

        self.assertEqual(result, 0)
        self.assertEqual(len(fake.calls_named("new_session")), 1)
        self.assertEqual(state.session_scope(self.label), ["alpha"])

    def test_injects_fleet_session_env(self) -> None:
        from fleet.commands import leader

        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "claude:opus"
        args.attach = False
        args.auto_paste = False
        args.prompt_delay = 0.0

        with use_fake_mux() as fake:
            leader.run(args)

        env = fake.calls_named("new_session")[0][1]["env"]
        self.assertEqual(env["FLEET_SESSION"], self.label)
        self.assertEqual(env["FLEET_STATE_DIR"], str(state.session_dir(self.label)))


    def _args(self, **over):
        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = "claude:opus"
        args.attach = False
        args.auto_paste = False
        args.prompt_delay = 0.0
        args.scope = None
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def test_existing_session_prints_backend_attach_hint(self) -> None:
        import contextlib
        import io

        from fleet.commands import leader

        out = io.StringIO()
        with (
            use_fake_mux(sessions={self.session: ["leader"]}) as fake,
            contextlib.redirect_stdout(out),
        ):
            result = leader.run(self._args())
        self.assertEqual(result, 0)
        self.assertIn(f"attach: tmux attach -t {self.session}", out.getvalue())
        self.assertEqual(fake.calls_named("new_session"), [])
        self.assertEqual(fake.calls_named("attach"), [])

    def test_attach_flag_hands_off_to_backend_attach(self) -> None:
        from fleet.commands import leader

        with use_fake_mux(attach_rc=0) as fake:
            result = leader.run(self._args(attach=True))
        self.assertEqual(result, 0)
        self.assertEqual(fake.calls_named("attach"), [((self.session, None), {})])

    def test_mux_error_on_session_creation_fails(self) -> None:
        import contextlib
        import io

        from fleet.commands import leader
        from fleet.mux import MuxError

        err = io.StringIO()
        with use_fake_mux() as fake, contextlib.redirect_stderr(err):
            fake.fail["new_session"] = MuxError("boom")
            result = leader.run(self._args())
        self.assertEqual(result, 1)
        self.assertIn("tmux setup failed: boom", err.getvalue())


if __name__ == "__main__":
    unittest.main()
