"""``fleet preflight`` — environment dependency check.

Verifies the toolbelt fleet relies on: Python version, tmux, git, and
the agent CLIs (claude / codex). Required tools missing → exit 1;
optional tools missing → warn but continue.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from typing import NamedTuple


class CheckResult(NamedTuple):
    name: str
    ok: bool
    detail: str
    required: bool


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "preflight",
        help="Check environment dependencies",
        description=(
            "Verify Python >=3.11, tmux, git, and the agent CLIs "
            "(claude / codex). Optional tools missing → warn; required "
            "tools missing → exit 1."
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    results = check_all()
    rc = 0
    for r in results:
        mark = "✔" if r.ok else ("✘" if r.required else "⚠")
        kind = "required" if r.required else "optional"
        print(f"  {mark} {r.name:<8} {kind:<8} {r.detail}")
        if r.required and not r.ok:
            rc = 1
    print()
    if rc == 0:
        print("preflight: ok")
    else:
        print("preflight: missing required tool(s)", file=sys.stderr)
    return rc


def check_all() -> list[CheckResult]:
    return [
        _check_python(),
        _check_command("tmux", ["tmux", "-V"], required=True),
        _check_command("git", ["git", "--version"], required=True),
        _check_command("claude", ["claude", "--version"], required=False),
        _check_command("codex", ["codex", "--version"], required=False),
    ]


def _check_python() -> CheckResult:
    ok = sys.version_info >= (3, 11)
    detail = (
        f"{sys.version.split()[0]} (need >=3.11)"
        if not ok
        else f"{sys.version.split()[0]}"
    )
    return CheckResult("python", ok, detail, required=True)


def _check_command(name: str, version_argv: list[str], *, required: bool) -> CheckResult:
    if not shutil.which(name):
        return CheckResult(name, False, "not on PATH", required)
    try:
        r = subprocess.run(version_argv, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return CheckResult(name, False, f"non-zero from `{name}`", required)
        detail = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "found"
        return CheckResult(name, True, detail, required)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return CheckResult(name, False, f"{type(e).__name__}", required)
