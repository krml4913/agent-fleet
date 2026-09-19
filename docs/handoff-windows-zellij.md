# Handoff: Windows / zellij support → fleet leader

> Written 2026-09-19 at the end of the Windows-support push. From here on the
> work is run by the fleet leader (`fleet` itself) instead of a standalone
> Claude Code session. This note is the entry point: what was done, why, what
> state things are in, and what is still open. The detailed spec and the
> record of every verified zellij behavior live in
> [`windows-support.md`](windows-support.md); this file does not repeat it.

---

## 1. Background: why this started

agent-fleet was macOS/Linux only: every pane operation went through tmux, and
the codebase assumed POSIX (`fcntl`, UTF-8 locale, executable shebang
scripts). The user wanted fleet to run **natively on Windows (no WSL)**. tmux
has no native Windows build, so the question was whether
[zellij](https://zellij.dev/) (which ships a native Windows build since 0.44)
could replace it for **every** fleet feature: leader launch, driver spawn,
prompt delivery, inbox wake-up, leader notification, stage handoff, verify
gate, cleanup / merge, and attach.

Sequence of events (all 2026-09-19):

1. **Feasibility study.** Each tmux primitive fleet uses was mapped to a zellij
   CLI action and tried by hand on the target machine (Windows 10 Pro 19045,
   Python 3.13, zellij 0.45.1, native claude CLI). Verdict: feasible, with
   several zellij quirks that the backend has to work around (§3 below).
2. **Plan written to docs** (#244, `docs/windows-support.md`), then a Phase 0
   re-check of the open questions (#246).
3. **Implementation by sub-agents.** A Claude Code session acted as leader:
   sub-agents implemented each PR on a `fleet/task/<id>` branch, the leader
   reviewed, waited for CI, and merged. No user approval was needed per PR.
4. **Live E2E on Windows** found bugs in stage handoff and in-pane tab launch,
   fixed in #253.
5. **Side work**: test-suite speedup (#254) and a driver-prompt / env-leak fix
   found during E2E (#255).
6. **Handoff** to the fleet leader (this document). Remaining items were filed
   as GitHub issues and are not being worked on.

## 2. What landed

All merged to `main` with merge commits, CI green (Linux 3.11–3.13 plus a
`unittest-windows` job).

| PR | Branch | What |
|---|---|---|
| #244 | `windows-plan` | Feasibility study + plan (`docs/windows-support.md`) |
| #245 | `claude-dialog-gate` | `VendorAdapter.is_dialog / is_ready / is_gated`: a claude selection dialog is no longer mistaken for the ready prompt (the delivery and leader-notify pollers use it) |
| #246 | `windows-phase0` | Phase 0 results folded into the plan |
| #247 | `windows-base` | `fleet/locking.py` (msvcrt / fcntl lock, `replace_file` with retry), `fleet/proc.py` `spawn_detached`, explicit UTF-8 everywhere, `fleet.cmd` / `fleet-agent.cmd` shims, the `unittest-windows` CI job |
| #248 | `mux-abstraction` | `src/fleet/mux/` (`Mux` base, `Key`, `MuxError`, tmux backend); `src/fleet/tmux.py` removed; `tests/_fake_mux.py` |
| #249 | `windows-toast` | Windows toast notifications via PowerShell + WinRT |
| #250 | `windows-preflight` | Preflight per backend: zellij version, spaces in the clone path, `core.longpaths`, `fleet-agent.cmd`, agent CLI resolution |
| #251 | `zellij-backend` | `src/fleet/mux/zellij.py`, `src/fleet/pane_launch.py`; zellij becomes the default on win32 |
| #252 | `windows-docs` | README / README.ja "Windows" section |
| #253 | `zellij-e2e-stages` | `src/fleet/deferred_launch.py` (stage handoff when closing a tab would kill the caller), in-pane tab launch fix, cmd.exe cp932 decoding |
| #254 | `test-speedup` | In-process CLI in tests (`FLEET_TEST_SUBPROCESS=1` restores subprocesses), `tests/run_parallel.py` (~9 s for about 1000 tests) |
| #255 | `driver-workdir` | The driver prompt names the working directory (worktree + branch, or the project root) and forbids editing `fleet-state/` directly; the pane launcher also strips `CLAUDE_EFFORT` and `AI_AGENT` |
| #262 | `none-pane-cwd` | workspace=none driver panes open in the project root, not the task dir under `fleet-state/` (task dir only as a fallback) |
| #263 | `windows-handoff` | This handoff note; `windows-support.md` §11 links each open item to its issue |

Backend selection: `FLEET_MUX=tmux|zellij` overrides the default (zellij on
win32, tmux elsewhere). `FLEET_NO_MUX` disables the multiplexer (tests).
`FLEET_ZELLIJ` points at a specific zellij binary.

## 3. Things to know before running fleet on Windows

Setup is in the README "Windows" section. The short version:

- Run fleet through `fleet.cmd` / `fleet-agent.cmd` (or `python fleet …`).
  `fleet.cmd preflight` checks everything below.
- zellij ≥ 0.45.0, native build. Clone path without spaces.
  `git config --global core.longpaths true`.
- Agent CLIs must be on the real Windows `PATH`. Windows does not expand `~`,
  so `~/.local/bin` in `PATH` works only in Git Bash.

zellij quirks the backend works around (details in `windows-support.md` §4).
Keep these in mind when reading logs or debugging:

- `zellij action …` **exits 0 even if the session or pane does not exist.**
  The backend confirms results by listing panes/tabs instead of trusting the
  exit code.
- Creating a session needs a hidden console (`CREATE_NEW_CONSOLE` +
  `SW_HIDE`, no stdio redirection).
- **zellij#5594:** a tab created in a detached session is 0×0 and is thrown
  away when the first client attaches. Workaround: a short-lived hidden
  client (`FLEET_ZELLIJ_TEMP_CLIENT`). The upstream fix (PR #5612) is not
  released; `FIXED_DETACHED_TAB_VERSION` is set to 0.46.0 as a guess. Recheck
  it when zellij 0.46 ships.
- Panes inherit the **zellij server's** environment, not the caller's, so
  per-pane env goes through `pane_launch.py` and an env file in
  `fleet-state/global/pane-env/<session>/`.
- Closing a zellij tab kills the process that closed it. That is why
  `fleet-agent done` defers the next stage's launch (`deferred_launch.py`,
  `Mux.window_close_kills_caller`).
- Killed sessions stay listed as EXITED (resurrectable), and tab ids get reused.
- `go-to-tab-name` only moves the client with the lowest id.

Operational rules followed during this work. Keep them:

- **Only touch zellij sessions fleet created** (`fleet-*`, or test
  sessions). The user runs personal zellij sessions on the same machine. Never
  kill, delete or attach to them.
- **Do not read shell rc files** (`~/.bashrc` etc.): they hold credentials.
- Tests must stay hermetic: they use `FLEET_NO_MUX` / the fake mux and must
  never create real zellij sessions. If a stray `fleet-test-*` session shows up
  in `zellij list-sessions`, a test is leaking.

## 4. Verified state

Verified end-to-end on the Windows machine with claude drivers:

- Leader launch.
- Solo `start`, with and without a client attached.
- Prompt delivery (`inbox_seen`), inbox wake-up, leader notification.
- Multi-stage handoff and the verify gate.
- merge / cleanup teardown (including worktree removal after claude exits).
- Attach logic (with hidden clients).

Not verified: see §5.

## 5. Open items (not being worked on)

Each has a GitHub issue. They are also listed in `windows-support.md` §11.

- **#256** codex driver under zellij: codex is not installed on the Windows
  machine.
- **#257** `fleet attach` in a real, visible terminal.
- **#258** zellij backend on macOS / Linux.
- **#259** claude's workspace-trust dialog needs one manual attach per
  worktree. There is a proposal for an opt-in `fleet init --trust-claude`; it
  was deferred because it writes to the user's claude config.
- **#260** optional `shell:` field for verify commands (they run under cmd.exe
  on Windows today).
- **#261** re-run a live tmux E2E of multi-stage handoff after #253.

## 6. Where to look

- `docs/windows-support.md` — the spec, the zellij quirks, design decisions
  (§6), the PR plan and its implementation notes (§7), and open follow-ups
  (§11).
- `src/fleet/mux/` — `base.py` (the interface), `tmux.py`, `zellij.py`.
- `src/fleet/pane_launch.py`, `src/fleet/deferred_launch.py`,
  `src/fleet/proc.py`, `src/fleet/locking.py`.
- `tests/run_parallel.py` — the fast test runner used in CI.
