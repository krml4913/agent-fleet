"""Build the initial prompt that a freshly-spawned driver reads.

Kept intentionally small. Design doc §10.2 calls out claude-forge's bloated
1000-line driver-prompts as the root cause of boot timeouts; this module
must resist accumulating optional context. New context belongs in a
plugin hook, not here.
"""
from __future__ import annotations

import shlex
from pathlib import Path

_CLONE_ROOT = Path(__file__).resolve().parent.parent.parent
_PROMPTS_DIR = _CLONE_ROOT / "docs" / "prompts"
_TEMPLATE_PATH = _PROMPTS_DIR / "driver-base.md"
_ROLES_DIR = _PROMPTS_DIR / "roles"


def fleet_agent_bin() -> str:
    """Return the path used to invoke ``fleet-agent`` from inside a driver pane.

    Driver panes do not get ``fleet-agent`` on their ``PATH``: ``start`` injects
    ``PATH=<clone-root>:…`` via ``tmux new-window -e``, but macOS ``path_helper``
    (run by ``/etc/zprofile`` on every zsh login) plus the user's rc files rebuild
    ``PATH`` from scratch and drop the injected entry. Plain env vars
    (``FLEET_TASK_ID`` etc.) survive that rebuild; ``PATH`` does not. So lifecycle
    signaling commands must be referenced by absolute path — the one channel that
    is identical for both vendors (claude/codex) and immune to the shell-rc
    ``PATH`` rebuild.

    Returns the absolute path to the ``fleet-agent`` script when it can be located
    next to this package, otherwise falls back to the bare ``fleet-agent`` name.
    """
    candidate = _CLONE_ROOT / "fleet-agent"
    if candidate.is_file():
        return str(candidate)
    return "fleet-agent"


def _load_base() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _load_role_fragment(role: str) -> str:
    if "/" in role or "\\" in role:
        return ""
    path = _ROLES_DIR / f"{role}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _memory_index_section(state_dir: Path | str | None) -> str:
    """Return the ``MEMORY.md`` index wrapped as a prompt section, or ``""``.

    Loads only the index (``<state>/memory/MEMORY.md``) — not every memory body —
    so any vendor driver sees the accumulated project knowledge at task start.
    Returns an empty string when no state dir is given or the index is absent,
    keeping the prompt lightweight (design direction, Issue #114).
    """
    if state_dir is None:
        return ""
    index_path = Path(state_dir) / "memory" / "MEMORY.md"
    if not index_path.is_file():
        return ""
    content = index_path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    return "## Project memory (index)\n\n" + content


def render(
    *,
    task_id: str,
    description: str,
    formation_name: str,
    role: str,
    agent: str,
    fleet_bin: str | None = None,
    state_dir: Path | str | None = None,
) -> str:
    """Return the prompt string to send to the driver.

    ``fleet-agent`` references in the fleet-managed prompt text (base + role
    fragment) are rewritten to an absolute path so the driver can run lifecycle
    commands regardless of whether ``fleet-agent`` is on ``PATH`` — see
    :func:`fleet_agent_bin` for why a bare ``fleet-agent`` is unreliable. The
    user-supplied ``description`` is left untouched.

    When ``state_dir`` is given and ``<state>/memory/MEMORY.md`` exists, its
    index is injected so any vendor driver starts with the shared project
    knowledge (Issue #114). The injected index is project content, so it is not
    subject to the ``fleet-agent`` path rewrite.
    """
    bin_path = fleet_bin if fleet_bin is not None else fleet_agent_bin()
    base = _load_base()
    parts = [base.rstrip()]
    role_fragment = _load_role_fragment(role).strip()
    if role_fragment:
        parts.append(role_fragment)
    body = "\n\n".join(parts)
    body = body.replace("fleet-agent", shlex.quote(bin_path))
    memory_section = _memory_index_section(state_dir)
    if memory_section:
        body = body + "\n\n" + memory_section
    return (
        body
        + "\n---\n"
        + f"task id:   task-{task_id}\n"
        + f"formation:  {formation_name}\n"
        + f"role:      {role}\n"
        + f"agent:     {agent}\n"
        + "---\n\n"
        + "Task description:\n\n"
        + description.rstrip()
        + "\n"
    )
