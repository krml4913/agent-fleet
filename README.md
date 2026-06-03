# agent-fleet

*[日本語版 README](README.ja.md)*

**agent-fleet** is a hierarchical, multi-vendor agent orchestrator that runs
driver agents (claude / codex) inside tmux panes. You talk to a single
**leader** agent and lightly toss it tasks; the leader spins up **driver**
agents to do the work, each in its own pane, following a per-project **team
formation** defined in YAML. Many tasks run concurrently. Everything is
keyboard-only over tmux, and at any moment you can attach into a driver's pane
to read what it's doing, nudge it, or take over mid-task. It is built for
humans-in-the-loop coding work, not lights-out autonomy.

Requires **Python ≥ 3.11** and **tmux**. There is **no `pip install`** — clone
the repo and run `./fleet`. Any Python dependency is vendored under `vendor/`.

---

## Concepts in 60 seconds

- **Leader** — the agent you chat with. One per project, living in a tmux
  session named `fleet-<project>`. It assigns tasks and relays your decisions.
  It does not write code itself; it dispatches drivers.
- **Driver** — an agent that actually works a single task, in its own tmux
  window. Drivers can be claude or codex. You can attach into any driver pane.
- **Formation** — a YAML file describing *who works a task and how*: the
  sequence of stages, which agent runs each one, whether there is AI peer
  review, and where human approval gates sit. Three are shipped: `solo`,
  `pair_review`, `multi_stage`. See [docs/formations.md](docs/formations.md).
- **Workspace** — how a task's working tree is isolated. `worktree` gives each
  task its own git worktree/branch; `none` works in place. Set per project.

The leader and drivers communicate through files in the project's state
directory (`inbox.md`, `outbox.md`, `questions.md`, an append-only
`events.jsonl`, an auto-generated `dashboard.md`). You drive the leader; the
leader drives the drivers via the `fleet-agent` CLI.

---

## Tutorial: from clone to first task

This walkthrough takes a fresh checkout to a running task you can watch and
intervene in. Commands assume you cloned agent-fleet to `~/dev/agent-fleet`;
adjust the path to match yours.

### 1. Clone and verify the environment

```bash
git clone <this-repo-url> agent-fleet
cd agent-fleet

./fleet preflight
```

`preflight` checks Python, tmux, git, and the agent CLIs (`claude`, `codex`)
on your `PATH`. It also warns if your Codex CLI is out of date or if its
directory trust is not set up. Resolve anything it flags before continuing.

### 2. Initialize a project

Point fleet at any git repository you want agents to work in. Here we make a
throwaway one:

```bash
mkdir -p /tmp/trial && cd /tmp/trial
git init -b main
echo hi > README.md
git add -A && git -c user.email=t@x -c user.name=t commit -m init

~/dev/agent-fleet/fleet init --name trial .
```

`init` registers the project in fleet's registry and creates its state under
`agent-fleet/fleet-state/projects/trial/`. It also offers to copy formation
templates in; pass `--formation solo,pair_review` (or `--no-formation`) to skip
the interactive picker. You can run the rest of the commands from inside the
project directory — fleet resolves the project name from your cwd.

### 3. Choose a workspace mode (optional)

By default tasks run in place. To give each task its own git branch/worktree:

```bash
~/dev/agent-fleet/fleet workspace set worktree
~/dev/agent-fleet/fleet workspace list   # confirm the active mode
```

With `worktree`, `fleet-agent start` warns (but continues) if your branch is
behind its known upstream — it never fetches and never blocks an offline start.

### 4. Launch the leader

```bash
~/dev/agent-fleet/fleet leader --attach
```

This creates the tmux session `fleet-trial` with the leader agent (default
`claude:opus`) running in it, and attaches you to it in the foreground. The
session is single-instance per project: if it already exists, fleet just prints
the attach command. Override the agent with `--agent claude:sonnet` etc.

You are now in a normal tmux session. Detach any time with `C-b d`; the leader
keeps running.

### 5. Dispatch a task

You normally *tell the leader in chat* what you want, and the leader issues the
`fleet-agent start` call for you. To see the mechanics directly, run it
yourself from a second shell:

```bash
cd /tmp/trial
~/dev/agent-fleet/fleet-agent start 1 "Implement a hello-world script." --formation solo
```

This writes the task state, renders a `driver-prompt.md`, opens a new tmux
window running the first stage's driver, and (by default) auto-pastes a pointer
to the prompt into the pane once the agent is ready. Pick the team shape with
`--formation` (`solo`, `pair_review`, `multi_stage`, or any custom one), and
override the first-stage agent with `--agent`. Use `--prompt-file PATH` to pass
a long description from a file instead of inline.

### 6. Watch progress

From any shell in the project:

```bash
~/dev/agent-fleet/fleet status                 # project + task list + recent events
~/dev/agent-fleet/fleet log 1                   # tail this task's events
cat fleet-state/projects/trial/dashboard.md     # human-readable rollup
```

`status` shows each task's status (`in_progress`, `awaiting_orders`, `done`,
…) and the latest events. `dashboard.md` is regenerated automatically and is a
good at-a-glance view to keep open.

### 7. Attach into a driver to intervene

This is the core of the felt experience. To look over a driver's shoulder or
take over:

```bash
~/dev/agent-fleet/fleet attach 1      # attach to task 1's driver pane
~/dev/agent-fleet/fleet attach        # attach to the leader (default target)
```

