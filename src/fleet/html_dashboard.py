"""Render and rebuild the cross-project static HTML dashboard.

Mirrors ``dashboard.py``'s shape (render / rebuild / rebuild_if_present),
but cross-project and HTML.  All dynamic strings pass through
``html.escape`` so task titles / project names / PR URLs cannot break
the page or inject markup.

No daemon, no polling.  The file is opt-in by presence: until the user
first runs ``fleet dashboard``, the event hook does nothing.  After that,
every state write (via ``dashboard.rebuild``) refreshes it while it exists.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import state as state_mod
from . import status_data
from .locking import atomic_write

REFRESH_SECONDS = 15
RECENT_EVENTS_DISPLAYED = 15


def global_dashboard_path() -> Path:
    """Return ``fleet-state/global/dashboard.html``."""
    return state_mod.global_dir() / "dashboard.html"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

_CSS = """\
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0;
  padding: 1rem 1.5rem;
  background: #1a1a1a;
  color: #e0e0e0;
  font-family: ui-monospace, "SFMono-Regular", "Menlo", "Consolas", monospace;
  font-size: 14px;
  line-height: 1.5;
}
a { color: #7eb8f7; text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { margin: 0 0 .25rem; font-size: 1.2rem; color: #fff; }
h2 { font-size: 1rem; margin: 1.25rem 0 .4rem; color: #bbb; border-bottom: 1px solid #333; padding-bottom: .2rem; }
h3 { font-size: .9rem; margin: 1rem 0 .3rem; color: #ccc; }
.header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 1rem; }
.header-meta { font-size: .8rem; color: #888; }
.banner {
  background: #3a0000;
  border: 1px solid #c00;
  border-radius: 4px;
  padding: .5rem .75rem;
  margin-bottom: 1rem;
  color: #ff8080;
}
.banner b { color: #ff4040; }
table { border-collapse: collapse; width: 100%; margin-bottom: .5rem; }
th { text-align: left; color: #888; font-weight: normal; padding: .2rem .5rem; border-bottom: 1px solid #333; }
td { padding: .2rem .5rem; vertical-align: top; }
tr:hover td { background: #222; }
.dot { font-size: .9rem; }
.ok { color: #4caf50; }
.active { color: #ffc107; }
.attention { color: #f44336; }
.neutral { color: #888; }
.stale { color: #555; }
.tag { font-size: .75rem; color: #888; border: 1px solid #555; border-radius: 3px; padding: 0 .3rem; margin-left: .4rem; }
.scope-line { font-size: .8rem; color: #888; margin: .1rem 0 .4rem; }
.no-tasks { color: #555; font-style: italic; }
.event-list { list-style: none; padding: 0; margin: 0; }
.event-list li { padding: .1rem 0; font-size: .8rem; color: #aaa; }
.event-ts { color: #555; }
.legend { margin-top: 1.5rem; font-size: .8rem; color: #888; }
"""


def _e(text: object) -> str:
    """HTML-escape a value for text content."""
    return html.escape(str(text))


def _attr(text: object) -> str:
    """HTML-escape a value for use inside an attribute."""
    return html.escape(str(text), quote=True)


def _dot(severity: str) -> str:
    css = {"ok": "ok", "active": "active", "attention": "attention"}.get(severity, "neutral")
    return f'<span class="dot {css}">&#9679;</span>'


def _stage_text(stage: dict | None) -> str:
    if stage is None:
        return "—"
    idx = stage["index"] + 1
    count = stage["count"]
    role = stage["role"]
    agent = stage["agent"]
    cell = f"{idx}/{count} {role} ({agent})"
    if stage.get("review"):
        cell += f" {stage['review']}"
    return cell


def render(snapshot: dict, *, refresh_seconds: int = REFRESH_SECONDS) -> str:
    """Return a complete, self-contained HTML document for *snapshot*."""
    generated_at = _e(snapshot.get("generated_at", ""))
    version = _e(snapshot.get("version", "?"))
    projects: list[dict] = snapshot.get("projects") or []
    sessions: list[dict] = snapshot.get("sessions") or []
    recent_events: list[dict] = snapshot.get("recent_events") or []

    # Collect all awaiting tasks across projects for the banner.
    all_awaiting: list[tuple[str, dict]] = []
    for proj in projects:
        for t in proj.get("awaiting") or []:
            all_awaiting.append((proj["name"], t))

    parts: list[str] = []

    parts.append(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>fleet — dashboard</title>
<style>
{_CSS}
</style>
</head>
<body>
"""
    )

    # Header
    parts.append(
        f'<div class="header">'
        f"<h1>fleet dashboard</h1>"
        f'<span class="header-meta">'
        f"generated {generated_at} &nbsp;·&nbsp; "
        f"fleet {version} &nbsp;·&nbsp; "
        f"auto-refreshes every {refresh_seconds}s"
        f"</span></div>\n"
    )

    # Awaiting-orders banner
    if all_awaiting:
        parts.append('<div class="banner">\n')
        parts.append(f"<b>&#9888; {len(all_awaiting)} task(s) awaiting orders</b><br>\n")
        for pname, t in all_awaiting:
            parts.append(
                f"&nbsp;&nbsp;{_e(pname)} / task-{_e(t['id'])} — {_e(t['title'])}<br>\n"
            )
        parts.append("</div>\n")

    # Projects
    parts.append("<h2>Projects</h2>\n")
    if not projects:
        parts.append('<p class="no-tasks">(no registered projects)</p>\n')
    for proj in projects:
        pname = proj["name"]
        repo = proj["repo"]
        parts.append(f"<h3>{_e(pname)}</h3>\n")
        repo_warning = "" if proj["repo_exists"] else " &#9888; repo missing"
        parts.append(f'<div class="scope-line">{_e(repo)}{_e(repo_warning)}</div>\n')

        if not proj["state_exists"]:
            parts.append('<p class="no-tasks">(state dir not found)</p>\n')
            continue

        by_status = proj.get("by_status") or {}
        if by_status:
            summary_parts = []
            for st, count in sorted(by_status.items()):
                sev = status_data.status_severity(st)
                summary_parts.append(f'{_dot(sev)} {_e(count)} {_e(st)}')
            parts.append(
                f'<div class="scope-line">tasks: {" &nbsp; ".join(summary_parts)}</div>\n'
            )
        else:
            parts.append('<div class="scope-line">tasks: (none)</div>\n')

        task_list = proj.get("tasks") or []
        if not task_list:
            parts.append('<p class="no-tasks">(no tasks)</p>\n')
            continue

        parts.append("<table>\n")
        parts.append(
            "<tr><th>ID</th><th>Title</th><th>Status</th>"
            "<th>Formation</th><th>Stage</th><th>Last seen</th><th>PR</th></tr>\n"
        )
        for t in task_list:
            sev = t["severity"]
            dot = _dot(sev)
            status_cell = f'{dot} <span class="{_attr(sev)}">{_e(t["status"])}</span>'
            pr_url = t.get("pr_url")
            pr_cell = (
                f'<a href="{_attr(pr_url)}" target="_blank">PR</a>'
                if pr_url
                else "—"
            )
            stage_cell = _e(_stage_text(t.get("stage")))
            parts.append(
                f"<tr>"
                f"<td>task-{_e(t['id'])}</td>"
                f"<td>{_e(t['title'])}</td>"
                f"<td>{status_cell}</td>"
                f"<td>{_e(t['formation'])}</td>"
                f"<td>{stage_cell}</td>"
                f"<td>{_e(t['last_seen'])}</td>"
                f"<td>{pr_cell}</td>"
                f"</tr>\n"
            )
        parts.append("</table>\n")

    # Sessions
    parts.append("<h2>Sessions</h2>\n")
    if not sessions:
        parts.append('<p class="no-tasks">(no leader sessions)</p>\n')
    for sess in sessions:
        label = sess["label"]
        live = sess["live"]
        if live is True:
            live_dot = '<span class="dot ok">&#9679;</span>'
            live_text = "live"
        elif live is False:
            live_dot = '<span class="dot stale">&#9679;</span>'
            live_text = "stale"
        else:
            live_dot = '<span class="dot neutral">&#9679;</span>'
            live_text = "?"

        since = sess.get("since", "")
        since_str = f"since {_e(since[:10])}" if len(since) >= 10 else ""
        no_rec = "" if sess["has_record"] else '<span class="tag">no record</span>'
        parts.append(
            f"<h3>{live_dot} {_e(label)} "
            f'<span class="header-meta">({live_text}) {_e(sess["agent"])} '
            f"{_e(sess['pane'])} {since_str}</span>{no_rec}</h3>\n"
        )
        scope = sess.get("scope")
        if scope is not None:
            parts.append(
                f'<div class="scope-line">scope: {_e(", ".join(scope))}</div>\n'
            )
        else:
            parts.append('<div class="scope-line">scope: (all projects)</div>\n')

        task_rows = sess.get("tasks") or []
        if not task_rows:
            parts.append('<p class="no-tasks">(no in-flight tasks)</p>\n')
            continue
        parts.append("<table>\n")
        parts.append("<tr><th>Project</th><th>ID</th><th>Status</th><th>Title</th></tr>\n")
        for t in task_rows:
            oos = t.get("out_of_scope", False)
            oos_tag = '<span class="tag">out of scope</span>' if oos else ""
            sev = status_data.status_severity(t["status"])
            parts.append(
                f"<tr>"
                f"<td>{_e(t['project'])}</td>"
                f"<td>task-{_e(t['id'])}</td>"
                f"<td><span class='{_attr(sev)}'>{_e(t['status'])}</span></td>"
                f"<td>{_e(t['title'])}{oos_tag}</td>"
                f"</tr>\n"
            )
        parts.append("</table>\n")

    # Recent events
    parts.append(f"<h2>Recent events (last {RECENT_EVENTS_DISPLAYED})</h2>\n")
    if not recent_events:
        parts.append('<p class="no-tasks">(none)</p>\n')
    else:
        parts.append('<ul class="event-list">\n')
        for ev in recent_events:
            ts = ev.get("ts", "?")
            etype = ev.get("type", "?")
            tid = ev.get("task_id", "—")
            proj = ev.get("project", "")
            parts.append(
                f'<li><span class="event-ts">{_e(ts)}</span>'
                f" <b>{_e(etype)}</b>"
                f" task-{_e(tid)}"
                f" <span class=\"header-meta\">{_e(proj)}</span></li>\n"
            )
        parts.append("</ul>\n")

    # Legend
    parts.append(
        '<div class="legend">'
        '<span class="dot ok">&#9679;</span> ok &nbsp;&nbsp;'
        '<span class="dot active">&#9679;</span> active &nbsp;&nbsp;'
        '<span class="dot attention">&#9679;</span> attention'
        "</div>\n"
    )

    parts.append("</body>\n</html>\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Rebuild helpers
# ---------------------------------------------------------------------------


def rebuild_global() -> Path:
    """Generate the HTML and write it to ``global_dashboard_path()``.

    Always writes the file (creates it the first time).  Exceptions propagate
    so the CLI user sees real failures.  Returns the path.
    """
    snapshot = status_data.collect_global_snapshot()
    text = render(snapshot)
    out = global_dashboard_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(out) as f:
        f.write(text)
    return out


def rebuild_global_if_present() -> Path | None:
    """Refresh the HTML only when the file already exists; else no-op.

    Must be exception-safe — it runs inside the state-write hook, so a render
    bug must never break or block a state write.
    """
    try:
        out = global_dashboard_path()
        if not out.exists():
            return None
        return rebuild_global()
    except Exception:  # noqa: BLE001
        return None
