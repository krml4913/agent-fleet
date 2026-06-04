# agent-fleet Design

> Design documentation for `agent-fleet`. This document records
> **settled design decisions**. Open questions and unresolved design
> issues are tracked in GitHub Issues.
>
> Last updated: 2026-05-29

---

## 1. Overview

### 1.1 Mission

> **Users delegate tasks to a leader and hash out task-level requirements
> directly with a driver. The leader combines multi-vendor agents
> (claude / codex) and drives development using the team formation
> defined for each project.**

### 1.2 The Three Pillars

| Pillar | Description |
|---|---|
| **1. Hierarchical dialogue UI** | Task delegation goes to the leader; task-level requirement refinement happens directly with the driver (both on tmux) |
| **2. Multi-vendor agents** | claude / codex can be combined (MVP supports 2 vendors; OpenAI / Gemini come later) |
| **3. Team formation definitions** | Per-project team formation (single driver / driver + reviewer / multi-stage) selected via YAML |

---

### 1.3 Design Principles

Hold every new feature or proposal up against these principles first.
Anything that violates them stays out, no matter how appealing.

1. **Cutting scope has value.** Features are not added because they "would
   be nice to have." Only add what the workflow cannot run without. When
   in doubt, leave it out.

2. **Decide based on real harm.** Decide by whether there is an actual
   problem to solve, not by how attractive or "clean" a feature is. Do not
   solve problems that cause no real harm. (Example: a proposal claiming
   "roles are managed in two places" was rejected because the claimed harm
   turned out to be a misperception.)

3. **Question the premise of every proposal.** Before asking "how do we
   implement it," ask "does this problem actually exist." Proposals and
   problem framings can themselves be wrong. Do not take them at face
   value.

4. **AI decides, mechanism wires.** The driver (AI) makes judgments and the
   orchestrator (program) wires things deterministically. Do not add AI for
   judgment that a mechanism can handle (there is no per-task "owner AI" —
   a state machine is enough). Mechanisms do not make judgments.

5. **Push exception-heavy work to AI, keep the routine in mechanism.** git
   commit / push / conflict resolution are full of exceptions → the driver
   (AI) handles them. Worktree creation/removal is routine → the mechanism
   handles it. Do not try to handle every exception in code.

6. **Protect simplicity through structure.** One task = one file, no
   multiple sources of truth, no daemon, no polling. Guarantee discipline
   through program structure rather than human attention.

7. **The leader is light.** Conversation and spawn only. Do not burden it
   with state tracking, progress management, or polling.

8. **Always leave a path for human intervention.** Never go fully
   autonomous. The user can talk to a driver directly — this is core to the
   mission.

9. **The core is minimal; development flow is bolted on.** The fleet core
   has no coding-specific features. Worktrees are a workspace mode;
   commit / PR / changelog and the like are left to the project (§8).

The fact that agent-fleet does not have parallel execution, inter-agent
communication, races, or dynamic prompt injection is all a consequence of
these principles (see §9 anti-scope).

---

## 2. Naming

| Item | Name | Notes |
|---|---|---|
| Repo name | `agent-fleet` | Expresses the multi-vendor premise |
| CLI name | `fleet` | Five characters, fast to type |
| leader | leader | — |
| driver | driver | Called "driver," not "agent," to keep it distinct from the leader |

---

## 3. Architecture Overview

```
[user] <--tmux--> [leader: conversation + start only]
                       |
                       v fleet-agent start
                  [driver pane] (tmux window)
                       |
                       +--> events.jsonl (append-only)
                       +--> notification (macOS / slack)
                       +--> dashboard (read-only view)
                       |
                       v user intervenes when needed
                  [user attaches directly to the driver pane to converse]
```

### 3.1 Key Ideas

- **The leader is light**: conversation and `fleet-agent start` only. It does
  not poll state or detect `awaiting_orders`.
- **The driver reaches the user directly**: via events.jsonl + notifications +
  dashboard, without relaying through the leader.
- **The user can talk to the driver directly**: a tmux attach lets them
  intervene in the pane.
- **The fleet itself is development-flow agnostic**: worktrees are a workspace
  mode; PR / changelog and the like are left to the project.

---

## 4. Responsibilities

### 4.1 Leader Responsibilities

- Task-delegation conversation with the user
- Starting tasks with `fleet-agent start` (deciding which agent vendor / model /
  formation to launch)
  - Long task descriptions can be passed from a file with
    `fleet-agent start <id> --prompt-file PATH`
