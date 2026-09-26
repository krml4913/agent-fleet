"""Tests for the global config (``fleet.config``) and ``fleet config`` CLI.

Hermetic: every test runs with ``FLEET_HOME`` on a throwaway dir (never the
host's real ``fleet-state/global/config.yaml``), ``FLEET_MUX`` unset, and a
fresh config cache.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import config  # noqa: E402
from tests._fleet_test_helpers import run_fleet  # noqa: E402


class _ConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # resolved like ``state.fleet_home()`` (Windows 8.3 short temp paths)
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

    def write(self, text: str | bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            self.path.write_bytes(text)
        else:
            self.path.write_text(text, encoding="utf-8")
        config.reset_cache()

    def load_with_stderr(self) -> tuple[dict[str, str], str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            values = config.load()
        return values, err.getvalue()


class ConfigLoadTests(_ConfigTestCase):
    def test_path_is_global_config_yaml_under_fleet_home(self) -> None:
        self.assertEqual(config.config_path(), self.path)

    def test_missing_file_means_defaults_silently(self) -> None:
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertEqual(err, "")
        self.assertEqual(config.get("mux"), ("zellij", "default"))

    def test_empty_file_means_defaults_silently(self) -> None:
        self.write("")
        values, err = self.load_with_stderr()
        self.assertEqual((values, err), ({}, ""))

    def test_configured_value(self) -> None:
        self.write("mux: tmux\n")
        self.assertEqual(config.get("mux"), ("tmux", "config"))

    def test_value_is_normalized(self) -> None:
        self.write("mux: ' TMUX '\n")
        self.assertEqual(config.get("mux"), ("tmux", "config"))

    def test_unknown_keys_are_ignored_without_a_warning(self) -> None:
        self.write("future_key: 1\nmux: tmux\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {"mux": "tmux"})
        self.assertEqual(err, "")

    def test_malformed_yaml_warns_and_falls_back(self) -> None:
        self.write("mux: [unclosed\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("warn:", err)
        self.assertIn("not valid YAML", err)
        self.assertEqual(config.get("mux"), ("zellij", "default"))

    def test_non_mapping_warns_and_falls_back(self) -> None:
        self.write("- tmux\n- zellij\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("must be a mapping", err)

    def test_undecodable_file_warns_and_falls_back(self) -> None:
        self.write(b"mux: \xff\xfe\x00tmux\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("warn:", err)

    def test_invalid_value_warns_lists_valid_ones_and_falls_back(self) -> None:
        self.write("mux: screen\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("warn:", err)
        self.assertIn("tmux, zellij", err)
        self.assertEqual(config.get("mux"), ("zellij", "default"))

    def test_non_string_value_is_invalid(self) -> None:
        self.write("mux: 1\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("warn:", err)

    def test_warns_once_per_process(self) -> None:
        self.write("mux: screen\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            config.load()
            config.load()
            config.get("mux")
        self.assertEqual(err.getvalue().count("warn:"), 1)

    def test_cached_until_reset(self) -> None:
        self.write("mux: tmux\n")
        self.assertEqual(config.get("mux"), ("tmux", "config"))
        # rewrite behind the cache's back: still the old value ...
        self.path.write_text("mux: zellij\n", encoding="utf-8")
        self.assertEqual(config.get("mux"), ("tmux", "config"))
        # ... until the cache is dropped
        config.reset_cache()
        self.assertEqual(config.get("mux"), ("zellij", "config"))

    def test_cache_follows_fleet_home(self) -> None:
        self.write("mux: tmux\n")
        self.assertEqual(config.get("mux"), ("tmux", "config"))
        with TemporaryDirectory() as other:
            with unittest.mock.patch.dict(os.environ, {"FLEET_HOME": other}):
                self.assertEqual(config.get("mux"), ("zellij", "default"))

    def test_get_unknown_key_raises_listing_known_keys(self) -> None:
        with self.assertRaises(config.ConfigError) as cm:
            config.get("nope")
        self.assertIn("mux", str(cm.exception))


class LeaderAgentConfigTests(_ConfigTestCase):
    """``leader_agent`` is free-form (validated like ``--agent``), not enumerable."""

    def test_default_is_built_in_claude_opus(self) -> None:
        self.assertEqual(config.get("leader_agent"), ("claude:opus", "default"))

    def test_configured_value(self) -> None:
        self.write("leader_agent: claude:claude-opus-5-5\n")
        self.assertEqual(
            config.get("leader_agent"), ("claude:claude-opus-5-5", "config")
        )

    def test_value_is_stripped_but_not_lowercased(self) -> None:
        # Unlike ``mux``, a spec's model half may be case-sensitive, so
        # leader_agent is only stripped (like --agent), never lowercased.
        self.write("leader_agent: ' codex:GPT-5.5 '\n")
        self.assertEqual(config.get("leader_agent"), ("codex:GPT-5.5", "config"))

    def test_invalid_spec_warns_lists_the_parser_error_and_falls_back(self) -> None:
        self.write("leader_agent: not-a-spec\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("warn:", err)
        self.assertIn("invalid value for leader_agent", err)
        self.assertIn("vendor:model", err)
        self.assertEqual(config.get("leader_agent"), ("claude:opus", "default"))

    def test_unsupported_vendor_warns_and_falls_back(self) -> None:
        self.write("leader_agent: nosuchvendor:model\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {})
        self.assertIn("unsupported vendor", err)
        self.assertEqual(config.get("leader_agent"), ("claude:opus", "default"))

    def test_mux_and_leader_agent_both_load_from_the_same_file(self) -> None:
        self.write("mux: tmux\nleader_agent: codex:gpt-5.5\n")
        values, err = self.load_with_stderr()
        self.assertEqual(values, {"mux": "tmux", "leader_agent": "codex:gpt-5.5"})
        self.assertEqual(err, "")

    def test_all_keys_includes_both(self) -> None:
        self.assertEqual(config.ALL_KEYS, ("mux", "leader_delivery", "leader_agent"))


class ConfigSetTests(_ConfigTestCase):
    def test_set_creates_the_file_and_dir(self) -> None:
        self.assertEqual(config.set_value("mux", "tmux"), "tmux")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "mux: tmux\n")
        self.assertEqual(config.get("mux"), ("tmux", "config"))

    def test_set_normalizes_key_and_value(self) -> None:
        self.assertEqual(config.set_value(" mux ", " ZELLIJ "), "zellij")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "mux: zellij\n")

    def test_set_invalidates_the_cache(self) -> None:
        self.assertEqual(config.get("mux"), ("zellij", "default"))
        config.set_value("mux", "tmux")
        self.assertEqual(config.get("mux"), ("tmux", "config"))

    def test_set_keeps_other_keys(self) -> None:
        self.write("future_key: 1\nmux: zellij\n")
        config.set_value("mux", "tmux")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("future_key: 1", text)
        self.assertIn("mux: tmux", text)

    def test_set_rejects_unknown_key_listing_known_ones(self) -> None:
        with self.assertRaises(config.ConfigError) as cm:
            config.set_value("nope", "tmux")
        self.assertIn("nope", str(cm.exception))
        self.assertIn("known keys: mux", str(cm.exception))
        self.assertFalse(self.path.exists())

    def test_set_rejects_invalid_value_listing_valid_ones(self) -> None:
        with self.assertRaises(config.ConfigError) as cm:
            config.set_value("mux", "screen")
        self.assertIn("screen", str(cm.exception))
        self.assertIn("tmux, zellij", str(cm.exception))
        self.assertFalse(self.path.exists())

    def test_set_leader_agent_valid_spec(self) -> None:
        self.assertEqual(
            config.set_value("leader_agent", "claude:claude-opus-5-5"),
            "claude:claude-opus-5-5",
        )
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "leader_agent: claude:claude-opus-5-5\n",
        )
        self.assertEqual(
            config.get("leader_agent"), ("claude:claude-opus-5-5", "config")
        )

    def test_set_leader_agent_rejects_missing_colon(self) -> None:
        with self.assertRaises(config.ConfigError) as cm:
            config.set_value("leader_agent", "opus")
        self.assertIn("invalid value for leader_agent", str(cm.exception))
        self.assertIn("vendor:model", str(cm.exception))
        self.assertFalse(self.path.exists())

    def test_set_leader_agent_rejects_unsupported_vendor(self) -> None:
        with self.assertRaises(config.ConfigError) as cm:
            config.set_value("leader_agent", "nosuchvendor:model")
        self.assertIn("unsupported vendor", str(cm.exception))
        self.assertFalse(self.path.exists())

    def test_set_refuses_to_overwrite_a_corrupt_file(self) -> None:
        self.write("mux: [unclosed\n")
        with self.assertRaises(config.ConfigError):
            config.set_value("mux", "tmux")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "mux: [unclosed\n")

    def test_set_goes_through_the_locked_atomic_writer(self) -> None:
        with unittest.mock.patch(
            "fleet.config.atomic_update", wraps=config.atomic_update
        ) as spy:
            config.set_value("mux", "tmux")
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[0], self.path)


class ConfigCmdTests(_ConfigTestCase):
    def _fleet(self, *args: str, env_extra: dict | None = None):
        return run_fleet("config", *args, fleet_home=self.fleet_home, env_extra=env_extra)

    def test_no_args_prints_every_key_with_its_source(self) -> None:
        r = self._fleet()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.path), r.stdout)
        self.assertIn("mux: zellij (default)", r.stdout)
        self.assertNotIn("FLEET_MUX", r.stdout)
        self.assertFalse(self.path.exists(), "printing must not create the file")

    def test_set_then_print_and_get(self) -> None:
        r = self._fleet("set", "mux", "tmux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mux: tmux", r.stdout)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "mux: tmux\n")

        r = self._fleet()
        self.assertIn("mux: tmux (config)", r.stdout)

        r = self._fleet("get", "mux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "tmux\n")

    def test_get_default(self) -> None:
        r = self._fleet("get", "mux")
        self.assertEqual((r.returncode, r.stdout), (0, "zellij\n"))

    def test_get_unknown_key_lists_known_keys(self) -> None:
        r = self._fleet("get", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("unknown config key", r.stderr)
        self.assertIn("known keys: mux", r.stderr)

    def test_set_unknown_key_lists_known_keys(self) -> None:
        r = self._fleet("set", "nope", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("known keys: mux", r.stderr)
        self.assertFalse(self.path.exists())

    def test_set_invalid_value_lists_valid_ones(self) -> None:
        r = self._fleet("set", "mux", "screen")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid value for mux", r.stderr)
        self.assertIn("tmux, zellij", r.stderr)
        self.assertFalse(self.path.exists())

    def test_no_args_prints_leader_agent_default(self) -> None:
        r = self._fleet()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("leader_agent: claude:opus (default)", r.stdout)

    def test_set_then_print_and_get_leader_agent(self) -> None:
        r = self._fleet("set", "leader_agent", "claude:claude-opus-5-5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("leader_agent: claude:claude-opus-5-5", r.stdout)

        r = self._fleet()
        self.assertIn("leader_agent: claude:claude-opus-5-5 (config)", r.stdout)

        r = self._fleet("get", "leader_agent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "claude:claude-opus-5-5\n")

    def test_set_leader_agent_invalid_spec_fails(self) -> None:
        r = self._fleet("set", "leader_agent", "opus")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid value for leader_agent", r.stderr)
        self.assertFalse(self.path.exists())

    def test_help_lists_leader_agent_and_its_format(self) -> None:
        r = self._fleet("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("leader_agent=vendor:model", r.stdout)

    def test_env_override_is_noted_but_get_reports_the_config_layer(self) -> None:
        self._fleet("set", "mux", "tmux")
        r = self._fleet(env_extra={"FLEET_MUX": "zellij"})
        self.assertIn("mux: tmux (config)", r.stdout)
        self.assertIn("FLEET_MUX=zellij", r.stdout)
        r = self._fleet("get", "mux", env_extra={"FLEET_MUX": "zellij"})
        self.assertEqual(r.stdout, "tmux\n")

    def test_bad_file_warns_but_never_crashes_reads(self) -> None:
        self.write("mux: [unclosed\n")
        r = self._fleet()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warn:", r.stderr)
        self.assertIn("mux: zellij (default)", r.stdout)

    def test_set_on_a_bad_file_fails_cleanly_and_keeps_it(self) -> None:
        self.write("mux: [unclosed\n")
        r = self._fleet("set", "mux", "tmux")
        self.assertEqual(r.returncode, 1)
        self.assertIn("error:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "mux: [unclosed\n")

    def test_set_takes_effect_for_mux_selection(self) -> None:
        from fleet import mux

        self._fleet("set", "mux", "tmux")
        config.reset_cache()
        self.assertEqual(mux.backend_selection(), ("tmux", "config"))

    def test_real_entrypoint(self) -> None:
        r = run_fleet("config", "set", "mux", "tmux", fleet_home=self.fleet_home, subprocess_=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_fleet("config", "get", "mux", fleet_home=self.fleet_home, subprocess_=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "tmux"))


if __name__ == "__main__":
    unittest.main()
