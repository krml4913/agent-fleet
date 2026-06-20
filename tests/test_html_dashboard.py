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
        self.assertNotIn("<script>", html)
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
