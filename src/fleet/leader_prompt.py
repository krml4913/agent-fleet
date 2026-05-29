"""Build the prompt pasted into a freshly-launched leader pane.

Symmetric with driver_prompt.py but much thinner: the leader has no task
metadata or role fragment. Kept small on purpose (design §10.2 /
§1.4 — base prompts must not balloon).
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "docs" / "prompts"
_TEMPLATE_PATH = _PROMPTS_DIR / "leader-base.md"


def render(*, project_name: str, state_dir: Path) -> str:
    """Return the prompt string to paste into the leader pane."""
    base = _TEMPLATE_PATH.read_text(encoding="utf-8").rstrip()
    return (
        base
        + "\n\n---\n"
        + f"project:    {project_name}\n"
        + f"state dir:  {state_dir}\n"
        + f"memory:     {state_dir}/memory/MEMORY.md  — read this first\n"
        + "---\n"
    )
