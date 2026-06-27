"""Tests for fleet.html_dashboard — renderer + rebuild helpers."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import html_dashboard, state  # noqa: E402
from fleet import status_data  # noqa: E402
from tests._fleet_test_helpers import make_project  # noqa: E402


def _minimal_snapshot(
    *,
    project_name: str = "demo",
    task_title: str = "do thing",
    task_status: str = "running",
    session_label: str = "main",
) -> dict:
    return {
        "generated_at": "2026-06-20T00:00:00Z",
        "version": "0.1.0",
        "projects": [
            {
                "name": project_name,
                "repo": "/tmp/demo",
                "repo_exists": True,
                "state_exists": True,
                "by_status": {task_status: 1},
                "awaiting": [],
                "tasks": [
                    {
                        "id": "1",
                        "title": task_title,
                        "status": task_status,
                        "severity": status_data.status_severity(task_status),
                        "formation": "solo",
                        "stage": None,
                        "last_seen": "5m ago",
                        "pr_url": None,
                    }
                ],
            }
        ],
        "sessions": [
            {
                "label": session_label,
                "live": True,
                "agent": "claude:opus",
                "pane": f"fleet-{session_label}:leader",
                "since": "2026-06-20T00:00:00Z",
                "has_record": True,
                "scope": None,
                "tasks": [],
            }
        ],
        "recent_events": [
            {
                "ts": "2026-06-20T00:00:00Z",
                "type": "heartbeat",
                "task_id": "1",
                "project": project_name,
            }
        ],
    }


def _session_filter_snapshot() -> dict:
    snap = _minimal_snapshot(project_name="alpha", session_label="main")
    snap["projects"].append(
        {
            "name": "beta",
            "repo": "/tmp/beta",
            "repo_exists": True,
            "state_exists": True,
            "by_status": {},
            "awaiting": [],
            "tasks": [],
        }
    )
    snap["sessions"] = [
        {
            "label": "main",
            "live": True,
            "agent": "claude:opus",
            "pane": "fleet-main:leader",
            "since": "2026-06-20T00:00:00Z",
            "has_record": True,
            "scope": ["alpha"],
            "tasks": [],
        },
        {
            "label": "all-projects",
            "live": True,
            "agent": "codex:gpt-5.5",
            "pane": "fleet-all-projects:leader",
            "since": "2026-06-20T00:00:00Z",
            "has_record": True,
            "scope": None,
            "tasks": [],
        },
        {
            "label": "ops",
            "live": True,
            "agent": "claude:opus",
            "pane": "fleet-ops:leader",
            "since": "2026-06-20T00:00:00Z",
            "has_record": True,
            "scope": ["beta"],
            "tasks": [],
        },
    ]
    return snap


class RenderTests(unittest.TestCase):
    def test_contains_project_name(self) -> None:
        snap = _minimal_snapshot(project_name="myproject")
        html = html_dashboard.render(snap)
        self.assertIn("myproject", html)

    def test_contains_task_title(self) -> None:
        snap = _minimal_snapshot(task_title="Fix something important")
        html = html_dashboard.render(snap)
        self.assertIn("Fix something important", html)

    def test_contains_task_status(self) -> None:
        snap = _minimal_snapshot(task_status="running")
        html = html_dashboard.render(snap)
        self.assertIn("running", html)

    def test_contains_session_label(self) -> None:
        snap = _minimal_snapshot(session_label="alpha")
        html = html_dashboard.render(snap)
        self.assertIn("alpha", html)

    def test_meta_refresh_tag(self) -> None:
        snap = _minimal_snapshot()
        html = html_dashboard.render(snap)
        self.assertIn('<meta http-equiv="refresh"', html)
        self.assertIn('content="15"', html)

    def test_custom_refresh_seconds(self) -> None:
        snap = _minimal_snapshot()
        html = html_dashboard.render(snap, refresh_seconds=30)
        self.assertIn('content="30"', html)

    def test_html_escaping_title(self) -> None:
        snap = _minimal_snapshot(task_title='<script>alert("xss")</script>')
        html = html_dashboard.render(snap)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_html_escaping_ampersand(self) -> None:
        snap = _minimal_snapshot(task_title="fix foo & bar")
        html = html_dashboard.render(snap)
        self.assertIn("fix foo &amp; bar", html)

    def test_html_escaping_project_name(self) -> None:
        snap = _minimal_snapshot(project_name='proj<"evil">')
        html = html_dashboard.render(snap)
        self.assertNotIn('<"evil">', html)
        self.assertIn("proj&lt;", html)

    def test_pr_url_link(self) -> None:
        snap = _minimal_snapshot()
        snap["projects"][0]["tasks"][0]["pr_url"] = "https://github.com/x/y/pull/1"
        html = html_dashboard.render(snap)
        self.assertIn('href="https://github.com/x/y/pull/1"', html)

    def test_awaiting_orders_banner(self) -> None:
        snap = _minimal_snapshot(task_status="awaiting_orders")
        snap["projects"][0]["awaiting"] = snap["projects"][0]["tasks"]
        html = html_dashboard.render(snap)
        self.assertIn("awaiting orders", html)

    def test_empty_snapshot_no_crash(self) -> None:
        snap: dict = {
            "generated_at": "2026-06-20T00:00:00Z",
            "version": "0.1.0",
            "projects": [],
            "sessions": [],
            "recent_events": [],
        }
        html = html_dashboard.render(snap)
        self.assertIn("fleet dashboard", html)
        self.assertIn("no registered projects", html)

    def test_missing_state_dir_shown(self) -> None:
        snap: dict = {
            "generated_at": "2026-06-20T00:00:00Z",
            "version": "0.1.0",
            "projects": [
                {
                    "name": "ghost",
                    "repo": "/missing",
                    "repo_exists": False,
                    "state_exists": False,
                    "by_status": {},
                    "awaiting": [],
                    "tasks": [],
                }
            ],
            "sessions": [],
            "recent_events": [],
        }
        html = html_dashboard.render(snap)
        self.assertIn("state dir not found", html)

    def test_session_filter_select_contains_all_and_sessions(self) -> None:
        snap = _session_filter_snapshot()
        html = html_dashboard.render(snap)
        self.assertIn('<select id="session-filter">', html)
        self.assertIn('<option value="">All</option>', html)
        self.assertIn('<option value="main">main</option>', html)
        self.assertIn('<option value="all-projects">all-projects</option>', html)
        self.assertIn('<option value="ops">ops</option>', html)

    def test_project_lanes_have_scope_session_data(self) -> None:
        snap = _session_filter_snapshot()
        html = html_dashboard.render(snap)
        self.assertIn('data-project-lane="1"', html)
        self.assertIn('data-sessions="main all-projects"', html)
        self.assertIn('data-sessions="all-projects ops"', html)

    def test_session_filter_escapes_label_in_options_and_data(self) -> None:
        snap = _minimal_snapshot(session_label='main"<tag>')
        html = html_dashboard.render(snap)
        self.assertIn(
            '<option value="main&quot;&lt;tag&gt;">main&quot;&lt;tag&gt;</option>',
            html,
        )
        self.assertIn('data-sessions="main&quot;&lt;tag&gt;"', html)

    def test_session_filter_script_persists_and_hides_project_lanes(self) -> None:
        snap = _session_filter_snapshot()
        html = html_dashboard.render(snap)
        self.assertIn("[hidden] { display: none !important; }", html)
        self.assertIn("fleet.dashboard.sessionFilter", html)
        self.assertIn("sessionStorage.getItem(storageKey)", html)
        self.assertIn("sessionStorage.setItem(storageKey, value)", html)
        self.assertIn('document.querySelectorAll(\'[data-project-lane="1"]\')', html)
        self.assertIn("lane.hidden = sessions.indexOf(value) === -1", html)
        self.assertIn('select.addEventListener("change"', html)


def _completed_snapshot(rows: list[dict] | None = None, *, truncated: int = 0) -> dict:
    snap = _minimal_snapshot(project_name="alpha")
    if rows is None:
        rows = [
            {
                "project": "alpha",
                "id": "old-feature",
                "title": "ship the old feature",
                "status": "completed",
                "severity": status_data.status_severity("completed"),
                "formation": "solo",
                "completed_ts": "2026-06-19T12:00:00Z",
                "completed_epoch": 1750334400,
            }
        ]
    snap["completed"] = {
        "tasks": rows,
        "within_days": 30,
        "truncated": truncated,
    }
    return snap


class CompletedSectionTests(unittest.TestCase):
    def test_completed_section_heading_and_selector(self) -> None:
        out = html_dashboard.render(_completed_snapshot())
        self.assertIn("<h2>Completed</h2>", out)
        self.assertIn('<select id="completed-window">', out)
        self.assertIn('<option value="1">Last 1 day</option>', out)
        self.assertIn('<option value="7">Last 7 days</option>', out)
        self.assertIn('<option value="30">Last 30 days</option>', out)

    def test_completed_row_fields(self) -> None:
        out = html_dashboard.render(_completed_snapshot())
        self.assertIn("ship the old feature", out)
        self.assertIn("completed", out)
        self.assertIn("solo", out)
        # Project shown in the row.
        self.assertIn("alpha", out)

    def test_completed_row_has_escaped_data_ts(self) -> None:
        out = html_dashboard.render(_completed_snapshot())
        self.assertIn('data-completed-ts="1750334400"', out)

    def test_completed_relative_time(self) -> None:
        # generated_at is 2026-06-20T00:00:00Z; completion 12h earlier.
        out = html_dashboard.render(_completed_snapshot())
        self.assertIn("12h ago", out)

    def test_completed_title_escaped(self) -> None:
        rows = [
            {
                "project": "alpha",
                "id": "x",
                "title": '<script>alert("xss")</script>',
                "status": "completed",
                "severity": "ok",
                "formation": "solo",
                "completed_ts": "2026-06-19T12:00:00Z",
                "completed_epoch": 1750334400,
            }
        ]
        out = html_dashboard.render(_completed_snapshot(rows))
        self.assertNotIn("<script>alert", out)
        self.assertIn("&lt;script&gt;", out)

    def test_completed_empty_shows_note_no_table(self) -> None:
        out = html_dashboard.render(_completed_snapshot(rows=[]))
        self.assertIn("no recently-completed tasks", out)
        self.assertNotIn('class="completed-table"', out)

    def test_completed_truncation_note(self) -> None:
        out = html_dashboard.render(_completed_snapshot(truncated=5))
        self.assertIn("5 older completed task(s) in range are not shown", out)

    def test_completed_section_present_when_snapshot_lacks_key(self) -> None:
        # Backward-compatible: a snapshot with no "completed" key still renders
        # the section with the empty note rather than crashing.
        snap = _minimal_snapshot()
        snap.pop("completed", None)
        out = html_dashboard.render(snap)
        self.assertIn("<h2>Completed</h2>", out)
        self.assertIn("no recently-completed tasks", out)

    def test_completed_window_script_persistence(self) -> None:
        out = html_dashboard.render(_completed_snapshot())
        self.assertIn("fleet.dashboard.completedWindow", out)
        self.assertIn('document.querySelectorAll("[data-completed-ts]")', out)
        self.assertIn("Date.now()", out)
        self.assertIn("completed-window-empty", out)


class RebuildGlobalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self.fleet_home = base / "fleet-state"
        self.fleet_home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        self._old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.repo)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old
        self._tmp.cleanup()

    def _add_task(self, task_id: str, title: str, status: str = "running") -> None:
        state.save_task(
            self.state_dir,
            task_id,
            {"id": task_id, "title": title, "status": status},
        )

    def test_rebuild_global_writes_file(self) -> None:
        self._add_task("1", "implement feature")
        path = html_dashboard.rebuild_global()
        self.assertEqual(path, html_dashboard.global_dashboard_path())
        self.assertTrue(path.is_file())
        content = path.read_text(encoding="utf-8")
        self.assertGreater(len(content), 0)
        self.assertIn("implement feature", content)

    def test_rebuild_global_returns_path(self) -> None:
        path = html_dashboard.rebuild_global()
        self.assertIsInstance(path, Path)
        self.assertTrue(path.exists())

    def test_rebuild_global_if_present_noop_when_absent(self) -> None:
        out = html_dashboard.global_dashboard_path()
        self.assertFalse(out.exists())
        result = html_dashboard.rebuild_global_if_present()
        self.assertIsNone(result)
        self.assertFalse(out.exists())

    def test_rebuild_global_if_present_refreshes_when_exists(self) -> None:
        path = html_dashboard.rebuild_global()
        self.assertTrue(path.is_file())
        self._add_task("2", "second task")
        result = html_dashboard.rebuild_global_if_present()
        self.assertEqual(result, path)
        content = path.read_text(encoding="utf-8")
        self.assertIn("second task", content)

    def test_rebuild_global_if_present_swallows_exceptions(self) -> None:
        path = html_dashboard.rebuild_global()
        self.assertTrue(path.is_file())

        def _raise() -> dict:
            raise RuntimeError("simulated render failure")

        with patch.object(status_data, "collect_global_snapshot", _raise):
            result = html_dashboard.rebuild_global_if_present()
        self.assertIsNone(result)


class UXImprovementTests(unittest.TestCase):
    """Tests for the v1→v2 UX polish: banner anchors, title cleaning,
    relative timestamps, event filtering, PR number labels, and active-first
    project layout."""

    def _snap_with_awaiting(self) -> dict:
        """Snapshot with one awaiting_orders task in project 'alpha'."""
        snap = _minimal_snapshot(
            project_name="alpha",
            task_title="# fleet-task : implement something",
            task_status="awaiting_orders",
        )
        snap["projects"][0]["awaiting"] = snap["projects"][0]["tasks"]
        return snap

    # ------------------------------------------------------------------
    # 1. Awaiting banner with anchor links
    # ------------------------------------------------------------------

    def test_banner_has_anchor_link_to_task_row(self) -> None:
        snap = self._snap_with_awaiting()
        out = html_dashboard.render(snap)
        # Banner must contain a link to the task row anchor.
        self.assertIn('href="#t-alpha-1"', out)

    def test_task_row_has_id_attribute(self) -> None:
        snap = self._snap_with_awaiting()
        out = html_dashboard.render(snap)
        # The task row must carry the matching id.
        self.assertIn('id="t-alpha-1"', out)

    def test_awaiting_card_has_css_class(self) -> None:
        snap = self._snap_with_awaiting()
        out = html_dashboard.render(snap)
        self.assertIn("awaiting-card", out)

    def test_render_uses_task_board_lanes(self) -> None:
        snap = _minimal_snapshot(task_status="running")
        out = html_dashboard.render(snap)
        self.assertIn('class="task-board"', out)
        self.assertIn("Your turn", out)
        self.assertIn("Running", out)
        self.assertIn("In review", out)
        # "Needs attention" lane folded into "Other" (failed/changes-requested/rejected).
        self.assertIn("Other", out)
        self.assertNotIn("Needs attention", out)

    def test_attention_statuses_route_to_other_lane(self) -> None:
        for status in ("failed", "changes-requested", "rejected"):
            self.assertEqual(html_dashboard._lane_for_status(status), "other", status)

    def test_banner_absent_when_zero_awaiting(self) -> None:
        snap = _minimal_snapshot(task_status="running")
        out = html_dashboard.render(snap)
        self.assertNotIn("awaiting orders", out)

    def test_no_external_assets_or_scripts(self) -> None:
        snap = _minimal_snapshot()
        snap["projects"][0]["tasks"][0]["pr_url"] = "https://github.com/x/y/pull/42"
        out = html_dashboard.render(snap)
        self.assertNotIn("<script src=", out)
        self.assertNotIn("<link", out)
        self.assertNotIn("@import", out)
        self.assertNotIn("url(", out)

    # ------------------------------------------------------------------
    # 2. Title cleaning
    # ------------------------------------------------------------------

    def test_title_strips_leading_hash(self) -> None:
        snap = _minimal_snapshot(task_title="# My task title")
        out = html_dashboard.render(snap)
        self.assertIn("My task title", out)
        # The raw "#" prefix should not appear as text content.
        self.assertNotIn("># My task title<", out)

    def test_long_title_truncated_with_full_in_hover(self) -> None:
        long_title = "A" * 70
        snap = _minimal_snapshot(task_title=long_title)
        out = html_dashboard.render(snap)
        # Truncation ellipsis present.
        self.assertIn("…", out)
        # Full title in title= attribute.
        self.assertIn(f'title="{long_title}"', out)

    def test_short_title_no_truncation(self) -> None:
        snap = _minimal_snapshot(task_title="Short title")
        out = html_dashboard.render(snap)
        self.assertIn("Short title", out)
        # No spurious title= attribute for short titles.
        self.assertNotIn('title="Short title"', out)

    # ------------------------------------------------------------------
    # 3. Relative timestamps on recent events
    # ------------------------------------------------------------------

    def test_event_timestamp_shown_as_relative(self) -> None:
        snap = _minimal_snapshot()
        # Add a significant event (milestone) that predates generated_at by 5 min.
        snap["recent_events"] = [
            {
                "ts": "2026-06-19T23:55:00Z",
                "type": "milestone",
                "task_id": "1",
                "project": "demo",
            }
        ]
        # generated_at is 2026-06-20T00:00:00Z (5 minutes later).
        out = html_dashboard.render(snap)
        self.assertIn("5m ago", out)

    def test_event_absolute_timestamp_in_title_attr(self) -> None:
        snap = _minimal_snapshot()
        snap["recent_events"] = [
            {
                "ts": "2026-06-19T23:55:00Z",
                "type": "milestone",
                "task_id": "1",
                "project": "demo",
            }
        ]
        out = html_dashboard.render(snap)
        self.assertIn('title="2026-06-19T23:55:00Z"', out)

    # ------------------------------------------------------------------
    # 4. Event noise filtering
    # ------------------------------------------------------------------

    def test_noise_events_excluded(self) -> None:
        snap = _minimal_snapshot()
        snap["recent_events"] = [
            {"ts": "2026-06-20T00:00:00Z", "type": "heartbeat", "task_id": "1", "project": "demo"},
            {"ts": "2026-06-20T00:00:00Z", "type": "inbox_seen", "task_id": "1", "project": "demo"},
            {"ts": "2026-06-20T00:00:00Z", "type": "inbox_message", "task_id": "1", "project": "demo"},
            {"ts": "2026-06-20T00:00:00Z", "type": "handoff_message", "task_id": "1", "project": "demo"},
            {"ts": "2026-06-20T00:00:00Z", "type": "prompt_deliverer_started", "task_id": "1", "project": "demo"},
        ]
        out = html_dashboard.render(snap)
        # All entries are noise → events section should be empty.
        self.assertIn("(none)", out)

    def test_significant_events_shown(self) -> None:
        snap = _minimal_snapshot()
        snap["recent_events"] = [
            {"ts": "2026-06-20T00:00:00Z", "type": "heartbeat", "task_id": "1", "project": "demo"},
            {"ts": "2026-06-20T00:00:00Z", "type": "done", "task_id": "1", "project": "demo"},
        ]
        out = html_dashboard.render(snap)
        # "done" event should appear in the event list.
        self.assertIn("done", out)
        # heartbeat should be filtered out — not shown in the events section.
        events_section = out.split("<h2>Recent events")[1] if "<h2>Recent events" in out else ""
        self.assertNotIn("heartbeat", events_section)

    def test_significant_event_coloring(self) -> None:
        snap = _minimal_snapshot()
        snap["recent_events"] = [
            {"ts": "2026-06-20T00:00:00Z", "type": "done", "task_id": "1", "project": "demo"},
        ]
        out = html_dashboard.render(snap)
        self.assertIn('class="ev-ok"', out)

    def test_attention_event_coloring(self) -> None:
        snap = _minimal_snapshot()
        snap["recent_events"] = [
            {"ts": "2026-06-20T00:00:00Z", "type": "failed", "task_id": "1", "project": "demo"},
        ]
        out = html_dashboard.render(snap)
        self.assertIn('class="ev-attention"', out)

    # ------------------------------------------------------------------
    # 4b. PR link shows PR number
    # ------------------------------------------------------------------

    def test_pr_link_shows_number(self) -> None:
        snap = _minimal_snapshot()
        snap["projects"][0]["tasks"][0]["pr_url"] = "https://github.com/x/y/pull/42"
        out = html_dashboard.render(snap)
        self.assertIn("PR #42", out)
        self.assertIn('href="https://github.com/x/y/pull/42"', out)

    def test_pr_url_attribute_escaped(self) -> None:
        snap = _minimal_snapshot()
        snap["projects"][0]["tasks"][0]["pr_url"] = 'https://example.com/pull/1?a="b"'
        out = html_dashboard.render(snap)
        self.assertNotIn('"b"', out)
        self.assertIn("&quot;b&quot;", out)

    # ------------------------------------------------------------------
    # 5. Active-first project layout
    # ------------------------------------------------------------------

    def test_active_project_before_empty(self) -> None:
        snap: dict = {
            "generated_at": "2026-06-20T00:00:00Z",
            "version": "0.1.0",
            "projects": [
                {
                    "name": "zzz-idle",
                    "repo": "/tmp/idle",
                    "repo_exists": True,
                    "state_exists": True,
                    "by_status": {},
                    "awaiting": [],
                    "tasks": [],
                },
                {
                    "name": "aaa-work",
                    "repo": "/tmp/work",
                    "repo_exists": True,
                    "state_exists": True,
                    "by_status": {"running": 1},
                    "awaiting": [],
                    "tasks": [
                        {
                            "id": "1",
                            "title": "do work",
                            "status": "running",
                            "severity": "active",
                            "formation": "solo",
                            "stage": None,
                            "last_seen": "2m ago",
                            "pr_url": None,
                        }
                    ],
                },
            ],
            "sessions": [],
            "recent_events": [],
        }
        out = html_dashboard.render(snap)
        # "aaa-work" has tasks; "zzz-idle" does not.  Even though "zzz" sorts
        # alphabetically before "aaa", active projects must appear first.
        pos_active = out.index("aaa-work")
        pos_idle = out.index("zzz-idle")
        self.assertLess(pos_active, pos_idle, "active project must appear before idle one")

    def test_empty_project_compressed(self) -> None:
        snap: dict = {
            "generated_at": "2026-06-20T00:00:00Z",
            "version": "0.1.0",
            "projects": [
                {
                    "name": "idle",
                    "repo": "/tmp/idle",
                    "repo_exists": True,
                    "state_exists": True,
                    "by_status": {},
                    "awaiting": [],
                    "tasks": [],
                },
            ],
            "sessions": [],
            "recent_events": [],
        }
        out = html_dashboard.render(snap)
        # Empty project should appear as a compact entry, not a full <table>.
        self.assertIn("idle", out)
        self.assertNotIn("<table>", out)
        self.assertIn("no tasks", out)

    def test_header_summary_stats(self) -> None:
        snap = _minimal_snapshot(task_status="running")
        out = html_dashboard.render(snap)
        self.assertIn("running 1", out)
        self.assertIn("awaiting 0", out)
        self.assertIn("projects 1", out)
