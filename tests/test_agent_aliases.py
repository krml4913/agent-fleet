"""Tests for agent aliases (``agent_aliases`` in the global config).

An alias names a full ``vendor:model`` spec and is accepted anywhere a spec is:
formation ``agent`` / ``peer_review.agent``, ``fleet-agent start --agent``,
``fleet leader --agent`` and the ``leader_agent`` config key. It is resolved
once, where the spec enters task / leader state.

Hermetic: ``FLEET_HOME`` on a throwaway dir, fresh config cache per test.
"""
from __future__ import annotations

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

from fleet import agents, config, state  # noqa: E402
from fleet import formation as formation_mod  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402
from tests._fleet_test_helpers import make_project, run_fleet, run_fleet_agent  # noqa: E402


class _AliasTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fleet_home = Path(self._tmp.name).resolve() / "fleet-state"
        self.fleet_home.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "FLEET_MUX"}
        env["FLEET_HOME"] = str(self.fleet_home)
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        config.reset_cache()
        self.addCleanup(config.reset_cache)

    @property
    def path(self) -> Path:
        return self.fleet_home / "global" / "config.yaml"

    def write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")
        config.reset_cache()


class ResolveSpecTests(_AliasTestCase):
    def test_full_spec_passes_through(self) -> None:
        self.assertEqual(agents.resolve_spec(" claude:sonnet "), "claude:sonnet")

    def test_alias_resolves_to_its_spec(self) -> None:
        self.assertEqual(
            agents.resolve_spec("deep", {"deep": "claude:opus"}), "claude:opus"
        )

    def test_alias_resolves_from_the_global_config_by_default(self) -> None:
        config.set_alias("fast", "claude:sonnet")
        self.assertEqual(agents.resolve_spec("fast"), "claude:sonnet")

    def test_unknown_alias_names_the_known_ones(self) -> None:
        with self.assertRaises(ValueError) as cm:
            agents.resolve_spec("nope", {"fast": "claude:sonnet", "deep": "claude:opus"})
        msg = str(cm.exception)
        self.assertIn("unknown agent alias 'nope'", msg)
        self.assertIn("known aliases: deep, fast", msg)

    def test_unknown_alias_with_no_aliases_says_how_to_add_one(self) -> None:
        with self.assertRaises(ValueError) as cm:
            agents.resolve_spec("nope")
        self.assertIn("no aliases are defined", str(cm.exception))
        self.assertIn("agent_aliases.<name>", str(cm.exception))

    def test_bad_spec_still_rejected(self) -> None:
        with self.assertRaises(ValueError) as cm:
            agents.resolve_spec("nosuchvendor:model")
        self.assertIn("unsupported vendor", str(cm.exception))

    def test_empty_and_non_string_rejected(self) -> None:
        for bad in ("", "  ", None, 3):
            with self.assertRaises(ValueError):
                agents.resolve_spec(bad)  # type: ignore[arg-type]

    def test_is_alias(self) -> None:
        self.assertTrue(agents.is_alias("deep"))
        self.assertFalse(agents.is_alias("claude:opus"))
        self.assertFalse(agents.is_alias(""))


