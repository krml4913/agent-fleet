"""``fleet dashboard`` — generate and open the cross-project HTML dashboard."""
from __future__ import annotations

import argparse

from .. import html_dashboard


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "dashboard",
        help="Generate and open the cross-project HTML dashboard",
        description=(
            "Render fleet-state/global/dashboard.html (all projects + sessions) "
            "and open it in the browser.  Re-running refreshes the file; an open "
            "browser tab auto-reloads every 15 s via <meta refresh>."
        ),
    )
    p.add_argument(
        "--no-open",
        action="store_true",
        dest="no_open",
        help="Write the HTML file but do not launch a browser",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    path = html_dashboard.rebuild_global()
    print(f"wrote {path}")
    if not args.no_open:
        import webbrowser
        webbrowser.open(path.as_uri())
    return 0
