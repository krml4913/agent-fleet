"""Shared filesystem-path helpers for fleet-managed prompts.

Holds the one neutral helper both prompt builders need —
:func:`fleet_agent_bin` — so neither ``driver_prompt`` nor ``leader_prompt``
has to import the other (an awkward sibling dependency). Kept tiny and free
of CLI / state concerns.
"""
from __future__ import annotations

from pathlib import Path

# src/fleet/paths.py → parents[0]=fleet, [1]=src, [2]=clone root
_CLONE_ROOT = Path(__file__).resolve().parents[2]


def fleet_agent_bin() -> str:
    """Return the path used to invoke ``fleet-agent`` from inside an agent pane.

    Agent panes (driver *and* leader) do not get ``fleet-agent`` on their
    ``PATH``: ``start`` / ``leader`` inject ``PATH=<clone-root>:…`` via
    ``tmux new-window -e``, but macOS ``path_helper`` (run by ``/etc/zprofile``
    on every zsh login) plus the user's rc files rebuild ``PATH`` from scratch
    and drop the injected entry. Plain env vars (``FLEET_TASK_ID`` etc.) survive
    that rebuild; ``PATH`` does not. So lifecycle signaling commands must be
    referenced by absolute path — the one channel that is identical for both
    vendors (claude/codex) and immune to the shell-rc ``PATH`` rebuild.

    Returns the absolute path to the ``fleet-agent`` script when it can be
    located next to this package, otherwise falls back to the bare
    ``fleet-agent`` name.
    """
    candidate = _CLONE_ROOT / "fleet-agent"
    if candidate.is_file():
        return str(candidate)
    return "fleet-agent"
