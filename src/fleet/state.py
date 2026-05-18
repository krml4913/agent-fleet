"""state directory bootstrap and helpers.

For MVP this only supports :func:`init_state` to create a fresh
``.fleet-state/``. File-based state is the source of truth; updates use
``flock`` + atomic rename (see §5.3-5.5 of docs/design.md). Those helpers
are added in Phase 2.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_state(state_dir: Path, *, name: str) -> None:
    """Create a fresh ``.fleet-state/`` directory tree.

    Caller is expected to verify ``state_dir`` does not exist; this function
    will let ``FileExistsError`` propagate if it does.
    """
    state_dir.mkdir(parents=True)
    (state_dir / "tasks").mkdir()
    (state_dir / "events.jsonl").touch()

    # MVP: simple key:value YAML — vendored PyYAML is introduced in Phase 3.
    project_yaml = state_dir / "project.yaml"
    project_yaml.write_text(
        f"name: {name}\n"
        f"created_at: {_utcnow_iso()}\n"
        f"version: 0.0.1\n"
    )
