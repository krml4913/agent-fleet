"""Tests for ``fleet edit`` transient local editor."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state as state_mod  # noqa: E402
from fleet.commands import edit as edit_cmd  # noqa: E402
from tests._fleet_test_helpers import make_project, seed_global_roles  # noqa: E402


class RunningEditServer:
    def __init__(self, context: edit_cmd.EditContext):
        self.server = edit_cmd.EditServer(context)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "RunningEditServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _request(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict | None = None,
    host: str | None = None,
) -> tuple[int, dict | str, dict[str, str]]:
    data = None
    headers: dict[str, str] = {}
    if token is not None:
        headers["X-Fleet-Token"] = token
    if host is not None:
        headers["Host"] = host
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            ctype = resp.headers.get("Content-Type", "")
            parsed: dict | str = json.loads(raw) if "application/json" in ctype else raw
            return resp.status, parsed, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        ctype = exc.headers.get("Content-Type", "")
        parsed = json.loads(raw) if "application/json" in ctype and raw else raw
        return exc.code, parsed, dict(exc.headers)


class EditCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fleet_home = Path(self.tmp.name) / "fleet-state"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.old_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.addCleanup(self._restore_home)
        self.state_dir = make_project(self.fleet_home, "proj", self.repo)
        seed_global_roles(self.fleet_home)

    def _restore_home(self) -> None:
        if self.old_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self.old_home

    def _context(self, token: str = "secret") -> edit_cmd.EditContext:
        return edit_cmd.EditContext(token=token, state_dir=self.state_dir, project_label="proj")

    def test_rejects_missing_wrong_token_and_wrong_host_without_cors(self) -> None:
        with RunningEditServer(self._context(token="secret")) as srv:
            status, _body, headers = _request(srv.base_url, "/api/list")
            self.assertEqual(status, 403)
            self.assertEqual(headers.get("Cache-Control"), "no-store")
            self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

            status, _body, _headers = _request(srv.base_url, "/api/list", token="wrong")
            self.assertEqual(status, 403)

            status, _body, _headers = _request(
                srv.base_url,
                "/api/list",
                token="secret",
                host="localhost:1234",
            )
            self.assertEqual(status, 403)

            status, body, headers = _request(srv.base_url, "/api/list", token="secret")
            self.assertEqual(status, 200)
            self.assertIsInstance(body, dict)
            self.assertTrue(body["ok"])
            self.assertEqual(headers.get("Cache-Control"), "no-store")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_root_allows_query_bootstrap_but_api_requires_header(self) -> None:
        with RunningEditServer(self._context(token="secret")) as srv:
            status, body, _headers = _request(srv.base_url, "/?token=secret")
            self.assertEqual(status, 200)
            self.assertIsInstance(body, str)
            self.assertIn("fleet edit", body)

            status, _body, _headers = _request(srv.base_url, "/api/list?token=secret")
            self.assertEqual(status, 403)

    def test_unresolved_project_context_lists_global_only_and_rejects_project_create(self) -> None:
        global_role = state_mod.global_roles_dir() / "global-only.md"
        global_role.parent.mkdir(parents=True, exist_ok=True)
        global_role.write_text("global\n", encoding="utf-8")
        context = edit_cmd.EditContext(token="secret", state_dir=None, project_label=None)
        with RunningEditServer(context) as srv:
            status, body, _headers = _request(srv.base_url, "/api/list", token="secret")
            self.assertEqual(status, 200)
            self.assertIsNone(body["data"]["project"])
            roles = {item["name"]: item for item in body["data"]["roles"]}
            self.assertIn("global-only", roles)
            self.assertTrue(all(tier.get("tier_key") != "project" for tier in roles["global-only"]["tiers"]))

            status, body, _headers = _request(
                srv.base_url,
                "/api/create",
                token="secret",
                method="POST",
                body={"kind": "role", "tier": "project", "name": "x", "mode": "blank"},
            )
            self.assertEqual(status, 400)
            self.assertIn("project tier is unavailable", body["error"])

    def test_list_shows_wins_shadowed_validation_and_drift(self) -> None:
        global_solo = state_mod.global_formations_dir() / "solo.yaml"
        global_solo.parent.mkdir(parents=True, exist_ok=True)
        global_solo.write_text(
            "name: solo\n"
            "description: changed\n"
            "user_aproval: required\n"
            "stages:\n"
            "  - role: driver\n"
            "    user_approval: required\n",
            encoding="utf-8",
        )
        with RunningEditServer(self._context(token="secret")) as srv:
            status, body, _headers = _request(srv.base_url, "/api/list", token="secret")
        self.assertEqual(status, 200)
        formations = {item["name"]: item for item in body["data"]["formations"]}
        self.assertIn("solo", formations)
        solo = formations["solo"]
        self.assertTrue(solo["drift"])
        tiers = {tier.get("tier_key") or tier["tier"]: tier for tier in solo["tiers"]}
        self.assertTrue(tiers["project"]["wins"])
        self.assertTrue(tiers["global"]["shadowed"])
        self.assertFalse(tiers["global"]["validation"]["valid"])

    def test_invalid_file_loads_but_invalid_save_does_not_write(self) -> None:
        path = self.state_dir / "formations" / "broken.yaml"
        path.write_text("name: broken\nuser_aproval: required\nstages:\n  - role: driver\n", encoding="utf-8")
        with RunningEditServer(self._context(token="secret")) as srv:
            status, body, _headers = _request(
                srv.base_url,
                "/api/file?kind=formation&tier=project&name=broken",
                token="secret",
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["file"]["content"], path.read_text(encoding="utf-8"))
            self.assertFalse(body["file"]["validation"]["valid"])
            base_hash = body["file"]["hash"]

            status, body, _headers = _request(
                srv.base_url,
                "/api/save",
                token="secret",
                method="POST",
                body={
                    "kind": "formation",
                    "tier": "project",
                    "name": "broken",
                    "base_hash": base_hash,
                    "content": "name: broken\nstages:\n  - role: driver\n    user_aproval: required\n",
                },
            )
        self.assertEqual(status, 400)
        self.assertIn("user_approval", "\n".join(body["errors"]))
        self.assertEqual(path.read_text(encoding="utf-8"), "name: broken\nuser_aproval: required\nstages:\n  - role: driver\n")

    def test_bad_or_empty_yaml_rejects_cleanly(self) -> None:
        path = self.state_dir / "formations" / "empty.yaml"
        path.write_text("name: empty\nstages:\n  - role: driver\n", encoding="utf-8")
        with RunningEditServer(self._context(token="secret")) as srv:
            _, loaded, _ = _request(
                srv.base_url,
                "/api/file?kind=formation&tier=project&name=empty",
                token="secret",
            )
            for content, expected in (("name: [unterminated\n", "while parsing"), ("   \n", "YAML mapping")):
                status, body, _headers = _request(
                    srv.base_url,
                    "/api/save",
                    token="secret",
                    method="POST",
                    body={
                        "kind": "formation",
                        "tier": "project",
                        "name": "empty",
                        "base_hash": loaded["file"]["hash"],
                        "content": content,
                    },
                )
                self.assertEqual(status, 400)
                self.assertIn(expected, "\n".join(body["errors"]))
        self.assertEqual(path.read_text(encoding="utf-8"), "name: empty\nstages:\n  - role: driver\n")

    def test_name_mismatch_rejected_and_comments_custom_keys_round_trip(self) -> None:
        path = self.state_dir / "formations" / "custom.yaml"
        content = (
            "# keep this comment\n"
            "name: custom\n"
            "stages:\n"
            "  - role: driver\n"
            "    very: custom\n"
            "    user_approval: required\n"
        )
        path.write_text(content, encoding="utf-8")
        with RunningEditServer(self._context(token="secret")) as srv:
            _, loaded, _ = _request(
                srv.base_url,
                "/api/file?kind=formation&tier=project&name=custom",
                token="secret",
            )
            status, body, _headers = _request(
                srv.base_url,
                "/api/save",
                token="secret",
                method="POST",
                body={
                    "kind": "formation",
                    "tier": "project",
                    "name": "custom",
                    "base_hash": loaded["file"]["hash"],
                    "content": content.replace("name: custom", "name: other"),
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("must match filename stem", "\n".join(body["errors"]))

            status, body, _headers = _request(
                srv.base_url,
                "/api/save",
                token="secret",
                method="POST",
                body={
                    "kind": "formation",
                    "tier": "project",
                    "name": "custom",
                    "base_hash": loaded["file"]["hash"],
                    "content": content,
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_crlf_is_normalized_to_lf_on_save(self) -> None:
        path = self.state_dir / "formations" / "crlf.yaml"
        path.write_text("name: crlf\nstages:\n  - role: driver\n", encoding="utf-8")
        crlf = "name: crlf\r\nstages:\r\n  - role: driver\r\n"
        with RunningEditServer(self._context(token="secret")) as srv:
            _, loaded, _ = _request(
                srv.base_url,
                "/api/file?kind=formation&tier=project&name=crlf",
                token="secret",
            )
            status, _body, _headers = _request(
                srv.base_url,
                "/api/save",
                token="secret",
                method="POST",
                body={
                    "kind": "formation",
                    "tier": "project",
                    "name": "crlf",
                    "base_hash": loaded["file"]["hash"],
                    "content": crlf,
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(path.read_bytes(), b"name: crlf\nstages:\n  - role: driver\n")

    def test_stale_save_gets_409_and_writes_nothing(self) -> None:
        path = self.state_dir / "formations" / "race.yaml"
        path.write_text("name: race\nstages:\n  - role: driver\n", encoding="utf-8")
        with RunningEditServer(self._context(token="secret")) as srv:
            _, loaded, _ = _request(
                srv.base_url,
                "/api/file?kind=formation&tier=project&name=race",
                token="secret",
            )
            path.write_text("name: race\nstages:\n  - role: implementer\n", encoding="utf-8")
            status, body, _headers = _request(
                srv.base_url,
                "/api/save",
                token="secret",
                method="POST",
                body={
                    "kind": "formation",
                    "tier": "project",
                    "name": "race",
                    "base_hash": loaded["file"]["hash"],
                    "content": "name: race\nstages:\n  - role: driver\n    user_approval: required\n",
                },
            )
        self.assertEqual(status, 409)
        self.assertIn("changed on disk", body["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), "name: race\nstages:\n  - role: implementer\n")

    def test_missing_role_reference_is_non_blocking_warning(self) -> None:
        path = self.state_dir / "formations" / "warn.yaml"
        path.write_text("name: warn\nstages:\n  - role: driver\n", encoding="utf-8")
        content = "name: warn\nstages:\n  - role: missing-role\n"
        with RunningEditServer(self._context(token="secret")) as srv:
            _, loaded, _ = _request(
                srv.base_url,
                "/api/file?kind=formation&tier=project&name=warn",
                token="secret",
            )
            status, body, _headers = _request(
                srv.base_url,
                "/api/save",
                token="secret",
                method="POST",
                body={
                    "kind": "formation",
                    "tier": "project",
                    "name": "warn",
                    "base_hash": loaded["file"]["hash"],
                    "content": content,
                },
            )
        self.assertEqual(status, 200)
        self.assertIn("no role named", "\n".join(body["validation"]["warnings"]))
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_create_rejects_bad_name_and_copy_down_materializes_project_file(self) -> None:
        global_role = state_mod.global_roles_dir() / "custom-role.md"
        global_role.parent.mkdir(parents=True, exist_ok=True)
        global_role.write_text("global role\n", encoding="utf-8")
        project_role = self.state_dir / "roles" / "custom-role.md"
        with RunningEditServer(self._context(token="secret")) as srv:
            status, body, _headers = _request(
                srv.base_url,
                "/api/create",
                token="secret",
                method="POST",
                body={"kind": "role", "tier": "project", "name": "../bad", "mode": "blank"},
            )
            self.assertEqual(status, 400)
            self.assertIn("[A-Za-z0-9_-]+", body["error"])

            status, body, _headers = _request(
                srv.base_url,
                "/api/create",
                token="secret",
                method="POST",
                body={"kind": "role", "tier": "project", "name": "custom-role", "mode": "copy-down"},
            )
            self.assertEqual(status, 201)
            self.assertEqual(body["file"]["content"], "global role\n")

            status, listing, _headers = _request(srv.base_url, "/api/list", token="secret")
        self.assertEqual(status, 200)
        self.assertEqual(project_role.read_text(encoding="utf-8"), "global role\n")
        roles = {item["name"]: item for item in listing["data"]["roles"]}
        tiers = {tier.get("tier_key") or tier["tier"]: tier for tier in roles["custom-role"]["tiers"]}
        self.assertTrue(tiers["project"]["wins"])
        self.assertTrue(tiers["global"]["shadowed"])

    def test_no_browser_subprocess_prints_serving_url_and_exits_on_done(self) -> None:
        env = os.environ.copy()
        env["FLEET_HOME"] = str(self.fleet_home)
        env.pop("FLEET_TASK_ID", None)
        env.pop("FLEET_STATE_DIR", None)
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "fleet"), "edit", "--project", "proj", "--no-browser"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(self.repo),
        )
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline().strip()
            match = re.search(r"(http://127\.0\.0\.1:\d+/\?token=([^ ]+))", line)
            self.assertIsNotNone(match, line)
            url = match.group(1)
            token = urllib.parse.unquote(match.group(2))
            parsed = urllib.parse.urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"

            status, body, _headers = _request(base, f"/?token={urllib.parse.quote(token)}")
            self.assertEqual(status, 200)
            self.assertIsInstance(body, str)

            status, body, _headers = _request(base, "/api/quit", token=token, method="POST")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            proc.wait(timeout=5)
            proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.communicate(timeout=5)

    def test_atomic_write_uses_temp_file_and_replace(self) -> None:
        path = self.state_dir / "roles" / "atomic.md"
        with unittest.mock.patch("fleet.commands.edit.os.replace") as replace:
            edit_cmd._atomic_write_text(path, "content\n")
        self.assertTrue(replace.called)
        tmp_name, dest = replace.call_args.args
        self.assertEqual(Path(dest), path)
        self.assertIn(".atomic.md.", Path(tmp_name).name)


if __name__ == "__main__":
    unittest.main()
