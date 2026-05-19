# Changelog

All notable changes to agent-fleet are recorded here. Entries are grouped by
development **Phase** (per `docs/design.md`) until the first tagged release.

## [Unreleased]

### `fleet-agent spawn` auto-paste by default (2026-05-20)

**Breaking change** — `--auto-prompt` flag removed; auto-paste is now the default.

- `fleet-agent spawn <id> "..."` alone starts the driver immediately (no manual paste needed).
- Use `--no-auto-paste` to restore the old behaviour (preloads buffer, manual paste via `C-b ]` or `fleet-agent send-prompt`).
- Migration: remove any `fleet-agent send-prompt` calls that immediately follow `spawn` — they are now redundant.

### Phase 13 — CLI split: `fleet` + `fleet-agent` (2026-05-20)

**Breaking change** — all commands are now split across two binaries.
No backwards-compatibility aliases. One-time cutover.

#### What changed

| Before | After | Binary |
|---|---|---|
| `fleet init` | `fleet init` | `fleet` |
| `fleet preflight` | `fleet preflight` | `fleet` |
| `fleet leader` | `fleet leader` | `fleet` |
| `fleet attach` | `fleet attach` | `fleet` |
| `fleet status` | `fleet status` | `fleet` |
| `fleet log` | `fleet log` | `fleet` |
| `fleet topology` | `fleet topology` | `fleet` |
| `fleet workflow` | `fleet workflow` | `fleet` |
| `fleet spawn` | **`fleet-agent spawn`** | `fleet-agent` |
| `fleet inbox` | **`fleet-agent inbox`** | `fleet-agent` |
| `fleet send-prompt` | **`fleet-agent send-prompt`** | `fleet-agent` |
| `fleet cleanup` | **`fleet-agent cleanup`** | `fleet-agent` |
| `fleet ask` | **`fleet-agent ask`** | `fleet-agent` |
| `fleet event` | **`fleet-agent event`** | `fleet-agent` |
| `fleet done` | **`fleet-agent done`** | `fleet-agent` |

#### New file

`./fleet-agent` — shebang script at repo root, same structure as `./fleet`.
Both import the same `src/fleet/` package; entrypoint decides which parser
to build (`build_parser_user()` vs `build_parser_agent()`).

#### PATH setup decision

`fleet-agent spawn` injects `PATH=<repo>:$PATH` into the spawned tmux window's
env. Rationale: repo-local injection keeps the binary self-contained without
requiring the user to modify their shell profile, and avoids conflicts if
multiple agent-fleet repos exist on the machine.

#### Migration note for running driver / leader panes

Any pane that was spawned before this change expects the old `fleet` command.
Stop those panes manually (`fleet-agent cleanup <id>`) and re-spawn. There is
no cutover path; WIP tasks should be restarted.

### Phase 12 — `fleet log` + `fleet send-prompt` (2026-05-19)

Observability + recovery shortcuts for the spawn flow.

- `fleet log [task-id] [-n N] [--type T]` — tail `events.jsonl` with
  optional task / type filters. The `--type` flag is repeatable
  (e.g. `--type spawn --type done`).
- `fleet send-prompt <task-id>` — re-load
  `<state>/tasks/task-<id>/driver-prompt.md` into the named tmux
  buffer and paste it into the task window. Companion to
  `fleet spawn`'s default (manual-paste) behaviour.
- Tests: 130 unittest cases pass (+5 log + 3 send_prompt).

### Phase 11 — `fleet attach` + `fleet inbox` (2026-05-19)

Two everyday-use shortcuts so the leader doesn't have to memorize tmux
syntax or hand-edit `inbox.md`.

- `fleet attach [<target>]`: shortcut for
  `tmux attach -t fleet-<project>:<window>`. Target is `leader`
  (default) or a task id (`fleet attach 1` → window `task-1`).
- `fleet inbox <task-id> "<message>"`: append a timestamped block to
  `<state>/tasks/task-<id>/inbox.md` and emit an `inbox_message`
  event so the dashboard reflects it.
