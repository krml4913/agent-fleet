"""Startup banner for ``fleet leader`` — a sailing fleet plus a ``FLEET`` logo.

The art lives in ``assets/leader-banner.txt`` (ships + sea, one blank line, then
the ANSI-Shadow logo) and is read at runtime; distribution is clone-from-``main``
(no installed package), so a file next to the module always ships with it.

Rendering degrades instead of failing — the banner is decoration and must never
make ``fleet leader`` fail:

* color only on a TTY with ``NO_COLOR`` unset (same rule as ``fleet sessions``);
* full art when the terminal is at least as wide as the art, the logo alone when
  it only fits the logo, and just the info line below that;
* the logo uses box-drawing characters, so a stdout encoding that cannot encode
  them (e.g. a non-UTF-8 Windows console) skips the art entirely;
* any error while rendering or writing is swallowed.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import TextIO

ART_PATH = Path(__file__).resolve().parent / "assets" / "leader-banner.txt"

#: Left margin of the info line, and of the logo when it is shown without the ships.
INDENT = "  "

_RESET = "\033[0m"
_SAIL = "\033[1;37m"  # sails / masts / pennants: white bold
_HULL = "\033[33m"  # hulls: yellow
_SEA = "\033[34m"  # sea ``~``: blue
_LOGO = "\033[1;36m"  # FLEET logo: cyan bold

_TOKEN_RE = re.compile(r"\S+")


def _load_art() -> tuple[list[str], list[str]]:
    """Return ``(ships, logo)`` — the art split at its first blank line."""
    lines = [ln.rstrip() for ln in ART_PATH.read_text(encoding="utf-8").splitlines()]
    split = lines.index("")
    ships = lines[:split]
    logo = [ln for ln in lines[split + 1:] if ln]
    return ships, logo


def _apply(line: str, styles: list[str | None]) -> str:
    """Wrap each run of identically styled characters in its SGR code + reset."""
    out: list[str] = []
    i = 0
    while i < len(line):
        j = i
        while j < len(line) and styles[j] == styles[i]:
            j += 1
        chunk = line[i:j]
        out.append(chunk if styles[i] is None else f"{styles[i]}{chunk}{_RESET}")
        i = j
    return "".join(out)


def _paint_ship_line(line: str) -> str:
    """Color one ships/sea row.

    Per whitespace-delimited token: one starting with ``)`` or ``|`` is rigging
    (sail, mast, pennant → white bold), anything else is hull (yellow); a ``~``
    is sea (blue) wherever it appears, including next to a hull.
    """
    styles: list[str | None] = [None] * len(line)
    for m in _TOKEN_RE.finditer(line):
        base = _SAIL if m.group()[0] in ")|" else _HULL
        for i in range(m.start(), m.end()):
            styles[i] = _SEA if line[i] == "~" else base
    return _apply(line, styles)


def _paint_logo_line(line: str) -> str:
    body = line.lstrip(" ")
    return line[: len(line) - len(body)] + f"{_LOGO}{body}{_RESET}"


def _can_encode(text: str, encoding: str | None) -> bool:
    """True when ``text`` survives ``encoding`` under the strict error handler.

    An unknown encoding (``None`` — e.g. a ``StringIO``) is taken as capable:
    there is nothing to encode against.
    """
    if not encoding:
        return True
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def format_info(label: str, agent: str, scope: list[str], encoding: str | None = None) -> str:
    """The single line under the art: ``  leader: main · agent: claude:opus · scope: all``."""
    sep = " · " if _can_encode("·", encoding) else " | "
    scope_text = ", ".join(scope) if scope else "all"
    return f"{INDENT}leader: {label}{sep}agent: {agent}{sep}scope: {scope_text}"


def render_art(*, columns: int, color: bool, encoding: str | None) -> str:
    """The art block (newline-terminated lines) that fits ``columns``; ``""`` if none does."""
    ships, logo = _load_art()
    # The ships are ASCII; the logo is what a legacy console cannot encode.
    if not _can_encode("\n".join(logo), encoding):
        return ""

    logo_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in logo)
    logo_only_width = len(INDENT) + max(len(ln) for ln in logo) - logo_indent
    art_width = max(len(ln) for ln in ships + logo)

    if columns >= art_width:
        rows = [_paint_ship_line(ln) if color else ln for ln in ships]
        rows.append("")
        rows += [_paint_logo_line(ln) if color else ln for ln in logo]
    elif columns >= logo_only_width:
        dedented = [INDENT + ln[logo_indent:] for ln in logo]
        rows = [_paint_logo_line(ln) if color else ln for ln in dedented]
    else:
        return ""
    return "\n".join(rows) + "\n"


def print_banner(
    *, label: str, agent: str, scope: list[str], stream: TextIO | None = None
) -> None:
    """Print the banner + info line to ``stream`` (default ``sys.stdout``); never raises."""
    try:
        stream = stream if stream is not None else sys.stdout
        encoding = getattr(stream, "encoding", None)
        color = stream.isatty() and "NO_COLOR" not in os.environ
        columns = shutil.get_terminal_size().columns
        try:
            art = render_art(columns=columns, color=color, encoding=encoding)
        except Exception:
            art = ""
        stream.write(art + format_info(label, agent, scope, encoding) + "\n")
        stream.flush()
    except Exception:
        pass
