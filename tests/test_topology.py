"""Tests for ``fleet.topology`` — load / validate / list (presets + custom)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import topology  # noqa: E402


class TopologyTests(unittest.TestCase):
    # ---- listing presets ----

    def test_list_presets_includes_known(self) -> None:
        names = set(topology.list_presets())
        self.assertIn("solo", names)
        self.assertIn("pair_review", names)
        self.assertIn("multi_stage", names)
        self.assertNotIn("race", names)

    # ---- loading + validating presets ----

    def test_each_preset_loads_and_validates(self) -> None:
        for name in topology.list_presets():
            data = topology.load_preset(name)
            topology.validate(data)
            self.assertEqual(data["name"], name)

    def test_load_preset_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            topology.load_preset("no-such-topology")

    # ---- custom topology resolution ----

    def test_custom_overrides_preset(self) -> None:
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / ".fleet-state"
            (state / "topologies").mkdir(parents=True)
            (state / "topologies" / "solo.yaml").write_text(
                "name: solo\n"
                "description: custom override\n"
                "roles:\n"
                "  - role: driver\n"
                "    agent: codex:o4-mini\n"
            )
            data = topology.load("solo", state_dir=state)
            self.assertEqual(data["description"], "custom override")
            self.assertEqual(data["roles"][0]["agent"], "codex:o4-mini")

    def test_load_falls_back_to_preset(self) -> None:
        data = topology.load("solo", state_dir=None)
        self.assertEqual(data["name"], "solo")

    def test_list_custom_handles_missing_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(topology.list_custom(Path(tmp)), [])

    # ---- expand_stages ----

    def test_expand_stages_solo(self) -> None:
        data = topology.load_preset("solo")
        stages = topology.expand_stages(data)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["role"], "driver")
        self.assertEqual(stages[0]["agent"], "claude:sonnet")
        self.assertEqual(stages[0]["status"], "pending")

    def test_expand_stages_pair_review(self) -> None:
        data = topology.load_preset("pair_review")
        stages = topology.expand_stages(data)
        self.assertEqual(len(stages), 2)
        self.assertEqual(stages[0]["role"], "implementer")
        self.assertEqual(stages[1]["role"], "reviewer")
        for s in stages:
            self.assertEqual(s["status"], "pending")

    def test_expand_stages_user_approval_normalised(self) -> None:
        data = topology.load_preset("pair_review")
        stages = topology.expand_stages(data)
        # reviewer has user_approval: required → should be normalised to object
        reviewer = stages[1]
        self.assertIn("user_approval", reviewer)
        ua = reviewer["user_approval"]
        self.assertIsInstance(ua, dict)
        self.assertTrue(ua["required"])
        self.assertEqual(ua["status"], "pending")

    def test_expand_stages_multi_stage(self) -> None:
        data = topology.load_preset("multi_stage")
        stages = topology.expand_stages(data)
        self.assertEqual(len(stages), 2)
        self.assertEqual(stages[0]["role"], "designer")
        self.assertEqual(stages[1]["role"], "implementer")

    def test_expand_stages_empty_topology(self) -> None:
        stages = topology.expand_stages({"name": "x", "stages": []})
        self.assertEqual(stages, [])

    def test_expand_stages_falls_back_to_roles(self) -> None:
        data = {"name": "x", "roles": [{"role": "coder", "agent": "claude:sonnet"}]}
        stages = topology.expand_stages(data)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["role"], "coder")
        self.assertEqual(stages[0]["status"], "pending")

    # ---- validation ----

    def test_validate_rejects_missing_name(self) -> None:
        with self.assertRaises(ValueError):
            topology.validate({"roles": [{"role": "x", "agent": "y"}]})

    def test_validate_rejects_missing_shape(self) -> None:
        with self.assertRaises(ValueError):
            topology.validate({"name": "x"})

    def test_validate_accepts_each_shape(self) -> None:
        topology.validate({"name": "a", "roles": [{"role": "r"}]})
        topology.validate({"name": "b", "stages": [{"role": "s"}]})


if __name__ == "__main__":
    unittest.main()