- driver-prompt injection is not pasted directly inside `start`. After
  launching the driver pane and the agent CLI, `start` detaches a small
  stdlib-only prompt deliverer and returns immediately. The deliverer polls
  tmux `capture-pane` at short intervals, and once the per-adapter ready regex
  (one each for claude / codex) matches, it pastes — via the tmux buffer — a
  pointer line referencing `driver-prompt.md`, then sends Enter to submit after
  the paste settles, and exits (it pastes a single pointer line, not the full
  prompt). After submitting, it does not interpret the pane's text; instead it
  waits for the task-scoped `inbox_seen` event fired by the `fleet-agent
  inbox-read` call at the top of the driver-prompt, treating that as the
  delivery acknowledgement. If it detects an interactive boot gate (update /
  trust / login etc.), it emits one `awaiting_orders` event plus a
  notification, but keeps polling until a hard timeout. Once a human clears the
  gate in the pane, the next ready detection triggers the auto-paste. On
  timeout it emits an `error` event and marks the task `failed`. This is a
  one-time startup handshake and is not used for heartbeat or continuous
  monitoring.
- High-level progress reports to the user as needed

The leader does **not** poll driver state or detect `awaiting_orders`.
Structure (events / dashboard / notifications) delivers those to the user
directly.

Because the codex driver shows a directory trust prompt on first launch,
`fleet-agent start` checks read-only whether the git repo root is trusted in
`~/.codex/config.toml` before launching the first-stage codex driver. If
untrusted, it aborts without creating the worktree / task state / prompt and
guides the user to launch `codex` once in that repo to approve it, then retry.
`fleet preflight` also surfaces the same trust status as an optional check.

The codex startup update prompt is suppressed at the source by passing the
Codex CLI per-invocation config override `-c
check_for_update_on_startup=false`. The fleet does not write to
`~/.codex/config.toml`. `fleet preflight` compares `codex --version` against
the latest `@openai/codex` on the npm registry, and additionally surfaces an
optional warning if the npm global install's package version differs from the
`codex` on PATH.

### 4.2 Driver Responsibilities

- Implementing the assigned task (including delegation to member subagents)
- Appending progress to events.jsonl
- When user input is needed, calling the dedicated **`fleet-agent ask`** CLI to
  deliver it (details in §7)
- Self-cleanup on completion

### 4.3 User Responsibilities

- Delegating tasks to the leader (on tmux)
- Tracking driver state via notifications / dashboard
- Attaching directly to a driver pane to converse when needed
- Answering driver questions and making merge decisions (depending on the
  formation)

---

## 5. Project / State Layout

### 5.1 Layout Policy

State is centralized under **`<agent-fleet clone>/fleet-state/`**.

- Nothing fleet-related is placed inside the project repo. All state is
  centralized.
- `fleet-state/` is gitignored (add `fleet-state/` as an entry). It is not a
  dotfolder — it is treated as a visible folder.
- Nothing is placed under the home directory (no `~/.fleet/` etc.). The
  `$FLEET_HOME` environment variable can override the location.
- The fleet resolves the clone root from `__file__` (three levels above
  `src/fleet/state.py`). If `$FLEET_HOME` is set, it takes precedence.
- **Per-project leaders are kept.** A shared-leader scheme was rejected.

### 5.2 Concurrent Multi-Project Startup

A global registry (`fleet-state/projects.yaml`) manages all projects centrally.

- The project identifier is **name** only. The registry enforces uniqueness.
- Omitting `--name` in `fleet init` uses the repo directory basename.
- Because the registry guarantees name uniqueness, the tmux session name
  `fleet-<name>` structurally eliminates the same-name collision footgun.

```bash
fleet init /path/to/image-gallery        # name is the basename "image-gallery"
fleet init --name api /path/to/api-repo  # explicit
fleet status --all                       # cross-project summary
# tmux ls
#   fleet-image-gallery: 1 windows
#   fleet-api: 1 windows
fleet leader --project image-gallery     # go to a specific project's leader
```

A project that is no longer needed can be removed from the registry with
`fleet rm <name>`, which also deletes its state tree.

### 5.3 State Structure

```
<agent-fleet clone>/
  src/  fleet  fleet-agent  ...        ← code (git managed)
  fleet-state/                         ← gitignored. not a dotfolder
    projects.yaml                      ← global registry (name → repo map)
    projects.yaml.lock                 ← for flock
    projects/
      <name>/                          ← state_dir for one project
        project.yaml     # name / repo / created_at / version / workspace
        events.jsonl     # append-only audit log
        dashboard.md     # read-only view (auto-generated, do not edit directly)
        notify.yaml      # notification settings
        memory/
          MEMORY.md      # knowledge index (drivers append as they go)
          GUIDE.md       # fleet memory discipline
          *.md           # individual memory files
        tasks/
          task-<id>/
            task.yaml         # task state
            inbox.md          # leader → driver instructions
            outbox.md         # driver → leader reports
            driver-prompt.md  # prompt expanded at spawn (the agent reads it itself)
        worktrees/
          task-<id>/     # only when workspace: worktree
```

