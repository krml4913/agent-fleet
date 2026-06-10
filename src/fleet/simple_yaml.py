"""Minimal YAML reader/writer for flat ``key: value`` files.

For Phase 2 we only need to round-trip ``project.yaml`` and ``task.yaml``,
both of which are intentionally restricted to flat string maps. A real
YAML parser (PyYAML, vendored) lands in Phase 3 when formation files —
which need nested structures and lists — arrive.

Constraints (intentional, to keep this file tiny):
  * Top-level only. No nesting, no lists.
  * Values are strings. Callers stringify whatever they need.
  * Values may not contain newlines.
  * Comment lines (``#``) and blank lines are ignored on read.
"""
from __future__ import annotations

from typing import Mapping


def load(text: str) -> dict[str, str]:
    """Parse a flat ``key: value`` document into a dict[str, str]."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"simple_yaml: line missing ':': {raw!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        if not key:
            raise ValueError(f"simple_yaml: empty key in line: {raw!r}")
        out[key] = _parse_value(value)
    return out


def _parse_value(value: str) -> str:
    """Resolve a raw value: unwrap a quoted scalar, else strip an inline comment.

    A fully-quoted value (single or double quotes) keeps its interior verbatim,
    including any ``#``; only the surrounding quotes are removed. An unquoted
    value has any trailing ``#`` comment stripped, where the ``#`` must be
    preceded by whitespace to count as a comment start (a ``#`` glued to a token
    is kept). Values stay ``str`` — see module docstring; a future PyYAML
    migration would replace this hand-rolled handling.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        return value[1:-1]
    return _strip_inline_comment(value)


def _strip_inline_comment(value: str) -> str:
    """Drop a ``whitespace + #`` trailing comment from an unquoted value."""
    for i, ch in enumerate(value):
        if ch == "#" and i > 0 and value[i - 1].isspace():
            return value[:i].strip()
    return value.strip()


def dump(data: Mapping[str, object]) -> str:
    """Serialize a flat map into ``key: value`` form (trailing newline)."""
    lines: list[str] = []
    for key, value in data.items():
        text = str(value)
        if "\n" in text:
            raise ValueError(
                f"simple_yaml: value for {key!r} contains newline; not supported"
            )
        lines.append(f"{key}: {text}")
    return "\n".join(lines) + "\n"
