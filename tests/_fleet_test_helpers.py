"""Shared test helpers for fleet's new central state layout.

All tests that touch fleet state must isolate ``$FLEET_HOME`` to a
tempdir so they don't interact with the live agent-fleet dogfooding state.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

os.environ.setdefault("FLEET_NO_NOTIFY", "1")

# Opt-in gate for tests that create REAL tmux sessions (and, for the leader
# launch tests, a real claude-code CLI session). These are skipped by default so
# a plain `python -m unittest` run leaks zero sessions onto the developer's
# machine. Set FLEET_LIVE_TMUX=1 (and have tmux installed) to run them.
requires_live_tmux = unittest.skipUnless(
    os.environ.get("FLEET_LIVE_TMUX") and shutil.which("tmux"),
    "live-tmux test: set FLEET_LIVE_TMUX=1 (and have tmux) to run",
)


def seed_global_roles(fleet_home: Path, *names: str) -> Path:
    """Populate a test-only global roles tier from shipped prompt files."""
    roles_dir = fleet_home / "global" / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    source_dir = ROOT / "docs" / "prompts" / "roles"
    selected = names or tuple(p.stem for p in sorted(source_dir.glob("*.md")))
    for name in selected:
        shutil.copyfile(source_dir / f"{name}.md", roles_dir / f"{name}.md")
    return roles_dir


def make_project(fleet_home: Path, name: str, repo: Path) -> Path:
    """Register *name*→*repo* in *fleet_home* and create the state dir.

    Returns the state_dir path.  The formations/ directory is created and
    populated with solo.yaml and pair_review.yaml so start tests work without
    an explicit --formation flag or leader-session.json.
    """
    import shutil
    from fleet import state, formation as formation_mod

    old = os.environ.get("FLEET_HOME")
    os.environ["FLEET_HOME"] = str(fleet_home)
    try:
        state_dir = state.project_state_dir(name)
        state.init_state(state_dir, name=name, repo=repo)
        state.register_project(name, repo)
        seed_global_roles(fleet_home)

        formations_dir = state_dir / "formations"
        formations_dir.mkdir(exist_ok=True)
        src = formation_mod.TEMPLATES_DIR / "solo.yaml"
        if src.is_file():
            shutil.copyfile(src, formations_dir / "solo.yaml")

        return state_dir
    finally:
        if old is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = old


def run_fleet(*args: str, fleet_home: Path | None = None, cwd: Path | None = None,
              env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    """Run the ``fleet`` CLI as a subprocess with FLEET_HOME isolated."""
    FLEET = ROOT / "fleet"
    env = os.environ.copy()
    env.pop("FLEET_TASK_ID", None)
    env.pop("FLEET_STATE_DIR", None)  # prevent leader-pane env leaking into tests
    if fleet_home is not None:
        env["FLEET_HOME"] = str(fleet_home)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


def run_fleet_agent(*args: str, fleet_home: Path | None = None, cwd: Path | None = None,
                    env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    """Run the ``fleet-agent`` CLI as a subprocess with FLEET_HOME isolated."""
    FLEET_AGENT = ROOT / "fleet-agent"
    env = os.environ.copy()
    env.pop("FLEET_TASK_ID", None)
    env.pop("FLEET_STATE_DIR", None)  # prevent leader-pane env leaking into tests
    if fleet_home is not None:
        env["FLEET_HOME"] = str(fleet_home)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(FLEET_AGENT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )
