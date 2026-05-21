"""Build the initial prompt that a freshly-spawned driver reads.

Kept intentionally small. Design doc §10.2 calls out claude-forge's bloated
1000-line driver-prompts as the root cause of boot timeouts; this module
must resist accumulating optional context. New context belongs in a
plugin hook, not here.
"""
from __future__ import annotations

from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent.parent / "docs" / "prompts" / "driver-base.md"


def _load_base() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def render(
    *,
    task_id: str,
    description: str,
    topology_name: str,
    role: str,
    agent: str,
    workflow_fragment: str = "",
) -> str:
    """Return the prompt string to send to the driver."""
    base = _load_base()
    parts = [base.rstrip()]
    fragment = workflow_fragment.strip()
    if fragment:
        parts.append(fragment)
    body = "\n\n".join(parts)
    return (
        body
        + "\n---\n"
        + f"task id:   task-{task_id}\n"
        + f"topology:  {topology_name}\n"
        + f"role:      {role}\n"
        + f"agent:     {agent}\n"
        + "---\n\n"
        + "Task description:\n\n"
        + description.rstrip()
        + "\n"
    )