class AliasConfigTests(_AliasTestCase):
    def test_round_trip_set_load_unset(self) -> None:
        self.assertEqual(config.set_alias(" fast ", " claude:sonnet "), ("fast", "claude:sonnet"))
        config.set_alias("deep", "claude:opus")
        self.assertEqual(
            config.load_aliases(), {"fast": "claude:sonnet", "deep": "claude:opus"}
        )
        self.assertEqual(config.get_alias("deep"), "claude:opus")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("agent_aliases:\n  fast: claude:sonnet\n  deep: claude:opus\n", text)

        config.unset_alias("fast")
        self.assertEqual(config.load_aliases(), {"deep": "claude:opus"})
        config.unset_alias("deep")
        self.assertEqual(config.load_aliases(), {})
        self.assertNotIn("agent_aliases", self.path.read_text(encoding="utf-8"))

    def test_set_alias_keeps_other_keys(self) -> None:
        self.write("mux: tmux\nfuture_key: 1\n")
        config.set_alias("fast", "claude:sonnet")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("mux: tmux", text)
        self.assertIn("future_key: 1", text)
        self.assertEqual(config.get("mux"), ("tmux", "config"))

    def test_alias_name_must_not_contain_colon_or_dot(self) -> None:
        for bad in ("a:b", "a.b", "", "has space"):
            with self.assertRaises(config.ConfigError):
                config.set_alias(bad, "claude:opus")
        self.assertFalse(self.path.exists())

    def test_target_must_be_a_full_spec(self) -> None:
        config.set_alias("deep", "claude:opus")
        with self.assertRaises(config.ConfigError) as cm:
            config.set_alias("deeper", "deep")
        self.assertIn("alias-to-alias chains are not supported", str(cm.exception))
        with self.assertRaises(config.ConfigError) as cm:
            config.set_alias("bad", "nosuchvendor:model")
        self.assertIn("unsupported vendor", str(cm.exception))
        self.assertEqual(config.load_aliases(), {"deep": "claude:opus"})

    def test_invalid_entries_in_the_file_warn_and_are_skipped(self) -> None:
        import contextlib
        import io

        self.write(
            "agent_aliases:\n"
            "  good: claude:sonnet\n"
            "  chain: good\n"
            "  'bad:name': claude:opus\n"
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            aliases = config.load_aliases()
        self.assertEqual(aliases, {"good": "claude:sonnet"})
        self.assertIn("chain", err.getvalue())
        self.assertIn("bad:name", err.getvalue())

    def test_non_mapping_aliases_warns(self) -> None:
        import contextlib
        import io

        self.write("agent_aliases: claude:opus\nmux: tmux\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(config.load_aliases(), {})
        self.assertIn("must be a mapping", err.getvalue())
        self.assertEqual(config.get("mux"), ("tmux", "config"))

    def test_unset_unknown_alias_fails(self) -> None:
        with self.assertRaises(config.ConfigError):
            config.unset_alias("nope")

    def test_get_unknown_alias_names_known(self) -> None:
        config.set_alias("deep", "claude:opus")
        with self.assertRaises(config.ConfigError) as cm:
            config.get_alias("nope")
        self.assertIn("known aliases: deep", str(cm.exception))

    def test_unset_scalar_key_restores_default(self) -> None:
        config.set_value("mux", "tmux")
        config.unset_value("mux")
        self.assertEqual(config.get("mux"), ("zellij", "default"))
        with self.assertRaises(config.ConfigError):
            config.unset_value("nope")


class LeaderAgentAliasConfigTests(_AliasTestCase):
    def test_set_leader_agent_to_a_known_alias(self) -> None:
        config.set_alias("deep", "claude:opus")
        self.assertEqual(config.set_value("leader_agent", "deep"), "deep")
        # The config layer keeps the alias name; fleet leader resolves it.
        self.assertEqual(config.get("leader_agent"), ("deep", "config"))

    def test_set_leader_agent_to_an_unknown_alias_is_refused(self) -> None:
        config.set_alias("deep", "claude:opus")
        with self.assertRaises(config.ConfigError) as cm:
            config.set_value("leader_agent", "fast")
        self.assertIn("unknown agent alias 'fast'", str(cm.exception))
        self.assertIn("known aliases: deep", str(cm.exception))
        self.assertEqual(config.get("leader_agent"), ("claude:opus", "default"))

    def test_leader_agent_alias_loads_from_the_same_file(self) -> None:
        # leader_agent before agent_aliases in the file: order must not matter.
        self.write("leader_agent: deep\nagent_aliases:\n  deep: claude:claude-opus-5-5\n")
        self.assertEqual(config.get("leader_agent"), ("deep", "config"))

    def test_leader_agent_with_a_dangling_alias_warns_and_falls_back(self) -> None:
        import contextlib
        import io

        self.write("leader_agent: gone\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(config.get("leader_agent"), ("claude:opus", "default"))
        self.assertIn("unknown agent alias 'gone'", err.getvalue())

    def test_unset_alias_used_by_leader_agent_is_refused(self) -> None:
        config.set_alias("deep", "claude:opus")
        config.set_value("leader_agent", "deep")
        with self.assertRaises(config.ConfigError) as cm:
            config.unset_alias("deep")
        self.assertIn("leader_agent uses agent alias 'deep'", str(cm.exception))
        self.assertEqual(config.load_aliases(), {"deep": "claude:opus"})


class ConfigCmdAliasTests(_AliasTestCase):
    def _fleet(self, *args: str):
        return run_fleet("config", *args, fleet_home=self.fleet_home)

    def test_set_get_show_unset(self) -> None:
        r = self._fleet("set", "agent_aliases.deep", "claude:opus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("agent_aliases.deep: claude:opus", r.stdout)

        r = self._fleet("get", "agent_aliases.deep")
        self.assertEqual((r.returncode, r.stdout), (0, "claude:opus\n"))

        r = self._fleet()
        self.assertIn("agent_aliases:\n  deep: claude:opus\n", r.stdout)

        r = self._fleet("unset", "agent_aliases.deep")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._fleet()
        self.assertIn("agent_aliases: (none)", r.stdout)

    def test_show_leader_agent_alias_with_its_resolution(self) -> None:
        self._fleet("set", "agent_aliases.deep", "claude:claude-opus-5-5")
        r = self._fleet("set", "leader_agent", "deep")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._fleet()
        self.assertIn("leader_agent: deep -> claude:claude-opus-5-5 (config)", r.stdout)
        r = self._fleet("get", "leader_agent")
        self.assertEqual(r.stdout, "deep\n")

    def test_set_leader_agent_unknown_alias_fails(self) -> None:
        r = self._fleet("set", "leader_agent", "deep")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown agent alias 'deep'", r.stderr)
        self.assertFalse(self.path.exists())

    def test_set_alias_chain_fails(self) -> None:
        self._fleet("set", "agent_aliases.deep", "claude:opus")
        r = self._fleet("set", "agent_aliases.deeper", "deep")
        self.assertEqual(r.returncode, 1)
        self.assertIn("alias-to-alias", r.stderr)

    def test_get_unknown_alias_fails(self) -> None:
        r = self._fleet("get", "agent_aliases.nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown agent alias 'nope'", r.stderr)

    def test_unset_leader_agent(self) -> None:
        self._fleet("set", "leader_agent", "claude:sonnet")
        r = self._fleet("unset", "leader_agent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("leader_agent: claude:opus (default)", self._fleet().stdout)


class FormationAliasTests(_AliasTestCase):
    def _formation(self, agent: str, reviewer: str = "claude:opus") -> dict:
        return {
            "name": "f",
            "stages": [
                {
                    "role": "implementer",
                    "agent": agent,
                    "peer_review": {"role": "code-reviewer", "agent": reviewer},
                }
            ],
        }

    def test_validate_accepts_known_aliases(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        config.set_alias("deep", "claude:opus")
        formation_mod.validate(self._formation("impl", "deep"))

    def test_validate_flags_unknown_alias(self) -> None:
        config.set_alias("deep", "claude:opus")
        with self.assertRaises(ValueError) as cm:
            formation_mod.validate(self._formation("impl"))
        msg = str(cm.exception)
        self.assertIn("stages[0] agent", msg)
        self.assertIn("unknown agent alias 'impl'", msg)
        self.assertIn("known aliases: deep", msg)

    def test_validate_flags_unknown_peer_review_alias(self) -> None:
        with self.assertRaises(ValueError) as cm:
            formation_mod.validate(self._formation("claude:sonnet", "deep"))
        self.assertIn("stages[0] peer_review.agent", str(cm.exception))

    def test_validate_flags_bad_spec(self) -> None:
        with self.assertRaises(ValueError) as cm:
            formation_mod.validate(self._formation("nosuchvendor:x"))
        self.assertIn("unsupported vendor", str(cm.exception))

    def test_resolve_stage_agents_records_spec_and_alias(self) -> None:
        data = self._formation("impl", "deep")
        stages = formation_mod.expand_stages(data)
        formation_mod.resolve_stage_agents(
            stages, {"impl": "claude:sonnet", "deep": "claude:opus"}
        )
        self.assertEqual(stages[0]["agent"], "claude:sonnet")
        self.assertEqual(stages[0]["agent_alias"], "impl")
        self.assertEqual(stages[0]["peer_review"]["agent"], "claude:opus")
        self.assertEqual(stages[0]["peer_review"]["agent_alias"], "deep")
        # The formation document itself is left untouched.
        self.assertEqual(data["stages"][0]["peer_review"]["agent"], "deep")

    def test_resolve_stage_agents_leaves_full_specs_alone(self) -> None:
        stages = formation_mod.expand_stages(self._formation("claude:sonnet"))
        formation_mod.resolve_stage_agents(stages, {})
        self.assertEqual(stages[0]["agent"], "claude:sonnet")
        self.assertNotIn("agent_alias", stages[0])


class StartAliasTests(_AliasTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = Path(self._tmp.name).resolve() / "proj"
        self.project.mkdir()
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        (self.state_dir / "formations" / "aliased.yaml").write_text(
            "name: aliased\n"
            "stages:\n"
            "  - role: implementer\n"
            "    agent: impl\n"
            "    peer_review:\n"
            "      role: code-reviewer\n"
            "      agent: deep\n",
            encoding="utf-8",
        )

    def _start(self, *extra: str, task_id: str = "1"):
        return run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "--formation", "aliased",
            *extra, task_id, "Do the thing",
            fleet_home=self.fleet_home,
        )

    def _start_events(self) -> list[dict]:
        lines = (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines if l and json.loads(l).get("type") == "start"]

    def test_task_state_records_the_resolved_spec(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        config.set_alias("deep", "claude:opus")
        r = self._start()
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        stage = task["stages"][0]
        self.assertEqual(stage["agent"], "claude:sonnet")
        self.assertEqual(stage["agent_alias"], "impl")
        self.assertEqual(stage["peer_review"]["agent"], "claude:opus")
        self.assertEqual(stage["peer_review"]["agent_alias"], "deep")
        self.assertEqual(self._start_events()[-1]["agent"], "claude:sonnet")
        prompt = (self.state_dir / "tasks" / "task-1" / "driver-prompt.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("claude:sonnet", prompt)

    def test_changing_the_alias_later_does_not_change_the_task(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        config.set_alias("deep", "claude:opus")
        self.assertEqual(self._start().returncode, 0)
        config.set_alias("impl", "claude:haiku")
        config.set_alias("deep", "claude:haiku")
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["stages"][0]["agent"], "claude:sonnet")
        self.assertEqual(task["stages"][0]["peer_review"]["agent"], "claude:opus")

    def test_agent_flag_accepts_an_alias(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        config.set_alias("deep", "claude:opus")
        config.set_alias("fast", "claude:haiku")
        r = self._start("--agent", "fast")
        self.assertEqual(r.returncode, 0, r.stderr)
        stage = state.load_task(self.state_dir, "1")["stages"][0]
        self.assertEqual((stage["agent"], stage["agent_alias"]), ("claude:haiku", "fast"))

    def test_unknown_alias_fails_before_task_creation(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        r = self._start()
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown agent alias 'deep'", r.stderr)
        self.assertIn("known aliases: impl", r.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-1").exists())

    def test_formation_list_flags_an_unknown_alias(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        r = run_fleet("formation", "list", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("aliased [project:demo] (wins)", r.stdout)
        self.assertIn("invalid: formation stages[0] peer_review.agent: unknown agent alias 'deep'", r.stdout)
        config.set_alias("deep", "claude:opus")
        r = run_fleet("formation", "list", "--project", "demo", fleet_home=self.fleet_home)
        self.assertNotIn("invalid:", r.stdout)

    def test_unknown_agent_flag_alias_fails(self) -> None:
        config.set_alias("impl", "claude:sonnet")
        config.set_alias("deep", "claude:opus")
        r = self._start("--agent", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown agent alias 'nope'", r.stderr)


class LeaderAliasTests(_AliasTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.label = "test-" + os.urandom(3).hex()

    def _args(self, **over):
        args = unittest.mock.MagicMock()
        args.name = self.label
        args.agent = None
        args.attach = False
        args.auto_paste = False
        args.prompt_delay = 0.0
        args.scope = None
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def _record(self) -> dict:
        return json.loads(state.session_record_path(self.label).read_text(encoding="utf-8"))

    def test_leader_agent_alias_resolved_at_launch(self) -> None:
        from fleet.commands import leader

        config.set_alias("deep", "claude:claude-opus-5-5")
        config.set_value("leader_agent", "deep")
        with use_fake_mux() as fake:
            self.assertEqual(leader.run(self._args()), 0)
        record = self._record()
        self.assertEqual(record["agent"], "claude:claude-opus-5-5")
        self.assertEqual(record["agent_alias"], "deep")
        argv = fake.calls_named("new_session")[0][1]["argv"]
        self.assertIn("claude-opus-5-5", argv)

    def test_agent_flag_alias_resolved_at_launch(self) -> None:
        from fleet.commands import leader

        config.set_alias("fast", "claude:sonnet")
        with use_fake_mux():
            self.assertEqual(leader.run(self._args(agent="fast")), 0)
        self.assertEqual(self._record()["agent"], "claude:sonnet")

    def test_full_spec_records_no_alias(self) -> None:
        from fleet.commands import leader

        with use_fake_mux():
            self.assertEqual(leader.run(self._args(agent="claude:opus")), 0)
        self.assertNotIn("agent_alias", self._record())

    def test_unknown_alias_fails_before_session_creation(self) -> None:
        import contextlib
        import io

        from fleet.commands import leader

        config.set_alias("deep", "claude:opus")
        err = io.StringIO()
        with use_fake_mux() as fake, contextlib.redirect_stderr(err):
            self.assertEqual(leader.run(self._args(agent="nope")), 1)
        self.assertEqual(fake.calls_named("new_session"), [])
        self.assertIn("known aliases: deep", err.getvalue())


if __name__ == "__main__":
    unittest.main()
