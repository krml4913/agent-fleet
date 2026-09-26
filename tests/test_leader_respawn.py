"""Tests for ``fleet leader --respawn`` (:mod:`fleet.leader_respawn`)."""
from __future__ import annotations

import argparse
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

from fleet import config, leader_respawn, state  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402


class LeaderRespawnTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        env = unittest.mock.patch.dict(os.environ, {"FLEET_HOME": str(self.fleet_home)})
        env.start()
        self.addCleanup(env.stop)
        config.reset_cache()
        self.addCleanup(config.reset_cache)
        self.label = "test-" + os.urandom(3).hex()
        self.session = f"fleet-{self.label}"
        # Unit tests run outside the session unless a test says otherwise.
        p = unittest.mock.patch.object(leader_respawn, "called_from_session", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        for target in ("fleet.commands.leader.time.sleep",):
            s = unittest.mock.patch(target)
            s.start()
            self.addCleanup(s.stop)

        self.session_dir = state.session_dir(self.label)
        self.session_dir.mkdir(parents=True)
        # A pre-#329 leader record: pane delivery, a scope and a start time.
        state.session_record_path(self.label).write_text(
            json.dumps(
                {
                    "label": self.label,
                    "agent": "claude:sonnet",
                    "started_at": "2026-09-01T00:00:00+00:00",
                    "pane": f"{self.session}:leader",
                    "scope": ["fleet"],
                }
            ),
            encoding="utf-8",
        )
        self.pending = self.session_dir / "leader-pending.jsonl"
        self.pending.write_text('{"kind": "done", "task_id": "x"}\n', encoding="utf-8")

    def _args(self, **kw) -> argparse.Namespace:
        base = dict(
            name=self.label,
            agent=None,
            attach=False,
            auto_paste=True,
            prompt_delay=0.0,
            scope=None,
            respawn=True,
        )
        base.update(kw)
        return argparse.Namespace(**base)

    def _run(self, fake_windows: list[str], titles: dict | None = None, **kw):
        from fleet.commands import leader

        with use_fake_mux(sessions={self.session: fake_windows}) as fake:
            fake.titles.update(titles or {})
            rc = leader.run(self._args(**kw))
        return rc, fake

    def _record(self) -> dict:
        return json.loads(state.session_record_path(self.label).read_text(encoding="utf-8"))

    def test_replaces_only_the_leader_window(self) -> None:
        rc, fake = self._run(["leader", "fleet-task-a", "fleet-task-b·reviewer"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.sessions[self.session], ["fleet-task-a", "fleet-task-b·reviewer", "leader"]
        )
        self.assertEqual([a[1] for a, _k in fake.calls_named("kill_window")], ["leader"])
        self.assertNotIn("new_session", fake.method_names())
        self.assertNotIn("kill_session", fake.method_names())
        # The new window starts before the old one closes: never windowless.
        names = fake.method_names()
        self.assertLess(names.index("new_window"), names.index("kill_window"))

    def test_relaunches_with_the_fresh_leader_argv(self) -> None:
        rc, fake = self._run(["leader", "drv"])
        self.assertEqual(rc, 0)
        (session, window), kwargs = fake.calls_named("new_window")[0]
        self.assertEqual((session, window), (self.session, leader_respawn.TEMP_WINDOW))
        argv = kwargs["argv"]
        self.assertEqual(argv[argv.index("--name") + 1], f"{self.label}-leader")
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(settings, {"crossSessionInbound": "accept"})
        self.assertEqual(kwargs["env"]["FLEET_SESSION"], self.label)
        self.assertEqual(kwargs["env"]["FLEET_STATE_DIR"], str(self.session_dir))
        # Same argv a fresh launch builds.
        from fleet.commands import leader

        self.assertEqual(argv, leader.leader_launch("claude:sonnet", self.label)[0])

    def test_updates_the_record_and_keeps_scope_start_and_pending(self) -> None:
        pending_before = self.pending.read_text(encoding="utf-8")
        rc, _fake = self._run(["leader", "drv"])
        self.assertEqual(rc, 0)
        rec = self._record()
        self.assertEqual(rec["delivery"], "send_message")
        self.assertEqual(rec["agent_name"], f"{self.label}-leader")
        self.assertEqual(rec["agent"], "claude:sonnet")
        self.assertEqual(rec["scope"], ["fleet"])
        self.assertEqual(rec["started_at"], "2026-09-01T00:00:00+00:00")
        self.assertIn("respawned_at", rec)
        self.assertEqual(self.pending.read_text(encoding="utf-8"), pending_before)
        events = [
            json.loads(line)
            for line in (self.session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(events[-1]["type"], "leader_respawn")

    def test_repastes_the_leader_prompt_into_the_new_leader_window(self) -> None:
        rc, fake = self._run(["leader", "drv"])
        self.assertEqual(rc, 0)
        sent = fake.sent()
        self.assertEqual([(kind, w) for kind, w, _p in sent], [("paste", "leader"), ("key", "leader")])
        self.assertTrue((self.session_dir / "leader-prompt.md").is_file())
        # Pasted only after the swap: the rename precedes the paste.
        names = fake.method_names()
        self.assertLess(names.index("rename_window"), names.index("paste"))

    def test_no_auto_paste(self) -> None:
        rc, fake = self._run(["leader", "drv"], auto_paste=False)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sent(), [])

    def test_agent_flag_overrides_the_recorded_agent(self) -> None:
        rc, _fake = self._run(["leader", "drv"], agent="claude:opus")
        self.assertEqual(rc, 0)
        self.assertEqual(self._record()["agent"], "claude:opus")

    def test_pane_delivery_config_is_honored(self) -> None:
        config.set_value("leader_delivery", "pane")
        rc, fake = self._run(["leader", "drv"])
        self.assertEqual(rc, 0)
        self.assertNotIn("--settings", fake.calls_named("new_window")[0][1]["argv"])
        self.assertEqual(self._record()["delivery"], "pane")

    def test_leader_only_session_survives(self) -> None:
        rc, fake = self._run(["leader"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sessions[self.session], ["leader"])

    def test_renamed_leader_window_is_found_by_title(self) -> None:
        rc, fake = self._run(
            ["my-leader", "drv"],
            titles={(self.session, "my-leader"): f"✳ {self.label}-leader"},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sessions[self.session], ["drv", "leader"])

    def test_missing_leader_window_still_starts_a_leader(self) -> None:
        rc, fake = self._run(["drv"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sessions[self.session], ["drv", "leader"])
        self.assertEqual(fake.calls_named("kill_window"), [])

    def test_leftover_temp_window_is_closed_first(self) -> None:
        rc, fake = self._run(["leader", leader_respawn.TEMP_WINDOW, "drv"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sessions[self.session], ["drv", "leader"])

    def test_missing_session_fails_without_side_effects(self) -> None:
        from fleet.commands import leader

        with use_fake_mux() as fake:
            rc = leader.run(self._args())
        self.assertEqual(rc, 1)
        self.assertNotIn("new_session", fake.method_names())
        self.assertNotIn("new_window", fake.method_names())

    def test_new_window_failure_leaves_the_old_leader(self) -> None:
        from fleet.commands import leader
        from fleet.mux.base import MuxError

        with use_fake_mux(sessions={self.session: ["leader", "drv"]}) as fake:
            fake.fail["new_window"] = MuxError("boom")
            rc = leader.run(self._args())
        self.assertEqual(rc, 1)
        self.assertEqual(fake.sessions[self.session], ["leader", "drv"])
        self.assertNotIn("delivery", self._record())

    def test_inside_the_session_hands_off_to_a_detached_helper(self) -> None:
        from fleet.commands import leader

        with (
            use_fake_mux(sessions={self.session: ["leader", "drv"]}) as fake,
            unittest.mock.patch.object(leader_respawn, "called_from_session", return_value=True),
            unittest.mock.patch.object(leader_respawn, "spawn_detached") as spawn,
        ):
            rc = leader.run(self._args(agent="claude:opus", scope=None))
        self.assertEqual(rc, 0)
        self.assertNotIn("new_window", fake.method_names())
        self.assertNotIn("kill_window", fake.method_names())
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[1:3], ["-m", "fleet.leader_respawn"])
        self.assertEqual(argv[argv.index("--label") + 1], self.label)
        self.assertEqual(argv[argv.index("--agent") + 1], "claude:opus")
        self.assertEqual(argv[argv.index("--wait-pid") + 1], str(os.getpid()))
        self.assertEqual(
            spawn.call_args.kwargs["log_path"], self.session_dir / leader_respawn.LOG_NAME
        )

    def test_detached_helper_main_respawns(self) -> None:
        with use_fake_mux(sessions={self.session: ["leader", "drv"]}) as fake:
            rc = leader_respawn.main(
                ["--label", self.label, "--agent", "claude:sonnet", "--prompt-delay", "0"]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sessions[self.session], ["drv", "leader"])
        self.assertEqual(self._record()["delivery"], "send_message")


class CalledFromSessionTests(unittest.TestCase):
    def _env(self, **env):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("FLEET_SESSION", "ZELLIJ_SESSION_NAME", "TMUX_PANE")}
        clean.update(env)
        return unittest.mock.patch.dict(os.environ, clean, clear=True)

    def test_leader_pane_env(self) -> None:
        with self._env(FLEET_SESSION="x"):
            self.assertTrue(leader_respawn.called_from_session("x", "fleet-x"))

    def test_zellij_session_name(self) -> None:
        with self._env(ZELLIJ_SESSION_NAME="fleet-x"):
            self.assertTrue(leader_respawn.called_from_session("x", "fleet-x"))

    def test_other_session(self) -> None:
        with self._env(FLEET_SESSION="y", ZELLIJ_SESSION_NAME="fleet-y"):
            self.assertFalse(leader_respawn.called_from_session("x", "fleet-x"))


class UpdateSessionRecordTests(unittest.TestCase):
    def test_merges_drops_and_keeps(self) -> None:
        with TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {"FLEET_HOME": tmp}):
            path = state.session_record_path("l")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"label": "l", "agent_alias": "a", "scope": ["p"]}))
            out = state.update_session_record("l", {"delivery": "pane"}, drop=("agent_alias",))
            self.assertEqual(out, {"label": "l", "scope": ["p"], "delivery": "pane"})
            self.assertEqual(json.loads(path.read_text()), out)

    def test_creates_a_missing_record(self) -> None:
        with TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {"FLEET_HOME": tmp}):
            out = state.update_session_record("l", {"delivery": "pane"})
            self.assertEqual(out, {"label": "l", "delivery": "pane"})


if __name__ == "__main__":
    unittest.main()