#### Project Resolution Logic

`resolve_state_dir(cwd, *, project_name=None)` resolves in this priority order:

1. **Explicit `project_name`** (`--project <name>`): resolve directly by
   registry name.
2. **cwd inside the fleet-state tree** (`fleet-state/projects/<name>/…`):
   resolve to that `<name>`. Effective when running a fleet command from a
   worktree / task dir.
3. **cwd under a registered repo path**: among all registry `repo` paths that
   are ancestors of cwd, choose the one with the **longest path** (naturally
   handling nested monorepo registrations).

The `FLEET_STATE_DIR` environment variable (always injected into the driver
pane) takes precedence over `resolve_state_dir` (only for the driver-facing
`task_context.resolve`).

### 5.4 Race Protection

State is hardened through Python structure:

| Measure | Description |
|---|---|
| **flock exclusive acquisition** | acquire write lock with `fcntl.flock(fd, LOCK_EX)` |
| **atomic rename** | write the full file to a `tmp` file → atomic swap with `os.replace(tmp, final)` |
| **no partial updates** | `sed` / `>>` against existing files is forbidden; always rewrite the whole file |
| **one task = one file** | a structure where cross-task concurrent updates cannot happen in principle |
| **events.jsonl** | append-only, atomic via POSIX `O_APPEND`, no lock needed |
| **registry RMW** | `atomic_update(projects.yaml, mutate)` keeps flock + read + write in one interval |

All writes go through `locking.atomic_write` / `locking.atomic_update`. The
registry's read-modify-write is covered by `atomic_update` within a full lock
interval.

### 5.5 Dashboard Update Policy

- **Auto-rebuild on every write** (fired by the state writer context manager's
  exit hook)
- dashboard.md is **read-only**; no human / driver / leader edits it directly
- If frequent rebuilds become a problem under many concurrent drivers,
  debounce (collapse successive updates within 100 ms to just the last one)
  can be introduced later

---

## 6. Fleet Memory

### Motivation

Multi-vendor (claude / codex / others) is a pillar. A claude driver can
accumulate project knowledge in claude's own auto-memory, but a codex driver
cannot read it. Sharing project knowledge across vendors requires a
**vendor-neutral memory store**. This is not a "nice to have" — it is a
real-harm-based requirement for making multi-vendor work (design principle
§1.3).

### Settled Design

- **Fleet memory = a multi-vendor version of claude auto-memory.** Per-project,
  placed under `fleet-state/projects/<name>/memory/` (outside the worktree, so
  all drivers share the same instance).
- A set of markdown files + frontmatter (`name` / `description` / `type`) + a
  `MEMORY.md` index + `[[name]]` cross-links.
- **Three types**: `feedback` / `project` / `reference`. The `user` type from
  claude auto-memory is excluded (fleet memory is per-project, and a user's
  persona spans projects so it does not bind to one project).
- **Autonomous saving**: each driver decides "this is worth saving" and writes
  it while doing the task. No explicit-command scheme is used.
- **Write mechanism**: no dedicated CLI. The driver writes files directly into
  `$FLEET_STATE_DIR/memory/`.
- **Coexistence with claude drivers**: a claude driver may also use claude's
  own auto-memory, but discipline steers it toward "write project knowledge to
  fleet memory." claude auto-memory is not disabled (that would be excessive).
- **Delivery to the driver**: `driver-base.md` carries only a 1–2 line entry
  point. The index, discipline, and memory body live under the memory
  directory and the driver reads them itself. The base prompt is not fattened
  (consistent with §8.2).

### "Do Not Save" Discipline

The following are derivable from code or git history, too volatile, or belong
elsewhere:

- Code patterns / conventions / architecture / file paths — readable from code
- git history / recent changes / who changed what — `git log` / `git blame` are
  authoritative
- debugging solutions or fix recipes — fixes go in code, context in commit
  messages
- content already in documentation such as design.md
- volatile task details (in-progress work, transient state)

### Discipline Details

Detailed read/write rules (type definitions / save timing / two-step
procedure / staleness countermeasures) live in the memory directory's
`GUIDE.md`, auto-generated by `fleet init`.

---

## 7. Team Formation

