# agent-fleet

*[日本語版 README](README.ja.md)*

**agent-fleet** is a hierarchical, multi-vendor agent orchestrator that runs
driver agents (claude / codex) inside terminal-multiplexer panes (zellij by
default, tmux optional). You talk to a single
**leader** agent and lightly toss it tasks; the leader spins up **driver**
agents to do the work, each in its own pane, following a per-project **team
formation** defined in YAML. Many tasks run concurrently. Everything is
keyboard-only inside the multiplexer, and at any moment you can attach into a driver's pane
to read what it's doing, nudge it, or take over mid-task. It is built for
humans-in-the-loop coding work, not lights-out autonomy.

Requires **Python ≥ 3.11** and a terminal multiplexer: **zellij ≥ 0.45.0**
(the default on every platform) or **tmux** (opt-in; see
[Choosing the multiplexer](#choosing-the-multiplexer) and
[Windows](#windows)). There is
**no `pip install`** — clone the repo and run `./fleet` (`fleet.cmd` on
Windows). Any Python dependency is vendored under `vendor/`.

---

## Concepts in 60 seconds

- **Leader** — the agent you chat with. One per project, living in a multiplexer
  session named `fleet-<project>`. It assigns tasks and relays your decisions.
  It does not write code itself; it dispatches drivers.
- **Driver** — an agent that actually works a single task, in its own multiplexer
  window (a tab on zellij). Drivers can be claude or codex. You can attach into any driver pane.
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

`preflight` checks Python, the terminal multiplexer (and prints which backend
is selected and where that choice came from: `env` / `config` / `default`), git,
and the agent CLIs (`claude`, `codex`) on your `PATH`. It also warns if your Codex CLI is out of date or if its
directory trust is not set up, and if Claude Code has not yet been trusted for the repo
(otherwise the first claude task stops at its trust prompt). Resolve anything it flags before continuing.

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

This creates the multiplexer session `fleet-trial` with the leader agent (default
`claude:opus`; persistently configurable, see
[Choosing the leader's agent](#choosing-the-leaders-agent)) running in it, and
attaches you in the foreground. The session is single-instance per project.
Detach any time (tmux: `C-b d`; zellij: `Ctrl o`, then `d`); the leader keeps
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
~/dev/agent-fleet/fleet dashboard              # cross-project GUI view (opens in browser)
```

To look over a driver's shoulder or take over, attach into its pane — this is
the core of the felt experience:

```bash
~/dev/agent-fleet/fleet attach status-json-flag   # a task's driver pane
~/dev/agent-fleet/fleet attach                     # the leader (default target; --session LABEL for another leader session)
```

You land directly in the live agent session — read its output, type into it,
correct its course, then detach (tmux: `C-b d`; zellij: `Ctrl o`, then `d`).

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
and the multiplexer window label, so pick something descriptive.

This writes the task state, renders a `driver-prompt.md`, opens a new multiplexer
window (a tab on zellij) running the first stage's driver, and (by default) auto-pastes a pointer
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
kills the task's multiplexer window, and drops its prompt buffer. It refuses to run on
a non-terminal task unless you pass `--force`.

To remove the whole project from fleet later:

```bash
~/dev/agent-fleet/fleet rm trial --yes
```

That unregisters the project and deletes its state. Active multiplexer sessions are
not killed for you — fleet warns if it spots one still running.

---

## Command reference

### `fleet` — the human CLI

| Command | Purpose |
|---|---|
| `fleet preflight` | Check Python / multiplexer (zellij or tmux; shows which is selected and why) / git / agent CLIs (incl. Codex / Claude trust and Codex update warnings; extra checks on Windows). |
| `fleet config [get <key> \| set <key> <value> \| unset <key>]` | Show / read / write the global config (`fleet-state/global/config.yaml`). Keys: `mux` = `zellij` \| `tmux` (see [Choosing the multiplexer](#choosing-the-multiplexer)); `leader_agent` = a `vendor:model` spec or an agent alias, the default agent for `fleet leader` (see [Choosing the leader's agent](#choosing-the-leaders-agent)); `leader_delivery` = `send_message` (default) \| `pane`, how driver notifications reach a claude leader (read when `fleet leader` starts); `agent_aliases.<name>` = an agent alias (see [Agent aliases](#agent-aliases)). |
| `fleet init [path] [--name N] [--formation N] [--no-formation]` | Register a project and create its state directory. |
| `fleet leader [--name LABEL] [--agent SPEC] [--attach]` | Launch / attach the leader session `fleet-<LABEL>` (default label `main`). Agent precedence: `--agent` > `leader_agent` in the global config > built-in default `claude:opus`. |
| `fleet attach [target] [--project P] [--session LABEL]` | Attach to a task's driver pane (in the session that owns the task, `fleet-<owner_session>`; `--project` locates the task) or, by default, to the `leader` pane of `fleet-<LABEL>` (`--session`, default `$FLEET_SESSION`, else `main`). Lists the live sessions if the target one isn't running. |
| `fleet status [name] [--all] [--unscoped] [--events N]` | Print project info, task list, recent events. With `--all`, filters to the session's scope by default; `--unscoped` shows all projects. |
| `fleet sessions` | List leader sessions and their in-flight tasks across all projects. |
| `fleet dashboard [--no-open]` | Render & open the cross-project HTML dashboard (`fleet-state/global/dashboard.html`). |
| `fleet edit [--project P] [--no-browser]` | Open a transient localhost editor for runtime formations and roles. |
| `fleet scope [label] [--set/--add/--rm/--clear]` | View or edit the set of projects a leader session is responsible for. |
| `fleet log [task_id] [-n N] [--type T]` | Tail `events.jsonl`, optionally filtered by task / type. |
| `fleet formation list \| show <name>` | Inspect runtime formations and template seed sources. |
| `fleet formation seed <name> [--global] [--project P] [--force]` | Copy a shipped formation seed into the project (default) or global tier; refuses to overwrite unless `--force`. |
| `fleet role seed <name> [--global] [--project P] [--force]` | Copy a shipped role prompt (`docs/prompts/roles/`) into the project (default) or global tier; refuses to overwrite unless `--force`. |
| `fleet workspace list \| set <mode>` | Show or set the workspace mode (`worktree` / `none`). |
| `fleet notify [--project P] [on\|off\|status]` | Show (no arg / `status`) or set the opt-in leader-pane push (`notify_leader_on_driver_done` in `project.yaml`, default off): when on, a driver's `done` / approval gate **and its `fleet-agent ask` question** are injected into the owning leader's pane (the ask line tells the leader to answer with `fleet-agent inbox <id> "<answer>" --project P`). Takes effect on the next `done` / `ask`. When a claude driver reports to a claude leader started by `fleet leader`, nothing is typed into the leader's pane: `done` / `ask` print the message and the driver sends it to the leader with Claude Code's `SendMessage`, so a draft in the leader's composer is never touched. `fleet config set leader_delivery pane` turns this off; a leader running from before this change keeps pane typing until it is restarted. |
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
| `fleet-agent reject <id> [--reason TEXT \| --reason-file PATH]` | Relay user rejection; the stage returns to implementation. The driver's inbox gets a `[fleet reject]` note carrying the reason. |
| `fleet-agent cleanup <id> [--archive] [--force] [--allow-from-driver]` | Tear down a finished task. |
| `fleet-agent merge <id> [--squash] [--keep] [--force] [--allow-from-driver]` | Merge the task's PR, then tear down and archive. |

`merge` / `cleanup` are leader-only and refuse to run from a driver pane (detected via `FLEET_TASK_ID`); `--allow-from-driver` overrides that soft guard on purpose (`--force` does not).

Driver-side (run inside a driver pane; `FLEET_TASK_ID` is pre-set):

| Command | Purpose |
|---|---|
| `fleet-agent ask "<question>"` | Flip the task to `awaiting_orders`, record the question, notify the user (and, with `fleet notify on`, the owning leader's pane). |
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
  global/
    dashboard.html              # cross-project GUI view (auto-generated, open with fleet dashboard)
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

## Choosing the multiplexer

> **Changed default — existing macOS/Linux users, read this.** The built-in
> default multiplexer is now **zellij on every platform**. It used to be tmux
> everywhere except Windows. To keep using tmux, do **one** of these:
>
> ```bash
> ./fleet config set mux tmux     # persistent (writes fleet-state/global/config.yaml)
> FLEET_MUX=tmux ./fleet ...      # per shell / per command
> ```
>
> Do this **before** your next fleet command if you have live tmux sessions:
> fleet only looks for sessions in the selected backend, so under the new
> default it would not see your running `fleet-<label>` tmux sessions.
> zellij on macOS/Linux is less exercised than tmux there
> ([#258](https://github.com/krml4913/agent-fleet/issues/258)).

The backend is chosen once per process; the first hit wins:

1. **`FLEET_MUX=tmux|zellij`** in the environment.
2. **`mux:`** in the global config, `fleet-state/global/config.yaml`
   (`$FLEET_HOME/global/config.yaml`).
3. The built-in default: **zellij**, on all platforms.

Manage the config with the CLI rather than by hand:

```bash
./fleet config                  # print every key, its value and where it comes from
./fleet config get mux          # print one value
./fleet config set mux tmux     # tmux | zellij; unknown keys / values are rejected
```

`fleet config` / `get` report the config layer (the file, else the default); an
active `FLEET_MUX` is noted, and `fleet preflight` shows the backend actually
selected and its source. `FLEET_NO_MUX` and `FLEET_ZELLIJ` are unchanged. A
missing config file just means the defaults; an unreadable or invalid one only
prints a warning and is ignored — it never stops a command.

---

## Choosing the leader's agent

`fleet leader` launches the leader pane with an agent spec (`vendor:model`,
e.g. `claude:opus`, `claude:claude-opus-5-5`, `codex:gpt-5.5`). Precedence,
first hit wins:

1. **`fleet leader --agent <spec>`** on the command line.
2. **`leader_agent:`** in the global config, `fleet-state/global/config.yaml`.
3. The built-in default: **`claude:opus`**.

```bash
./fleet config set leader_agent claude:claude-opus-5-5   # persistent
./fleet leader --agent claude:claude-opus-5-5             # one-off, this launch only
```

The value is validated the same way as `--agent` (an unknown vendor is
rejected, listing the supported ones); a missing or invalid `leader_agent` in
the config file only warns and falls back to the built-in default, like every
other global config key. Both `--agent` and `leader_agent` also accept an
[agent alias](#agent-aliases) (`./fleet config set leader_agent deep`); it is
resolved to the full spec at launch, and `fleet config` shows both
(`leader_agent: deep -> claude:opus (config)`).

---

## Agent aliases

An agent alias is a short name for a whole `vendor:model` spec, defined once in
the global config and usable anywhere a spec goes: a formation stage's `agent`
and `peer_review.agent`, `fleet-agent start --agent`, `fleet leader --agent`,
and the `leader_agent` config key.

```bash
./fleet config set agent_aliases.fast claude:sonnet
./fleet config set agent_aliases.deep claude:opus
./fleet config get agent_aliases.deep       # -> claude:opus
./fleet config unset agent_aliases.fast
./fleet config                              # lists every alias
```

```yaml
# formation stage
- role: implementer
  agent: fast
  peer_review:
    role: code-reviewer
    agent: deep
```

- An alias maps to one full `vendor:model` spec; alias-to-alias chains are
  rejected. Alias names use letters, digits, `_` and `-` only (never `:`), so
  they can't collide with a real spec.
- An alias is resolved **once**, when the spec enters task / leader state:
  `task.yaml`, events, the dashboard and cost/usage carry the resolved spec
  (the alias name is kept next to it as `agent_alias`). Changing an alias later
  never changes a task or leader that is already running.
- An unknown alias is an error naming the known aliases — at `fleet-agent
  start`, `fleet leader`, `fleet config set leader_agent`, and formation
  validation (`fleet formation show`).
- Aliases are global only (no per-project layer yet).

---

## macOS

Install zellij with `brew install zellij` — Homebrew bottles are fetched with
curl and carry no quarantine attribute.

A zellij release binary downloaded with a browser instead inherits Apple's
`com.apple.quarantine` attribute. Left unchecked, Gatekeeper would block the
first exec with a "cannot verify developer" dialog and kill the process —
whose **Move to Trash** button deletes the binary. `fleet preflight` (and the
zellij backend itself) checks for the attribute *before* exec'ing and refuses
to run a quarantined binary instead, reporting the fix below — so that dialog
should never appear from fleet.

- **Fix:** confirm where the binary came from, then either clear the flag
  (`xattr -d com.apple.quarantine <path>`) or re-download with `curl` instead
  of a browser (curl downloads carry no quarantine attribute). Re-signing with
  `codesign` does not help — release binaries are already validly ad-hoc
  signed. Don't disable Gatekeeper (`spctl --master-disable`) to work around
  this.
- **Diagnose:** `xattr -l <path>` shows whether the quarantine attribute is
  present.

---

## Windows

fleet runs natively on Windows (no WSL), using
[zellij](https://zellij.dev/) instead of tmux. Leader and driver panes live in
a zellij session named `fleet-<label>`, and each driver window is a zellij
**tab**. Everything else — formations, state files, the `fleet` /
`fleet-agent` commands — is the same as on macOS/Linux.

### Requirements

- **Python ≥ 3.11** (the `py` launcher or `python` on `PATH`).
- **zellij ≥ 0.45.0**, the native Windows build. Older versions are rejected
  (0.44.x lacks `new-tab --no-focus`).
- **Git for Windows.**
- The agent CLIs you use (`claude`, `codex`), **on `PATH`** (see below).

Install zellij with `winget install Zellij.Zellij`, or unzip a Windows build
from the [zellij releases](https://github.com/zellij-org/zellij/releases) into
a directory on `PATH`.

### Setup

```powershell
git clone <this-repo-url> D:\dev\agent-fleet     # a path WITHOUT spaces
git config --global core.longpaths true
D:\dev\agent-fleet\fleet.cmd preflight
```

- **Run fleet through `fleet.cmd` / `fleet-agent.cmd`** (from PowerShell or
  cmd), or as `python fleet …`. The extensionless `fleet` / `fleet-agent`
  scripts are not directly executable on Windows. The `.cmd` shims prefer
  `py -3`, fall back to `python`, and set `PYTHONUTF8=1`. Agents call
  `fleet-agent.cmd` themselves: fleet embeds its path in their prompts.
- **Clone to a path without spaces.** The `fleet-agent` path is embedded
  unquoted in prompts, so a space breaks the agents' calls.
- **`core.longpaths`** keeps deep worktree paths under
  `fleet-state/projects/<p>/worktrees/` from hitting `MAX_PATH`.
- **Agent CLIs must be on `PATH`.** Windows does not expand `~` in `PATH`, so
  an entry like `~/.local/bin` works in Git Bash but not in PowerShell, cmd,
  or a zellij pane. Add the real directory (e.g. `%USERPROFILE%\.local\bin`)
  instead.
- **`FLEET_MUX=tmux|zellij`** overrides the backend (see
  [Choosing the multiplexer](#choosing-the-multiplexer)). The default is zellij
  on every platform.

### What `fleet preflight` checks on Windows

On top of the usual checks: the zellij version (fails below 0.45.0; ⚠ on
0.45.0–0.45.1, where the zellij#5594 workaround is active), a clone path
without spaces, `core.longpaths` (with the fix command), and that
`fleet-agent.cmd` exists. `claude` / `codex` are shown with their resolved
absolute paths. A CLI found only outside `PATH` (in `%USERPROFILE%\.local\bin`
or via a `~`-prefixed `PATH` entry) is flagged ⚠, because agent panes may not
find it.

### Attaching under zellij

`fleet leader --attach` and `fleet attach [<task>]` run `zellij attach
fleet-<label>`. Detach with zellij's `Ctrl o`, then `d`. Every zellij client
has its own focus, so attaching never moves anyone else's view. But fleet can
steer a client to a tab only while that client is the only one attached:

- If no other client is attached, `fleet attach <task>` lands on the task's
  tab.
- If another client is attached (e.g. you are already watching the leader in
  another terminal), fleet prints the task's tab number instead, and you
  switch yourself: `Ctrl t`, then the number.

### Known limitations

- **zellij 0.45.0–0.45.1:** tabs created while no client is attached are
  discarded ([zellij#5594](https://github.com/zellij-org/zellij/issues/5594)).
  fleet works around it by briefly attaching a hidden client while it opens a
  driver tab (`FLEET_ZELLIJ_TEMP_CLIENT=0|1` forces the workaround off / on).
- **claude's workspace-trust dialog.** The first claude task in a newly
  registered project stops at claude's "Is this a project you created or one you
  trust?" dialog (claude remembers the answer per repo root). fleet never answers
  it, but it is loud: `fleet-agent start` and `fleet preflight` warn beforehand
  (read-only look at `~/.claude.json`), and if the pane still stops there the task
  goes `awaiting_orders` with a notification saying it is waiting on the workspace
  trust prompt. Run `claude` once in the repo and accept it — or attach and choose
  "Yes, I trust this folder" — and the prompt is then delivered automatically.
- **Verify commands run under `cmd.exe`** on Windows by default, so a
  `verify` command must be valid cmd syntax — unless the formation sets
  `verify.shell` (`bash` = Git Bash, `pwsh`, `powershell`, `sh`, `cmd`; see
  [docs/formations.md](docs/formations.md) §2.5).
- **codex under zellij is not verified yet.** claude drivers are.

Desktop notifications use a Windows toast (on by default; set
`windows: {enabled: false}` in the project's `notify.yaml` to turn it off).
Out of the box the toast shows as **"Windows PowerShell"** (it borrows
PowerShell's AppUserModelID) and clicking it does nothing. Run
`fleet.cmd notify setup-windows` once, per user, to register a fleet-owned
sender name and click-to-attach:

```powershell
D:\dev\agent-fleet\fleet.cmd notify setup-windows
```

This writes two `HKCU`-only registry keys (no admin rights): an
`AppUserModelId\agent-fleet` key (so the toast shows as "agent-fleet"
instead of "Windows PowerShell") and a `fleet://` URL protocol handler
(so a `done` / `ask` / approval toast that carries a task can be clicked to
open a terminal attached to that task's pane). `fleet.cmd notify
teardown-windows` removes exactly what setup created. `fleet preflight`
reports whether setup has been done (informational only — unconfigured
machines keep working exactly as before). See
[`fleet.windows_notify_setup`](src/fleet/windows_notify_setup.py) for exactly
what is written.

The investigation behind this port and the remaining follow-ups are in
[docs/windows-support.md](docs/windows-support.md).

---

## License

MIT. See [LICENSE](./LICENSE).