- Tests: 122 unittest cases pass (+4 inbox + 3 attach).

### Phase 10 — README, CI, `fleet preflight` (2026-05-19)

Project hygiene pass — anyone arriving via `git clone` now has a clear
on-ramp.

- `fleet preflight` — verify Python ≥ 3.11, tmux, git, claude, codex.
  Required tools missing → exit 1; optional tools missing → warn.
- `.github/workflows/test.yml` — GitHub Actions runs `unittest` against
  Python 3.11 / 3.12 / 3.13 on `push` and `pull_request`.
- README rewritten: quick-start walkthrough, command catalogue (project
  / leader / tasks / driver-side / configuration), repo layout, and
  state-dir layout.
- Tests: 115 unittest cases pass (+4 preflight).

### Phase 9 — `fleet cleanup` + workflow teardown (2026-05-19)

Physical teardown is split from logical completion (`fleet done`).

- `fleet cleanup [task-id] [--archive] [--force]`:
  * Runs the workflow plugin's `on_cleanup` hook.
  * Kills the task's tmux window (if any) and drops its prompt buffer.
  * Optionally archives `tasks/task-<id>/` to `tasks/_archive/task-<id>/`.
  * Refuses non-terminal statuses (`completed`/`failed`/`cancelled`)
    unless `--force` is passed.
- `git_worktree` plugin gains `on_cleanup`:
  * `git worktree remove --force` the worktree.
  * `git branch -D task/<id>` (best-effort; missing branches are
    tolerated).
- Cleanup emits a `cleanup` event and rebuilds dashboard.md (archived
  tasks disappear from `list_tasks` because `_archive/` is outside the
  `task-*` glob).
- Tests: 111 unittest cases pass (+5 cleanup + 1 git_worktree cleanup).

### Phase 8 — dashboard rebuild + heartbeat (2026-05-19)

Dashboard becomes information-dense; drivers gain a "still alive"
signal — without forge's 6-feature lifecycle daemon.

- `fleet.heartbeat`:
  - `parse_ts(ts)`, `humanize_age(seconds)` ("12s ago", "3h ago"…)
  - `last_per_task(events)` — derive "last seen" per task from
    events.jsonl. Append-only audit log means latest entry wins
    naturally; no extra state needed.
- `fleet.dashboard.render`:
  - "⚠ Needs your input" highlight section above the task table when
    any task has status `needs_input`.
  - Task table gains **Workflow** + **Last seen** columns.
  - "Recent events (last 10)" section at the bottom.
  - Workflow shown in the header block too.
- `fleet status` — same enrichment (needs-input call-out + workflow +
  "seen" age + nicer event formatting).
- `driver_prompt`: rule added — "between long tool calls, emit a
  heartbeat (`fleet-agent event emit heartbeat`)". Still under the 40-line
  bloat tripwire.
- Explicit non-goal: no daemon, no auto-cleanup. forge's lifecycle
  layer (heartbeat / liveness / tamagotchi / janitor / custodian /
  leader_context) is replaced with **"surface the data, let the human
  decide"**. (design doc §10.2)
- Tests: 105 unittest cases pass (Phase 8 adds heartbeat 9 + dashboard 5).

### Phase 7 — `fleet leader` (2026-05-19)

The user-facing entry point from design doc §3 lands.

- `fleet leader [--project P] [--agent SPEC] [--attach]`:
  Creates a detached tmux session `fleet-<project>` with a single
  `leader` window in the project root and starts the agent CLI inside.
  Default agent: `claude:sonnet`.
- Single-instance per project: if the session already exists, prints
  the attach command and exits. `--attach` execs into `tmux attach`.
- Leader window inherits `FLEET_PROJECT` and `FLEET_STATE_DIR` env
  vars; cwd is set to the project root.
- `tmux.new_session(..., cwd=..., env=...)` extended to accept both.
- Emits a `leader_start` event with the chosen agent + session name.
- Tests: 90 unittest cases pass (+3 leader tests, 2 skipped if `tmux`
  missing).