### 7.1 Design Policy

- Defined in **YAML** (start simple)
- A two-tier structure: **formation templates** (shipped with fleet) +
  **formations** (owned by the project)
- **No count** (the leader decides on dynamic parallel launch as needed)
- Can express **user_approval** (human approval points made explicit via a
  stage attribute)

### 7.2 Formation Examples

```yaml
# Formation A: solo driver (handles everything through to PR alone)
name: solo
stages:
  - role: driver
    agent: claude:sonnet

# Formation B: pair review (implementer + AI review + user approval)
name: pair_review
stages:
  - role: implementer
    agent: codex:gpt-5.5
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required

# Formation C: multi-stage (design → implementation + AI review + user approval)
name: multi_stage
stages:
  - role: designer
    agent: claude:opus
    user_approval: required
  - role: implementer
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required
```

Execution order within each stage:
```
implement → peer_review (AI review loop, max 3 times) → user_approval → stage complete
```

In a stage with peer_review, the implementer's and reviewer's agent CLIs are
kept running for the duration of the stage. Only the first reviewer is launched
as a new tmux window when needed; subsequent iteration handoffs wake the
existing pane via an inbox notification with `send-keys`. This preserves agent
context, so the agent CLI boot gate is only crossed at the stage's first
launch. In multi_stage, this long-lived behavior is stage-local; an ordinary
cross-stage advance launches the next stage's driver fresh.

### 7.3 State Machine (Orchestrator)

- When `fleet-agent done --result approved|changes-requested` is called,
  `orchestrator.advance()` decides what comes next.
- approved: on the driver / reviewer's completion, it decides the next state
  for peer_review / user_approval. A peer_review handoff wakes a live pane via
  an inbox notification if present, otherwise launches that role for the first
  time. If there is no user_approval, it marks the current stage done and
  launches the next stage (task completed if there is no next stage).
- changes-requested: loops according to the peer_review phase. When sending
  back to the implementer, it injects an inbox notification into the existing
  implementer pane rather than relaunching.
- When the peer_review cap (3) is exceeded, task.status changes to
  `awaiting_orders` and the user is notified.
