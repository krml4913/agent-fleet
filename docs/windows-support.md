# Windows Support Plan (zellij backend)

> **Status: plan — not implemented.** Records the feasibility investigation
> and the implementation plan for running fleet natively on Windows, using
> [zellij](https://zellij.dev/) in place of tmux as the terminal multiplexer.
>
> Investigated 2026-09-19 on Windows 10 Pro 19045, Python 3.13, zellij 0.45.1
> (native Windows build), claude CLI (native). codex was not installed and is
> not yet verified. Every zellij behavior marked "verified" below was observed
> on that machine, not taken from documentation. Phase 0 (§7) re-ran the open
> items on the same machine the same day; its results are folded in below.
>
> When the plan lands, update `docs/design.md` (§3, §8.6, §11.2, §11.5 still
> say "tmux") and turn this doc into a record of the result or delete it.

---

## 1. Goal and scope

**Goal:** `fleet` / `fleet-agent` run natively on Windows (no WSL). Leader and
driver panes live in zellij sessions, and every fleet feature works: leader
launch, driver spawn, prompt delivery, inbox wake-up, leader notification,
stage handoff, verify gate, cleanup / merge, and attach.

**In scope**

- A multiplexer abstraction with two backends: tmux (existing behavior) and
  zellij (new).
- The Windows-compatibility fixes outside the multiplexer that fleet needs just
  to start on Windows (§5).

**Out of scope**

- Changing the default on macOS / Linux. tmux stays the default there. The
  zellij backend may work on POSIX too, but that is not a goal of this plan.
- WSL2. fleet already runs unchanged under WSL2 + tmux. This plan is about
  native Windows.

---

## 2. Feasibility verdict

**Feasible.** Every tmux primitive fleet uses has a zellij 0.45.1 counterpart
(§3), with three caveats:

1. **Detached `new-tab` bug in zellij 0.45.x**
   ([#5594](https://github.com/zellij-org/zellij/issues/5594)). A tab created
   while no client is attached gets a 0×0 viewport, has no pane, and is thrown
   away when a client attaches. The fix
   ([#5612](https://github.com/zellij-org/zellij/pull/5612)) was still open on
   2026-09-19. **Workaround (verified):** attach a hidden temporary client
   while creating the tab, then kill it. The tab survives the detach and later
   re-attaches.
2. **No per-pane environment.** zellij has no equivalent of
   `tmux new-window -e`. Panes inherit the zellij *server's* environment, not
   the calling client's (verified). A small launcher that sets
   `FLEET_TASK_ID` / `FLEET_STATE_DIR` and then runs the agent CLI solves this
   (verified via an initial command).
3. **`fleet attach <task>` degrades.** zellij already gives each client its own
   focus, so the tmux grouped-view-session trick (Issue #76) is unnecessary.
   But `go-to-tab-name` run from outside zellij only moves the *first*
   connected client. There is no way to point a newly attached client at a
   given tab (verified). So attach can land on the driver's tab only when the
   attaching client is the only one. That case works (verified in Phase 0).

The larger share of the work is **outside zellij**. Today fleet does not even
start on Windows: `python fleet --help` fails with
`ModuleNotFoundError: No module named 'fcntl'` (§5).

---

## 3. tmux → zellij mapping

Only the primitives fleet actually uses (from `src/fleet/tmux.py` plus the raw
`tmux` calls in `commands/attach.py` / `commands/leader.py`).

| fleet use | tmux | zellij 0.45.1 | Verified |
|---|---|---|---|
| session liveness | `has-session -t S` | `list-sessions -n`, then drop `(EXITED - attach to resurrect)` entries | ✅ |
| create detached session | `new-session -d -s S -n W -c DIR -e K=V` | `attach -b S [-- <launcher argv>]` (Windows needs a console, §4.2) | ✅ |
| open driver window | `new-window -d -t S -n W -c DIR -e K=V` | `-s S action new-tab --name W --cwd DIR --no-focus -- <launcher argv>`; prints the new tab id. `--no-focus` needs zellij ≥ 0.45.0 (§6.6) | ✅ incl. no focus change for an attached client; ⚠ needs the #5594 workaround (§4.3) |
| per-window env | `-e K=V` | not supported; set by the pane launcher (§6.3) | ✅ |
| list windows | `list-windows -F '#{window_name}'` | `action list-tabs -j` / `action list-panes -a -j` | ✅ |
| kill window | `kill-window -t S:W` | look up the tab id by name, then `action close-tab-by-id ID` (also kills the agent process) | ✅ |
| kill session | `kill-session -t S` | `kill-session S`, then `delete-session S` (drops the resurrectable entry) | ✅ |
| type text | `send-keys -t S:W TEXT` | `action write-chars -p terminal_N TEXT` | ✅ incl. Japanese and `·` |
| press keys | `send-keys -t S:W Enter` / `C-u` | `action send-keys -p terminal_N "Enter"` / `"Ctrl u"` | ✅ Enter; ✅ `Ctrl u` clears claude's composer (cmd.exe has no line-kill key and just echoes `^U`) |
| paste pointer | `load-buffer` + `paste-buffer` | `action paste -p terminal_N TEXT` (bracketed paste; no buffer needed) | ✅ in cmd.exe and into claude (paste, 0.25 s, `Enter` submits) |
| capture pane | `capture-pane -p -J -S -200 -t S:W` | `action dump-screen -p terminal_N` (viewport; `-f` for full scrollback) | ✅ incl. a non-focused tab and claude's TUI |
| attach | `attach -t S` (`execvp`) | `attach S` (as a subprocess; `execvp` is not a real exec on Windows) | ✅ |
| attach to a task window without disturbing other clients | grouped view session (Issue #76) | per-client focus is built in; `go-to-tab-name` steers the new client only when it is the sole client | ⚠ degraded when other clients are attached (§6.5) |

Pane addressing: tmux targets `S:W` by name. zellij's `-p` takes a pane id.
The backend resolves `(session, tab name) → terminal_<id>` at call time from
`action list-panes -a -j` (fields: `id`, `is_plugin`, `tab_name`, `tab_id`,
`exited`, `pane_command`, `pane_cwd`).

---

## 4. zellij behaviors the backend must handle (verified)

### 4.1 `action` exits 0 even on failure

`zellij -s <missing> action …` prints
`Session '<missing>' not found. The following sessions are active: …` and
exits **0**. With a missing pane id, `write-chars` is a silent no-op,
`dump-screen` prints nothing, and `close-tab-by-id` exits 0. (Only
`delete-session` on a missing session exits non-zero.)

→ The tmux wrapper detects errors by return code. The zellij backend must
instead check explicitly (session in `list-sessions`, pane in `list-panes`)
and raise its `MuxError` itself.

### 4.2 Creating a session needs a console (Windows)

Starting `zellij attach -b S` from a process with redirected stdio (e.g. an
agent's Bash tool, or a `subprocess` call with `stdin/stdout` set) returns 0,
but the first pane's shell inherits the caller's stdio and exits at once, and
the server shuts down. The zellij log shows
`failed to kill child processes for pane 0 … (os error 87)`.

→ On Windows, create sessions with `creationflags=CREATE_NEW_CONSOLE`
(+ `CREATE_BREAKAWAY_FROM_JOB`) and a hidden window
(`STARTUPINFO.wShowWindow = SW_HIDE`), **without** redirecting std handles.
This works from Python (verified).

Other `action` calls work fine from redirected / console-less processes.

### 4.3 Detached `new-tab` (zellij 0.45.0–0.45.1, #5594)

With zero attached clients, `new-tab` exits 0 and prints a tab id. But the
tab has `VP 0×0`, zero panes, and the log shows
`Failed to apply layout: Not enough room for panes`. The next client attach
discards it. The zellij issue attributes this to 0.45.0's per-client tab
sizing; 0.44.3 is reported unaffected.

→ Workaround (verified): when `action list-clients` shows no clients,
temporarily attach a hidden-console client (`zellij attach S`, same flags as
§4.2). Wait until it appears in `list-clients`, run `new-tab --no-focus`,
then terminate the client. The tab keeps the temp client's size (e.g.
120×30) until a real client attaches, and it survives detach and re-attach.
Skip the workaround when a client is already attached, and on zellij versions
that contain the fix.

Phase 0 measurements: the temp client shows up in `list-clients` about
0.3–0.6 s after spawn. Calling `new-tab` right then (no extra settle) worked in
23 of 24 runs, with both `cmd.exe` and `claude.exe` as the tab command. The
tab stayed 120×28 after the client was killed. In the one failure (the first
probe of the day, on a session created 1 s earlier), `new-tab` printed a tab id
but the tab was gone by the time the client had been killed. That failure did
not reproduce. So after `new-tab` the backend must confirm the tab exists by
name, with a terminal pane, in `list-panes -a -j`. It confirms again after
killing the temp client, and retries the whole sequence once if the tab is
missing.

The first client attached to a fresh session lands on a floating
"About Zellij" plugin pane (`plugin_3`, `zellij:about`), and that is what
`list-clients` reports as its focused pane. Match clients by the first
column (client id) only, never by the pane column.

### 4.4 Panes do not inherit the client's environment

A pane started by `new-tab` did not see a variable set in the environment of
the `zellij action` call. Panes are spawned by the server, which keeps the
environment of the process that created the session.

→ Per-pane env goes through the launcher (§6.3). The same applies to `PATH`:
the agent CLI is resolved to an absolute path **by fleet** at launch time.
(On the investigation machine the Windows `PATH` held a literal
`~/.local/bin`, so PowerShell could not find `claude` even though Git Bash
could.)

Phase 0 confirmed the full picture. A session created from a Python process
whose env had an extra first `PATH` entry and a marker variable gave every
later `new-tab` pane that exact `PATH` (extra entry still first) and the
marker. A different marker value in the env of the `zellij action new-tab`
caller did not reach the pane. The result was the same whether the creating
Python was started from PowerShell or from Git Bash (except `SHELL`, §4.5).

**The creator's Claude Code markers leak into every pane.** When the session
is created by a process running under Claude Code (a leader's tool call, or
these probes), panes see `CLAUDECODE=1`, `CLAUDE_CODE_CHILD_SESSION=1`,
`CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET` / `_TOKEN`,
`CLAUDE_PID`, and others. A claude started in such a pane runs, but shows
`⚠ Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker`.
That breaks the usage accounting that reads claude's session JSONL. The
launcher must strip these variables (§6.3). tmux has the same exposure when a
leader creates the tmux server, but there it is already the existing
behavior.

### 4.5 The default shell depends on who created the session

The first pane ran `cmd.exe` when the session was created from PowerShell,
and Git Bash's `bash.exe` when it was created from Git Bash (`$SHELL` set).

→ Never rely on the default shell. Panes run the launcher directly as their
command.

### 4.6 Killed sessions stay listed as resurrectable

After `kill-session`, `list-sessions` shows
`S [Created …] (EXITED - attach to resurrect)`.

→ `session_exists` must ignore `EXITED` entries, and `kill_session` must also
run `delete-session`. Otherwise `attach S` would "resurrect" a dead leader.

### 4.7 CLI focus actions only drive the first client

With two clients attached, `action go-to-tab-name X` moved only client 1.
With no clients attached, it did not affect which tab the next client landed
on. There is no `--client-id` option. See §6.5.

Phase 0 details (via `list-clients`, mapping each client's pane id to a tab
through `list-panes -a -j`):

- The target is the client with the **lowest id**. Client ids restart at 1
  once every client has detached. When client 1 detached, client 2 became
  the one `go-to-tab-name` moves.
- A sole client moves as expected, even when `go-to-tab-name` runs right
  after the client first appears in `list-clients` (about 0.3 s after spawn).
- A newly attached client starts on the tab the session last had focused.
  With another client already attached, that is the other client's current
  tab.
- `new-tab --no-focus` left an attached client's focus alone (it stayed on
  tab `A` across three `--no-focus` tabs). `new-tab` without `--no-focus`
  moved the client to the new tab.

### 4.8 Tab ids can be reused

After the discarded tabs from §4.3 went away, the next tab got id `1` again.

→ Never persist tab or pane ids. Resolve by tab name on every operation.

### 4.9 Other verified points

- Tab and session names containing `·` work.
- `close-tab-by-id` terminated the `claude.exe` running in that tab.
- A Python child started with
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`
  outlived the agent tool call that spawned it, and could drive zellij
  afterwards. That is what the prompt deliverer and leader notifier need.
- A fresh background session's first tab is 50×50 until a client attaches.
- `kill-session` also ended the `claude.exe` running in its tabs (no stray
  processes afterwards).
- After claude's `/exit`, the tab stays with the pane held:
  `list-panes` reports `"exited": true, "exit_status": 0`.
- `dump-screen` of claude's composer can show stale cells. While text is in
  the composer, the dump shows `❯Reply…` with no space after `❯`. After
  `Ctrl u`, the dump showed `❯t` (the first typed character) even though the
  composer was empty: the next submitted message had no leading `t`. So never
  treat composer text in a dump as exact. `ClaudeAdapter.ready` is not
  affected.

---

## 5. Windows blockers outside the multiplexer

| # | Problem | Where | Fix |
|---|---|---|---|
| 1 | `import fcntl` fails, so **nothing starts** | `src/fleet/locking.py:15`, `src/fleet/leader_notifier.py:31` | Portable lock helper: `fcntl.flock` on POSIX, `msvcrt.locking` (byte 0; `LK_NBLCK`, retrying when blocking) on Windows, plus a non-blocking variant for the notifier lock. Still stdlib only. |
| 2 | `os.replace` raises `PermissionError` while another process holds the target open (CPython opens files without `FILE_SHARE_DELETE`) | `src/fleet/locking.py:55,105` | Short bounded retry on Windows. |
| 3 | Locale encoding is cp932. Unencoded file I/O and `print` to a pipe break on non-cp932 characters (`UnicodeEncodeError` on `—` reproduced) | `commands/start.py:339,530,531,540`; 20 `text=True` subprocess calls in 7 files; all `print` output | Explicit `encoding="utf-8"` (`errors="replace"` when decoding tool output); reconfigure stdout/stderr to UTF-8 at the CLI entrypoints; set `PYTHONUTF8=1` in the shims and in child environments. |
| 4 | `start_new_session=True` is silently ignored on Windows | `prompt_deliverer.py:92`, `leader_notifier.py:308` | One `spawn_detached()` helper: POSIX `start_new_session`; Windows `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP \| CREATE_BREAKAWAY_FROM_JOB` (retry without breakaway on `OSError`). |
| 5 | `PATH` joined with a hard-coded `:` | `commands/start.py:150` | `os.pathsep`. |
| 6 | The agent command line is built with `shlex.quote` and typed into a shell (POSIX quoting) | `commands/start.py:164`, `commands/leader.py:135` | Pass argv to the backend (`new_window(argv=…)`). zellij runs the launcher with argv directly, so no shell quoting. |
| 7 | `os.execvp` for attach (not a real exec on Windows) | `commands/attach.py:128`, `commands/leader.py:105,201` | `subprocess.call` and return its exit code on Windows (keep `execvp` on POSIX). |
| 8 | `fleet` / `fleet-agent` are extensionless Python scripts. PowerShell / cmd cannot run them, and Git Bash's shebang lookup finds the Microsoft Store `python3` stub | `fleet`, `fleet-agent`, `paths.fleet_agent_bin()` | Add `fleet.cmd` / `fleet-agent.cmd` (`py -3`, falling back to `python`; set `PYTHONUTF8=1`). On Windows `fleet_agent_bin()` returns the `.cmd` path with forward slashes. |
| 9 | Prompts embed `shlex.quote(bin_path)`. PowerShell (codex's shell) cannot invoke a single-quoted path without `&`, and Git Bash eats unquoted backslashes | `driver_prompt.py:138`, `leader_prompt.py:92` | On Windows, embed an unquoted forward-slash path. Require (and preflight-check) a clone path without spaces. |
| 10 | Git Bash converts POSIX-looking arguments (`/k` became `K:/` in the investigation) when an agent calls `fleet-agent` | agent tool calls | The launcher sets `MSYS_NO_PATHCONV=1` in the pane environment. |
| 11 | Path comparisons assume case-sensitive, `/`-separated paths | e.g. `commands/start.py` `_infer_project_from_promptfile` | Compare with `os.path.normcase`. |
| 12 | The verify gate runs `shell=True`, which is `cmd.exe` on Windows | `orchestrator.py:529` | Document that verify commands run under `cmd.exe` on Windows (an optional `shell:` field can come later). |
| 13 | Desktop notifications are macOS-only | `notify.py:113` | **Done** (`windows-toast`): `notify.py` `_windows_notify` shows a WinRT toast via `powershell.exe -EncodedCommand` (config `windows: {enabled: true}`, default-on). Slack also works. |
| 14 | Deep worktree paths can exceed `MAX_PATH` | `fleet-state/projects/<p>/worktrees/task-<id>/…` | Preflight checks `git config core.longpaths`. |
| 15 | codex's `config.toml` trust key format on Windows is unknown (may be `\\?\`-prefixed) | `agents.codex_repo_trusted` | Verify in Phase 0 and normalize. |

Already Windows-safe (checked): the claude usage-log dir escaping
(`D:\dev\agent-fleet` → `D--dev-agent-fleet` matches Claude Code's naming),
`webbrowser`, the `fleet edit` HTTP server, and the `git worktree` calls.

---

## 6. Design decisions

### 6.1 Multiplexer abstraction: `fleet/mux/`

13 modules import `fleet.tmux` directly (`commands/{attach,cleanup,done,inbox,leader,rm,send_prompt,sessions,start}.py`,
`leader_notifier.py`, `orchestrator.py`, `prompt_deliverer.py`,
`status_data.py`), and `prompt_pointer.py` takes the module as a parameter.
They move behind one interface, still mechanism-only like today's
`tmux.py` (it must not know about drivers, formations, or tasks):

```python
class Mux(Protocol):
    name: str                                    # "tmux" | "zellij"
    def available(self) -> bool: ...
    def session_exists(self, session: str) -> bool: ...
    def new_session(self, session: str, *, window: str | None = None,
                    argv: list[str] | None = None, cwd: str | None = None,
                    env: dict[str, str] | None = None) -> None: ...
    def new_window(self, session: str, window: str, *, argv: list[str],
                   cwd: str | None = None, env: dict[str, str] | None = None) -> None: ...
    def list_windows(self, session: str) -> list[str]: ...
    def kill_window(self, session: str, window: str) -> None: ...
    def kill_session(self, session: str) -> None: ...
    def send_text(self, session: str, window: str, text: str, *, enter: bool = True) -> None: ...
    def send_key(self, session: str, window: str, key: str) -> None: ...   # normalized: "Enter", "Ctrl-u"
    def paste(self, session: str, window: str, text: str) -> None: ...
    def capture(self, session: str, window: str) -> str: ...
    def attach(self, session: str, window: str | None = None) -> int: ...
    def attach_hint(self, session: str, window: str | None = None) -> str: ...
```

Notes:

- **Buffers leave the interface.** `load_buffer` / `paste_buffer` /
  `delete_buffer` become `paste(text)`. The tmux backend still uses a named
  buffer internally, so `--no-auto-paste`'s manual `C-b ]` path keeps
  working. The zellij backend has no buffer, and its `--no-auto-paste` hint
  points to `fleet-agent send-prompt` only.
- **Keys become explicit.** Adapters return rename steps as
  `(text, press_enter)`, and codex relies on tmux interpreting the text
  `"C-u"` as a key. The step type gains an explicit key form (e.g. a `Key`
  marker) and each backend translates normalized key names (`Ctrl-u` →
  tmux `C-u` / zellij `Ctrl u`).
- **Launch takes argv.** `new_window(argv=…)` replaces
  `new_window()` + `send_keys(cli_quoted)`. The tmux backend keeps today's
  behavior exactly (shell window, `-e` env, typed `shlex`-quoted command),
  so the pane keeps a shell after the agent exits. The zellij backend runs
  the launcher as the tab's command.
- **Test guard.** `FLEET_NO_TMUX` becomes `FLEET_NO_MUX`, with the old name
  kept as an alias.

### 6.2 Layout: driver tabs inside the owner session (chosen)

| Option | Shape | Pros | Cons |
|---|---|---|---|
| **A. Tabs in the owner session** *(chosen)* | `fleet-<label>` = leader tab + one tab per driver window (`<task>·<role>`) | Same model as tmux and design §5.2 (the owner session holds its leader and drivers). Status / liveness logic unchanged. | Needs the #5594 workaround on 0.45.x. Attach cannot pick the tab when other clients are attached. |
| B. One session per driver | `fleet-<label>` for the leader, `fleet-<label>--<task>·<role>` per driver | No #5594 (the first tab of `attach -b` is sized). Exact attach target. Verified to work. | Leaves the design model. One zellij server process per driver. Session-list clutter. |

A is chosen. B is the fallback if the workaround proves unreliable in
Phase 0 / PR3.

### 6.3 Pane launcher: `fleet/pane_launch.py`

A zellij pane cannot receive env or a shell-typed command reliably, so each
pane's command is:

```
<python> -m fleet.pane_launch --env-file <task_dir>/pane-env.json -- <agent argv…>
```

Responsibilities:

- Apply the env (`FLEET_TASK_ID`, `FLEET_STATE_DIR`, `FLEET_SESSION`,
  `PATH` prefixed with the clone root, `PYTHONUTF8=1`, `MSYS_NO_PATHCONV=1`).
- Remove the inherited agent-session markers before starting the agent:
  `CLAUDECODE`, every `CLAUDE_CODE_*`, and `CLAUDE_PID` (§4.4). Keep
  user-level settings such as `CLAUDE_CONFIG_DIR` and `ANTHROPIC_*`.
- Resolve the agent CLI to an absolute path with `shutil.which` under that
  `PATH`. On failure, print a clear error and keep the pane open.
- Run the agent (inheriting the console) and wait. When it exits, the pane
  shows zellij's exited / re-run state.

A JSON env file avoids command-line quoting and length limits. The same
launcher can serve the leader pane (`fleet leader`).

### 6.4 Backend selection

`FLEET_MUX` (`tmux` | `zellij`) wins. Otherwise use `zellij` on
`sys.platform == "win32"` and `tmux` everywhere else. One backend per
process, chosen at first use. Once a task records which multiplexer it was
spawned in, a later process cannot switch backends under it.

### 6.5 Attach on zellij

`fleet attach [<target>]`:

- Run `zellij attach fleet-<label>` as a subprocess.
- If no other client is attached, a short helper waits for the new client to
  appear in `list-clients` and then runs `go-to-tab-name <window>`. The only
  client is the first client, so this lands on the right tab (verified in
  Phase 0; no extra settle is needed after the client appears).
- Otherwise, print the tab's position before attaching (e.g.
  "task `42·implementer` is tab 3 — press Ctrl+t then 3"). Never run
  `go-to-tab-name` in this case: it would move the *other*, lower-id client
  (§4.7). The new client starts on the other client's current tab.
- Check `list-clients` just before `attach`. A client that arrives between
  that check and `go-to-tab-name` would be the one moved. Accept that race;
  it only moves a tab and loses no state.

### 6.6 Supported zellij versions

- Minimum **0.45.0**. Phase 0 checked the 0.44.3 Windows release (`--help`
  only): it has `action paste -p`, `close-tab-by-id`, `list-panes -a -j`,
  `list-tabs -j`, `dump-screen -p` / `-f`, `send-keys -p`, `write-chars`,
  `list-clients`, `go-to-tab-name`, `attach -b`, and `list-sessions -n`, and
  `new-tab` returns a tab id. But it has **no `new-tab --no-focus`**, so on
  0.44.x every driver spawn would pull the first attached client (for
  example the user watching the leader) onto the driver tab. 0.45.0 has
  `--no-focus`.
- **0.45.0–0.45.1:** supported with the §4.3 workaround. `fleet preflight`
  reports that the workaround is active. 0.45.1 was still the latest release
  on 2026-09-19, and the fix (#5612) was still unmerged.
- Different zellij versions share one session namespace on Windows: the
  0.44.3 binary listed a session served by 0.45.1. fleet must always run the
  same `zellij` binary for the server and every `action` / `attach`, so it
  should resolve the binary once to an absolute path. Preflight should warn
  when `zellij --version` differs from the version of a running fleet
  session's server, if that can be found out cheaply.

---

## 7. Implementation plan

Each PR follows AGENTS.md (feature branch → PR → `main`, with a
`changelog.d/<task-id>.md` fragment) and keeps POSIX / tmux behavior
unchanged unless stated.

### Phase 0 — remaining verifications (no PR, before PR3)

1. codex on Windows under zellij: ready / gate regexes against
   `dump-screen`, the `/rename` flow including `Ctrl u`, and the
   `config.toml` trust-key format (§5 #15).
2. claude: bracketed `paste` of the pointer followed by `Enter` submits, and
   the `inbox_seen` ack arrives.
3. `new-tab --no-focus` does not move a real, visible attached client.
4. The §6.5 sole-client focus behavior.
5. From a real leader pane (claude in zellij → Git Bash tool →
   `fleet-agent start`), the detached deliverer survives the tool call.
6. Which `PATH` the panes see when the session was created from the user's
   terminal vs. from the leader.

#### Phase 0 results (2026-09-19)

Environment: Windows 10 Pro 19045, Python 3.13, zellij 0.45.1 (plus the
0.44.3 and 0.45.0 Windows release zips, `--help` only), claude CLI 2.1.277
(`--model sonnet`). zellij was driven from Python `subprocess` with argv
lists. Sessions were created with `CREATE_NEW_CONSOLE` and a hidden window,
and tabs were opened while a hidden temp client was attached (§4.3). All
probe sessions were killed and deleted afterwards.

| # | Item | Result |
|---|---|---|
| 1 | claude under zellij: ready prompt, pointer paste + `Enter` | ✅ `paste -p` + 0.25 s + `send-keys -p … Enter` submitted, both a one-liner and the real `pointer_text()` with a Windows path. `write-chars` + 0.25 s + `Enter` also submitted. `/exit` ended claude cleanly. The `inbox_seen` ack needs the real deliverer, so it moves to PR3's "done when". |
| 2 | `send-keys "Ctrl u"` clears a typed line | ✅ in claude's composer. In cmd.exe it only echoes `^U`: cmd.exe has no such key, so this is not a zellij issue. |
| 3 | `new-tab --no-focus` keeps an attached client's focus | ✅ with a hidden client. A visible, human-driven client was not tried, but zellij treats both kinds of client the same. |
| 4 | Sole-client attach focus (§6.5) | ✅ the sole client moves to the `go-to-tab-name` target, even with no settle. With two clients, only the lowest-id client moves (§4.7). |
| 5 | `PATH` / env seen by panes | ✅ panes get the session creator's env exactly (extra `PATH` entry first, marker set). The caller's env is ignored. The same holds when the creator is launched from PowerShell or from Git Bash. ⚠ Claude Code markers leak (§4.4). A session created from a real user terminal was not tried here: every process on this machine descended from Claude Code. |
| 6 | zellij 0.44.3 compatibility | ❌ no `new-tab --no-focus`, so the minimum is 0.45.0 (§6.6). Everything else is present. #5594 was not live-tested on 0.44.3, because the 0.44.3 binary shares the session namespace with the running 0.45.1 (it listed the 0.45.1 session). |
| 7 | codex | ⬜ not installed; still unverified (item 1 above, §5 #15). |
| — | Phase 0 item 5 (deliverer survives a real leader tool call) | ⬜ not run in this pass; covered by PR3's "done when". |

Screens captured for `src/fleet/adapters/claude.py` (from `dump-screen`,
120×28):

- Startup dialog when the Claude in Chrome extension is installed. It
  appears before the input prompt. It is skipped entirely with
  **`claude --no-chrome`**:

  ```
    Claude in Chrome extension detected
    …
    ❯ No, keep browser tools off
      Yes, use my browser

    Enter to confirm · Esc to keep browser tools off
  ```

  The bare `ready` regex matches this screen. Since #245, `is_ready()`
  is `False` and `is_gated()` is `True` for it (§10).
- Ready input prompt (idle; the text after `❯` is claude's grey
  placeholder, or a suggested follow-up after a turn):

  ```
                                                                 ● high · /effort
  ────────────────────────────────────────────────────────────────────────────
  ❯ Try "fix lint errors"
  ────────────────────────────────────────────────────────────────────────────
    ⏵⏵ auto mode on (shift+tab to cycle)
  ```

  An empty composer renders as `❯ ` on its own line. While claude is working
  the composer still shows `❯ `, and the footer adds `· esc to interrupt`. So
  `ready` also matches a busy screen, as it does under tmux.
- Permission dialog triggered by the pointer when the prompt file lies
  outside claude's working directories. `gate` matches it and `ready` does
  not:

  ```
   Allow reads outside the working directories?
   ❯ 1. Yes, keep allowing reads outside the working directories
     2. No, block reads outside the working directories from now on
     3. No, ask again next time
  ```

  The probe ran claude without `--dangerously-skip-permissions`. fleet's
  `driver-prompt.md` lives under `<state>/tasks/…`, which is also outside the
  worktree, but fleet launches claude with that flag. Whether the dialog can
  still appear then was not tested. If it does, `gate` already holds the
  deliverer back. The dialog is not Windows-specific.

### PR1 — Windows-compatible core (`windows-base`)

- Portable lock helper plus the `os.replace` retry (§5 #1–2).
- UTF-8 everywhere (§5 #3).
- `fleet/proc.py` with `spawn_detached()`, used by both detached spawners
  (§5 #4).
- `os.pathsep` (§5 #5); `normcase` path comparisons (§5 #11).
- `fleet.cmd` / `fleet-agent.cmd`; Windows `fleet_agent_bin()` and the
  prompt-embedding rule (§5 #8–9).
- CI: add a `windows-latest` unittest job (tmux-dependent tests skip on
  `win32`).

**Done when** `fleet --help`, `fleet init`, `fleet status`, and
`fleet-agent start --dry-run` work on Windows, and POSIX CI stays green.

### PR2 — multiplexer abstraction, tmux only (`mux-abstraction`)

> Implemented. `fleet.mux` exposes the §6.1 interface plus two optional
> methods: `preload_paste(name, text)` / `drop_paste(name)` (tmux: the named
> per-task buffer for `--no-auto-paste`'s `C-b ]`; no-op elsewhere) and
> `kill_session_hint(session)`. Explicit keys are `fleet.mux.Key("Ctrl-u")`,
> re-exported from `fleet.adapters`.

- Add `fleet/mux/{__init__,base,tmux}.py` (§6.1) and move all 13 importers
  plus `prompt_pointer.py` onto it.
- Buffer → `paste`; explicit key steps in the adapters; argv-based
  `new_window`; backend-provided `attach` / `attach_hint` (replacing the
  hard-coded `tmux attach -t …` strings).
- `FLEET_NO_MUX` (alias `FLEET_NO_TMUX`).
- Update the 17 test files that patch `fleet.*.tmux*`. Consider a shared
  fake backend in `tests/` instead of per-function patches.

**Done when** there is no behavior change on macOS / Linux: manual smoke of
leader, start, prompt delivery, inbox wake, done → leader notify, handoff,
cleanup, and attach.

### PR3 — zellij backend (`zellij-backend`)

- `fleet/mux/zellij.py` implementing §3 and §4: explicit existence checks,
  hidden-console session creation on Windows, the gated #5594 workaround,
  name → pane-id resolution, kill + delete, `EXITED` filtering.
- `fleet/pane_launch.py` (§6.3).
- Backend selection (§6.4).

**Done when**, on Windows: the leader starts; a driver tab opens both with
and without a client attached; the prompt is delivered (`inbox_seen` ack);
inbox wake-up and leader-notification injection work; cleanup closes the tab
and ends the agent process.

### PR4 — Windows UX and docs (`windows-ux`)

- zellij attach (§6.5).
- `fleet preflight`, per backend:
  - multiplexer presence and version (fail below 0.45.0; flag 0.45.0–0.45.1);
  - agent CLIs resolvable to absolute paths;
  - clone path without spaces;
  - `core.longpaths`.
- README / README.ja: a Windows section (install zellij, `fleet.cmd`, no
  spaces in the clone path).
- `docs/design.md`: "tmux" → "terminal multiplexer (tmux / zellij)" in §3,
  §8.6, §11.2, and §11.5.
- Update this doc's status.

**Done when** a fresh Windows machine, set up by following the README, passes
the §8 checklist.

### PR5 — optional follow-ups

- Windows toast notification (§5 #13).
- An optional `shell:` field for verify commands (§5 #12).
- Anything codex-specific that Phase 0 turns up.

---

## 8. Validation

- **Automated:** the unittest suite on `ubuntu-latest` and `windows-latest`.
  The zellij backend gets unit tests against recorded `list-panes -j` /
  `list-sessions` output. Real-zellij integration is not run in CI (session
  creation needs a console, §4.2).
- **Manual E2E checklist (Windows, per release that touches this area):**
  1. `fleet preflight` clean.
  2. `fleet leader`, then attach and detach.
  3. `solo` task while detached → prompt delivered → driver runs
     `fleet-agent inbox-read`.
  4. `fleet-agent inbox <id> "…"` wakes the driver.
  5. `fleet-agent done` → leader notification injected.
  6. `multi_stage` handoff opens the next tab.
  7. The verify gate passes and fails.
  8. `fleet attach <task>`, with and without another client attached.
  9. `cleanup` / `merge` close the tab and end the agent process.
  10. Repeat steps 3–5 with a codex driver.

---

## 9. Risks and open questions

- **zellij on Windows is young.** Native support arrived in 0.44.0
  (2026-03), and 0.45 already regressed (#5594). Keep a tested-version note
  and a preflight warning. The session-per-driver layout (§6.2 B) is the
  escape hatch.
- **No real-zellij CI.** Windows correctness relies on the manual checklist.
- **Temp-client cleanup (#5594 workaround).** If killing the hidden client
  fails, it lingers and keeps the tab sized to its console. Reap stray
  clients via `list-clients` and track the pid. (`Popen.kill()` on the
  `zellij attach` process removed the client within 1 s in every Phase 0
  run.)
- **Workaround reliability.** 1 of 24 Phase 0 runs lost the new tab
  (§4.3). Verify the tab after creation and retry once. Fall back to §6.2 B
  if losses keep happening in PR3 testing.
- **Inherited agent env.** A session created from inside an agent passes
  that agent's session markers to every pane (§4.4). The launcher strips
  them.
- **Agent TUIs under ConPTY.** claude rendered correctly under `dump-screen`.
  codex is unverified.
- **Clone path with spaces** breaks the unquoted prompt embedding (§5 #9).
  Preflight warns; quoting per vendor shell is a possible later improvement.

---

## 10. Related finding (not Windows-specific)

During the investigation, claude showed a "Claude in Chrome extension
detected" startup dialog whose default option renders as
`❯ No, keep browser tools off`. `ClaudeAdapter.ready`
(`^\s*❯(?!\s*\d+\.)`) **matches** that line, and `ClaudeAdapter.gate` does
not: it only knows numbered options and login / trust / update wording. The
prompt deliverer would therefore paste the pointer into the dialog. This
needs its own Issue: tighten `ready`, or teach `gate` about un-numbered
selection menus.

**Update:** fixed by #245 (`fix: treat un-numbered selection dialogs as a
boot gate, not ready`). Phase 0 fed that code the screens captured under
zellij. For the Chrome dialog, `ClaudeAdapter.is_ready()` is `False` and
`is_gated()` is `True`. For the idle prompt (including the `❯t` stale-cell
artifact from §4.9), `is_ready()` is `True` and `is_gated()` is `False`. For
the outside-read permission dialog, `is_ready()` is `False` and `is_gated()`
is `True`.

Phase 0 also found that claude has a **`--no-chrome`** flag ("Disable Claude
in Chrome integration"). With it, the dialog never appears and claude goes
straight to the input prompt (verified). Adding it to
`ClaudeAdapter.cli_command` would remove the boot-gate notification for users
with the extension. That is optional, because #245 already keeps the
deliverer from pasting into the dialog.

---

## References

- zellij #5594 — Tabs created in a detached session are discarded when the
  first client attaches: <https://github.com/zellij-org/zellij/issues/5594>
- zellij PR #5612 — fix: give tabs created in a detached session a real size:
  <https://github.com/zellij-org/zellij/pull/5612>
- Zellij 0.44.0: Remote Sessions, Windows Support, CLI Automation:
  <https://zellij.dev/news/remote-sessions-windows-cli/>
- zellij releases: <https://github.com/zellij-org/zellij/releases>
