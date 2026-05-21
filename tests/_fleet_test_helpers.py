"""Shared test helpers for fleet's new central state layout.

All tests that touch fleet state must isolate ``$FLEET_HOME`` to a
tempdir so they don't interact with the live agent-fleet dogfooding state.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))


def make_project(fleet_home: Path, name: str, repo: Path) -> Path:
    """Register *name*→*repo* in *fleet_home* and create the state dir.

    Returns the state_dir path.
    """
    from fleet import state

    old = os.environ.get("FLEET_HOME")
    os.environ["FLEET_HOME"] = str(fleet_home)
    try:
        state_dir = state.project_state_dir(name)
        state.init_state(state_dir, name=name, repo=repo)
        state.register_project(name, repo)
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
