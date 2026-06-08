"""Build the prompt pasted into a freshly-launched leader pane.

Symmetric with driver_prompt.py but much thinner: the leader has no task
metadata or role fragment. Kept small on purpose (design §10.2 /
§1.4 — base prompts must not balloon).
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .paths import fleet_agent_bin

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "docs" / "prompts"
_TEMPLATE_PATH = _PROMPTS_DIR / "leader-base.md"


def _selection_guide_section(state_dir: Path | str | None) -> str:
    """Return ``<state>/formations/SELECTION.md`` wrapped as a prompt section, or ``""``.

    Loads only that one file — a per-project, co-authored guide for *which*
    formation to pick — so the leader sees the project-tuned selection criteria
    when choosing a formation at ``start``. Mirrors the driver prompt's
    ``MEMORY.md`` injection (Issue #114): no-op when the file is missing or
    empty, keeping the prompt lightweight (Issue #118). It is plain-markdown
    guidance, not a mechanism — the leader still decides.
    """
    if state_dir is None:
        return ""
    guide_path = Path(state_dir) / "formations" / "SELECTION.md"
    if not guide_path.is_file():
        return ""
    content = guide_path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    return "## Formation selection guide (this project)\n\n" + content


def render(
    *,
    project_name: str,
    state_dir: Path,
    fleet_bin: str | None = None,
) -> str:
    """Return the prompt string to paste into the leader pane.

    ``fleet-agent`` references in the fleet-managed base text are rewritten to an
    absolute path (Issue #143), mirroring :func:`fleet.driver_prompt.render`, so a
    leader never needs to ``cd`` into the agent-fleet clone to invoke it — the
    ``cd`` is what made cwd-based project resolution land tasks in the wrong
    project. See :func:`fleet.paths.fleet_agent_bin` for why a bare
    ``fleet-agent`` is unreliable.

    When ``<state>/formations/SELECTION.md`` exists, its contents are injected so
    the leader consults the project's own formation-selection criteria when
    picking a formation (Issue #118). The injected guide and the footer are
    project content / metadata, so neither is subject to the path rewrite.
    """
    bin_path = fleet_bin if fleet_bin is not None else fleet_agent_bin()
    base = _TEMPLATE_PATH.read_text(encoding="utf-8").rstrip()
    base = base.replace("fleet-agent", shlex.quote(bin_path))
    selection_section = _selection_guide_section(state_dir)
    if selection_section:
        base = base + "\n\n" + selection_section
    return (
        base
        + "\n\n---\n"
        + f"project:    {project_name}\n"
        + f"state dir:  {state_dir}\n"
        + f"memory:     {state_dir}/memory/MEMORY.md  — read this first\n"
        + f"start cmd:  {shlex.quote(bin_path)} start <id> --project {project_name} --formation <name>"
        + "  (always pass --project)\n"
        + "---\n"
    )
