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
