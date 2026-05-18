# Changelog

All notable changes to agent-fleet are recorded here. Entries are grouped by
development **Phase** (per `docs/design.md`) until the first tagged release.

## [Unreleased]

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