You land directly in the agent's pane. Read its output, type into it, correct
its course — it is a live agent session. Detach with `C-b d` when done. To
leave the driver an asynchronous note instead of attaching, the leader can drop
a message in its inbox:

```bash
~/dev/agent-fleet/fleet-agent inbox 1 "Use argparse, not sys.argv parsing."
```

### 8. Answer questions and approval gates

When a driver needs you, it calls `fleet-agent ask`, which flips the task to
`awaiting_orders` and fires a notification — pane output alone never reaches
you. Formations with a `user_approval` gate pause the same way when a stage
finishes. You make the call; the **leader relays it** (the leader never
self-approves):

```bash
~/dev/agent-fleet/fleet-agent approve 1     # approve the pending gate
~/dev/agent-fleet/fleet-agent reject 1      # reject; the stage returns to work
```

In a `pair_review` formation, the implementer hands off to an AI reviewer
automatically; only the final user-approval gate needs you.

### 9. Finish and clean up

When a task is done, tear it down (and optionally archive its state):

```bash
~/dev/agent-fleet/fleet-agent cleanup 1 --archive
```

This runs the workspace cleanup hook (removing the worktree if you used one),
kills the task's tmux window, and drops its prompt buffer. It refuses to run on
a non-terminal task unless you pass `--force`.

To remove the whole project from fleet later:

```bash
~/dev/agent-fleet/fleet rm trial --yes
```

That unregisters the project and deletes its state. Active tmux sessions are
not killed for you — fleet warns if it spots one still running.

---

## Command reference

### `fleet` — the human CLI

| Command | Purpose |
|---|---|
| `fleet preflight` | Check Python / tmux / git / agent CLIs (incl. Codex trust + update warnings). |
| `fleet init [path] [--name N] [--formation N] [--no-formation]` | Register a project and create its state directory. |
| `fleet leader [--project P] [--agent SPEC] [--attach]` | Launch / attach the leader pane (default agent `claude:opus`). |
| `fleet attach [target] [--project P]` | Attach to the leader (default) or a task driver pane. |
| `fleet status [name] [--all] [--events N]` | Print project info, task list, recent events. |
| `fleet log [task_id] [-n N] [--type T]` | Tail `events.jsonl`, optionally filtered by task / type. |
| `fleet formation list \| show <name> \| init --from <template>` | Inspect or create formations. |
| `fleet workspace list \| set <mode>` | Show or set the workspace mode (`worktree` / `none`). |
| `fleet rm <name> [--yes]` | Unregister a project and delete its state. |

### `fleet-agent` — the agent CLI

Run by the leader and drivers. Not intended for routine direct human use, but
useful to understand the moving parts.

Leader-side:

| Command | Purpose |
|---|---|
| `fleet-agent start <id> "<desc>" [--formation F] [--agent A] [--title T] [--prompt-file P]` | Start a task: write state, render the prompt, open the first driver pane. |
| `fleet-agent inbox <id> "<msg>"` | Append a timestamped note to a driver's `inbox.md` and wake the pane. |
| `fleet-agent send-prompt <id>` | (Re)deliver the `driver-prompt.md` pointer into the task pane. |
| `fleet-agent approve <id>` | Relay user approval for a pending `user_approval` gate. |
| `fleet-agent reject <id>` | Relay user rejection; the stage returns to implementation. |
| `fleet-agent cleanup <id> [--archive] [--force]` | Tear down a finished task. |

Driver-side (run inside a driver pane; `FLEET_TASK_ID` is pre-set):

| Command | Purpose |
|---|---|
| `fleet-agent ask "<question>"` | Flip the task to `awaiting_orders`, record the question, notify the user. |
| `fleet-agent inbox-read` | Read `inbox.md` and emit an `inbox_seen` ack. |
| `fleet-agent event emit <type> [--field K=V ...]` | Append an audit event. |
| `fleet-agent done [--result approved\|changes-requested]` | Mark the stage done; the orchestrator advances the task. |

---

## The shipped formations

| Formation | Shape |
|---|---|
| `solo` | One driver works the task end to end. No review, no gates. |
| `pair_review` | Implementer → AI peer review (up to 3 rounds) → user sign-off. The showcase multi-vendor flow (e.g. codex implements, claude reviews). |
| `multi_stage` | Design stage → user approval → implementation stage with review and approval. |

Formations are plain YAML you can edit per project — swap agents, add a
reviewer, drop a gate. Full schema and a leader's cookbook are in
[docs/formations.md](docs/formations.md).

---

## Project state layout

After `fleet init --name trial`, state lives under the agent-fleet checkout:

```
agent-fleet/fleet-state/
  projects.yaml                 # registry of known projects
  projects/trial/
    project.yaml                # name / workspace mode / created_at
    events.jsonl                # append-only audit log
    dashboard.md                # auto-generated read-only view
    formations/                 # this project's formations (YAML)
    tasks/
      task-1/
        task.yaml               # status / title / agent / formation / ...
        driver-prompt.md        # the rendered initial prompt
        inbox.md                # leader -> driver
        outbox.md               # driver -> leader
        questions.md            # `fleet-agent ask` records here
      _archive/                 # cleanup --archive lands here
```

---

## License

MIT. See [LICENSE](./LICENSE).