- A `user_approval.status == asked` gate is relayed by the leader, who takes
  the user's decision via `fleet-agent approve <id>` / `fleet-agent reject
  <id>`.
  - approve: sets `user_approval.status` to `approved` and proceeds to
    stage-completion handling.
  - reject: returns `user_approval.status` to `pending` and sends the stage
    back to implementation. In a peer_review stage, it wakes the existing
    implementer pane.
- For backward compatibility, relaying asked-gate approval/rejection via `done
  --result approved|changes-requested` remains for now but is not used in the
  new path.

### 7.4 Formation YAML Schema

The required and optional fields of a formation YAML are specified below. No
schema language (JSON Schema etc.) is used (§1.3 principle 1).

**Top level**

| Field | Required | Description |
|---|---|---|
| `name` | required | formation identifier. Must match the file name (stem) |
| `description` | optional | human-facing description |
| `stages` | required | list of stage objects. At least one required |

**`stages[]` (each stage)**

| Field | Required | Description |
|---|---|---|
| `role` | required | the role the driver plays (e.g. `driver`, `implementer`, `designer`) |
| `agent` | optional | the agent to use (e.g. `claude:sonnet`). If omitted, the `--agent` argument value is used |
| `peer_review` | optional | specified when inserting AI review. Subfields: `role` (reviewer's role, required), `agent` (reviewer's agent, optional). If `agent` is omitted, it falls back in order to the stage's `agent` → `claude:sonnet` |
| `user_approval` | optional | human approval point. The string `"required"` / `"optional"`, or an object form |

`validate()` performs the top-level `name` / `stages` required checks and the
per-stage `role` required check. Further structural validation (e.g. the
`peer_review` structure) is delegated to the orchestrator.

### 7.5 Formation Templates / Formations

- **Formation templates** (`src/fleet/templates/`): shipped with fleet. Three:
  `solo` / `pair_review` / `multi_stage`. Not directly executable.
- **Formations** (`<state>/formations/<name>.yaml`): the actual instances owned
  by the project. The runtime resolves only these.
- A template shows recommended defaults — "write it like this and it works."
  Copying it via `fleet init --formation <name>` or `fleet formation init
  --from <name>` makes it a project formation, independent thereafter (no
  tracking of the template).
- After copying, you are free to change the `agent:` defaults, add/remove
  `user_approval`, and so on.

**`fleet-agent start --formation` resolution rules:**

| Situation | Result |
|---|---|
| `--formation <name>` explicit | load `<state>/formations/<name>.yaml`. Absence is an error (no template fallback) |
| omitted + 1 file in formations/ | auto-adopt that one |
| omitted + formations/ empty | synthesize a 1-stage solo on the fly (`_leader_solo`) using the agent from `<state>/leader-session.json` |
| omitted + 2+ files in formations/ | ambiguity error (prompts to pass `--formation <name>`) |

---

## 8. Workspace and the Development-Flow Boundary

### 8.1 Design Philosophy

> **The fleet itself carries no development-flow discipline (commit / push /
> PR / conflict resolution / merge decisions).**
> **The development flow is entirely up to the project — the project owner
> writes it in CLAUDE.md / AGENTS.md / role prompts.**
> **The only thing the fleet holds about development flow is a single flag:
> "cut a worktree or not."**

This means:

- Per-project / per-task workflows are not bound to fleet vocabulary and can be
  steered flexibly via prompts (even "don't commit" for an ad-hoc investigation
  task is a single line in the task prompt, requiring no per-task override
  mechanism).
- Non-coding uses (research / monitoring) fit easily too (just choose
  `workspace=none`).
- Holding no "discipline plugin" extension point is a defense line against
  feature creep.

### 8.2 Layer Separation

| Layer | Character | Owner / Location |
|---|---|---|
| **formation** | team formation. Heavy discipline | fleet (YAML) |
| **workspace** | cut a worktree or not | fleet mechanism. The only development-flow switch the fleet holds |
| **fleet protocol** | done / inbox / ask / heartbeat / memory | fleet (`driver-base.md`, always injected, vendor-neutral) |
| **development-flow discipline** | commit / push / PR / conflict / merge decisions | entirely up to the project (`CLAUDE.md` / `AGENTS.md` / role prompts). The fleet holds nothing |

### 8.3 Workspace Enum

- The value is **`worktree`** or **`none`**. A single `workspace:` field in
  `project.yaml`.
- It is effectively a bool (cut a worktree or not). It is an enum to leave room
  for future `clone` / `container`.
- The default is **`worktree`** (parallel work is the fleet's main point; if
  you don't use worktrees, you wouldn't use the fleet at all).
- There is no arbitrary Python plugin loader (`<state>/plugins/<name>.py`).
  There is no route for a project to inject its own workspace implementation
  (judged to cause no real harm, §1.3 principle 1).

### 8.4 git Operation Responsibility Boundary

| Kind | Operation | Owner | Reason |
|---|---|---|---|
| **lifecycle-boundary git** | `worktree add` / `worktree remove` | **fleet (when workspace=worktree)** | routine operation. The driver cannot create its own workspace (chicken-and-egg) |
| **work git** | commit / push / PR creation / conflict resolution / rebase / merge decisions | **the project owner writes it in `CLAUDE.md` / `AGENTS.md` / role prompts → the driver (AI) reads and executes it** | exception-heavy, discipline differs per project. §1.3 principle 5 |

- The fleet core's Python code never touches work git (commit / push / PR /
  changelog).
- The strings the fleet injects into the driver-prompt also contain no work-git
  steps (`driver-base.md` + role fragment + task description only).
- The fleet does not decide PR merges. If needed, the project writes "merging
  is left to the leader / user" in its `CLAUDE.md`.

### 8.5 Placement

| Feature | Placement |
|---|---|
| tmux pane launch | core |
| inbox / outbox file communication | core |
| driver-prompt injection | core |
| state DB update | core |
| events.jsonl recording | core |
| dashboard generation | core |
| `on_pre_start` / `on_cleanup` hook (built into workspace) | core |
| worktree creation / removal | core (only when workspace=worktree) |
| commit / push / PR / changelog / review-request | **up to the project (driver)** |
| a vendor-neutral "discipline file" seam | **not held** (the project owner provides `CLAUDE.md` / `AGENTS.md` individually) |

---

## 9. Non-Goals (Anti-Scope)

Things explicitly **not done / not included** are recorded here as a defense
line against scope expansion.

| Item | Reason |
|---|---|
| Turning into a general-purpose orchestrator beyond coding | the core assumes coding; other uses are handled by workspace=none + the project |
| Full autonomy (minimizing human intervention) | contrary to the mission; being able to intervene is the essence |
| Cost-based routing | not needed for MVP; add later if needed |
| OpenAI / Gemini / local LLM support | MVP is claude + codex only |
| Centralized global metadata management | nothing under the home directory; self-contained |
| Driver-state polling by the leader | replaced by structure (events / dashboard / notifications) |
| No-code / GUI-based | terminal native, for power users |

---

## 10. Driver Communication Protocol

### 10.1 Driver Requests for User Input

When a driver wants to ask the user a question or seek a decision, it calls a
**dedicated CLI**:

```bash
fleet-agent ask "<question>"
```

When this is called:
1. emit an `awaiting_orders` event to `events.jsonl`
2. regenerate `dashboard.md` (reflecting the awaiting_orders mark)
3. fire a notification (macOS / slack)

If a driver merely writes a question in the pane, it reaches **nowhere**. The
structure applies pressure to follow the rule — input only reaches the user if
the protocol is followed — which holds more firmly than a prompt instruction
alone.

### 10.2 The Leader Does Not Mediate

The path driver → events / dashboard / notifications → user does **not** go
through the leader. The leader is structurally kept to conversation and
`fleet-agent start` only, so it stays unburdened.

---

## 11. Language / Dependencies / Installation

### 11.1 Language

- **Python 3.11+ only** (no bash, no agent SDK)
- Why 3.11 is required: `tomllib` in stdlib (room for future TOML adoption),
  match statements, improved type hints.
- bash is excluded: even tmux / git operations that look like "one shell line"
  do not actually fit in one line once error detection + pipes are involved;
  `subprocess.run([...])` is safer (no shell injection, clear errors, no
  quoting needed).
- A Python-only baseline from the start avoids accumulating bash-monolith debt.

### 11.2 Agent SDK

- **Not adopted** (Anthropic Agent SDK / LangGraph / a homegrown framework)
- Reasons:
  - Launching claude / codex CLIs directly in a tmux pane aligns with the
    fleet's distinctive human fallback.
  - Going through an SDK loses pane visibility.
  - Handling multi-vendor (claude / codex) through an SDK introduces vendor SDK
    compatibility problems.
- A driver = a claude / codex CLI process launched inside a tmux pane;
  communication is via files + the tmux pane.

### 11.3 Dependencies

- **stdlib only** as the baseline
- When needed, handle it with a **vendored dependency** (committed under
  `vendor/`)
- No `pip install` / `venv` / `uv` / `poetry` whatsoever; it runs immediately
  on `git clone`

```
agent-fleet/
  fleet                  # CLI entrypoint (shebang #!/usr/bin/env python3)
  fleet/                 # Python package
  vendor/                # only when needed, bundling pure-Python PyYAML etc.