### Phase 6 — spawn robustness: tmux env + prompt buffer (2026-05-19)

Sharpens `fleet spawn` for real-world tmux use. The forge-era prompt
race ("cat | head" sometimes lands before the agent TTY is ready) is
structurally removed.

- `fleet.tmux.new_window(..., env=...)` — pass `-e KEY=VAL` so each
  spawned window inherits `FLEET_TASK_ID` and `FLEET_STATE_DIR`. Drivers
  can now call `fleet ask` / `event emit` / `done` without `--task-id`.
- `fleet.tmux.load_buffer` / `paste_buffer` / `delete_buffer` — wrap the
  tmux paste-buffer machinery.
- `fleet spawn` preloads `driver-prompt.md` into a named tmux buffer
  (`fleet-task-<id>`). Default behavior shows manual paste instructions
  (safer). `--auto-prompt [--prompt-delay SEC]` opts into the old
  auto-paste after a delay.
- `driver_prompt`: BASE updated to mention the `FLEET_*` env vars so
  the driver knows it doesn't need `--task-id`.
- Tests: 87 unittest cases pass (Phase 6 adds 4 tmux integration tests,
  skipped if `tmux` isn't on PATH). The driver-prompt bloat tripwire
  still holds (< 40 lines).

### Phase 5 — workflow plugin system + `bare` / `git_worktree` (2026-05-19)

Spawn and done now run through a plugin hook layer (design doc §8). The
core orchestrator stays development-flow-agnostic; git-specific bits
live in an opt-in plugin.

- `fleet.plugins` package:
  - `load_workflow(state_dir)` resolves `project.yaml` ➜ plugin module.
    Custom plugins under `<state>/plugins/<name>.py` shadow built-ins.
  - `run_hook(module, hook_name, ctx)` — silent no-op if the hook
    isn't defined; `ctx` is a mutable dict carrying state_dir, task_id,
    topology, role, agent, etc.
  - `list_builtin()` / `list_custom(state_dir)`.
- Built-in plugins:
  - `bare` — default no-op for non-git projects.
  - `git_worktree` — creates `<state>/worktrees/task-<id>` on branch
    `task/<id>` from the project root; overrides spawn cwd; records
    worktree + branch in `task.yaml`.
- `fleet workflow list | show <name> | set <name>` — inspect and pick
  the active workflow plugin.
- Hooks wired into existing commands:
  - `fleet spawn`: calls `on_pre_spawn` before `save_task`; merges
    `ctx['task_extra']` into the task; honors `ctx['cwd']` as the tmux
    window's working directory.
  - `fleet done`: calls `on_post_done` after status flip (failures
    warn but never block).
- Tests: 83 unittest cases pass (Phase 5 adds 14: plugins 7, workflow
  CLI 5, git_worktree 2 — the last skipped if `git` is missing).

### Phase 4 — driver communication protocol (2026-05-19)

Drivers can now report up to the user without going through the leader
(design doc §7 — "leader は介在しない").

- `fleet.task_context.resolve(...)` — figure out which task a driver-side
  CLI call is acting on, from (1) explicit `--task-id`, (2) `FLEET_TASK_ID`
  env var, (3) cwd inspection (`<state>/tasks/task-<id>/`).
- `fleet.notify` — best-effort macOS Notification Center + Slack webhook
  dispatch, configured per-project via `.fleet-state/notify.yaml`.
  Failures warn-only, never raise.
- `fleet ask "<question>"` — record `needs_input` event, flip task status
  to `needs_input`, append to `questions.md`, fire notification.
  Non-blocking; the driver re-checks `inbox.md` on its own cadence.
- `fleet event emit <type> [--field K=V ...]` — append arbitrary audit
  events to `events.jsonl` tagged with the current task id.
- `fleet done [task-id]` — flip task status to `completed`, emit `done`
  event. Real cleanup (worktree / branch / tmux window) is delegated to
  workflow plugins (Phase 5).
- Tests: 69 unittest cases pass total (+ Phase 4: task_context 6,
  notify 5, ask 3, event 3, done 3).

### Phase 3 — topology YAML + `fleet spawn` (2026-05-19)

`fleet` can now actually spawn a driver. End-to-end:
`fleet init` → `fleet spawn <id> "<desc>"` → claude/codex CLI lives in a
tmux window, with task state + prompt prepared on disk.

**Topology layer (3a):**
- `vendor/yaml/`: PyYAML 6.0.3 pure-Python sources, MIT license preserved.
  `./fleet` prepends `vendor/` to `sys.path`, so `import yaml` works
  without `pip install`.
- `src/fleet/presets/`: 4 stock topologies — `solo`, `pair_review`,
  `multi_stage`, `race`. Cover roles / stages / candidates shapes.
- `fleet.topology`: `list_presets`, `list_custom`, `load_preset`,
  `load_custom`, `load(name, state_dir=)` (custom wins), `validate`
  (requires `name` + one of roles|stages|candidates).
- `fleet topology list` / `fleet topology show <name>` CLI.

**Spawn layer (3b):**
- `fleet.agents.parse_spec("claude:sonnet")` / `cli_command(...)` —
  resolve `vendor:model` specs into argv. claude + codex only (design
  doc §13.2).
- `fleet.driver_prompt.render(...)` — produces a compact (~30 line)
  initial prompt. Bloat tripwire test ensures it stays small.
- `fleet.tmux` — thin `subprocess` wrap around tmux session/window/keys.
  Mechanism only, no fleet vocabulary.
- `fleet spawn <id> "<description>"`:
  - Resolves topology + role + agent (with `--role` / `--agent` overrides).
  - Writes `task.yaml` (status=spawning, agent, topology, role).
  - Writes empty `inbox.md` / `outbox.md` + rendered `driver-prompt.md`.
  - Emits a `spawn` event.
  - Opens a tmux window and launches the agent CLI inside.
  - `--dry-run` skips the tmux step (used in tests / CI).
- Tests: 49 unittest cases pass total (+ Phase 3b: agents (8), driver_prompt
  (3), spawn (7)).

### Phase 2 — state writer + dashboard auto-rebuild (2026-05-19)

State updates are now race-safe and `dashboard.md` is regenerated on every write.

- `fleet.locking.atomic_write(path)` — flock-guarded context manager that
  writes via temp file + `os.replace` for atomic, all-or-nothing updates
  (design doc §5.4).
- `fleet.events.append_event(path, type, **fields)` — POSIX `O_APPEND`
  audit log helper for `events.jsonl`.
- `fleet.simple_yaml` — minimal flat `key: value` reader/writer (the real
  YAML parser lands in Phase 3 when topology files demand nesting).
- `fleet.state` extended:
  - `discover_state_dir(start)` walks parents looking for `.fleet-state/`
  - `load_project` / `save_project`
  - `list_tasks` / `load_task` / `save_task`
  - Every state write triggers `dashboard.rebuild`.
- `fleet.dashboard.render` / `rebuild` — render state into Markdown,
  marked **read-only** in the header (design doc §5.5).
- `fleet status [path] [--events N]` — print project info, task list,
  recent events.
- Tests: 22 cases pass (locking concurrency, events, state, dashboard,
  init, status).

### Phase 1 — skeleton + `fleet init` (2026-05-19)

Bootstrap the repository so a minimal `fleet init` works end-to-end.

- Repository skeleton:
  - `./fleet` — executable Python entrypoint (shebang, `git clone` and run, no `pip install`)
  - `src/fleet/` — Python package (`cli`, `state`, `commands/init`)
  - `tests/` — stdlib `unittest`-based smoke tests
  - `docs/design.md` — design document carried over from claude-forge
  - `.gitignore`, `README.md`
- Command:
  - `fleet init --name <name> [path]` — creates `.fleet-state/` with
    `project.yaml`, empty `events.jsonl`, empty `tasks/`. Rejects already-initialized
    projects and non-directory paths.
- Python 3.11+ required; no third-party dependencies (vendored libs land in later phases).
