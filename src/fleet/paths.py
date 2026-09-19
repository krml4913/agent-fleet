"""Shared filesystem-path helpers for fleet-managed prompts.

Holds the neutral helpers both prompt builders need —
:func:`fleet_agent_bin` and :func:`prompt_bin_ref` — so neither
``driver_prompt`` nor ``leader_prompt`` has to import the other (an awkward
sibling dependency). Kept tiny and free of CLI / state concerns.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

# src/fleet/paths.py → parents[0]=fleet, [1]=src, [2]=clone root
_CLONE_ROOT = Path(__file__).resolve().parents[2]


def _is_windows() -> bool:
    return sys.platform == "win32"


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

    On Windows the extensionless Python script cannot be run by PowerShell /
    cmd (and Git Bash's shebang lookup hits the Microsoft Store ``python3``
    stub), so the ``fleet-agent.cmd`` shim is returned instead, with forward
    slashes: every shell an agent may use (PowerShell, cmd, Git Bash) accepts
    ``D:/x/fleet-agent.cmd``, while Git Bash eats unquoted backslashes.
    """
    if _is_windows():
        candidate = _CLONE_ROOT / "fleet-agent.cmd"
        if candidate.is_file():
            return candidate.as_posix()
        return "fleet-agent"
    candidate = _CLONE_ROOT / "fleet-agent"
    if candidate.is_file():
        return str(candidate)
    return "fleet-agent"


def prompt_bin_ref(bin_path: str) -> str:
    """Return ``bin_path`` as it should be embedded in an agent prompt.

    POSIX: ``shlex.quote`` (the agent types it into a POSIX shell).

    Windows: the forward-slash path is embedded *unquoted*. PowerShell (the
    shell codex uses) cannot invoke a single-quoted path without ``&``, and
    no single quoting works in PowerShell, cmd and Git Bash alike.
    Known limitation: a clone path containing spaces therefore breaks the
    embedded command on Windows — clone agent-fleet to a path without spaces
    (a preflight warning for this is planned in a later PR).
    """
    if _is_windows():
        return bin_path
    return shlex.quote(bin_path)
