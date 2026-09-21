"""Tests for the zellij backend (``fleet.mux.zellij``).

Most tests drive :class:`ZellijMux` against an in-memory simulation of the
zellij CLI (``SimZellij``) that answers with output recorded from zellij
0.45.1 on Windows, so they run anywhere without zellij. The live tests at the
bottom use a real zellij and are opt-in via ``FLEET_LIVE_ZELLIJ=1`` (Windows
only in practice: creating a session needs a console there; the Linux CI job
runs ``tests.test_mux_zellij_live`` instead).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import unittest.mock
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from tests._fleet_test_helpers import requires_live_zellij  # noqa: E402  (also sets the hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)

from fleet import mux  # noqa: E402
from fleet.mux import Key, MuxError  # noqa: E402
from fleet.mux import zellij as zmod  # noqa: E402
from fleet.mux.zellij import (  # noqa: E402
    ZellijError,
    ZellijMux,
    parse_clients,
    parse_exited_sessions,
    parse_sessions,
    parse_version,
    terminal_panes,
    zellij_key,
)

# --- recorded outputs (zellij 0.45.1, Windows) -------------------------------

LIST_SESSIONS = (
    "fleet-p3probe [Created 0s ago] \n"
    "circular-apple [Created 2h 3m ago] (EXITED - attach to resurrect)\n"
    "fleet-main·x [Created 12s ago] (current)\n"
)

LIST_PANES = json.dumps(
    [
        {"id": 0, "is_plugin": True, "title": "(.) - zellij:link", "exited": False,
         "tab_id": 0, "tab_position": 0, "tab_name": "Tab #1"},
        {"id": 1, "is_plugin": True, "title": "tab-bar", "exited": False,
         "tab_id": 0, "tab_position": 0, "tab_name": "Tab #1"},
        {"id": 3, "is_plugin": True, "title": "About Zellij", "is_floating": True,
         "exited": False, "tab_id": 0, "tab_position": 0, "tab_name": "Tab #1"},
        {"id": 0, "is_plugin": False, "title": "C:\\WINDOWS\\system32\\cmd.exe",
         "exited": False, "tab_id": 0, "tab_position": 0, "tab_name": "Tab #1",
         "pane_command": "C:\\WINDOWS\\system32\\cmd.exe", "pane_cwd": "D:\\dev\\"},
        {"id": 4, "is_plugin": True, "title": "tab-bar", "exited": False,
         "tab_id": 1, "tab_position": 1, "tab_name": "7·driver"},
        {"id": 2, "is_plugin": False, "title": "claude", "exited": False,
         "tab_id": 1, "tab_position": 1, "tab_name": "7·driver"},
    ],
    indent=2,
)

LIST_TABS = json.dumps(
    [
        {"position": 1, "name": "7·driver", "active": False, "tab_id": 1},
        {"position": 0, "name": "leader", "active": True, "tab_id": 0},
    ]
)

LIST_CLIENTS_NONE = "CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n"
LIST_CLIENTS_ONE = (
    "CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n"
    "1         plugin_3       zellij:about   \n"
)


class ParserTests(unittest.TestCase):
    def test_parse_sessions_drops_exited(self) -> None:
        self.assertEqual(parse_sessions(LIST_SESSIONS), ["fleet-p3probe", "fleet-main·x"])
        self.assertEqual(parse_exited_sessions(LIST_SESSIONS), ["circular-apple"])

    def test_parse_sessions_none(self) -> None:
        self.assertEqual(parse_sessions("No active zellij sessions found.\n"), [])
        self.assertEqual(parse_sessions(""), [])

    def test_parse_clients_first_column_only(self) -> None:
        self.assertEqual(parse_clients(LIST_CLIENTS_NONE), [])
        self.assertEqual(parse_clients(LIST_CLIENTS_ONE), ["1"])

    def test_terminal_panes_skip_plugins(self) -> None:
        panes = json.loads(LIST_PANES)
        self.assertEqual([p["id"] for p in terminal_panes(panes, "Tab #1")], [0])
        self.assertEqual([p["id"] for p in terminal_panes(panes, "7·driver")], [2])
        self.assertEqual(terminal_panes(panes, "missing"), [])

    def test_parse_version(self) -> None:
        self.assertEqual(parse_version("zellij 0.45.1\n"), (0, 45, 1))
        self.assertIsNone(parse_version("garbage"))

    def test_key_translation(self) -> None:
        self.assertEqual(zellij_key("Enter"), "Enter")
        self.assertEqual(zellij_key("Escape"), "Esc")
        self.assertEqual(zellij_key(Key("Ctrl-u")), "Ctrl u")
        self.assertEqual(zellij_key("Alt-b"), "Alt b")
        self.assertEqual(zellij_key("Backspace"), "Backspace")
        with self.assertRaises(MuxError):
            zellij_key("C-u")


# --- simulation ---------------------------------------------------------------


class FakeProc:
    def __init__(self, sim: "SimZellij", argv: list[str], client_id: str | None = None) -> None:
        self.sim = sim
        self.argv = argv
        self.client_id = client_id
        self.killed = False
        self.returncode: int | None = 0 if client_id is None else None

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1
        self.sim.on_client_killed(self)

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0

    def poll(self) -> int | None:
        return self.returncode


class SimZellij(ZellijMux):
    """ZellijMux whose zellij CLI is an in-memory model of one server."""

    def __init__(self, tmp: Path, *, version: str = "zellij 0.45.1") -> None:
        super().__init__(binary="zellij", pane_env_dir=tmp / "pane-env")
        self._binary = "/opt/zellij/zellij"
        self._binary_resolved = True
        self.version_text = version
        self.clock = 0.0
        self.calls: list[list[str]] = []
        self.spawned: list[FakeProc] = []
        # name -> {"tabs": [{"id", "name", "terminal": bool}], "clients": [ids]}
        self.sessions: dict[str, dict] = {}
        self.exited: set[str] = set()
        self._next_tab = 0
        self._next_pane = 0
        self._next_client = 1
        # Knobs.
        self.client_attach_delay = 0.4  # seconds until a spawned client shows up
        self.client_never_attaches = False
        self.lose_tab_on_detach = 0  # how many new tabs vanish when the temp client goes
        self.not_found_rc = 0  # zellij's rc for "Session '…' not found"
        self._pending_clients: list[tuple[float, str, str]] = []
        self._fragile_tabs: set[int] = set()
        self.attach_calls: list[list[str]] = []

    # -- time -------------------------------------------------------------
    def _sleep(self, seconds: float) -> None:
        self.clock += seconds
        self._settle()

    def _now(self) -> float:
        return self.clock

    def _settle(self) -> None:
        for item in list(self._pending_clients):
            due, session, cid = item
            if self.clock >= due and session in self.sessions:
                self.sessions[session]["clients"].append(cid)
                self._pending_clients.remove(item)

    # -- model helpers -----------------------------------------------------
    def add_session(self, name: str, tabs: list[str] | None = None, clients: int = 0) -> None:
        self.sessions[name] = {"tabs": [], "clients": []}
        for t in tabs or ["Tab #1"]:
            self._add_tab(name, t, terminal=True)
        for _ in range(clients):
            self.sessions[name]["clients"].append(str(self._next_client))
            self._next_client += 1

    def _add_tab(self, session: str, name: str, *, terminal: bool) -> int:
        tab_id = self._next_tab
        self._next_tab += 1
        pane_id = self._next_pane
        self._next_pane += 1
        self.sessions[session]["tabs"].append(
            {"id": tab_id, "name": name, "terminal": terminal, "pane": pane_id}
        )
        return tab_id

    def tab_names(self, session: str) -> list[str]:
        return [t["name"] for t in self.sessions[session]["tabs"]]

    def on_client_killed(self, proc: FakeProc) -> None:
        for s in self.sessions.values():
            if proc.client_id in s["clients"]:
                s["clients"].remove(proc.client_id)
        self._pending_clients = [p for p in self._pending_clients if p[2] != proc.client_id]
        for s in self.sessions.values():
            for t in list(s["tabs"]):
                if t["id"] in self._fragile_tabs:
                    s["tabs"].remove(t)
                    self._fragile_tabs.discard(t["id"])

    # -- CLI -----------------------------------------------------------------
    def _run(self, argv, *, timeout=30):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        self._settle()
        args = argv[1:]

        def out(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(argv, rc, stdout, stderr)

        if args == ["--version"]:
            return out(self.version_text + "\n")
        if args[:2] == ["list-sessions", "-n"]:
            lines = [f"{n} [Created 1s ago] " for n in self.sessions]
            lines += [f"{n} [Created 1m ago] (EXITED - attach to resurrect)" for n in self.exited]
            if not lines:
                return out("", 1, "No active zellij sessions found.\n")
            return out("\n".join(lines) + "\n")
        if args[0] == "kill-session":
            if self.sessions.pop(args[1], None) is not None:
                self.exited.add(args[1])
            return out()
        if args[0] == "delete-session":
            name = args[-1]
            if name in self.exited:
                self.exited.discard(name)
                return out()
            if "--force" in args and self.sessions.pop(name, None) is not None:
                return out()
            return out("", 2, f'Session: "{name}" not found.\n')
        if args[0] == "-s" and args[2] == "action":
            return self._action_cmd(args[1], args[3:], out)
        raise AssertionError(f"unexpected zellij call: {argv}")

    def _action_cmd(self, session, a, out):
        if session not in self.sessions:
            others = "\n".join(self.sessions)
            return out(
                f"Session '{session}' not found. The following sessions are active:\n{others}\n",
                self.not_found_rc,
            )
        s = self.sessions[session]
        cmd = a[0]
        if cmd == "list-clients":
            lines = ["CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND"]
            lines += [f"{c}         plugin_3       zellij:about   " for c in s["clients"]]
            return out("\n".join(lines) + "\n")
        if cmd == "list-tabs":
            return out(json.dumps([
                {"position": i, "name": t["name"], "tab_id": t["id"]}
                for i, t in enumerate(s["tabs"])
            ]))
        if cmd == "list-panes":
            panes = []
            for i, t in enumerate(s["tabs"]):
                panes.append({"id": 100 + t["id"], "is_plugin": True, "tab_id": t["id"],
                              "tab_position": i, "tab_name": t["name"]})
                if t["terminal"]:
                    panes.append({"id": t["pane"], "is_plugin": False, "tab_id": t["id"],
                                  "tab_position": i, "tab_name": t["name"]})
            return out(json.dumps(panes))
        if cmd == "new-tab":
            name = a[a.index("--name") + 1]
            has_client = bool(s["clients"])
            tab_id = self._add_tab(session, name, terminal=has_client)
            if has_client and self.lose_tab_on_detach > 0:
                self.lose_tab_on_detach -= 1
                self._fragile_tabs.add(tab_id)
            return out(f"{tab_id}\n")
        if cmd == "rename-tab-by-id":
            for t in s["tabs"]:
                if str(t["id"]) == a[1]:
                    t["name"] = a[2]
            return out()
        if cmd == "close-tab-by-id":
            s["tabs"] = [t for t in s["tabs"] if str(t["id"]) != a[1]]
            return out()
        if cmd in ("write-chars", "paste", "send-keys", "go-to-tab-name"):
            return out()
        if cmd == "dump-screen":
            return out("❯ \n")
        raise AssertionError(f"unexpected action: {a}")

    def _spawn_client(self, argv, *, cwd=None, tty=False):
        argv = [str(a) for a in argv]
        self.calls.append(["SPAWN", *argv])
        if argv[1:3] == ["attach", "-b"]:
            session = argv[3]
            self.add_session(session)
            proc = FakeProc(self, argv)
        else:
            session = argv[2]
            cid = str(self._next_client)
            self._next_client += 1
            if not self.client_never_attaches:
                self._pending_clients.append((self.clock + self.client_attach_delay, session, cid))
            proc = FakeProc(self, argv, client_id=cid)
        self.spawned.append(proc)
        return proc

    def _call_attach(self, argv):
        self.attach_calls.append(list(argv))
        return 0

    # -- assertions helpers ----------------------------------------------------
    def actions(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if len(c) > 4 and c[3] == "action" and c[4] == name]

    def live_temp_clients(self) -> list[FakeProc]:
        return [p for p in self.spawned if p.client_id is not None and not p.killed]


class ZellijSimTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.z = SimZellij(self.tmp)
        env = {k: v for k, v in os.environ.items()
               if k not in ("FLEET_NO_MUX", "FLEET_NO_TMUX", "FLEET_ZELLIJ_TEMP_CLIENT")}
        self._env = unittest.mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()


class SessionTests(ZellijSimTestCase):
    def test_session_exists_ignores_exited(self) -> None:
        self.z.add_session("fleet-a")
        self.z.exited.add("fleet-dead")
        self.assertTrue(self.z.session_exists("fleet-a"))
        self.assertFalse(self.z.session_exists("fleet-dead"))
        self.assertFalse(self.z.session_exists("fleet-none"))

    def test_session_exists_with_no_sessions(self) -> None:
        self.assertFalse(self.z.session_exists("fleet-a"))

    def test_new_session_runs_launcher_and_names_first_tab(self) -> None:
        self.z.new_session(
            "fleet-p", window="leader", argv=["claude", "--model", "opus"],
            cwd=str(self.tmp), env={"FLEET_SESSION": "p"},
        )
        spawn = [c for c in self.z.calls if c[0] == "SPAWN"][0]
        self.assertEqual(spawn[1:5], ["/opt/zellij/zellij", "attach", "-b", "fleet-p"])
        cmd = spawn[spawn.index("--") + 1:]
        self.assertIn("pane_launch.py", cmd[3])
        self.assertEqual(cmd[-3:], ["claude", "--model", "opus"])
        env_file = Path(cmd[cmd.index("--env-file") + 1])
        data = json.loads(env_file.read_text(encoding="utf-8"))
        self.assertEqual(data["env"], {"FLEET_SESSION": "p"})
        self.assertEqual(data["cwd"], str(self.tmp))
        self.assertEqual(self.z.tab_names("fleet-p"), ["leader"])
        self.assertEqual(self.z.list_windows("fleet-p"), ["leader"])

    def test_new_session_deletes_resurrectable_entry_first(self) -> None:
        self.z.exited.add("fleet-p")
        self.z.new_session("fleet-p", window="leader")
        deletes = [c for c in self.z.calls if c[1:2] == ["delete-session"]]
        self.assertEqual(deletes[0][-1], "fleet-p")
        spawn_idx = next(i for i, c in enumerate(self.z.calls) if c[0] == "SPAWN")
        self.assertLess(self.z.calls.index(deletes[0]), spawn_idx)

    def test_new_session_duplicate_raises(self) -> None:
        self.z.add_session("fleet-p")
        with self.assertRaises(ZellijError):
            self.z.new_session("fleet-p", window="leader")

    def test_new_session_times_out_when_server_never_lists(self) -> None:
        def spawn(argv, *, cwd=None):
            return FakeProc(self.z, list(argv))

        self.z._spawn_client = spawn  # server never appears
        with self.assertRaises(ZellijError):
            self.z.new_session("fleet-p", window="leader")

    def test_kill_session_kills_and_deletes(self) -> None:
        self.z.add_session("fleet-p")
        (self.z.pane_env_dir() / "fleet-p").mkdir(parents=True)
        self.z.kill_session("fleet-p")
        self.assertFalse(self.z.session_exists("fleet-p"))
        self.assertNotIn("fleet-p", self.z.exited)
        self.assertFalse((self.z.pane_env_dir() / "fleet-p").exists())
        verbs = [c[1] for c in self.z.calls if c[1] in ("kill-session", "delete-session")]
        self.assertEqual(verbs, ["kill-session", "delete-session"])

    def test_kill_missing_session_raises(self) -> None:
        with self.assertRaises(ZellijError):
            self.z.kill_session("fleet-none")

    def test_list_windows_missing_session_raises(self) -> None:
        with self.assertRaises(MuxError):
            self.z.list_windows("fleet-none")


class ErrorDetectionTests(ZellijSimTestCase):
    def test_not_found_with_rc0_is_an_error(self) -> None:
        self.z.not_found_rc = 0
        with self.assertRaises(ZellijError) as cm:
            self.z._action("fleet-none", "list-clients")
        self.assertIn("not found", str(cm.exception))

    def test_not_found_with_rc1_is_an_error(self) -> None:
        self.z.not_found_rc = 1
        with self.assertRaises(ZellijError):
            self.z._action("fleet-none", "list-tabs", "-j")

    def test_nonzero_rc_is_an_error(self) -> None:
        self.z.add_session("fleet-p")
        with unittest.mock.patch.object(
            self.z, "_run",
            return_value=subprocess.CompletedProcess([], 2, "", "Invalid key"),
        ):
            with self.assertRaises(ZellijError):
                self.z._action("fleet-p", "send-keys", "Bogus")

    def test_missing_tab_is_an_error_even_though_zellij_would_exit_0(self) -> None:
        self.z.add_session("fleet-p", ["leader"])
        with self.assertRaises(ZellijError):
            self.z.send_text("fleet-p", "nope", "hi")
        with self.assertRaises(ZellijError):
            self.z.capture("fleet-p", "nope")
        with self.assertRaises(ZellijError):
            self.z.kill_window("fleet-p", "nope")
        self.assertEqual(self.z.actions("write-chars"), [])

    def test_missing_session_for_io(self) -> None:
        with self.assertRaises(MuxError):
            self.z.paste("fleet-none", "leader", "x")


class NewWindowTests(ZellijSimTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.z.add_session("fleet-p", ["leader"])

    def test_temp_client_when_no_client_attached(self) -> None:
        self.z.new_window(
            "fleet-p", "7·driver", argv=["claude"], cwd=str(self.tmp),
            env={"FLEET_TASK_ID": "7"},
        )
        self.assertEqual(self.z.tab_names("fleet-p"), ["leader", "7·driver"])
        temp = [p for p in self.z.spawned if p.client_id is not None]
        self.assertEqual(len(temp), 1)
        self.assertEqual(temp[0].argv[1:], ["attach", "fleet-p"])
        self.assertTrue(temp[0].killed)
        self.assertEqual(self.z.sessions["fleet-p"]["clients"], [])
        new_tab = self.z.actions("new-tab")[0]
        self.assertIn("--no-focus", new_tab)
        self.assertEqual(new_tab[new_tab.index("--name") + 1], "7·driver")
        self.assertEqual(new_tab[new_tab.index("--cwd") + 1], str(self.tmp))
        # new-tab ran while the temp client was attached (after it appeared).
        spawn_idx = next(i for i, c in enumerate(self.z.calls) if c[0] == "SPAWN")
        self.assertGreater(self.z.calls.index(new_tab), spawn_idx)

    def test_no_temp_client_when_a_client_is_attached(self) -> None:
        self.z.sessions["fleet-p"]["clients"].append("9")
        self.z.new_window("fleet-p", "7·driver", argv=["claude"])
        self.assertEqual(self.z.spawned, [])
        self.assertIn("7·driver", self.z.tab_names("fleet-p"))

    def test_no_temp_client_on_fixed_version(self) -> None:
        self.z._version_probed = False
        self.z.version_text = "zellij 0.46.0"
        # The simulated bug would lose the pane without a client; model the fix.
        self.z.sessions["fleet-p"]["clients"] = []
        orig = self.z._action_cmd

        def fixed(session, a, out):
            if a[0] == "new-tab":
                name = a[a.index("--name") + 1]
                tab_id = self.z._add_tab(session, name, terminal=True)
                return out(f"{tab_id}\n")
            return orig(session, a, out)

        self.z._action_cmd = fixed
        self.z.new_window("fleet-p", "7·driver", argv=["claude"])
        self.assertEqual(self.z.spawned, [])
        self.assertFalse(self.z.needs_temp_client())

    def test_env_override_forces_temp_client_off(self) -> None:
        os.environ["FLEET_ZELLIJ_TEMP_CLIENT"] = "0"
        self.assertFalse(self.z.needs_temp_client())
        os.environ["FLEET_ZELLIJ_TEMP_CLIENT"] = "1"
        self.z.version_text = "zellij 9.0.0"
        self.assertTrue(self.z.needs_temp_client())

    def test_retry_once_when_tab_lost_at_detach(self) -> None:
        self.z.lose_tab_on_detach = 1
        self.z.new_window("fleet-p", "7·driver", argv=["claude"])
        self.assertEqual(self.z.tab_names("fleet-p").count("7·driver"), 1)
        self.assertEqual(len(self.z.actions("new-tab")), 2)
        temp = [p for p in self.z.spawned if p.client_id is not None]
        self.assertEqual(len(temp), 2)
        self.assertEqual(self.z.live_temp_clients(), [])
        self.assertEqual(self.z.sessions["fleet-p"]["clients"], [])

    def test_gives_up_after_two_losses_and_leaves_no_client(self) -> None:
        self.z.lose_tab_on_detach = 2
        with self.assertRaises(ZellijError):
            self.z.new_window("fleet-p", "7·driver", argv=["claude"])
        self.assertEqual(len(self.z.actions("new-tab")), 2)
        self.assertEqual(self.z.live_temp_clients(), [])
        self.assertNotIn("7·driver", self.z.tab_names("fleet-p"))

    def test_temp_client_never_attaches(self) -> None:
        self.z.client_never_attaches = True
        with self.assertRaises(ZellijError) as cm:
            self.z.new_window("fleet-p", "7·driver", argv=["claude"])
        self.assertIn("did not attach", str(cm.exception))
        self.assertEqual(self.z.actions("new-tab"), [])
        self.assertEqual(self.z.live_temp_clients(), [])

    def test_temp_client_killed_when_new_tab_raises(self) -> None:
        orig = self.z._action_cmd

        def boom(session, a, out):
            if a[0] == "new-tab":
                return out("", 2, "error: boom")
            return orig(session, a, out)

        self.z._action_cmd = boom
        with self.assertRaises(ZellijError):
            self.z.new_window("fleet-p", "7·driver", argv=["claude"])
        self.assertEqual(self.z.live_temp_clients(), [])

    def test_existing_same_name_tab_does_not_count_as_created(self) -> None:
        self.z._add_tab("fleet-p", "7·driver", terminal=True)
        self.z.sessions["fleet-p"]["clients"].append("5")
        orig = self.z._action_cmd

        def noop(session, a, out):
            if a[0] == "new-tab":
                return out("42\n")  # claims success, creates nothing
            return orig(session, a, out)

        self.z._action_cmd = noop
        with self.assertRaises(ZellijError):
            self.z.new_window("fleet-p", "7·driver", argv=["claude"])

    def test_missing_session_raises(self) -> None:
        with self.assertRaises(ZellijError):
            self.z.new_window("fleet-none", "7·driver", argv=["claude"])


class IoTests(ZellijSimTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.z.add_session("fleet-p", ["leader", "7·driver"])
        self.pane = f"terminal_{self.z.sessions['fleet-p']['tabs'][1]['pane']}"

    def test_send_text_with_enter(self) -> None:
        self.z.send_text("fleet-p", "7·driver", "-hello")
        wc = self.z.actions("write-chars")[0]
        self.assertEqual(wc[5:], ["-p", self.pane, "--", "-hello"])
        sk = self.z.actions("send-keys")[0]
        self.assertEqual(sk[5:], ["-p", self.pane, "Enter"])
        self.assertLess(self.z.calls.index(wc), self.z.calls.index(sk))

    def test_send_text_without_enter_and_enter_only(self) -> None:
        self.z.send_text("fleet-p", "7·driver", "x", enter=False)
        self.assertEqual(self.z.actions("send-keys"), [])
        self.z.send_text("fleet-p", "7·driver", "", enter=True)
        self.assertEqual(len(self.z.actions("write-chars")), 1)
        self.assertEqual(len(self.z.actions("send-keys")), 1)

    def test_send_key_translates(self) -> None:
        self.z.send_key("fleet-p", "7·driver", Key("Ctrl-u"))
        self.z.send_key("fleet-p", "7·driver", "Escape")
        keys = [c[-1] for c in self.z.actions("send-keys")]
        self.assertEqual(keys, ["Ctrl u", "Esc"])

    def test_paste_and_capture(self) -> None:
        self.z.paste("fleet-p", "7·driver", "pointer text")
        self.assertEqual(self.z.actions("paste")[0][5:], ["-p", self.pane, "--", "pointer text"])
        self.assertEqual(self.z.capture("fleet-p", "7·driver"), "❯ \n")
        self.assertEqual(self.z.actions("dump-screen")[0][5:], ["-p", self.pane])

    def test_kill_window_resolves_tab_id_by_name(self) -> None:
        tab_id = self.z.sessions["fleet-p"]["tabs"][1]["id"]
        self.z.kill_window("fleet-p", "7·driver")
        self.assertEqual(self.z.actions("close-tab-by-id")[0][-1], str(tab_id))
        self.assertEqual(self.z.list_windows("fleet-p"), ["leader"])

    def test_kill_window_waits_for_pane_processes(self) -> None:
        from fleet import pane_launch

        env_path = self.z._env_path("fleet-p", "7·driver")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("{}", encoding="utf-8")
        pane_launch.write_pid_file(pane_launch.pid_file_for(env_path), launcher_pid=11, agent_pid=22)
        checks: list[int] = []
        alive_until = {11: 0.5, 22: 1.0}  # fake-clock seconds until each exits

        def alive(pid: int) -> bool:
            checks.append(pid)
            return self.z.clock < alive_until[pid]

        self.z._pid_alive = alive
        self.z.kill_window("fleet-p", "7·driver")
        self.assertGreaterEqual(self.z.clock, 1.0)
        self.assertIn(22, checks)
        self.assertFalse(env_path.exists())
        self.assertFalse(pane_launch.pid_file_for(env_path).exists())

    def test_task_helpers_work_on_tabs(self) -> None:
        self.assertEqual(mux.task_window_names("fleet-p", "7", backend=self.z), ["7·driver"])
        mux.kill_task_windows("fleet-p", "7", backend=self.z)
        self.assertEqual(self.z.list_windows("fleet-p"), ["leader"])

    def test_preload_paste_is_none(self) -> None:
        self.assertIsNone(self.z.preload_paste("buf", "x"))


class AttachTests(ZellijSimTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.z.add_session("fleet-p", ["leader", "7·driver"])

    def test_sole_client_gets_focus_helper(self) -> None:
        with unittest.mock.patch.object(self.z, "start_focus_helper") as helper:
            rc = self.z.attach("fleet-p", "7·driver")
        self.assertEqual(rc, 0)
        helper.assert_called_once_with("fleet-p", "7·driver")
        self.assertEqual(self.z.attach_calls, [["/opt/zellij/zellij", "attach", "fleet-p"]])

    def test_other_client_attached_prints_position_and_never_moves_focus(self) -> None:
        self.z.sessions["fleet-p"]["clients"].append("1")
        err = io.StringIO()
        with unittest.mock.patch.object(self.z, "start_focus_helper") as helper, redirect_stderr(err):
            self.z.attach("fleet-p", "7·driver")
        helper.assert_not_called()
        self.assertIn("tab 2", err.getvalue())
        self.assertIn("Ctrl t", err.getvalue())
        self.assertEqual(self.z.actions("go-to-tab-name"), [])

    def _attach_with_other_client(self, stdin: io.StringIO, *, stdin_tty: bool, stderr_tty: bool):
        class _Stream(io.StringIO):
            def __init__(self, initial: str = "", tty: bool = False) -> None:
                super().__init__(initial)
                self._tty = tty

            def isatty(self) -> bool:
                return self._tty

        self.z.sessions["fleet-p"]["clients"].append("1")
        fake_in = _Stream(stdin.getvalue(), stdin_tty)
        fake_err = _Stream("", stderr_tty)
        with unittest.mock.patch.object(self.z, "start_focus_helper") as helper, \
                unittest.mock.patch.object(sys, "stdin", fake_in), \
                unittest.mock.patch.object(sys, "stderr", fake_err):
            rc = self.z.attach("fleet-p", "7·driver")
        return rc, fake_in, fake_err, helper

    def test_other_client_on_tty_waits_for_enter_before_attaching(self) -> None:
        rc, fake_in, fake_err, helper = self._attach_with_other_client(
            io.StringIO("\n"), stdin_tty=True, stderr_tty=True
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake_in.read(), "")  # the Enter was consumed
        self.assertIn("Ctrl t", fake_err.getvalue())
        self.assertIn("press Enter to attach", fake_err.getvalue())
        helper.assert_not_called()
        self.assertEqual(len(self.z.attach_calls), 1)

    def test_other_client_on_tty_with_eof_stdin_still_attaches(self) -> None:
        rc, _, fake_err, _ = self._attach_with_other_client(
            io.StringIO(""), stdin_tty=True, stderr_tty=True
        )
        self.assertEqual(rc, 0)
        self.assertIn("press Enter to attach", fake_err.getvalue())
        self.assertEqual(len(self.z.attach_calls), 1)

    def test_other_client_without_tty_never_waits(self) -> None:
        for stdin_tty, stderr_tty in ((False, False), (True, False), (False, True)):
            with self.subTest(stdin_tty=stdin_tty, stderr_tty=stderr_tty):
                self.z.attach_calls.clear()
                self.z.sessions["fleet-p"]["clients"] = []
                rc, fake_in, fake_err, _ = self._attach_with_other_client(
                    io.StringIO("unread\n"), stdin_tty=stdin_tty, stderr_tty=stderr_tty
                )
                self.assertEqual(rc, 0)
                self.assertEqual(fake_in.read(), "unread\n")  # stdin untouched
                self.assertIn("Ctrl t", fake_err.getvalue())
                self.assertNotIn("press Enter", fake_err.getvalue())
                self.assertEqual(len(self.z.attach_calls), 1)

    def test_sole_client_on_tty_does_not_prompt(self) -> None:
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        fake_in, fake_err = _Tty("x\n"), _Tty()
        with unittest.mock.patch.object(self.z, "start_focus_helper") as helper, \
                unittest.mock.patch.object(sys, "stdin", fake_in), \
                unittest.mock.patch.object(sys, "stderr", fake_err):
            self.z.attach("fleet-p", "7·driver")
        helper.assert_called_once_with("fleet-p", "7·driver")
        self.assertEqual(fake_in.read(), "x\n")
        self.assertEqual(fake_err.getvalue(), "")

    def test_focus_helper_waits_for_client_then_goes_to_tab(self) -> None:
        self.z._pending_clients.append((0.3, "fleet-p", "1"))
        t = self.z.start_focus_helper("fleet-p", "7·driver", timeout=5)
        t.join(5)
        self.assertEqual(self.z.actions("go-to-tab-name")[0][-1], "7·driver")

    def test_focus_helper_gives_up_without_client(self) -> None:
        t = self.z.start_focus_helper("fleet-p", "7·driver", timeout=1)
        t.join(5)
        self.assertEqual(self.z.actions("go-to-tab-name"), [])

    def test_attach_without_window(self) -> None:
        self.z.attach("fleet-p")
        self.assertEqual(self.z.actions("go-to-tab-name"), [])
        self.assertEqual(len(self.z.attach_calls), 1)

    def test_attach_missing_session(self) -> None:
        with self.assertRaises(ZellijError):
            self.z.attach("fleet-none")
        self.assertEqual(self.z.attach_calls, [])

    def test_hints(self) -> None:
        self.assertEqual(self.z.attach_hint("fleet-p"), "zellij attach fleet-p")
        self.assertIn("7·driver", self.z.attach_hint("fleet-p", "7·driver"))
        self.assertIn("delete-session", self.z.kill_session_hint("fleet-p"))


class BinaryTests(unittest.TestCase):
    def test_available_false_when_disabled(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"FLEET_NO_MUX": "1"}):
            self.assertFalse(ZellijMux(binary=sys.executable).available())

    def test_available_false_when_missing(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("FLEET_NO_MUX", "FLEET_NO_TMUX")}
        with unittest.mock.patch.dict(os.environ, env, clear=True), unittest.mock.patch(
            "fleet.mux.zellij.shutil.which", return_value=None
        ):
            m = ZellijMux(binary="definitely-not-zellij-xyz")
            self.assertFalse(m.available())
            self.assertIsNone(m.binary)
            self.assertFalse(m.session_exists("fleet-x"))
            with self.assertRaises(ZellijError):
                m.new_session("fleet-x")

    def test_fleet_zellij_env_override_resolved_once(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"FLEET_ZELLIJ": sys.executable}):
            with unittest.mock.patch(
                "fleet.mux.zellij.shutil.which", wraps=shutil.which
            ) as which:
                m = ZellijMux()
                first = m.binary
                second = m.binary
        self.assertEqual(first, os.path.abspath(shutil.which(sys.executable) or sys.executable))
        self.assertEqual(first, second)
        self.assertEqual(which.call_count, 1)

    def test_version_parsed_and_cached(self) -> None:
        m = ZellijMux(binary=sys.executable)
        with unittest.mock.patch.object(
            m, "_run", return_value=subprocess.CompletedProcess([], 0, "zellij 0.45.1\n", "")
        ) as run:
            self.assertEqual(m.version, (0, 45, 1))
            self.assertEqual(m.version, (0, 45, 1))
        self.assertEqual(run.call_count, 1)
        self.assertTrue(m.needs_temp_client())


class SpawnFlagsTests(unittest.TestCase):
    def test_windows_hidden_new_console_without_redirection(self) -> None:
        m = ZellijMux(binary=sys.executable)

        class SI:
            def __init__(self) -> None:
                self.dwFlags = 0
                self.wShowWindow = 1

        calls = []

        def popen(args, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise OSError("breakaway not allowed")
            return "proc"

        with unittest.mock.patch.object(zmod, "_is_windows", return_value=True), \
                unittest.mock.patch.object(zmod.subprocess, "STARTUPINFO", SI, create=True), \
                unittest.mock.patch.object(zmod.subprocess, "Popen", side_effect=popen):
            self.assertEqual(m._spawn_client(["zellij", "attach", "-b", "s"]), "proc")
        first, second = calls
        self.assertTrue(first["creationflags"] & zmod.CREATE_NEW_CONSOLE)
        self.assertTrue(first["creationflags"] & zmod.CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(second["creationflags"] & zmod.CREATE_BREAKAWAY_FROM_JOB)
        for kw in calls:
            self.assertEqual(kw["startupinfo"].wShowWindow, zmod.SW_HIDE)
            for std in ("stdin", "stdout", "stderr"):
                self.assertNotIn(std, kw)

    def test_spawned_client_env_drops_zellij_pane_markers(self) -> None:
        # A fleet command run from a pane of S (leader ``start``, driver
        # ``done``) inherits ZELLIJ_SESSION_NAME=S; ``zellij attach S`` then
        # panics ("attach to the current session … not supported").
        m = ZellijMux(binary=sys.executable)
        pane_env = {
            "ZELLIJ": "0",
            "ZELLIJ_SESSION_NAME": "fleet-main",
            "ZELLIJ_PANE_ID": "3",
            "FLEET_TASK_ID": "t1",
            "PATH": os.environ.get("PATH", ""),
        }
        for windows in (True, False):
            with self.subTest(windows=windows), \
                    unittest.mock.patch.dict(os.environ, pane_env, clear=True), \
                    unittest.mock.patch.object(zmod, "_is_windows", return_value=windows), \
                    unittest.mock.patch.object(zmod.subprocess, "STARTUPINFO", create=True), \
                    unittest.mock.patch.object(zmod.subprocess, "Popen", return_value="proc") as popen:
                m._spawn_client(["zellij", "attach", "fleet-main"])
                env = popen.call_args.kwargs["env"]
                for var in zmod.ZELLIJ_PANE_VARS:
                    self.assertNotIn(var, env)
                self.assertEqual(env["FLEET_TASK_ID"], "t1")

    def test_client_env_is_case_insensitive(self) -> None:
        env = zmod.client_env({"zellij_session_name": "s", "Zellij": "0", "KEEP": "1"})
        self.assertEqual(env, {"KEEP": "1"})

    def test_window_close_kills_caller_only_on_windows(self) -> None:
        m = ZellijMux(binary=sys.executable)
        with unittest.mock.patch.object(zmod, "_is_windows", return_value=True):
            self.assertTrue(m.window_close_kills_caller)
        with unittest.mock.patch.object(zmod, "_is_windows", return_value=False):
            self.assertFalse(m.window_close_kills_caller)

    def test_run_uses_utf8_and_no_window_on_windows(self) -> None:
        m = ZellijMux(binary=sys.executable)
        with unittest.mock.patch.object(zmod, "_is_windows", return_value=True), \
                unittest.mock.patch.object(zmod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            m._run(["zellij", "list-sessions", "-n"])
        kw = run.call_args.kwargs
        self.assertEqual(kw["encoding"], "utf-8")
        self.assertEqual(kw["errors"], "replace")
        self.assertEqual(kw["creationflags"], zmod.CREATE_NO_WINDOW)


# --- live ----------------------------------------------------------------------

LIVE_SESSION = "fleet-ztest-" + os.urandom(3).hex()


@requires_live_zellij
class ZellijLiveTests(unittest.TestCase):
    """Real zellij: session, detached tab (temp client), I/O, kill."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.m = ZellijMux(pane_env_dir=cls.tmp / "pane-env")
        py = sys.executable
        cls.m.new_session(
            LIVE_SESSION, window="leader",
            argv=[py, "-c", "import time; print('LEADER-UP', flush=True); time.sleep(600)"],
            cwd=str(cls.tmp), env={"FLEET_LIVE_MARK": "leader"},
        )

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            if cls.m.session_exists(LIVE_SESSION):
                cls.m.kill_session(LIVE_SESSION)
        finally:
            cls._tmp.cleanup()

    def _wait_capture(self, window: str, needle: str, timeout: float = 20) -> str:
        deadline = time.monotonic() + timeout
        screen = ""
        while time.monotonic() < deadline:
            screen = self.m.capture(LIVE_SESSION, window)
            if needle in screen:
                return screen
            time.sleep(0.3)
        self.fail(f"{needle!r} not in {window} screen:\n{screen}")

    def test_1_leader_tab(self) -> None:
        self.assertTrue(self.m.session_exists(LIVE_SESSION))
        self.assertIn("leader", self.m.list_windows(LIVE_SESSION))
        self._wait_capture("leader", "LEADER-UP")

    def test_2_detached_tab_env_and_input(self) -> None:
        self.assertEqual(self.m._clients(LIVE_SESSION), [])
        code = (
            "import os,sys; print('MARK=' + os.environ.get('FLEET_TASK_ID','-'), "
            "'CC=' + os.environ.get('CLAUDECODE','-'), flush=True); "
            "line = sys.stdin.readline(); print('GOT=' + line.strip(), flush=True); "
            "import time; time.sleep(600)"
        )
        self.m.new_window(
            LIVE_SESSION, "9·driver", argv=[sys.executable, "-c", code],
            cwd=str(self.tmp), env={"FLEET_TASK_ID": "9"},
        )
        self.assertIn("9·driver", self.m.list_windows(LIVE_SESSION))
        self.assertEqual(self.m._clients(LIVE_SESSION), [])  # temp client gone
        self._wait_capture("9·driver", "MARK=9 CC=-")
        self.m.send_text(LIVE_SESSION, "9·driver", "héllo·ok")
        self._wait_capture("9·driver", "GOT=héllo·ok")
        from fleet import pane_launch

        pids = pane_launch.read_pid_file(
            pane_launch.pid_file_for(self.m._env_path(LIVE_SESSION, "9·driver"))
        )
        self.assertEqual(len(pids), 2)
        self.m.kill_window(LIVE_SESSION, "9·driver")
        self.assertNotIn("9·driver", self.m.list_windows(LIVE_SESSION))
        # kill_window returns only once the pane's processes are gone.
        self.assertFalse(any(pane_launch.pid_alive(p) for p in pids))


if __name__ == "__main__":
    unittest.main()
