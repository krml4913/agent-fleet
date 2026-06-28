"""``fleet changelog`` - assemble changelog fragments into CHANGELOG.md."""
from __future__ import annotations

import argparse
import datetime as _datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path


class ChangelogError(Exception):
    """Raised when changelog assembly cannot proceed."""


@dataclass(frozen=True)
class Fragment:
    path: Path
    body: str


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "changelog",
        help="Assemble changelog.d fragments into CHANGELOG.md",
        description=(
            "Concatenate changelog.d/*.md fragments into CHANGELOG.md and "
            "delete the fragment files. By default entries go under "
            "## [Unreleased]; pass --version to create a versioned section."
        ),
    )
    p.add_argument(
        "--repo",
        default=".",
        help="Repository root containing CHANGELOG.md (default: cwd)",
    )
    p.add_argument(
        "--fragment-dir",
        default="changelog.d",
        help="Fragment directory relative to --repo (default: changelog.d)",
    )
    p.add_argument(
        "--version",
        default=None,
        help="Create a versioned section, e.g. --version 0.2.0",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Date for --version headings (default: today, YYYY-MM-DD)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        result = assemble_changelog(
            repo=Path(args.repo),
            fragment_dir_name=args.fragment_dir,
            version=args.version,
            date=args.date,
        )
    except ChangelogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result.fragment_count == 0:
        print(f"no changelog fragments found in {result.fragment_dir}")
    else:
        print(
            f"assembled {result.fragment_count} fragment(s) into "
            f"{result.changelog_path} under {result.heading}"
        )
    return 0


@dataclass(frozen=True)
class AssembleResult:
    changelog_path: Path
    fragment_dir: Path
    fragment_count: int
    heading: str


def assemble_changelog(
    *,
    repo: Path,
    fragment_dir_name: str = "changelog.d",
    version: str | None = None,
    date: str | None = None,
) -> AssembleResult:
    repo = repo.resolve()
    changelog_path = repo / "CHANGELOG.md"
    fragment_dir = repo / fragment_dir_name

    if date and not version:
        raise ChangelogError("--date requires --version")
    if not changelog_path.is_file():
        raise ChangelogError(f"CHANGELOG.md not found at {changelog_path}")

    fragments = _read_fragments(fragment_dir)
    heading = _target_heading(version=version, date=date)
    if not fragments:
        return AssembleResult(
            changelog_path=changelog_path,
            fragment_dir=fragment_dir,
            fragment_count=0,
            heading=heading,
        )

    text = changelog_path.read_text(encoding="utf-8")
    entries = _format_entries(fragments)
    if version:
        new_text = _insert_versioned_section(text, heading=heading, entries=entries)
    else:
        new_text = _insert_under_unreleased(text, entries=entries)

    changelog_path.write_text(new_text, encoding="utf-8")
    for fragment in fragments:
        fragment.path.unlink()

    return AssembleResult(
        changelog_path=changelog_path,
        fragment_dir=fragment_dir,
        fragment_count=len(fragments),
        heading=heading,
    )


def _read_fragments(fragment_dir: Path) -> list[Fragment]:
    if not fragment_dir.exists():
        return []
    if not fragment_dir.is_dir():
        raise ChangelogError(f"fragment path is not a directory: {fragment_dir}")

    fragments: list[Fragment] = []
    for path in sorted(fragment_dir.glob("*.md")):
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            raise ChangelogError(f"empty changelog fragment: {path}")
        fragments.append(Fragment(path=path, body=body))
    return fragments


def _target_heading(*, version: str | None, date: str | None) -> str:
    if not version:
        return "## [Unreleased]"
    release_date = date or _datetime.date.today().isoformat()
    return f"## [{version}] - {release_date}"


def _format_entries(fragments: list[Fragment]) -> str:
    return "\n\n".join(fragment.body for fragment in fragments)


def _unreleased_match(text: str) -> re.Match[str]:
    match = re.search(r"(?m)^## \[Unreleased\]\s*$", text)
    if not match:
        raise ChangelogError("CHANGELOG.md is missing a ## [Unreleased] heading")
    return match


def _next_h2(text: str, start: int) -> int:
    match = re.search(r"(?m)^## ", text[start:])
    if not match:
        return len(text)
    return start + match.start()


def _insert_under_unreleased(text: str, *, entries: str) -> str:
    unreleased = _unreleased_match(text)
    section_start = unreleased.end()
    section_end = _next_h2(text, section_start)
    existing_body = text[section_start:section_end].strip()

    body_parts = [entries]
    if existing_body:
        body_parts.append(existing_body)

    suffix = text[section_end:].lstrip("\n")
    new_section = "\n\n" + "\n\n".join(body_parts).rstrip() + "\n"
    if suffix:
        new_section += "\n" + suffix
    return text[:section_start] + new_section


def _insert_versioned_section(text: str, *, heading: str, entries: str) -> str:
    unreleased = _unreleased_match(text)
    insert_at = _next_h2(text, unreleased.end())
    prefix = text[:insert_at].rstrip()
    suffix = text[insert_at:].lstrip("\n")
    section = f"{heading}\n\n{entries.rstrip()}\n"
    if suffix:
        return f"{prefix}\n\n{section}\n{suffix}"
    return f"{prefix}\n\n{section}"
