"""Tests for ``fleet.driver_prompt.render``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import driver_prompt  # noqa: E402


class DriverPromptTests(unittest.TestCase):
    def test_includes_required_fields(self) -> None:
        text = driver_prompt.render(
            task_id="42",
            description="Implement the foo feature.",
            topology_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        self.assertIn("task id:   task-42", text)
        self.assertIn("topology:  solo", text)
        self.assertIn("role:      driver", text)
        self.assertIn("agent:     claude:sonnet", text)
        self.assertIn("Implement the foo feature.", text)

    def test_keeps_prompt_under_budget(self) -> None:
        """Guard against driver-prompt bloat regression (design doc §10.2)."""
        text = driver_prompt.render(
            task_id="1",
            description="x",
            topology_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        line_count = text.count("\n")
        # Be generous; we just want a tripwire if BASE balloons.
        self.assertLess(line_count, 40, f"driver-prompt got fat: {line_count} lines")

    def test_mentions_fleet_agent_ask_rule(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            topology_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        self.assertIn("fleet-agent ask", text)


if __name__ == "__main__":
    unittest.main()
