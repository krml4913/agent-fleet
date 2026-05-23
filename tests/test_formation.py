"""Tests for ``fleet.formation`` — load / validate / list (templates + custom)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import formation  # noqa: E402


class FormationTemplateTests(unittest.TestCase):
    # ---- listing templates ----

    def test_list_templates_includes_known(self) -> None:
        names = set(formation.list_templates())
        self.assertIn("solo", names)
        self.assertIn("pair_review", names)
        self.assertIn("multi_stage", names)
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

    # ---- custom formation loading (no template fallback) ----

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
            with self.assertRaises(FileNotFoundError):
                formation.load_formation("no-such-formation", state)

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
            with self.assertRaises(formation.ResolutionError):
                formation.resolve_formation(state, "no-such")

    def test_resolve_formation_single_custom_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            (state / "formations" / "solo.yaml").write_text(
                "name: solo\ndescription: x\nstages:\n  - role: driver\n    agent: claude:sonnet\n"
            )
            name, data = formation.resolve_formation(state, None)
            self.assertEqual(name, "solo")

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
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            import json
            (state / "leader-session.json").write_text(
                json.dumps({"agent": "claude:opus", "started_at": "2026-01-01T00:00:00+00:00"})
            )
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                name, data = formation.resolve_formation(state, None)
            self.assertEqual(name, "_leader_solo")
            self.assertEqual(data["stages"][0]["agent"], "claude:opus")

    def test_resolve_formation_zero_customs_no_leader_session_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "formations").mkdir(parents=True)
            with self.assertRaises(formation.ResolutionError):
                formation.resolve_formation(state, None)

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


if __name__ == "__main__":
    unittest.main()
