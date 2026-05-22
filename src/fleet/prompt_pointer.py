"""Helpers for pasting prompt-file pointers into agent panes."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def pointer_text(prompt_path: Path) -> str:
    """Return the small tmux-pasted instruction for a full prompt file."""
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


def load_pointer_buffer(tmux_mod: Any, buffer_name: str, prompt_path: Path) -> Path:
    """Load a prompt pointer, not the prompt body, into a tmux buffer."""
    path = write_pointer_file(prompt_path)
    tmux_mod.load_buffer(buffer_name, str(path))
    return path


def paste_pointer_buffer(
    tmux_mod: Any,
    *,
    session: str,
    window: str,
    buffer_name: str,
    prompt_path: Path,
) -> Path:
    """Load and paste a prompt pointer into a tmux pane."""
    path = load_pointer_buffer(tmux_mod, buffer_name, prompt_path)
    tmux_mod.paste_buffer(session, window, buffer_name)
    return path
