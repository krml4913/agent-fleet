"""Shared test helpers for fleet's new central state layout.

All tests that touch fleet state must isolate ``$FLEET_HOME`` to a
tempdir so they don't interact with the live agent-fleet dogfooding state.
Importing this module makes that the default: see ``AMBIENT_FLEET_HOME``.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

os.environ.setdefault("FLEET_NO_NOTIFY", "1")

# Hermetic global tier (#319): point the *ambient* ``$FLEET_HOME`` at a
# throwaway dir before any test imports fleet. Without it, every test that
# does not set its own FLEET_HOME resolves ``<clone>/fleet-state`` -- the
# developer's live state -- so a seeded ``global/`` tier (formations, roles,
# config, sessions) leaks into assertions, and anything that moves a task
# through the orchestrator rewrites the real ``global/dashboard.html`` and
# touches ``global/sessions/<label>/``. An inherited FLEET_HOME is overridden
# on purpose: the suite must never run against a real state dir. Tests that
# need a populated tier still set ``$FLEET_HOME`` per test, on top of this.
AMBIENT_FLEET_HOME = tempfile.mkdtemp(prefix="fleet-test-home-")
os.environ["FLEET_HOME"] = AMBIENT_FLEET_HOME
atexit.register(shutil.rmtree, AMBIENT_FLEET_HOME, ignore_errors=True)

# Hermetic by default: no test may create a REAL multiplexer session (tmux, or
# zellij — the default backend on Windows) unless live tests are explicitly
# opted into. ``FLEET_NO_MUX`` makes every backend report unavailable (and the
# zellij backend refuse to run at all); subprocesses inherit it.
if not (os.environ.get("FLEET_LIVE_TMUX") or os.environ.get("FLEET_LIVE_ZELLIJ")):
    os.environ.setdefault("FLEET_NO_MUX", "1")

# Skip fsync in fleet.locking's atomic writes: durability after a power loss
# is not observable from a unit test, and ~2k fsyncs were ~15% of suite wall
# time on Windows. The write/rename/lock logic still runs unchanged. Set
# FLEET_TEST_FSYNC=1 to keep the real syscall.
if not os.environ.get("FLEET_TEST_FSYNC"):
    from fleet import locking as _locking

    _locking.fsync = lambda _fd: None

# Windows: tests that call command ``run()`` functions in-process print
# ``·`` / ``—`` to the runner's stdout, which uses the ANSI code page (e.g.
# cp932) when piped. The real CLI entrypoints reconfigure stdio to UTF-8
# (``fleet.cli._ensure_utf8_stdio``); mirror that for the test process.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

# Opt-in gate for tests that create REAL tmux sessions (and, for the leader
# launch tests, a real claude-code CLI session). These are skipped by default so
# a plain `python -m unittest` run leaks zero sessions onto the developer's
# machine. Set FLEET_LIVE_TMUX=1 (and have tmux installed) to run them.
requires_live_tmux = unittest.skipUnless(
    os.environ.get("FLEET_LIVE_TMUX") and shutil.which("tmux"),
    "live-tmux test: set FLEET_LIVE_TMUX=1 (and have tmux) to run",
)

# Same for the zellij backend: set FLEET_LIVE_ZELLIJ=1 (and have zellij on PATH,
# or FLEET_ZELLIJ pointing at it) to run the tests that create real zellij sessions.
requires_live_zellij = unittest.skipUnless(
    os.environ.get("FLEET_LIVE_ZELLIJ") and shutil.which(os.environ.get("FLEET_ZELLIJ") or "zellij"),
    "live-zellij test: set FLEET_LIVE_ZELLIJ=1 (and have zellij) to run",
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


#: Set ``FLEET_TEST_SUBPROCESS=1`` to run every :func:`run_fleet` /
#: :func:`run_fleet_agent` call as a real ``python fleet …`` subprocess (the
#: pre-speedup behaviour) — handy when bisecting an in-process artefact.
SUBPROCESS_CLI = os.environ.get("FLEET_TEST_SUBPROCESS") == "1"


def _cli_env(fleet_home: Path | None, env_extra: dict | None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("FLEET_TASK_ID", None)
    env.pop("FLEET_STATE_DIR", None)  # prevent leader-pane env leaking into tests
    if fleet_home is not None:
        env["FLEET_HOME"] = str(fleet_home)
    if env_extra:
        env.update(env_extra)
    return env


def _refuse_exec(*_args, **_kwargs):
    raise RuntimeError("in-process CLI test attempted os.exec*; use a subprocess test")


def _run_cli_in_process(entry_name: str, prog: str, args: tuple[str, ...], env: dict[str, str],
                        cwd: Path | None, stdin: str | None) -> subprocess.CompletedProcess[str]:
    """Run ``fleet.cli.<entry_name>(args)`` in this process, subprocess-style.

    Emulates what a child process would see: the environment is *replaced* by
    ``env`` (and restored afterwards), the cwd is switched, stdin is ``stdin`` (default empty),
    stdout/stderr are captured, the process-wide mux backend and the
    global-config cache start unselected / unloaded (re-chosen from ``env``),
    and ``SystemExit`` / uncaught exceptions become a return code (1 +
    traceback on stderr, like the interpreter). ``os.exec*`` is refused so a
    stray attach can never replace the test runner.
    """
    import contextlib
    import io
    import traceback
    from unittest import mock

    from fleet import cli, config, mux

    out, err = io.StringIO(), io.StringIO()
    saved_env = os.environ.copy()
    saved_cwd = os.getcwd()
    saved_backend = mux.set_backend(None)
    config.reset_cache()
    saved_stdin = sys.stdin
    rc: int
    try:
        os.environ.clear()
        os.environ.update(env)
        if cwd is not None:
            os.chdir(cwd)
        sys.stdin = io.StringIO(stdin or "")
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            for name in ("execv", "execve", "execvp", "execvpe", "execl", "execlp"):
                if hasattr(os, name):
                    stack.enter_context(mock.patch.object(os, name, _refuse_exec))
            try:
                rc = getattr(cli, entry_name)(list(args))
            except SystemExit as e:
                if e.code is None:
                    rc = 0
                elif isinstance(e.code, int):
                    rc = e.code
                else:
                    print(e.code, file=sys.stderr)
                    rc = 1
            except Exception:  # noqa: BLE001 - mirror an interpreter crash
                traceback.print_exc()
                rc = 1
    finally:
        sys.stdin = saved_stdin
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
        mux.set_backend(saved_backend)
        config.reset_cache()
    return subprocess.CompletedProcess([prog, *args], rc, out.getvalue(), err.getvalue())


def _run_cli_subprocess(script: str, args: tuple[str, ...], env: dict[str, str],
                        cwd: Path | None, stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        capture_output=True,
        text=True, encoding="utf-8",
        cwd=str(cwd) if cwd else None,
        env=env,
        input=stdin,
    )


def run_fleet(*args: str, fleet_home: Path | None = None, cwd: Path | None = None,
              env_extra: dict | None = None, stdin: str | None = None,
              subprocess_: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the ``fleet`` CLI with FLEET_HOME isolated.

    In-process by default (``fleet.cli.main``; see :func:`_run_cli_in_process`),
    which is ~50x cheaper than spawning an interpreter. Pass
    ``subprocess_=True`` for a true end-to-end run of the ``fleet`` script.
    """
    env = _cli_env(fleet_home, env_extra)
    if subprocess_ or SUBPROCESS_CLI:
        return _run_cli_subprocess("fleet", args, env, cwd, stdin)
    return _run_cli_in_process("main", "fleet", args, env, cwd, stdin)


def run_fleet_agent(*args: str, fleet_home: Path | None = None, cwd: Path | None = None,
                    env_extra: dict | None = None, stdin: str | None = None,
                    subprocess_: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the ``fleet-agent`` CLI with FLEET_HOME isolated (see :func:`run_fleet`)."""
    env = _cli_env(fleet_home, env_extra)
    if subprocess_ or SUBPROCESS_CLI:
        return _run_cli_subprocess("fleet-agent", args, env, cwd, stdin)
    return _run_cli_in_process("main_agent", "fleet-agent", args, env, cwd, stdin)
