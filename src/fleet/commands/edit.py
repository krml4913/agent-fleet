"""``fleet edit`` — transient localhost editor for formations and roles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from .. import driver_prompt
from .. import formation as formation_mod
from .. import state as state_mod
from .. import task_context

_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_ROLES_DIR = _ROOT / "docs" / "prompts" / "roles"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class EditContext:
    token: str
    state_dir: Path | None
    project_label: str | None


class EditServer(ThreadingHTTPServer):
    """HTTP server carrying the per-run editor context."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, context: EditContext):
        super().__init__(("127.0.0.1", 0), EditHandler)
        self.context = context
        self.expected_host = f"127.0.0.1:{self.server_port}"


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "edit",
        help="Open a transient local editor for formations and roles",
        description=(
            "Serve a token-gated local web editor for runtime formation YAML "
            "and role markdown files. The process runs in the foreground until "
            "Ctrl-C or the UI Done button."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help=(
            "Project name (registry); when omitted, resolve like other "
            "project-centric commands and degrade to global-only if unresolved"
        ),
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the URL without opening a browser",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    state_dir, project_label = _resolve_project(project_name)
    token = secrets.token_urlsafe(32)
    server = EditServer(EditContext(token=token, state_dir=state_dir, project_label=project_label))
    url = f"http://127.0.0.1:{server.server_port}/?token={urllib.parse.quote(token)}"
    print(f"fleet edit: {url}", flush=True)
    if state_dir is None:
        print("fleet edit: no project resolved; editing global tier only", file=sys.stderr, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nfleet edit: stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def _resolve_project(project_name: str | None) -> tuple[Path | None, str | None]:
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound:
        return None, None
    try:
        loaded = state_mod.load_project(state_dir)
        label = str(loaded.get("name") or state_dir.name)
    except Exception:
        label = state_dir.name
    return state_dir, label


class EditHandler(BaseHTTPRequestHandler):
    server: EditServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        if not self._authorized(allow_query_bootstrap=self._path_parts()[0] == "/"):
            return
        path, query = self._path_parts()
        if path == "/":
            self._send_html(_html_page())
        elif path == "/api/list":
            self._send_json({"ok": True, "data": _list_items(self.server.context)})
        elif path == "/api/file":
            self._handle_get_file(query)
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        if not self._authorized(allow_query_bootstrap=False):
            return
        path, _query = self._path_parts()
        if path == "/api/save":
            self._handle_save()
        elif path == "/api/create":
            self._handle_create()
        elif path == "/api/quit":
            self._send_json({"ok": True, "message": "editor closed"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "not found")

    def _path_parts(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def _authorized(self, *, allow_query_bootstrap: bool) -> bool:
        host = self.headers.get("Host", "")
        if host != self.server.expected_host:
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return False
        token = self.headers.get("X-Fleet-Token")
        if allow_query_bootstrap and not token:
            _path, query = self._path_parts()
            values = query.get("token") or []
            token = values[0] if values else None
        if token != self.server.context.token:
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return False
        return True

    def _handle_get_file(self, query: dict[str, list[str]]) -> None:
        try:
            ref = _parse_ref(_query_one(query, "kind"), _query_one(query, "tier"), _query_one(query, "name"))
            path = _runtime_path(self.server.context, ref["kind"], ref["tier"], ref["name"])
            content = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        validation = _validate_file(self.server.context, ref["kind"], ref["name"], content)
        self._send_json({
            "ok": True,
            "file": {
                **ref,
                "path": str(path),
                "content": content,
                "hash": _hash_text(content),
                "validation": validation,
                "shadowed_by": _shadowed_by(self.server.context, ref["kind"], ref["tier"], ref["name"]),
            },
        })

    def _handle_save(self) -> None:
        try:
            data = self._read_json()
            ref = _parse_ref(str(data.get("kind", "")), str(data.get("tier", "")), str(data.get("name", "")))
            content = _normalize_lf(str(data.get("content", "")))
            base_hash = str(data.get("base_hash", ""))
            path = _runtime_path(self.server.context, ref["kind"], ref["tier"], ref["name"])
            current = path.read_text(encoding="utf-8")
            if _hash_text(current) != base_hash:
                self._send_json(
                    {"ok": False, "error": "file changed on disk — reload before saving"},
                    status=HTTPStatus.CONFLICT,
                )
                return
            errors, warnings = _validate_for_save(self.server.context, ref["kind"], ref["name"], content)
            if errors:
                self._send_json({"ok": False, "errors": errors, "warnings": warnings}, status=HTTPStatus.BAD_REQUEST)
                return
            _atomic_write_text(path, content)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json({
            "ok": True,
            "hash": _hash_text(content),
            "validation": _validation_result(True, errors=[], warnings=warnings),
        })

    def _handle_create(self) -> None:
        try:
            data = self._read_json()
            kind = str(data.get("kind", ""))
            tier = str(data.get("tier", ""))
            name = str(data.get("name", ""))
            mode = str(data.get("mode", "blank"))
            source_tier = data.get("source_tier")
            _parse_ref(kind, tier, name)
            if tier == "project" and self.server.context.state_dir is None:
                raise ValueError("project tier is unavailable because no project resolved")
            path = _runtime_path(self.server.context, kind, tier, name)
            if path.exists():
                raise ValueError(f"{kind} {name!r} already exists in {tier}")
            content = _initial_content(self.server.context, kind, name, mode, source_tier)
            errors, warnings = _validate_for_save(self.server.context, kind, name, content)
            if errors:
                self._send_json({"ok": False, "errors": errors, "warnings": warnings}, status=HTTPStatus.BAD_REQUEST)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(path, content)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json({
            "ok": True,
            "file": {
                "kind": kind,
                "tier": tier,
                "name": name,
                "path": str(path),
                "content": content,
                "hash": _hash_text(content),
                "validation": _validation_result(True, errors=[], warnings=warnings),
            },
        }, status=HTTPStatus.CREATED)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8")
        data = json.loads(body or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")


def _query_one(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    if not values:
        raise ValueError(f"missing query parameter: {name}")
    return values[0]


def _parse_ref(kind: str, tier: str, name: str) -> dict[str, str]:
    if kind not in ("formation", "role"):
        raise ValueError("kind must be 'formation' or 'role'")
    if tier not in ("project", "global"):
        raise ValueError("tier must be 'project' or 'global'")
    if not _NAME_RE.fullmatch(name):
        raise ValueError("name must match [A-Za-z0-9_-]+")
    return {"kind": kind, "tier": tier, "name": name}


def _runtime_path(context: EditContext, kind: str, tier: str, name: str) -> Path:
    suffix = ".yaml" if kind == "formation" else ".md"
    if tier == "project":
        if context.state_dir is None:
            raise ValueError("project tier is unavailable because no project resolved")
        subdir = state_mod.FORMATIONS_SUBDIR if kind == "formation" else state_mod.ROLES_SUBDIR
        return context.state_dir / subdir / f"{name}{suffix}"
    if tier == "global":
        base = state_mod.global_formations_dir() if kind == "formation" else state_mod.global_roles_dir()
        return base / f"{name}{suffix}"
    raise ValueError("runtime edits are limited to project and global tiers")


def _seed_path(kind: str, name: str) -> Path:
    if kind == "formation":
        return formation_mod.TEMPLATES_DIR / f"{name}.yaml"
    return _SHIPPED_ROLES_DIR / f"{name}.md"


def _list_names(directory: Path, suffix: str) -> set[str]:
    if not directory.is_dir():
        return set()
    return {p.stem for p in directory.glob(f"*{suffix}") if p.is_file() and _NAME_RE.fullmatch(p.stem)}


def _list_items(context: EditContext) -> dict[str, Any]:
    return {
        "project": context.project_label,
        "formations": _list_kind(context, "formation"),
        "roles": _list_kind(context, "role"),
    }


def _list_kind(context: EditContext, kind: str) -> list[dict[str, Any]]:
    suffix = ".yaml" if kind == "formation" else ".md"
    project_dir = None
    if context.state_dir is not None:
        project_dir = context.state_dir / (state_mod.FORMATIONS_SUBDIR if kind == "formation" else state_mod.ROLES_SUBDIR)
    global_dir = state_mod.global_formations_dir() if kind == "formation" else state_mod.global_roles_dir()
    seed_dir = formation_mod.TEMPLATES_DIR if kind == "formation" else _SHIPPED_ROLES_DIR

    project_names = _list_names(project_dir, suffix) if project_dir is not None else set()
    global_names = _list_names(global_dir, suffix)
    seed_names = _list_names(seed_dir, suffix)
    items: list[dict[str, Any]] = []
    for name in sorted(project_names | global_names | seed_names):
        tiers: list[dict[str, Any]] = []
        if project_dir is not None and name in project_names:
            path = project_dir / f"{name}{suffix}"
            tiers.append(_tier_entry(context, kind, "project", name, path, wins=True))
        if name in global_names:
            path = global_dir / f"{name}{suffix}"
            tiers.append(_tier_entry(context, kind, "global", name, path, wins=name not in project_names))
        if name in seed_names:
            path = seed_dir / f"{name}{suffix}"
            entry = {
                "tier": "seed:shipped",
                "wins": False,
                "shadowed": False,
                "editable": False,
                "path": str(path),
                "validation": _validate_existing_file(context, kind, name, path),
            }
            tiers.append(entry)
        drift = False
        if name in global_names and name in seed_names:
            drift = (global_dir / f"{name}{suffix}").read_bytes() != (seed_dir / f"{name}{suffix}").read_bytes()
        items.append({"kind": kind, "name": name, "tiers": tiers, "drift": drift})
    return items


def _tier_entry(context: EditContext, kind: str, tier: str, name: str, path: Path, *, wins: bool) -> dict[str, Any]:
    return {
        "tier": f"project:{context.project_label}" if tier == "project" else "global",
        "tier_key": tier,
        "wins": wins,
        "shadowed": not wins,
        "editable": True,
        "path": str(path),
        "validation": _validate_existing_file(context, kind, name, path),
    }


def _validate_existing_file(context: EditContext, kind: str, name: str, path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _validation_result(False, errors=[str(exc)], warnings=[])
    return _validate_file(context, kind, name, content)


def _validate_file(context: EditContext, kind: str, name: str, content: str) -> dict[str, Any]:
    errors, warnings = _validate_for_save(context, kind, name, content)
    return _validation_result(not errors, errors=errors, warnings=warnings)


def _validate_for_save(context: EditContext, kind: str, name: str, content: str) -> tuple[list[str], list[str]]:
    if kind == "role":
        return [], []
    errors: list[str] = []
    warnings: list[str] = []
    try:
        loaded = yaml.safe_load(_normalize_lf(content))
    except yaml.YAMLError as exc:
        return [str(exc)], warnings
    if loaded is None or not isinstance(loaded, dict):
        errors.append("formation must be a YAML mapping at the top level")
        return errors, warnings
    try:
        formation_mod.validate(loaded)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings
    found_name = loaded.get("name")
    if found_name != name:
        errors.append(f"formation name {found_name!r} must match filename stem {name!r}")
        return errors, warnings
    try:
        driver_prompt.validate_formation_roles(loaded, context.state_dir)
    except driver_prompt.RoleResolutionError as exc:
        warnings.append(str(exc))
    return errors, warnings


def _validation_result(valid: bool, *, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "message": "valid" if valid and not warnings else ("warning" if valid else "invalid"),
    }


def _shadowed_by(context: EditContext, kind: str, tier: str, name: str) -> str | None:
    if tier != "global" or context.state_dir is None:
        return None
    suffix = ".yaml" if kind == "formation" else ".md"
    subdir = state_mod.FORMATIONS_SUBDIR if kind == "formation" else state_mod.ROLES_SUBDIR
    project_path = context.state_dir / subdir / f"{name}{suffix}"
    if project_path.is_file():
        return f"project:{context.project_label}"
    return None


def _initial_content(context: EditContext, kind: str, name: str, mode: str, source_tier: Any) -> str:
    if mode == "blank":
        if kind == "formation":
            return f"name: {name}\nstages:\n  - role: driver\n"
        return ""
    if mode == "seed":
        path = _seed_path(kind, name)
    elif mode == "copy-down":
        if source_tier not in (None, "global"):
            raise ValueError("copy-down only supports the global source tier")
        path = _runtime_path(context, kind, "global", name)
    else:
        raise ValueError("mode must be blank, seed, or copy-down")
    if not path.is_file():
        raise ValueError(f"source file does not exist: {path}")
    return _normalize_lf(path.read_text(encoding="utf-8"))


def _normalize_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _html_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fleet edit</title>
<style>
:root { color-scheme: light dark; --line: #8b949e55; --bad: #b42318; --warn: #a15c00; --ok: #1a7f37; }
body { margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; }
header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--line); }
main { display: grid; grid-template-columns: minmax(280px, 34%) 1fr; min-height: calc(100vh - 57px); }
aside { border-right: 1px solid var(--line); padding: 12px; overflow: auto; }
section { padding: 12px; min-width: 0; }
button, select, input { font: inherit; }
button { border: 1px solid var(--line); background: Canvas; color: CanvasText; border-radius: 6px; padding: 6px 10px; cursor: pointer; }
button.primary { background: #155eef; border-color: #155eef; color: white; }
.toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.item { border: 1px solid var(--line); border-radius: 6px; margin: 8px 0; padding: 8px; }
.item h3 { margin: 0 0 6px; font-size: 15px; }
.tier { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 4px 0; border-top: 1px solid var(--line); }
.tier:first-of-type { border-top: 0; }
.meta { color: color-mix(in srgb, CanvasText 70%, transparent); font-size: 12px; overflow-wrap: anywhere; }
.tag { border: 1px solid var(--line); border-radius: 999px; padding: 1px 6px; font-size: 12px; white-space: nowrap; }
.bad { color: var(--bad); }
.warn { color: var(--warn); }
.ok { color: var(--ok); }
textarea { box-sizing: border-box; width: 100%; min-height: 62vh; resize: vertical; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; padding: 10px; border: 1px solid var(--line); border-radius: 6px; }
.banner { border: 1px solid var(--line); border-left: 4px solid var(--warn); padding: 8px; margin: 8px 0; border-radius: 6px; }
.messages { white-space: pre-wrap; margin: 8px 0; }
.create { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 10px 0; }
@media (max-width: 760px) { main { grid-template-columns: 1fr; } aside { border-right: 0; border-bottom: 1px solid var(--line); } .create { grid-template-columns: 1fr 1fr; } }
</style>
</head>
<body>
<header><strong>fleet edit</strong><div class="toolbar"><button id="reload">Reload</button><button id="done">Done</button></div></header>
<main>
<aside>
  <div class="create">
    <select id="createKind"><option value="formation">formation</option><option value="role">role</option></select>
    <select id="createTier"><option value="project">project</option><option value="global">global</option></select>
    <select id="createMode"><option value="blank">new blank</option><option value="seed">seed shipped</option><option value="copy-down">copy-down</option></select>
    <input id="createName" placeholder="name">
  </div>
  <button id="create" class="primary">Create</button>
  <div id="list"></div>
</aside>
<section>
  <div id="current" class="meta">Select a file.</div>
  <div id="banner"></div>
  <textarea id="editor" spellcheck="false" disabled></textarea>
  <div class="toolbar"><button id="save" class="primary" disabled>Save</button></div>
  <div id="messages" class="messages"></div>
</section>
</main>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
const headers = {'X-Fleet-Token': token};
let current = null;
let baseHash = null;

async function api(path, options = {}) {
  try {
    const res = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}});
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {ok:false, error:text}; }
    if (!res.ok) {
      const message = data.error || (data.errors || []).join('\\n') || res.statusText;
      throw Object.assign(new Error(message), {status: res.status, data});
    }
    return data;
  } catch (err) {
    if (err instanceof TypeError) throw new Error('editor closed — rerun `fleet edit`');
    throw err;
  }
}

function showMessages(validation, extra = '') {
  const lines = [];
  if (extra) lines.push(extra);
  if (validation) {
    for (const e of validation.errors || []) lines.push('error: ' + e);
    for (const w of validation.warnings || []) lines.push('warning: ' + w);
  }
  document.getElementById('messages').textContent = lines.join('\\n');
}

async function loadList() {
  const out = document.getElementById('list');
  out.textContent = 'Loading...';
  try {
    const {data} = await api('/api/list');
    document.getElementById('createTier').querySelector('option[value="project"]').disabled = !data.project;
    out.innerHTML = '';
    renderGroup(out, 'Formations', data.formations);
    renderGroup(out, 'Roles', data.roles);
  } catch (err) {
    out.textContent = err.message;
  }
}

function renderGroup(out, title, items) {
  const h = document.createElement('h2');
  h.textContent = title;
  out.appendChild(h);
  if (!items.length) {
    const p = document.createElement('p');
    p.className = 'meta';
    p.textContent = 'None';
    out.appendChild(p);
    return;
  }
  for (const item of items) {
    const box = document.createElement('div');
    box.className = 'item';
    const h3 = document.createElement('h3');
    h3.textContent = item.name;
    box.appendChild(h3);
    if (item.drift) {
      const drift = document.createElement('div');
      drift.className = 'tag warn';
      drift.textContent = 'differs from shipped seed';
      box.appendChild(drift);
    }
    for (const tier of item.tiers) {
      const row = document.createElement('div');
      row.className = 'tier';
      const left = document.createElement('div');
      left.innerHTML = `<span class="tag">${escapeHtml(tier.tier)}</span> <span class="tag">${tier.wins ? 'wins' : (tier.shadowed ? 'shadowed' : 'seed')}</span> <span class="${tier.validation.valid ? 'ok' : 'bad'}">${tier.validation.valid ? 'valid' : 'invalid'}</span>`;
      const right = document.createElement('div');
      if (tier.editable) {
        const btn = document.createElement('button');
        btn.textContent = 'Edit';
        btn.onclick = () => loadFile(item.kind, tier.tier_key, item.name);
        right.appendChild(btn);
      }
      row.append(left, right);
      box.appendChild(row);
      const path = document.createElement('div');
      path.className = 'meta';
      path.textContent = tier.path;
      box.appendChild(path);
    }
    out.appendChild(box);
  }
}

async function loadFile(kind, tier, name) {
  try {
    const res = await api(`/api/file?kind=${kind}&tier=${tier}&name=${encodeURIComponent(name)}`);
    current = res.file;
    baseHash = res.file.hash;
    document.getElementById('current').textContent = `${kind} ${name} [${tier}] ${res.file.path}`;
    document.getElementById('editor').disabled = false;
    document.getElementById('save').disabled = false;
    document.getElementById('editor').value = res.file.content;
    document.getElementById('banner').innerHTML = res.file.shadowed_by ? `<div class="banner">Shadowed by ${escapeHtml(res.file.shadowed_by)}; saving this file is allowed but it will not take effect for the current project.</div>` : '';
    showMessages(res.file.validation);
  } catch (err) {
    showMessages(null, err.message);
  }
}

async function saveFile() {
  if (!current) return;
  try {
    const res = await api('/api/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kind: current.kind, tier: current.tier, name: current.name, base_hash: baseHash, content: document.getElementById('editor').value})
    });
    baseHash = res.hash;
    showMessages(res.validation, 'saved');
    await loadList();
  } catch (err) {
    if (err.status === 409) showMessages(null, 'file changed on disk — reload');
    else showMessages(err.data ? {errors: err.data.errors || [err.data.error], warnings: err.data.warnings || []} : null, err.message);
  }
}

async function createFile() {
  const kind = document.getElementById('createKind').value;
  const tier = document.getElementById('createTier').value;
  const mode = document.getElementById('createMode').value;
  const name = document.getElementById('createName').value.trim();
  try {
    const res = await api('/api/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kind, tier, mode, name})
    });
    await loadList();
    await loadFile(kind, tier, name);
    showMessages(res.file.validation, 'created');
  } catch (err) {
    showMessages(err.data ? {errors: err.data.errors || [err.data.error], warnings: err.data.warnings || []} : null, err.message);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

document.getElementById('reload').onclick = loadList;
document.getElementById('save').onclick = saveFile;
document.getElementById('create').onclick = createFile;
document.getElementById('done').onclick = async () => {
  try { await api('/api/quit', {method: 'POST'}); document.body.textContent = 'editor closed — rerun `fleet edit`'; }
  catch (err) { showMessages(null, err.message); }
};
loadList();
</script>
</body>
</html>
"""
