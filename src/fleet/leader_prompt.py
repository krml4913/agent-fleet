"""Build the prompt pasted into a freshly-launched leader pane.

Symmetric with driver_prompt.py but much thinner: the leader has no task
metadata or role fragment. Kept small on purpose (design §10.2 /
§1.4 — base prompts must not balloon).

Project-agnostic since Issue #166: a leader session is not bound to a project
(§4.1), so :func:`render` takes no ``project_name`` / ``state_dir`` and no longer
startup-injects a project's ``SELECTION.md``. It injects the **global**
leader-memory index instead (the two-tier memory's GLOBAL layer, §6, mirroring
the driver ``MEMORY.md`` injection of Issue #114); per-project discipline —
``projects/<name>/memory/`` and ``formations/SELECTION.md`` — is read **first
touch** by the leader (§4.1, §12.8), pointed at by ``leader-base.md``.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from . import state as state_mod
from .paths import fleet_agent_bin

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "docs" / "prompts"
_TEMPLATE_PATH = _PROMPTS_DIR / "leader-base.md"


def _scope_roster_section(session_label: str) -> str:
    """Return a prompt section listing the session's scoped projects, or ``""``.

    When the session has a declared scope, injects a short roster so the leader
    knows which projects it is responsible for without needing to run a command.
    Unscoped sessions get a brief note listing all registered projects.
    Empty / unresolvable registry: section omitted.
    """
    from . import state as _state
    reg = _state.load_registry()
    all_projects = sorted(reg.get("projects", {}).keys())
    if not all_projects:
        return ""

    scope = _state.session_scope(session_label)
    if scope is not None:
        items = "\n".join(f"- {p}" for p in scope)
        return (
            "## Projects in scope\n\n"
            + items
            + "\n\nDispatch only to these unless the user explicitly asks otherwise."
        )
    else:
        listed = ", ".join(all_projects)
        return (
            "## Projects\n\n"
            f"This session is unscoped (serves all registered projects): {listed}."
        )


def _global_memory_index_section() -> str:
    """Return the GLOBAL leader-memory index wrapped as a prompt section, or ``""``.

    Loads only the index (``global/leader-memory/MEMORY.md``) — not every memory
    body — so a leader of any vendor starts every session with its user-global
    preferences and router operating rules (the two-tier memory's GLOBAL layer,
    design §6). Mirrors the driver prompt's ``MEMORY.md`` injection (Issue #114):
    a no-op when the file is missing or empty, keeping the prompt lightweight.
    """
    index_path = state_mod.global_leader_memory_dir() / "MEMORY.md"
    if not index_path.is_file():
        return ""
    content = index_path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    return "## Leader memory (global index)\n\n" + content


def render(*, fleet_bin: str | None = None, session_label: str | None = None) -> str:
    """Return the prompt string to paste into a leader pane.

    Project-agnostic (Issue #166): there is no project to name at startup, so the
    prompt carries the generic protocol (``leader-base.md``), the injected global
    leader-memory index, and a footer pointing at the global store plus the
    ``--project``-mandatory ``start`` shape. The leader reads per-project
    discipline first-touch (§4.1) rather than having it injected here.

    ``fleet-agent`` references in the fleet-managed base text are rewritten to an
    absolute path (Issue #143), mirroring :func:`fleet.driver_prompt.render`, so a
    leader never needs to ``cd`` into the agent-fleet clone to invoke it. The
    injected global index and the footer are content / metadata, so neither is
    subject to the path rewrite.
    """
    bin_path = fleet_bin if fleet_bin is not None else fleet_agent_bin()
    base = _TEMPLATE_PATH.read_text(encoding="utf-8").rstrip()
    base = base.replace("fleet-agent", shlex.quote(bin_path))

    parts = [base]
    memory_section = _global_memory_index_section()
    if memory_section:
        parts.append(memory_section)
    if session_label:
        roster_section = _scope_roster_section(session_label)
        if roster_section:
            parts.append(roster_section)
    body = "\n\n".join(parts)

    global_index = state_mod.global_leader_memory_dir() / "MEMORY.md"
    return (
        body
        + "\n\n---\n"
        + f"global memory:  {global_index}  — read this first\n"
        + f"start cmd:  {shlex.quote(bin_path)} start <id> --project <name> --formation <name>"
        + "  (always pass --project)\n"
        + "---\n"
    )
