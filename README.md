# agent-fleet

Hierarchical multi-vendor agent orchestration over tmux.

`fleet` lets a human leader collaborate with one or more **driver
agents** (claude / codex) inside tmux panes, with **team formation**
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
# If the current branch is behind its upstream, start warns but still continues.

# Launch the leader pane (claude by default)
/path/to/agent-fleet/fleet leader --attach

# In a separate shell — start a task (leader uses fleet-agent)
/path/to/agent-fleet/fleet-agent start 1 "Implement a hello-world script."

# Inspect overall state
/path/to/agent-fleet/fleet status
cat .fleet-state/dashboard.md

# When the driver is done with the task (leader cleanup)
/path/to/agent-fleet/fleet-agent cleanup 1 --archive
```

Requires Python ≥ 3.11. **No `pip install`** — any dependency we need
is vendored under `vendor/`.

---

## Goals

1. The user talks to the **leader** for task assignment, and directly
   to a **driver** when refining one task.
2. **Multi-vendor agents** (claude / codex) coexist in one project.
3. **Team formation** (solo / pair-review / multi-stage) is
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

### `fleet` — human CLI

| Command | Purpose |
|---|---|
| `fleet init --name <name> [path]` | Create `.fleet-state/` in `path` |
| `fleet preflight` | Verify Python / tmux / git / agent CLIs, including Codex trust/update warnings |
| `fleet leader [--project P] [--agent SPEC] [--attach]` | Launch / attach the leader pane |
| `fleet attach [<target>]` | Attach to leader or a task pane |
| `fleet status [path] [--events N]` | Print project info + tasks + recent events |
| `fleet log [<id>] [-n N] [--type T]` | Tail `events.jsonl` |
| `fleet formation list \| show <name>` | Inspect available formations |
| `fleet workflow list \| show <name> \| set <name>` | Inspect / pick the active workflow plugin |

### `fleet-agent` — agent CLI (leader / driver internal use)

Leader-side (run by the leader agent):

| Command | Purpose |
|---|---|
| `fleet-agent start <id> "<desc>" [--formation T] [--agent A] [--no-auto-paste]` | Start a new task (creates state + launches first stage driver; a detached deliverer pastes a prompt-file pointer once the pane is ready) |
| `fleet-agent start <id> --prompt-file PATH [--formation T] [--agent A]` | Start a new task using the description read from a file |
| `fleet-agent inbox <id> "<message>"` | Append a message to the driver's `inbox.md` |
| `fleet-agent send-prompt <id>` | Start a detached deliverer to paste a `driver-prompt.md` pointer when the task pane is ready |
| `fleet-agent cleanup <id> [--archive] [--force]` | Tear down a finished task (workflow + tmux + optional archive) |

Driver-side (run inside a driver pane — `FLEET_TASK_ID` / `FLEET_STATE_DIR` are
pre-set, so no `--task-id` is needed):

| Command | Purpose |
|---|---|
| `fleet-agent ask "<question>"` | Record `needs_input`, append `questions.md`, notify the user |
| `fleet-agent event emit <type> [--field K=V ...]` | Append an audit event |
| `fleet-agent done [--result approved\|changes-requested]` | Signal role completion; orchestrator advances the task |

When the active workflow is `git_worktree`, `fleet-agent start` checks the
current branch against its locally known upstream before creating the task
worktree. If the branch is behind, it prints a warning with the commit count and
continues; it does not fetch and it does not block offline starts.

Codex drivers are launched with a per-invocation config override
(`check_for_update_on_startup=false`) so Codex's interactive update prompt
does not consume the driver prompt. `fleet preflight` still warns when the
local Codex CLI is older than npm's latest `@openai/codex`, or when npm's global
install and the `codex` currently on `PATH` disagree.

---

## Layout

```
agent-fleet/
  fleet               # CLI entrypoint (executable Python script)
  src/fleet/          # cli, state, dashboard, ...
    commands/         # one module per subcommand
    plugins/          # bare, git_worktree
    presets/          # solo, pair_review, multi_stage
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
    formations/             # custom YAML formations (shadow built-ins)
    plugins/                # custom workflow plugins (shadow built-ins)
    worktrees/              # git_worktree plugin lives here
    tasks/
      task-<id>/
        task.yaml           # status / title / agent / workflow / ...
        inbox.md            # leader → driver
        outbox.md           # driver → leader
        questions.md        # `fleet-agent ask` records here
        driver-prompt.md    # initial prompt file; a pointer to it is pasted once the pane is ready
      _archive/             # cleanup --archive lands here
```

---

## License

TBD.
