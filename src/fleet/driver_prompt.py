"""Build the initial prompt that a freshly-spawned driver reads.

Kept intentionally small. Design doc §10.2 calls out claude-forge's bloated
1000-line driver-prompts as the root cause of boot timeouts; this module
must resist accumulating optional context. New context belongs in a
plugin hook, not here.
"""
from __future__ import annotations

BASE = """\
You are a fleet driver — one agent inside a multi-agent team.
Your job is the task described below. Work it to completion.

Environment:
  - FLEET_TASK_ID and FLEET_STATE_DIR are pre-set in this pane.
  - `fleet-agent ask` / `fleet-agent event emit` / `fleet-agent done` resolve
    the task automatically from those — no --task-id needed.

Communication:
  - inbox.md   — instructions from the leader; check it each turn.
  - outbox.md  — append reports here at milestones.
  - `fleet-agent ask "<question>"`           — record needs_input + notify user.
  - `fleet-agent event emit <type> [...]`    — append an audit event.

Rules:
  - Never edit dashboard.md; it is auto-generated.
  - If you need user input, you MUST call `fleet-agent ask`. Writing the
    question into the pane alone will not reach anyone.
  - Between long tool calls, emit a heartbeat:
        fleet-agent event emit heartbeat
    so `fleet status` and the dashboard's "Last seen" column stay fresh.
  - When done, call `fleet-agent done` so the task is marked complete.
"""


def render(
    *,
    task_id: str,
    description: str,
    topology_name: str,
    role: str,
    agent: str,
) -> str:
    """Return the prompt string to send to the driver."""
    return (
        BASE
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
