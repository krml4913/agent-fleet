"""Helpers for pasting prompt-file pointers into agent panes."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mux import Mux


def pointer_text(prompt_path: Path) -> str:
    """Return the small pasted instruction for a full prompt file."""
    resolved = prompt_path.resolve()
    return (
        "Read the prompt file at this path before doing anything else, "
        f"then follow its instructions: {resolved}"
    )


def pointer_path(prompt_path: Path) -> Path:
    """Return the sidecar path that contains the pasteable pointer text."""
    return prompt_path.with_name(f".{prompt_path.name}.paste-pointer")


def write_pointer_file(prompt_path: Path) -> Path:
    """Write and return a short pointer file for ``prompt_path``."""
    path = pointer_path(prompt_path)
    path.write_text(pointer_text(prompt_path), encoding="utf-8")
    return path


def preload_pointer(backend: Mux, name: str, prompt_path: Path) -> str | None:
    """Stage the prompt pointer (not the body) for a manual paste by a human.

    Writes the pointer sidecar file, then asks the backend to stage the pointer
    text under ``name`` (tmux: the named buffer ``name`` for ``C-b ]``).
    Returns the backend's manual-paste instruction, or ``None`` when the
    backend has no such concept.
    """
    path = write_pointer_file(prompt_path)
    return backend.preload_paste(name, path.read_text(encoding="utf-8"))


def paste_pointer(
    backend: Mux,
    *,
    session: str,
    window: str,
    prompt_path: Path,
) -> Path:
    """Write the pointer sidecar file and paste its text into a pane."""
    path = write_pointer_file(prompt_path)
    backend.paste(session, window, path.read_text(encoding="utf-8"))
    return path
