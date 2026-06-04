"""``fleet-agent memory <list|read|write>`` — vendor-neutral access to fleet memory.

Operates exclusively on ``<state>/memory/`` (resolved from ``FLEET_STATE_DIR``),
the project-level shared memory store any driver — claude, codex, or other —
reads and writes. This is *not* a vendor's own built-in auto-memory; the two are
deliberately separate stores so there is no double management.

Write/index rules follow ``<state>/memory/GUIDE.md``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import state as state_mod

_INDEX_NAME = "MEMORY.md"
_RESERVED = {"MEMORY.md", "GUIDE.md"}
_INDEX_HEADER = "# Memory Index\n"


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "memory",
        help="Read/write fleet project memory (<state>/memory/)",
        description=(
            "Vendor-neutral access to the project-level shared memory store at "
            "<state>/memory/. Separate from any vendor's own auto-memory. "
            "Follow <state>/memory/GUIDE.md when writing."
        ),
    )
    sp = p.add_subparsers(dest="memory_cmd", required=True, metavar="<sub>")

    p_list = sp.add_parser("list", help="List memory entries with descriptions")
    p_list.add_argument("--state-dir", default=None, help="Override the resolved state dir")
    p_list.set_defaults(func=run_list)

    p_read = sp.add_parser("read", help="Print a memory file's contents")
    p_read.add_argument("name", help="Memory name (with or without .md)")
    p_read.add_argument("--state-dir", default=None, help="Override the resolved state dir")
    p_read.set_defaults(func=run_read)

    p_write = sp.add_parser(
        "write",
        help="Create/update a memory file and its MEMORY.md index line",
        description="Body is read from stdin unless --body-file is given.",
    )
    p_write.add_argument("name", help="Memory name (kebab-case, with or without .md)")
    p_write.add_argument("--description", default="", help="One-line summary for the index/frontmatter")
    p_write.add_argument(
        "--type",
        dest="mem_type",
        default="project",
        help="Memory type: feedback | project | reference (default: project)",
    )
    p_write.add_argument(
        "--body-file",
        default=None,
        help="Read the memory body from this file instead of stdin",
    )
    p_write.add_argument("--state-dir", default=None, help="Override the resolved state dir")
    p_write.set_defaults(func=run_write)


# ---------------------------------------------------------------------------
# Resolution / validation helpers
# ---------------------------------------------------------------------------


def _resolve_state_dir(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_dir() else None
    env_state_dir = os.environ.get("FLEET_STATE_DIR")
    if env_state_dir:
        candidate = Path(env_state_dir)
        if candidate.is_dir():
            return candidate
    return state_mod.resolve_state_dir(Path.cwd())


def _normalize_name(name: str) -> str | None:
    """Return the bare slug (no ``.md``) or ``None`` if the name is unsafe."""
    slug = name[:-3] if name.endswith(".md") else name
    if not slug or "/" in slug or "\\" in slug or slug.startswith(".") or ".." in slug:
        return None
    return slug


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract top-level ``name``/``description``/``type`` from frontmatter."""
    fields: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if key in ("name", "description", "type") and value:
            fields[key] = value
    return fields


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def run_list(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    if state_dir is None:
        print("error: could not resolve state dir (set FLEET_STATE_DIR)", file=sys.stderr)
        return 1
    memory_dir = state_dir / "memory"
    if not memory_dir.is_dir():
        print("(no memory directory)")
        return 0

    entries: list[tuple[str, str]] = []
    for path in sorted(memory_dir.glob("*.md")):
        if path.name in _RESERVED:
            continue
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        name = fm.get("name", path.stem)
        entries.append((name, fm.get("description", "")))

    if not entries:
        print("(no memory entries)")
        return 0
    for name, description in entries:
        print(f"{name} — {description}" if description else name)
    return 0


def run_read(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    if state_dir is None:
        print("error: could not resolve state dir (set FLEET_STATE_DIR)", file=sys.stderr)
        return 1
    slug = _normalize_name(args.name)
    if slug is None:
        print(f"error: invalid memory name {args.name!r}", file=sys.stderr)
        return 1
    path = state_dir / "memory" / f"{slug}.md"
    if not path.is_file():
        print(f"error: no memory named {slug!r}", file=sys.stderr)
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def run_write(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args.state_dir)
    if state_dir is None:
        print("error: could not resolve state dir (set FLEET_STATE_DIR)", file=sys.stderr)
        return 1
    slug = _normalize_name(args.name)
    if slug is None:
        print(f"error: invalid memory name {args.name!r}", file=sys.stderr)
        return 1

    if args.body_file is not None:
        body_path = Path(args.body_file)
        if not body_path.is_file():
            print(f"error: --body-file not found: {args.body_file}", file=sys.stderr)
            return 1
        body = body_path.read_text(encoding="utf-8")
    else:
        body = sys.stdin.read()
    body = body.strip()

    memory_dir = state_dir / "memory"
    memory_dir.mkdir(exist_ok=True)

    front = (
        "---\n"
        f"name: {slug}\n"
        f"description: {args.description}\n"
        "metadata:\n"
        f"  type: {args.mem_type}\n"
        "---\n"
    )
    content = front + ("\n" + body + "\n" if body else "\n")
    (memory_dir / f"{slug}.md").write_text(content, encoding="utf-8")

    _update_index(memory_dir, slug, args.description)
    print(f"wrote memory {slug!r}")
    return 0


def _update_index(memory_dir: Path, slug: str, description: str) -> None:
    """Insert or replace the ``MEMORY.md`` index line for *slug*."""
    line = f"- [{slug}]({slug}.md) — {description}" if description else f"- [{slug}]({slug}.md)"
    index_path = memory_dir / _INDEX_NAME
    if index_path.is_file():
        text = index_path.read_text(encoding="utf-8")
    else:
        text = _INDEX_HEADER + "\n"

    marker = f"]({slug}.md)"
    lines = text.splitlines()
    for i, existing in enumerate(lines):
        if marker in existing:
            lines[i] = line
            index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return

    # No existing entry — append, ensuring a blank line separates the header block.
    new_text = text if text.endswith("\n") else text + "\n"
    if not new_text.endswith("\n\n"):
        # keep a single trailing newline before appending the entry
        new_text = new_text.rstrip("\n") + "\n"
    new_text += line + "\n"
    index_path.write_text(new_text, encoding="utf-8")
