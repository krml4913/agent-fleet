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

## Quickstart — the user's path

This is what using fleet actually feels like: a little one-time setup, then you
mostly **talk to the leader in chat**. You rarely type the per-task mechanics
yourself — the leader issues them for you. Commands assume you cloned
agent-fleet to `~/dev/agent-fleet`; adjust the path to match yours.

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
`agent-fleet/fleet-state/projects/trial/`. Run the rest of the commands from
inside the project directory — fleet resolves the project name from your cwd.

Optionally give each task its own git branch/worktree (the default is in-place):

```bash
~/dev/agent-fleet/fleet workspace set worktree
```

### 3. Launch the leader

```bash
~/dev/agent-fleet/fleet leader --attach
```

This creates the tmux session `fleet-trial` with the leader agent (default
`claude:opus`) running in it, and attaches you in the foreground. The session is
single-instance per project. Detach any time with `C-b d`; the leader keeps
running. This is the one pane you live in.

### 4. Talk to the leader

Here is the actual core of using fleet: **you describe the task in plain prose
to the leader in chat**, and the leader does the rest — it picks a formation,
chooses the agent(s), and spawns the driver(s) for you.

```
you ▸ Add a --json flag to the status command and cover it with a test.
      Use the pair_review formation.

leader ▸ Starting `status-json-flag` as pair_review (codex implements,
         claude reviews). I'll ping you when it needs your sign-off.
```

You do **not** normally run `fleet-agent start` yourself — the leader translates
your request into that call. From here on you mostly type prose, not shell
commands: steer the work by chatting with the leader.

### 5. Watch, intervene, and approve

Keep an eye on progress from any shell in the project:

```bash
~/dev/agent-fleet/fleet status                 # tasks + recent events
cat fleet-state/projects/trial/dashboard.md    # human-readable rollup (auto-updated)
```

To look over a driver's shoulder or take over, attach into its pane — this is
the core of the felt experience:

```bash
~/dev/agent-fleet/fleet attach status-json-flag   # a task's driver pane
~/dev/agent-fleet/fleet attach                     # the leader (default target)
```

You land directly in the live agent session — read its output, type into it,
correct its course, then detach with `C-b d`.

When a driver needs a decision it fires a notification (pane output alone never
reaches you), and formations with a `user_approval` gate pause the same way.
You make the call and **tell the leader**; the leader relays it (it never
self-approves). Saying "looks good, ship it" or "no, fix X first" in chat is all
you do — the leader runs the actual approve/reject for you.

That is the whole loop: **init → launch the leader → chat → watch / approve.**
You do not normally touch `fleet-agent start / inbox / approve / cleanup` —
those are the leader's job. The next section shows them anyway, for when you
want to understand the mechanics or hand-drive a task.

---

## Under the hood — driving it manually

> You don't normally type any of these. The leader runs them on your behalf
> when you chat with it. This section is a reference for understanding the
> moving parts — or for driving a task by hand without a leader.

Everything below uses the `hello-world` task id as the example.

### Dispatch a task

```bash
cd /tmp/trial
~/dev/agent-fleet/fleet-agent start hello-world "Implement a hello-world script." --formation solo
```

The first argument (`hello-world` here) is the **task id** you choose — a short
kebab-case slug (lowercase letters, digits, hyphens) that names the task. It is
not an auto-assigned number; it becomes the branch name, the state directory,
and the tmux window label, so pick something descriptive.

This writes the task state, renders a `driver-prompt.md`, opens a new tmux
window running the first stage's driver, and (by default) auto-pastes a pointer
to the prompt into the pane once the agent is ready. Pick the team shape with
`--formation` (`solo`, `pair_review`, `multi_stage`, or any custom one), and
override the first-stage agent with `--agent`. Use `--prompt-file PATH` to pass
a long description from a file instead of inline.

### Leave a driver an asynchronous note

Instead of attaching, drop a message into a driver's inbox:

```bash
~/dev/agent-fleet/fleet-agent inbox hello-world "Use argparse, not sys.argv parsing."
```

This appends a timestamped note to the task's `inbox.md` and wakes the pane.

### Approve or reject a gate

When a driver calls `fleet-agent ask`, or a stage with a `user_approval` gate
finishes, the task flips to `awaiting_orders`. Relay the decision:

```bash
~/dev/agent-fleet/fleet-agent approve hello-world   # approve the pending gate
~/dev/agent-fleet/fleet-agent reject hello-world    # reject; the stage returns to work
```

In a `pair_review` formation, the implementer hands off to an AI reviewer
automatically; only the final user-approval gate needs a human.

### Finish and clean up

When a task is done, tear it down (and optionally archive its state):

```bash
~/dev/agent-fleet/fleet-agent cleanup hello-world --archive
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
