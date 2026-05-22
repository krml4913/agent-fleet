"""``fleet preflight`` — environment dependency check.

Verifies the toolbelt fleet relies on: Python version, tmux, workflow
dependencies, and the agent CLIs (claude / codex). Required tools
missing → exit 1; optional tools missing → warn but continue.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from .. import agents as agents_mod
from .. import plugins as plugins_mod
from .. import state as state_mod


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
    git_required = _git_required_for_cwd(Path.cwd())
    return [
        _check_python(),
        _check_command("tmux", ["tmux", "-V"], required=True),
        _check_command("git", ["git", "--version"], required=git_required),
        _check_command("claude", ["claude", "--version"], required=False),
        _check_command("codex", ["codex", "--version"], required=False),
        _check_codex_update(),
        _check_codex_trust(),
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


def _git_required_for_cwd(cwd: Path) -> bool:
    state_dir = state_mod.resolve_state_dir(cwd)
    if state_dir is None:
        return False
    try:
        workflow = plugins_mod.load_workflow(state_dir)
    except Exception:
        return False
    return getattr(workflow, "WORKFLOW_NAME", "bare") == "git_worktree"


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    root = r.stdout.strip()
    return Path(root).resolve() if root else None


def _check_codex_trust() -> CheckResult:
    if not shutil.which("codex"):
        return CheckResult(
            "codex-trust",
            True,
            "skipped (codex not on PATH)",
            required=False,
        )

    repo_root = _git_toplevel(Path.cwd())
    if repo_root is None:
        return CheckResult(
            "codex-trust",
            True,
            "skipped (not in a git repo)",
            required=False,
        )

    if agents_mod.codex_repo_trusted(repo_root):
        return CheckResult("codex-trust", True, f"trusted: {repo_root}", required=False)
    return CheckResult(
        "codex-trust",
        False,
        f"not trusted: {repo_root} (run `codex` here and approve)",
        required=False,
    )


def _check_codex_update() -> CheckResult:
    codex_path = shutil.which("codex")
    if not codex_path:
        return CheckResult(
            "codex-update",
            True,
            "skipped (codex not on PATH)",
            required=False,
        )

    current = _codex_version()
    if current is None:
        return CheckResult(
            "codex-update",
            False,
            "could not parse `codex --version`",
            required=False,
        )

    latest = _npm_latest_codex_version()
    npm_global = _npm_global_codex_version()

    details: list[str] = []
    ok = True
    if latest and _version_lt(current, latest):
        ok = False
        details.append(
            f"{current} < latest {latest}; update prompt may appear"
        )
    elif latest:
        details.append(f"{current} is current")
    else:
        details.append(f"{current}; latest unavailable")

    if npm_global and npm_global != current:
        ok = False
        details.append(
            f"npm global @openai/codex is {npm_global} but PATH runs {codex_path}"
        )

    if not ok:
        details.append("fleet launches codex with update checks disabled")
    return CheckResult("codex-update", ok, "; ".join(details), required=False)


def _codex_version() -> str | None:
    try:
        r = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    text = (r.stdout or r.stderr).strip().splitlines()
    if not text:
        return None
    return _extract_version(text[0])


def _npm_latest_codex_version() -> str | None:
    if not shutil.which("npm"):
        return None
    try:
        r = subprocess.run(
            ["npm", "view", "@openai/codex", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return _extract_version(r.stdout.strip())


def _npm_global_codex_version() -> str | None:
    if not shutil.which("npm"):
        return None
    try:
        r = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    root = r.stdout.strip()
    if not root:
        return None
    package_json = Path(root) / "@openai" / "codex" / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return _extract_version(version) if isinstance(version, str) else None


def _extract_version(text: str) -> str | None:
    for part in text.replace(",", " ").split():
        candidate = part.strip()
        if _version_tuple(candidate) is not None:
            return candidate
    return None


def _version_lt(left: str, right: str) -> bool:
    left_tuple = _version_tuple(left)
    right_tuple = _version_tuple(right)
    if left_tuple is None or right_tuple is None:
        return False
    return left_tuple < right_tuple


def _version_tuple(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)