```

### 11.4 Configuration Format

- **YAML** (formation / project config)
- To stay compatible with the zero-dependency baseline, only the pure-Python
  part of PyYAML 6.0.2 is bundled vendored
- Why YAML: for the expressiveness of user-written formations (deep nesting +
  comments + arrays) it is superior to TOML

### 11.5 CLI Entrypoints

- Two entrypoint scripts (both with shebang `#!/usr/bin/env python3`):
  - `./fleet`       — typed by humans (users): `init` / `preflight` / `leader` /
                      `attach` / `status` / `log` / `formation` / `workspace`
  - `./fleet-agent` — invoked automatically by the system (leader / driver
                      agents): `start` / `inbox` / `inbox-read` / `send-prompt` /
                      `cleanup` / `ask` / `event` / `approve` / `reject` / `done`
- Both are shebang scripts that import the same `src/fleet/` module. A design
  that physically separates "what humans type" from "what the system invokes
  automatically."
- pyproject.toml / setuptools entry_points are **not used in the MVP** (no
  `pip install` assumed)
- Room is left to switch to pyproject when distribution becomes a goal

### 11.6 Development Infrastructure (MVP)

| Item | Adopted |
|---|---|
| pytest | yes (write tests) |
| ruff | yes (lint + format) |
| mypy / pyright | optional (helps reliability but not MVP-required) |
| CI (GitHub Actions) | yes |

Only developers `pip install pytest ruff`; the fleet itself stays
zero-dependency.

---

## 12. Design Study: Formation Auto-Recommend

