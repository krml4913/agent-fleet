"""Build the prompt pasted into a freshly-launched leader pane.

Symmetric with driver_prompt.py but much thinner: the leader has no task
metadata or role fragment. Kept small on purpose (design §10.2 /
§1.4 — base prompts must not balloon).
"""
from __future__ import annotations

from pathlib import Path

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


def render(*, project_name: str, state_dir: Path) -> str:
    """Return the prompt string to paste into the leader pane.

    When ``<state>/formations/SELECTION.md`` exists, its contents are injected so
    the leader consults the project's own formation-selection criteria when
    picking a formation (Issue #118). Absent, nothing is injected.
    """
    base = _TEMPLATE_PATH.read_text(encoding="utf-8").rstrip()
    selection_section = _selection_guide_section(state_dir)
    if selection_section:
        base = base + "\n\n" + selection_section
    return (
        base
        + "\n\n---\n"
        + f"project:    {project_name}\n"
        + f"state dir:  {state_dir}\n"
        + f"memory:     {state_dir}/memory/MEMORY.md  — read this first\n"
        + "---\n"
    )
