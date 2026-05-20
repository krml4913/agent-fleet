"""Workflow plugin loader.

A *workflow plugin* is a Python module that hooks into start and done.
A project picks exactly one via ``workflow:`` in ``project.yaml`` (default
``bare``). Built-in plugins ship under this package; project-defined ones
live in ``<state_dir>/plugins/<name>.py`` and take priority.

Plugin contract (everything optional):

    WORKFLOW_NAME: str
    on_pre_start(ctx: dict) -> None
    on_post_done(ctx: dict) -> None

``ctx`` is a mutable dict — plugins may add a ``task_extra`` sub-dict
(merged into ``task.yaml``) and/or override ``cwd`` (used as the start
window's working directory).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

DEFAULT_WORKFLOW = "bare"
HERE = Path(__file__).resolve().parent
PROJECT_PLUGIN_SUBDIR = "plugins"


def load_workflow(state_dir: Path) -> ModuleType:
    """Resolve and import the workflow plugin for ``state_dir``.

    Custom plugins under ``<state_dir>/plugins/<name>.py`` shadow built-ins.
    Falls back to the ``bare`` plugin if ``project.yaml`` lacks a
    ``workflow`` field.
    """
    from .. import state as state_mod  # local: break cycle

    project = state_mod.load_project(state_dir)
    name = project.get("workflow") or DEFAULT_WORKFLOW
    return _resolve(name, state_dir)


def run_hook(module: ModuleType, hook_name: str, ctx: dict[str, Any]) -> Any:
    fn = getattr(module, hook_name, None)
    if fn is None:
        return None
    return fn(ctx)


def list_builtin() -> list[str]:
    return sorted(
        p.stem for p in HERE.glob("*.py") if p.stem != "__init__"
    )


def list_custom(state_dir: Path) -> list[str]:
    d = state_dir / PROJECT_PLUGIN_SUBDIR
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.py") if not p.stem.startswith("_"))


def _resolve(name: str, state_dir: Path) -> ModuleType:
    custom = state_dir / PROJECT_PLUGIN_SUBDIR / f"{name}.py"
    if custom.is_file():
        return _load_from_path(name, custom)
    return importlib.import_module(f"fleet.plugins.{name}")


def _load_from_path(name: str, path: Path) -> ModuleType:
    qualname = f"fleet.plugins._custom_{name}"
    spec = importlib.util.spec_from_file_location(qualname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load custom plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = module
    spec.loader.exec_module(module)
    return module
