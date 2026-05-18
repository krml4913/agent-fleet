# agent-fleet

Hierarchical multi-vendor agent orchestration over tmux.

`fleet` lets a human leader collaborate with one or more **driver
agents** (claude / codex) inside tmux panes, with **team topology**
defined per-project in YAML and an opt-in **workflow plugin** layer
for git / PR / cleanup mechanics.

Designed as the successor to [claude-forge](https://github.com/krml4913/claude-forge);
see [docs/design.md](docs/design.md) for the rationale.

> Status: WIP — Phase 1-9 landed.  Bootstrapped 2026-05-19.

---

## Quick start

```bash
git clone <this repo> && cd agent-fleet

# Verify the toolbelt
./fleet preflight

# Init a project (creates .fleet-state/ inside the target dir)
mkdir -p /tmp/trial && cd /tmp/trial
git init -b main && echo hi > README.md && git add -A && git -c user.email=t@x -c user.name=t commit -m init
/path/to/agent-fleet/fleet init --name trial .

# Optional: pick the git-worktree workflow so each task gets its own branch
/path/to/agent-fleet/fleet workflow set git_worktree

# Launch the leader pane (claude by default)
/path/to/agent-fleet/fleet leader --attach

# In a separate shell — spawn a driver for a task
/path/to/agent-fleet/fleet spawn 1 "Implement a hello-world script."

# Inspect overall state
/path/to/agent-fleet/fleet status
cat .fleet-state/dashboard.md

# When the driver is done with the task
/path/to/agent-fleet/fleet done 1
/path/to/agent-fleet/fleet cleanup 1 --archive
```

Requires Python ≥ 3.11. **No `pip install`** — any dependency we need
is vendored under `vendor/`.

---

## Goals

1. The user talks to the **leader** for task assignment, and directly
   to a **driver** when refining one task.
2. **Multi-vendor agents** (claude / codex) coexist in one project.
3. **Team topology** (solo / pair-review / multi-stage / race) is
   chosen per task via YAML.
4. **Workflow plugins** keep git-specific bits (worktrees, branches,
   PRs) out of the core; non-coding workflows can plug in the same
   way.

## Non-goals

- Fully autonomous operation. Human intervention is the point.
- General-purpose agent orchestration. The core targets coding.
- `pip install`. `git clone` and run `./fleet`.

---

## Commands

### Project / leader

| Command | Purpose |
|---|---|
| `fleet init --name <name> [path]` | Create `.fleet-state/` in `path` |
| `fleet leader [--project P] [--agent SPEC] [--attach]` | Launch / attach the leader pane |
| `fleet status [path] [--events N]` | Print project info + tasks + recent events |
| `fleet preflight` | Verify Python / tmux / git / agent CLIs |

### Tasks (from the leader / your shell)

| Command | Purpose |
|---|---|
| `fleet spawn <id> "<desc>" [--topology T] [--role R] [--agent A] [--auto-prompt]` | Spawn a driver for a new task |
| `fleet cleanup <id> [--archive] [--force]` | Tear down a finished task (workflow + tmux + optional archive) |

### Driver-side (run inside a driver pane)

`FLEET_TASK_ID` and `FLEET_STATE_DIR` are pre-set, so no `--task-id`
is needed:

| Command | Purpose |
|---|---|
| `fleet ask "<question>"` | Record `needs_input`, append `questions.md`, notify the user |
| `fleet event emit <type> [--field K=V ...]` | Append an audit event |
| `fleet done` | Mark the task `completed` |

### Configuration

| Command | Purpose |
|---|---|
| `fleet topology list \| show <name>` | Inspect available topologies |
| `fleet workflow list \| show <name> \| set <name>` | Inspect / pick the active workflow plugin |

---

## Layout

```
agent-fleet/
  fleet               # CLI entrypoint (executable Python script)
  src/fleet/          # cli, state, dashboard, ...
    commands/         # one module per subcommand
    plugins/          # bare, git_worktree
    presets/          # solo, pair_review, multi_stage, race
  vendor/             # PyYAML pure-Python (no pip required)
  tests/              # stdlib unittest, 111+ cases
  docs/design.md      # design document
  CHANGELOG.md
```

## Layout of a project's `.fleet-state/`

```
<project-root>/
  .fleet-state/
    project.yaml            # name / workflow / created_at
    notify.yaml             # macOS + Slack settings (optional)
    events.jsonl            # append-only audit log
    dashboard.md            # auto-generated read-only view
    topologies/             # custom YAML topologies (shadow built-ins)
    plugins/                # custom workflow plugins (shadow built-ins)
    worktrees/              # git_worktree plugin lives here
    tasks/
      task-<id>/
        task.yaml           # status / title / agent / workflow / ...
        inbox.md            # leader → driver
        outbox.md           # driver → leader
        questions.md        # `fleet ask` records here
        driver-prompt.md    # initial prompt sent to the agent
      _archive/             # cleanup --archive lands here
```

---

## License

TBD.
