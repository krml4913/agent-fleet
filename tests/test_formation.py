"""Tests for ``fleet.formation`` — load / validate / list (templates + custom)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import formation, state as state_mod  # noqa: E402


class FormationTemplateTests(unittest.TestCase):
    # ---- listing templates ----

    def test_list_templates_includes_known(self) -> None:
        names = set(formation.list_templates())
        self.assertIn("solo", names)
        self.assertIn("pair_review", names)
        self.assertIn("multi_stage", names)
        self.assertIn("research", names)
        self.assertNotIn("race", names)

    # ---- loading + validating templates ----

    def test_each_template_loads_and_validates(self) -> None:
        for name in formation.list_templates():
            data = formation.load_template(name)
            formation.validate(data)
            self.assertEqual(data["name"], name)

    def test_load_template_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            formation.load_template("no-such-template")

    # ---- formation loading cascade ----

    def test_load_formation_from_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            (state / "formations" / "solo.yaml").write_text(
                "name: solo\n"
                "description: custom\n"
                "stages:\n"
                "  - role: driver\n"
                "    agent: codex:o4-mini\n"
            )
            data = formation.load_formation("solo", state)
            self.assertEqual(data["description"], "custom")
            self.assertEqual(data["stages"][0]["agent"], "codex:o4-mini")

    def test_load_formation_missing_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            with self.assertRaises(FileNotFoundError) as ctx:
                formation.load_formation("no-such-formation", state)
            message = str(ctx.exception)
            self.assertIn(str(state / "formations" / "no-such-formation.yaml"), message)
            self.assertIn("global/formations/no-such-formation.yaml", message)
            self.assertIn("templates/no-such-formation.yaml", message)

    def test_load_formation_falls_back_to_template(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            data = formation.load_formation("pair_review", state)
            self.assertEqual(data["name"], "pair_review")

    def test_research_resolves_via_cascade_with_no_project_copy(self) -> None:
        # `--formation research` must resolve from the shipped template alone,
        # with no per-project copy in the cascade (Issue #202 dogfoods #198).
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            name, data = formation.resolve_formation(state, "research")
            self.assertEqual(name, "research")
            formation.validate(data)
            self.assertEqual(data["name"], "research")
            self.assertEqual(data["stages"][0]["role"], "researcher")
            self.assertEqual(data["stages"][0]["peer_review"]["role"], "fact-checker")

    def test_load_formation_global_shadows_template(self) -> None:
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("FLEET_HOME")
            os.environ["FLEET_HOME"] = str(Path(tmp) / "fleet-state")
            try:
                state = Path(tmp) / ".fleet-state"
                (state / "formations").mkdir(parents=True)
                global_dir = state_mod.global_formations_dir()
                global_dir.mkdir(parents=True)
                (global_dir / "solo.yaml").write_text(
                    "name: solo\n"
                    "description: global\n"
                    "stages:\n"
                    "  - role: driver\n"
                    "    agent: codex:o4-mini\n"
                )
                data = formation.load_formation("solo", state)
                self.assertEqual(data["description"], "global")
                self.assertEqual(data["stages"][0]["agent"], "codex:o4-mini")
            finally:
                if old_home is None:
                    os.environ.pop("FLEET_HOME", None)
                else:
                    os.environ["FLEET_HOME"] = old_home

    def test_load_formation_project_shadows_global(self) -> None:
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("FLEET_HOME")
            os.environ["FLEET_HOME"] = str(Path(tmp) / "fleet-state")
            try:
                state = Path(tmp) / ".fleet-state"
                (state / "formations").mkdir(parents=True)
                global_dir = state_mod.global_formations_dir()
                global_dir.mkdir(parents=True)
                (global_dir / "solo.yaml").write_text(
                    "name: solo\n"
                    "description: global\n"
                    "stages:\n"
                    "  - role: driver\n"
                    "    agent: claude:opus\n"
                )
                (state / "formations" / "solo.yaml").write_text(
                    "name: solo\n"
                    "description: project\n"
                    "stages:\n"
                    "  - role: driver\n"
                    "    agent: codex:gpt-5.5\n"
                )
                data = formation.load_formation("solo", state)
                self.assertEqual(data["description"], "project")
                self.assertEqual(data["stages"][0]["agent"], "codex:gpt-5.5")
            finally:
                if old_home is None:
                    os.environ.pop("FLEET_HOME", None)
                else:
                    os.environ["FLEET_HOME"] = old_home

    def test_list_custom_handles_missing_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(formation.list_custom(Path(tmp)), [])

    # ---- resolve_formation ----

    def test_resolve_formation_explicit(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            (state / "formations" / "solo.yaml").write_text(
                "name: solo\ndescription: x\nstages:\n  - role: driver\n    agent: claude:sonnet\n"
            )
            name, data = formation.resolve_formation(state, "solo")
            self.assertEqual(name, "solo")
            self.assertEqual(data["name"], "solo")

    def test_resolve_formation_explicit_missing_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            with self.assertRaises(formation.ResolutionError) as ctx:
                formation.resolve_formation(state, "no-such")
            message = str(ctx.exception)
            self.assertIn("Looked in:", message)
            self.assertIn("formations/no-such.yaml", message)
            self.assertIn("global/formations/no-such.yaml", message)
            self.assertIn("templates/no-such.yaml", message)

    def test_resolve_formation_single_custom_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            (state / "formations" / "solo.yaml").write_text(
                "name: solo\ndescription: x\nstages:\n  - role: driver\n    agent: claude:sonnet\n"
            )
            name, data = formation.resolve_formation(state, None)
            self.assertEqual(name, "solo")
            self.assertEqual(data["stages"][0]["agent"], "claude:sonnet")

    def test_resolve_formation_omitted_ignores_global_and_templates(self) -> None:
        import contextlib
        import io
        import json

        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("FLEET_HOME")
            os.environ["FLEET_HOME"] = str(Path(tmp) / "fleet-state")
            try:
                state_path = Path(tmp) / ".fleet-state"
                (state_path / "formations").mkdir(parents=True)
                global_dir = state_mod.global_formations_dir()
                global_dir.mkdir(parents=True)
                (global_dir / "solo.yaml").write_text(
                    "name: solo\n"
                    "stages:\n"
                    "  - role: driver\n"
                    "    agent: codex:o4-mini\n"
                )
                rec = state_mod.session_record_path("main")
                rec.parent.mkdir(parents=True, exist_ok=True)
                rec.write_text(json.dumps({
                    "label": "main",
                    "agent": "claude:opus",
                    "started_at": "2026-01-01T00:00:00+00:00",
                }))
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    name, data = formation.resolve_formation(
                        state_path, None, owner_session="main"
                    )
                self.assertEqual(name, "_leader_solo")
                self.assertEqual(data["stages"][0]["agent"], "claude:opus")
            finally:
                if old_home is None:
                    os.environ.pop("FLEET_HOME", None)
                else:
                    os.environ["FLEET_HOME"] = old_home

    def test_resolve_formation_multiple_customs_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            for f in ("solo.yaml", "pair_review.yaml"):
                (state / "formations" / f).write_text(
                    f"name: {f[:-5]}\nstages:\n  - role: driver\n    agent: claude:sonnet\n"
                )
            with self.assertRaises(formation.ResolutionError):
                formation.resolve_formation(state, None)

    def test_resolve_formation_zero_customs_with_leader_session(self) -> None:
        # The _leader_solo fallback reads the owner session's record under
        # global/sessions/<label>/ (Issue #166), not a per-project leader-session.json.
        import contextlib
        import io
        import json

        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("FLEET_HOME")
            os.environ["FLEET_HOME"] = str(Path(tmp) / "fleet-state")
            try:
                state_path = Path(tmp) / ".fleet-state"
                (state_path / "formations").mkdir(parents=True)
                rec = state_mod.session_record_path("main")
                rec.parent.mkdir(parents=True, exist_ok=True)
                rec.write_text(json.dumps(
                    {"label": "main", "agent": "claude:opus",
                     "started_at": "2026-01-01T00:00:00+00:00"}
                ))
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    name, data = formation.resolve_formation(
                        state_path, None, owner_session="main"
                    )
                self.assertEqual(name, "_leader_solo")
                self.assertEqual(data["stages"][0]["agent"], "claude:opus")
            finally:
                if old_home is None:
                    os.environ.pop("FLEET_HOME", None)
                else:
                    os.environ["FLEET_HOME"] = old_home

    def test_resolve_formation_zero_customs_no_leader_session_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("FLEET_HOME")
            os.environ["FLEET_HOME"] = str(Path(tmp) / "fleet-state")
            try:
                state_path = Path(tmp) / ".fleet-state"
                (state_path / "formations").mkdir(parents=True)
                with self.assertRaises(formation.ResolutionError):
                    formation.resolve_formation(state_path, None, owner_session="main")
            finally:
                if old_home is None:
                    os.environ.pop("FLEET_HOME", None)
                else:
                    os.environ["FLEET_HOME"] = old_home

    # ---- expand_stages ----

    def test_expand_stages_solo(self) -> None:
        data = formation.load_template("solo")
        stages = formation.expand_stages(data)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["role"], "driver")
        self.assertEqual(stages[0]["agent"], "claude:sonnet")
        self.assertEqual(stages[0]["status"], "pending")

    def test_expand_stages_pair_review(self) -> None:
        data = formation.load_template("pair_review")
        stages = formation.expand_stages(data)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["role"], "implementer")
        self.assertEqual(stages[0]["status"], "pending")
        self.assertIn("peer_review", stages[0])
        self.assertEqual(stages[0]["peer_review"]["role"], "code-reviewer")

    def test_expand_stages_user_approval_normalised(self) -> None:
        data = formation.load_template("pair_review")
        stages = formation.expand_stages(data)
        implementer = stages[0]
        self.assertIn("user_approval", implementer)
        ua = implementer["user_approval"]
        self.assertIsInstance(ua, dict)
        self.assertTrue(ua["required"])
        self.assertEqual(ua["status"], "pending")

    def test_expand_stages_research(self) -> None:
        data = formation.load_template("research")
        stages = formation.expand_stages(data)
        self.assertEqual(len(stages), 1)
        researcher = stages[0]
        self.assertEqual(researcher["role"], "researcher")
        self.assertEqual(researcher["agent"], "claude:opus")
        self.assertEqual(researcher["status"], "pending")
        self.assertEqual(researcher["peer_review"]["role"], "fact-checker")
        self.assertEqual(researcher["peer_review"]["agent"], "claude:opus")
        ua = researcher["user_approval"]
        self.assertIsInstance(ua, dict)
        self.assertTrue(ua["required"])
        self.assertEqual(ua["status"], "pending")

    def test_expand_stages_multi_stage(self) -> None:
        data = formation.load_template("multi_stage")
        stages = formation.expand_stages(data)
        self.assertEqual(len(stages), 2)
        self.assertEqual(stages[0]["role"], "designer")
        self.assertEqual(stages[1]["role"], "implementer")

    def test_expand_stages_empty_formation(self) -> None:
        stages = formation.expand_stages({"name": "x", "stages": []})
        self.assertEqual(stages, [])

    def test_expand_stages_empty_if_no_stages_key(self) -> None:
        data = {"name": "x", "roles": [{"role": "coder", "agent": "claude:sonnet"}]}
        stages = formation.expand_stages(data)
        self.assertEqual(stages, [])

    # ---- validation ----

    def test_validate_rejects_missing_name(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({"stages": [{"role": "x", "agent": "y"}]})

    def test_validate_rejects_missing_stages(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({"name": "x"})

    def test_validate_rejects_roles_key_without_stages(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({"name": "a", "roles": [{"role": "r"}]})

    def test_validate_accepts_stages(self) -> None:
        formation.validate({"name": "b", "stages": [{"role": "s"}]})

    def test_validate_rejects_empty_stages(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({"name": "x", "stages": []})

    def test_validate_rejects_stage_without_role(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({"name": "x", "stages": [{"agent": "claude:sonnet"}]})

    def test_validate_accepts_valid_custom_formation(self) -> None:
        formation.validate({
            "name": "custom",
            "description": "test formation",
            "stages": [
                {"role": "driver", "agent": "claude:sonnet"},
                {"role": "reviewer"},
            ],
        })

    # ---- gate keys: fail loud so a safety boundary cannot silently vanish ----

    def test_validate_rejects_user_approval_typo(self) -> None:
        # The headline bug: a near-miss typo silently drops the approval gate.
        with self.assertRaises(ValueError) as ctx:
            formation.validate({
                "name": "x",
                "stages": [{"role": "driver", "user_aproval": "required"}],
            })
        self.assertIn("user_approval", str(ctx.exception))

    def test_validate_rejects_peer_review_typo(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            formation.validate({
                "name": "x",
                "stages": [{"role": "implementer",
                            "peer_reveiw": {"role": "code-reviewer"}}],
            })
        self.assertIn("peer_review", str(ctx.exception))

    def test_validate_rejects_gate_key_case_typo(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "driver", "User_Approval": "required"}],
            })

    def test_validate_rejects_user_approval_wrong_string(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "driver", "user_approval": "yes"}],
            })

    def test_validate_rejects_user_approval_wrong_type(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "driver", "user_approval": ["required"]}],
            })

    def test_validate_rejects_user_approval_object_missing_required(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "driver", "user_approval": {"status": "pending"}}],
            })

    def test_validate_rejects_user_approval_required_not_bool(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "driver", "user_approval": {"required": "yes"}}],
            })

    def test_validate_rejects_peer_review_not_dict(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "implementer", "peer_review": "code-reviewer"}],
            })

    def test_validate_rejects_peer_review_missing_role(self) -> None:
        with self.assertRaises(ValueError):
            formation.validate({
                "name": "x",
                "stages": [{"role": "implementer", "peer_review": {"agent": "claude:opus"}}],
            })

    def test_validate_accepts_valid_gates(self) -> None:
        # String shorthand, object form, and a valid peer_review all pass.
        formation.validate({
            "name": "x",
            "stages": [
                {"role": "designer", "user_approval": "required"},
                {"role": "implementer",
                 "user_approval": {"required": True},
                 "peer_review": {"role": "code-reviewer", "agent": "claude:opus"}},
            ],
        })

    def test_validate_allows_unrelated_custom_keys(self) -> None:
        # Open schema (§7.4): custom keys far from the gate keys are untouched.
        formation.validate({
            "name": "x",
            "stages": [{"role": "driver", "priority": "high", "review": "later"}],
        })


if __name__ == "__main__":
    unittest.main()