> **Status: decided.** This chapter reasons through Issue #118 ("should the
> leader infer the formation from the task description, and how far?"). §12.1–12.7
> record the analysis and recommendation; §12.8 records the repo owner's adopted
> decision. The decision is the recommendation's lightweight prompt-guidance step,
> made **per-project** and **persisted**: a co-authored, leader-injected
> `SELECTION.md` guide. No deciding mechanism is added and no command or schema in
> §7 changes — only a prompt injection (the same shape as the MEMORY.md injection
> of Issue #114).

### 12.1 Current Reality

The leader is itself an LLM. When a user delegates a task, the leader already
chooses a formation in-context as part of `fleet-agent start` (§4.1: "deciding
which agent vendor / model / formation to launch"). The heuristics are implicit
and unwritten, but real:

- A small, low-risk fix → `solo`.
- A heavier code change that wants a review gate → `pair_review`.
- A task that needs a design pass before implementation → `multi_stage`.

So "the leader infers the formation from the task description" is not a missing
capability — it is **already happening**, every time, inside the leader's
ordinary reasoning. The honest framing of Issue #118 is therefore not "can we
build formation inference?" but: **should this already-working implicit judgment
be formalized into a separate, explicit mechanism, and is that worth it?**
(§1.3 principle 3: question the premise before asking how to implement.)

### 12.2 Rule Options

Two families of "rule," plus an orthogonal question of task classes.

| Option | What it is | Assessment |
|---|---|---|
| **A. LLM-inferred (status quo)** | The leader, an LLM, picks the formation from the task request in-context. No new code. | This is what happens today. It handles nuance, mixed signals, and unusual phrasings that no fixed rule anticipates. |
| **B. Static keyword rules** | A program scans the description for keywords (`fix`, `bug`, `refactor`, `design`, `docs`, …) and maps them to a formation. | Brittle. Keywords are a poor proxy for risk/scope; "small fix to the auth flow" and "fix a typo" share a keyword but want different formations. A mechanism here would be **making a judgment**, which §1.3 principle 4 explicitly forbids ("Mechanisms do not make judgments"). |
| **C. Task classes** | Introduce an explicit taxonomy — `design-study` / `implementation` / `documentation` — then map class → formation. | Adds vocabulary and a classification step the workflow can run without. The leader already distinguishes these implicitly; naming them buys consistency only if the class is also written down somewhere and kept in sync — a second source of truth (§1.3 principle 6). |

Option B is the classic anti-pattern this codebase already rejects elsewhere:
encoding judgment into static rules that an LLM is strictly better at. Option C
is not wrong in principle, but it earns its keep only if a *downstream consumer*
needs the class as data (e.g. analytics, routing). For formation selection alone
it is redundant with the leader's in-context read.

### 12.3 Override Semantics

If auto-recommend were adopted, the user (or leader) naming a formation
explicitly must win. Two shapes:

- **Hard override** — an explicit `--formation <name>` bypasses inference
  entirely. This is already the behavior in §7.5's resolution table, and it is
  the right default: an explicit human choice is the strongest possible signal
  and should never be second-guessed by a mechanism.
- **Suggest-and-confirm** — inference proposes a formation and waits for a
  human "yes." This adds a round-trip to every task launch. For a leader that is
  *already* an LLM choosing in-context, the confirm step is friction with no
  added judgment: the user delegated precisely so they would not have to
  hand-pick the formation.

Conclusion for override: keep the existing hard-override semantics (§7.5).
Any inference layer must sit *below* an explicit `--formation`, never above it.

### 12.4 Input / Output

| Axis | Options | Note |
|---|---|---|
| **Input** | (a) description only; (b) description + past-task formation history | History could bias toward "what this project usually does," but the fleet keeps no centralized cross-task metadata store (§9: "Centralized global metadata management" is a non-goal), and reading `events.jsonl` across tasks to build a prior edges toward the polling/state-tracking the leader must not do (§1.3 principle 7). Description-only is the mission-consistent input. |
| **Output** | (a) a single formation name; (b) a ranked candidate list | A ranked list only has value if something *chooses among* the candidates — which is exactly the judgment the leader already makes. A single resolved name matches how `start` consumes the value today. A ranked list would push the decision downstream to either the user (re-introducing the confirm round-trip, §12.3) or another mechanism (more surface area, §1.3 principle 1). |

Both axes point the same way: **description-only input, single-name output** —
which is, again, a description of what the leader already does in its head.

### 12.5 Failure Behavior

If inference cannot decide, it must fall back deterministically rather than
stall. The natural fallback is **`solo`**: it is the lightest formation, it is
what §7.5 already synthesizes when no formation file is present
(`_leader_solo`), and an under-powered guess (solo when pair_review was wanted)
is cheaply correctable mid-task — the user can intervene in the driver pane
(§1.3 principle 8), or the leader can launch a review follow-up. Over-powering
(pair_review for a typo fix) wastes an agent boot and a review loop. Fail toward
the lighter formation.

### 12.6 Mission Consistency — the Key Tension

This is the decisive section. Hold the proposal against the principles:

- **§1.3 principle 4 — "AI decides, mechanism wires."** The leader (AI) is the
  decider; the orchestrator (mechanism) wires deterministically. Formation
  choice is a *judgment*, so by this principle it belongs to the AI — and the
  leader **is** that AI. A separate inference mechanism (Option B/C) would be a
  mechanism making a judgment, which the principle forbids. A separate
  inference *LLM call* would be a second AI doing what the first AI already
  does in the same breath.
- **§1.3 principle 7 — "The leader is light."** The leader does conversation +
  spawn only, no state tracking or polling. Pulling past-task history to inform
  inference (§12.4) is exactly the state-tracking this principle rules out.
- **§1.3 principle 1 — "Cutting scope has value."** A new command, schema, or
  classification step must clear the bar of "the workflow cannot run without
  it." The workflow demonstrably runs without it today.

The core realization: **the leader already performs formation inference as an
intrinsic part of being an LLM that reads the task and calls `start`.** A
*separate* auto-recommend mechanism does not add a capability that is missing;
at best it relocates an in-context judgment into either (a) static rules that
are strictly worse at judgment, or (b) a redundant extra LLM step. Running
explicit inference logic inside the leader is therefore largely **redundant with
the leader itself**. The one thing a separate mechanism could add — *consistency*
and *auditability* of the heuristic — comes at the cost of a second source of
truth to maintain (§1.3 principle 6) and is not currently a demonstrated harm
(§1.3 principle 2: decide on real harm, not on "would be cleaner").

### 12.7 Recommendation

**Recommended: do not build a separate formation auto-recommend mechanism.**
Keep formation selection where it already lives — the leader's in-context
judgment at `start` time — for these reasons:

1. The capability is **not missing**; it is intrinsic to the leader being an
   LLM. A mechanism would relocate, not add, judgment (§12.6).
2. Static keyword rules (Option B) violate "mechanisms do not make judgments"
   (§1.3 principle 4) and are brittle against real task phrasing.
3. History-based input violates "the leader is light" / no polling
   (§1.3 principle 7, §9).
4. Hard override (§7.5) and a `solo` fallback (§12.5) are already the right
   defaults and need no new code.

The **one lightweight, mechanism-free** step worth considering — *not* a new
command — is to **write the implicit heuristics down as guidance** that the
leader consults at `start`. That improves the consistency of the existing
in-context judgment without introducing a separate mechanism, a second source of
truth for state, or any change to §7's schema. It is prompt guidance, which is
how the fleet steers judgment elsewhere (§8.1).

One correction to the framing above, though: those example heuristics ("small
fix → solo; heavy change needing review → pair_review; design-before-build →
multi_stage") read as *universal*, but they are not. The bundled
`solo` / `pair_review` / `multi_stage` are only **templates** — every project
customizes its formations (names, stages, roles, agents; §7). So the *criteria
for picking among them* are equally **per-project**. A universal "small fix →
solo" rule baked into the leader base prompt would be wrong for a project that
renamed `solo` or whose cheapest formation is something else. The guidance must
therefore be per-project, not a global constant.

### 12.8 Adopted Decision — per-project `SELECTION.md`

The repo owner adopted the lightweight prompt-guidance step from §12.7, made
**per-project** and **persisted** (and not a deciding mechanism):

- **Where.** A plain-markdown guide at `<state>/formations/SELECTION.md`,
  alongside the project's actual formation files. It is per-project because
  formations are per-project (§12.7 correction).
- **How it reaches the leader.** `leader_prompt.render` injects the file's
  contents into the rendered leader prompt under the heading
  `## Formation selection guide (this project)` when it exists, and injects
  nothing when it is absent. This mirrors the MEMORY.md index injection added for
  Issue #114 (`driver_prompt._memory_index_section`): load one named file, wrap
  it under a clear heading, no-op if missing — no new command, no schema change.
- **How it is authored.** Co-authored by the leader and user: when the user
  wants to define or refine the project's selection criteria, the leader proposes
  a draft from the project's real formations, refines it in chat, and saves it to
  that path. `docs/prompts/leader-base.md` carries the standing instruction to do
  this and to consult the injected guide (with the real formation files) when
  choosing a formation.
- **Why this and not a mechanism.** This is exactly the "lightweight
  prompt-guidance" of §12.7, kept consistent with the principle analysis: the
  leader (an LLM) still makes the judgment (§12.6, principle 4); nothing polls or
  tracks state (principle 7); no command or schema is added (principle 1). The
  guide is persisted, project-tuned prompt context — not a rule engine that
  decides. The recommendation against a *separate auto-recommend mechanism*
  (§12.7) stands; this adopts only the prompt-guidance escape hatch it called out.

This closes Issue #118.
