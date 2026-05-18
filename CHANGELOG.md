# Changelog

All notable changes to agent-fleet are recorded here. Entries are grouped by
development **Phase** (per `docs/design.md`) until the first tagged release.

## [Unreleased]

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
