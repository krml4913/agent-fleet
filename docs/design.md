# agent-fleet Design

> Design documentation for `agent-fleet`. This document records
> **settled design decisions**. Open questions and unresolved design
> issues are tracked in GitHub Issues.
>
> Last updated: 2026-06-17

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
| **1. Hierarchical dialogue UI** | Task delegation goes to the leader; task-level requirement refinement happens directly with the driver (both on tmux). A leader is a project-agnostic **session** — one conversational counterpart that may serve one or several projects (§5.6) |
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
- **The leader is project-agnostic**: a leader session is not bound to one
  project. It takes task requests for any project and routes each `start` with an
  explicit `--project`; the **task** carries the binding (`owner_session`, §5.6).
- **The driver reaches the user directly**: via events.jsonl + notifications +
  dashboard, without relaying through the leader.
- **The user can talk to the driver directly**: a tmux attach lets them
  intervene in the pane.
- **The fleet itself is development-flow agnostic**: worktrees are a workspace
  mode; PR / changelog and the like are left to the project.

---

## 4. Responsibilities

### 4.1 Leader Responsibilities

A leader is a **project-agnostic session** (Issue #166, resolved 2026-06-17). It
is not bound to one project: it takes task requests for any project, and the user
may run one or several leader sessions (§5.6). The leader's responsibilities:

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

**Project-agnostic discipline.** Because a leader session is not bound to a
project, it does not resolve the project from cwd:

- **`--project` is mandatory** on every leader dispatch (`fleet-agent start
  … --project <name>`, `fleet status --all` / `--project <name>`). There is no
  persisted "active project" pointer — the conversational active project is focus
  only (consistent with #67 sub-point 4); every dispatch names its project
  explicitly. cwd is pinned to the **agent-fleet clone root**, not a project repo
  (this retires the cwd-based project resolution for leaders; see §5.6 and the
  `leader-cwd-discipline` memory).
- **First-touch project load.** A session starts with only its code-delivered
  protocol (this base prompt) plus the **global leader memory** (§6) loaded. The
  first time the session acts on project X, it reads X's discipline — per-project
  memory (`projects/X/memory/MEMORY.md`) and the formation-selection guide
  (`formations/SELECTION.md`) — **once** and retains it for the session; it does
  not reload on a later project-switch. This is the pointer-not-payload pattern
  (§13.4) applied to leader project context: the leader is *pointed at* the
  per-project files and reads them on demand, rather than having them injected at
  startup. It keeps the base prompt short (§8.2) and keeps weak / non-Opus models
  viable (the multi-vendor pillar), since context is paid once per session, not
  per dispatch.
- **Contamination guard.** Per-project operating policy differs (e.g. fleet
  delegates PR merge to the leader; another project has the user review/merge).
  A project-agnostic leader must always act **under the active project's policy**.
  The guard is structural: acting on project X requires having loaded X's
  discipline, which first-touch load guarantees. The durable policy lives in
  per-project memory (§6), so it is identical for a leader of any vendor.

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
- **Leaders are project-agnostic sessions** (Issue #166, resolved 2026-06-17).
  This **overturns the earlier "per-project leaders are kept / shared-leader
  rejected" decision** (#67). The unit bound to a project is the **task**, which
  records its `owner_session`; a leader session may serve one or several
  projects. The original rejection rationale ("N projects in one leader = forge
  bloat") conflated codebase context with on-demand project discipline — today's
  leader holds no codebase, and per-project discipline is loaded first-touch and
  bounded by the per-session context dial (§5.6), so the real cost (context size)
  is handled without forbidding the model.

### 5.2 Concurrent Multi-Project Startup

A global registry (`fleet-state/projects.yaml`) manages all projects centrally.

- The project identifier is **name** only. The registry enforces uniqueness.
- Omitting `--name` in `fleet init` uses the repo directory basename.

**Sessions are launched by label, not by project.** `fleet leader [--name
<label>]` starts a project-agnostic leader session in tmux `fleet-<label>`
(default label `main`). The label is free-form, so the operational style falls
out of how you name sessions (§5.6). A driver window the session spawns is
opened **in that session's tmux** (`fleet-<label>`) — the owner session holds
both the leader window and the driver windows it started.

```bash
fleet init /path/to/image-gallery        # name is the basename "image-gallery"
fleet init --name api /path/to/api-repo  # explicit
fleet leader                             # one project-agnostic session, tmux fleet-main
fleet leader --name migration            # a second session for a workstream
fleet sessions                           # live leader sessions + their in-flight tasks
fleet status --all                       # cross-project task summary
# inside a leader session, every dispatch names its project:
#   fleet-agent start <id> --project image-gallery --formation <name>
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
    global/                            ← reserved namespace for cross-project concerns (§6, §5.6)
      leader-memory/                   ← two-tier leader memory: GLOBAL layer (loaded every session)
        MEMORY.md      # index
        GUIDE.md       # discipline
        *.md           # user-global prefs (tone) + router operating rules
      dashboard.html   # cross-project GUI view (auto-generated by fleet dashboard; §5.5)
      sessions/
        <label>/                       ← per-session leader state (one dir per live/known session)
          session.json          # label / agent spec / started_at / tmux pane
          leader-pending.jsonl  # queued driver done/gate notifications for this session
          leader-notifier.lock  # flock: one notifier per session at a time
    projects/
      <name>/                          ← state_dir for one project
        project.yaml     # name / repo / created_at / version / workspace
        events.jsonl     # append-only audit log
        dashboard.md     # read-only view (auto-generated, do not edit directly)
        notify.yaml      # notification settings
        memory/                        ← PER-PROJECT layer (loaded first-touch, §6)
          MEMORY.md      # knowledge index (drivers append as they go)
          GUIDE.md       # fleet memory discipline
          *.md           # individual memory files
        formations/
          SELECTION.md   # per-project formation-selection guide (read first-touch, §12.8)
          *.yaml         # project formations
        tasks/
          task-<id>/
            task.yaml         # task state (incl. owner_session — the spawning session's label)
            inbox.md          # leader → driver instructions
            outbox.md         # driver → leader reports
            driver-prompt.md  # prompt expanded at spawn (the agent reads it itself)
        worktrees/
          task-<id>/     # only when workspace: worktree
```

`fleet-state/global/` is a reserved namespace for cross-project concerns. It is
still inside the self-contained state tree (under the clone, or `$FLEET_HOME`),
so it does not violate the "nothing under the home directory" non-goal (§9) —
"global" here means cross-project-within-the-tree, not a home-dir store. The
per-session state under `global/sessions/<label>/` is keyed by `owner_session`
(the session label), since a session spans projects: its notification queue and
its leader agent spec belong to the session, not to any one project (§10.3).

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

For a **leader**, only priority 1 applies: a project-agnostic leader always
passes `--project` explicitly (§4.1) and its cwd is the clone root, so the
cwd-based fallbacks (2, 3) never resolve a project for it. Those fallbacks remain
for drivers (running inside a task dir / worktree) and for ad-hoc human
invocations — not for leader dispatch.

**Unified resolution + a session-aware default (Issue #171).** Both the
task-centric entry point (`task_context.resolve`) and the project-centric one
(`task_context.resolve_project_state_dir`) run the **same** three-step core, so
`--project` behaves identically everywhere and the project-centric path no longer
needs a task id. The driver-facing layer adds two rules on top of
`resolve_state_dir`:

1. `--project <name>` wins outright — registry-by-name, ignoring `FLEET_STATE_DIR`
   and cwd (the leader path; priority 1 above).
2. With no `--project`, `FLEET_STATE_DIR` wins over cwd — **except** when it
   points at a leader **session dir** (`global/sessions/<label>/`, which carries
   no `tasks/`). That absence is the signal of a leader pane: rather than
   dead-end on the wrong project, the resolver refuses and tells the caller to
   name the project (`--project`, or `--all` for `fleet status`). Otherwise it
   falls back to cwd via the registry for ad-hoc human use.

So a bare `fleet status` / `fleet-agent …` from a leader pane is a guided error
("pass `--project <name>` or `--all`"), from a worktree resolves that task's
project, and from a registered repo resolves by longest-path match.

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
- `dashboard.md` is **read-only**; no human / driver / leader edits it directly
- If frequent rebuilds become a problem under many concurrent drivers,
  debounce (collapse successive updates within 100 ms to just the last one)
  can be introduced later

**Cross-project HTML dashboard** (`global/dashboard.html`) — confirmed design
(Issues #182, #200):
- Generated on demand by `fleet dashboard` (opens the browser via `file://` URI).
- Refreshed automatically by the same write hook (`dashboard.rebuild`) **while
  the file exists** (opt-in by presence; zero cross-project scan cost until the
  user first runs `fleet dashboard`).
- Browser liveness via `<meta http-equiv="refresh" content="15">` — no daemon,
  no resident process.
- A client-side session selector filters the already-rendered project lanes by
  session scope. `All` is the default unfiltered view; selecting a session shows
  projects in that session's scope, and an unscoped session matches every project.
  The selected value is stored in `sessionStorage` and reapplied after the
  15-second meta refresh. The filter is vanilla inline JavaScript only; no server,
  daemon, Node, build step, or alternate dashboard file is introduced.
- Complement to `dashboard.md` (per-project markdown stays); `global/dashboard.html`
  is the cross-project GUI view.
- `global/dashboard.html` is in `fleet-state/` which is already `.gitignore`d —
  never committed.

### 5.6 Session = Context-Scope Unit

> Issue #166, resolved 2026-06-17. This is the foundational shift behind §4.1,
> §5.1, §5.2, §6's two-tier memory, and §10.3's routing.

A **session** is the unit of context scope and the binding key for
notifications. It replaces the old "1 project = 1 leader" coupling.

**Entrypoint and naming.** `fleet leader [--name <label>]` starts a
project-agnostic session as tmux `fleet-<label>` (default label `main`). The
label is free-form, so the operational style is expressed by how you name
sessions:

| Style | How | Who it suits |
|---|---|---|
| everything in one | `fleet leader` (`main`) | casual / strong model (Opus) — many projects : 1 session |
| workstream split | `fleet leader --name migration` | grouping related work across projects |
| clean 1 : 1 | `fleet leader --name fleet` (mirror the project) | clean operator / weak model — minimal context, full isolation |

**Storage stays per-project; only load-scope is per-session.** The durable source
of truth (memory, formations, task state) remains per-project under
`projects/<name>/` and persists across sessions (resolved: per-session *load
scope*, not per-session *storage*). What is per-session is only **what a session
has loaded into context**: a session loads a project's discipline on **first
touch** and retains it for the session (§4.1), so the same durable files serve
every session, loaded on demand.

**Context size is a user-tunable dial.** Because partitioning projects across
sessions is just a naming choice, "too much context in one session" stops being a
hard architectural limit and becomes a dial the user turns: load fewer projects
per session (toward 1 : 1) for a weak model or a clean operator, more (toward
many : 1) for a strong model. This is consistent with fleet's "mechanism gives a
dial, the user picks" ethos, and it is what **protects the multi-vendor pillar**
— a non-Opus leader runs 1 project : 1 session with minimal context.

**The session binds notifications, not a persisted active project.** Each task
records `owner_session` = the label of the session that spawned it (§10.3).
There is no persisted "active project" pointer; the conversational active project
is focus only, and every dispatch passes `--project` explicitly (§4.1). The
session→pane mapping and each session's in-flight tasks are surfaced by `fleet
sessions` (§10.3), the cross-session CLI view.

**Session scope — an optional project allow-list (Issue #172).** A session may
declare a **scope**: the set of projects it is responsible for. Scope is the
optional `scope` field on `global/sessions/<label>/session.json` (a sorted name
list). It is a focus / safety layer over the per-project storage above —
**absent field ⇒ unscoped ⇒ all registered projects** (full backward
compatibility; sessions that predate scope keep serving everything). Surfaces:

| Surface | Behaviour |
|---|---|
| `fleet scope <label> [--set / --add / --rm / --clear]` | view (no flag) or mutate the scope; `--set` / `--add` validate names against the registry |
| `fleet leader --scope a,b,c` | set the scope at session launch — names are registry-validated **before** the pane / `session.json` are created |
| `fleet status --all` | defaults to the session's scope; `--unscoped` shows every registered project, with a one-line note of how many were hidden |
| `fleet-agent start …` (dispatch) | **hard-blocks** a dispatch whose target project is outside the owner session's scope; `--allow-out-of-scope` overrides for an intentional cross-scope dispatch |
| leader prompt | the session's scope roster is injected so the leader knows its projects (unscoped ⇒ a note listing all registered projects) |

Scope is a **dial, not a wall**: by default it filters views and guards dispatch,
but every guard has an explicit override, consistent with §1.3 ("mechanism gives
a dial, the user picks"). It binds to the session rather than a project because
the session is the context-scope unit, and one project may be served by several
sessions.

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

### Two-Tier Leader Memory

> Issue #166, resolved 2026-06-17. The per-project memory above is shared by
> drivers and leaders; a project-agnostic leader additionally needs a durable,
> user-editable store for knowledge that spans projects. So leader memory is
> **two-tier**.

| Layer | Location | Loaded | Holds |
|---|---|---|---|
| **Global** | `fleet-state/global/leader-memory/` (new) | every session start | user-global preferences — *how the leader relates to the user* (e.g. tone) — and router operating rules |
| **Per-project** | `projects/<name>/memory/` (the §6 store, unchanged) | first-touch, retained for the session (§5.6) | per-project operating policy — *how a project's output should be* (e.g. merge authority, "English docs/issues" convention) |

- **Split axis.** *How the leader relates to the user → global; how a project's
  output should be → per-project.* Tone is global; "English docs/issues" is
  per-project. This finally gives the cross-project, user-spanning knowledge a
  home: §6's "the `user` type is excluded because fleet memory is per-project" was
  the right call *for the per-project store* — that knowledge now lives one tier
  up, in `global/leader-memory/`, instead of being filed against an arbitrary
  project.
- **Same shape, same discipline.** The global layer is the same markdown +
  frontmatter + `MEMORY.md` index + `GUIDE.md` as the per-project store, just at
  a different scope. It is vendor-neutral, so a leader of any vendor reads it.
- **Loading is pointer-not-payload.** The base leader **protocol** stays
  code-delivered (`leader-base.md`) — it is already the project-independent layer.
  Only the durable, user-editable global knowledge needs this new store. The
  global index is injected at session start (like the driver's `MEMORY.md` index,
  Issue #114); the bodies are read on demand. The per-project layer is read
  first-touch (§4.1) — not injected at startup, which is the leader-prompt
  assembly change (§12.8).
- **Migration.** User-global bits currently filed under fleet's *per-project*
  memory (e.g. the `leader-tone` entry) move up to the global layer. This is a
  one-time data move in `fleet-state/` performed by the operator/leader, not by
  fleet core (fleet core never edits memory content).

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
- A cascade structure: **project formations** override **global formations**,
  which override shipped **formation templates**
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
- `awaiting_orders` is a leader-gated pause: only `fleet-agent approve`/`reject`
  settle it; a driver's `done` is a no-op while a task awaits a human decision
  (it cannot self-clear a `user_approval` gate or a peer_review escalation).

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
per-stage `role` required check. It additionally fails **loud** on a malformed or
misspelled *safety gate*, so a `user_approval` / `peer_review` boundary (P8)
cannot silently vanish into a bare solo:

- a present `user_approval` must be the string `"required"` / `"optional"` or an
  object carrying a bool `required`;
- a present `peer_review` must be an object carrying `role`;
- a stage key that is a **near-miss misspelling** of a gate key — a case-only
  difference or an edit distance ≤ 2 from `user_approval` / `peer_review`
  (e.g. `user_aproval`, `peer_reveiw`) — is rejected.

This lint is scoped to the two gate keys *only*: every other key stays
unvalidated, so the open schema (no schema language; custom keys allowed) above
is preserved. `validate()` runs once at `fleet-agent start` (P4), so a botched
gate aborts the start rather than running ungated.

### 7.5 Formation Resolution

- **Project formations**
  (`projects/<project>/formations/<name>.yaml`): the most specific runtime tier.
  They override global formations and shipped templates with the same name.
- **Global formations** (`global/formations/<name>.yaml`): user-writable
  cross-project formations. They define a formation once for every project, but
  remain shadowed by a project override with the same name.
- **Formation templates** (`src/fleet/templates/<name>.yaml`): shipped defaults.
  Three ship with fleet: `solo` / `pair_review` / `multi_stage`. They are
  directly usable by explicit name when neither project nor global overrides
  exist.
- Copying a template via `fleet init --formation <name>` or `fleet formation init
  --from <name>` makes it a project formation, independent thereafter (no
  tracking of the template). `fleet formation init --from <name> --global`
  copies it to the global tier instead.
- After copying, you are free to change the `agent:` defaults, add/remove
  `user_approval`, and so on.

**`fleet-agent start --formation` resolution rules:**

| Situation | Result |
|---|---|
| `--formation <name>` explicit | resolve project → global → shipped template; absence from all three tiers is an error |
| omitted + 1 project formation | auto-adopt that one |
| omitted + project formations/ empty | synthesize a 1-stage solo on the fly (`_leader_solo`) using the agent from the owner session's record (`global/sessions/<owner_session>/session.json`, §5.6) |
| omitted + 2+ project formations | ambiguity error (prompts to pass `--formation <name>`) |

The omitted-formation auto-pick intentionally considers only the project tier.
Global formations and shipped templates are reachable only by explicit name, so
a fresh project with no formation files still falls back to `_leader_solo`
instead of becoming ambiguous among the shipped defaults.

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
| Global metadata **under the home directory** (`~/.fleet/` etc.) | self-contained: all state lives under the clone's `fleet-state/` (or `$FLEET_HOME`). Cross-project state that the model genuinely needs (the registry, `global/leader-memory/`, `global/sessions/`, §5.3) lives **inside** that self-contained tree, not in a home-dir store |
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

### 10.3 Notification Routing by `owner_session`

> Issue #166, resolved 2026-06-17. This concerns the **opt-in** leader-pane push
> (`notify_leader_on_driver_done`), where a driver `done` / approval gate is
> injected into the *owning leader's* pane for review. The always-on user
> notification path (§10.1, §10.2) is unchanged and never routes through a leader.

With leaders decoupled from projects (§5.1), "the project's leader" is no longer
a well-defined push target — a project may be served by several sessions, or a
session by several projects. The routing key therefore changes from **project**
to **`owner_session`**:

- **Record at spawn.** `fleet-agent start` stamps the spawning session's label
  onto the task as `owner_session` (the leader pane carries its label in the
  environment, e.g. `FLEET_SESSION`, so `start` knows who it is). The same label
  also decides **which tmux session the driver window opens in** (`fleet-<label>`,
  §5.2) — `owner_session` governs both window placement and notification routing,
  so a session sees its own drivers' panes and gets its own drivers' notifications.
- **Resolve at `done`.** When the gated event fires, the notifier resolves
  `owner_session` → the `fleet-<label>` pane (via `global/sessions/<label>/`) and
  injects the coalesced summary there, reading that session's agent spec to pick
  the vendor idle (`ready`) regex.
- **Queue when absent, flush on reattach.** If the owning session is dead or
  detached, the record stays in that **session's** queue
  (`global/sessions/<label>/leader-pending.jsonl`) and is flushed by the next
  `done` or re-attach. This reuses the existing notifier machinery (persisted
  never-drop queue + non-blocking flock + detached idle-poll injection) — **only
  the routing key and the queue's home change** (project → session). The queue and
  the leader agent record move from per-project (`projects/<name>/`) to per-session
  (`global/sessions/<label>/`) because a session spans projects: a single notifier
  can then flush all of a session's pending notifications regardless of which
  project produced them, and reattach finds the session's queue directly instead
  of scanning every project.

**`fleet sessions`** is the cross-session CLI view: live leader sessions (label →
pane, agent) and each session's in-flight tasks (task.yaml across projects where
`owner_session == label` and status is non-terminal). It reads state on demand
(no polling, consistent with principle 7) and is the CLI ancestor of the future
cross-project web view (Issue #166 facet A).

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
                      `sessions` / `attach` / `status` / `log` / `formation` /
                      `workspace`
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
- **How it reaches the leader.** The guide is read on **first touch** of its
  project (§4.1, §5.6) — the leader is pointed at
  `projects/<name>/formations/SELECTION.md` and reads it (with the real formation
  files) the first time it acts on that project, retaining it for the session.
  - *Superseded delivery (≤ #166):* originally `leader_prompt.render` injected
    the file at **startup** under the heading `## Formation selection guide (this
    project)`, mirroring the MEMORY.md index injection (Issue #114). That worked
    when a leader was bound to one project at launch. With project-agnostic
    sessions (#166) there is no single project to inject at startup, so the guide
    moves to first-touch read. **The artifact, its path, and its co-authoring flow
    are unchanged** — only the delivery shifts from startup-injection to
    first-touch read. This delivery shift is the "leader-prompt assembly change"
    called out as a highest-risk surface in the implementation plan.
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

---

## 13. Design Study: Shared / Inherited Driver Context

> **Status: study — outcome: not adopted, status quo retained.** This chapter
> reasons through Issue #152 ("is a shared/warm-context mechanism worth it for
> fleet?"). It is a balanced study, not finalized design. The owner has since
> ruled on the recommendation below: **no mechanism — status quo kept** (Issue
> #152 closed as resolved). The chapter stays as the durable record of *why*;
> everything below §13.6 is the reasoning behind that call, not a settled
> mechanism to build. The rest of `docs/design.md` holds confirmed design only —
> do not read this chapter as such.

### 13.1 The Question

Raised by the image-gallery leader (re-mapped from Claude-harness vocabulary
onto fleet): every driver is a fresh CLI that re-reads the same large context
(CLAUDE.md, big source files) from scratch, so N parallel or serial drivers
re-pay the same token cost to load it. fleet has no shared- or
inherited-context mechanism; today the leader hand-embeds summaries into the
task description when context matters — a lossy, manual workaround.

The honest first move (§1.3 principle 3: question the premise) is to separate
two claims bundled in that framing:

1. *Drivers re-read shared context from scratch.* — True, and largely **by
   design**. A driver is an independent CLI in its own pane (§3.1, §4.2); its
   freshness is the isolation that lets the user attach and converse with it as a
   standalone agent (§1.3 principle 8). "Re-pays context" is the cost side of the
   isolation the mission buys on purpose.
2. *That re-payment is a problem worth a mechanism.* — This is the part to test
   against real harm (§1.3 principle 2), not to assume.

### 13.2 What "Re-pay Context" Actually Costs

Be precise about the cost before weighing fixes.

- **CLAUDE.md / AGENTS.md** are deliberately small (this repo's are a few
  hundred lines combined) and are loaded by the *vendor CLI's own* project-file
  mechanism, not by a fleet prompt. fleet does not pay to inject them; the CLI
  does, once per process.
- **The driver base prompt is intentionally short** (§8.2, §6: "the base prompt
  is not fattened"). Dynamic prompt injection was explicitly dropped from
  claude-forge and is listed implicitly under §9 anti-scope. So the part fleet
  *controls* is already minimized — there is little fleet-side bloat left to
  amortize.
- **Big source files** are re-read by each driver, but a driver reads the files
  *its task touches*, which differ per task. The genuinely shared, re-read-
  identically payload across drivers is narrower than the framing implies —
  mostly the project docs, which are already small.

So the measured harm is: each driver spends some input tokens re-reading a
modest, mostly-small shared context. For fleet's actual usage — a handful of
drivers per project, rarely many in tight parallel (§9: parallel execution is
not a core capability) — this is a real but **small, bounded** cost. The
dogfooding signal to date (memory: `dogfooding-value-signal`) is that the value
fleet delivered was "light multi-session task handling," and no recorded
intervention has cited context re-payment as a pain point. That is weak evidence
that this is *fine*, not that it *hurts*.

### 13.3 Options Weighed

| Option | What it is | Assessment |
|---|---|---|
| **1. Injected pre-read snippet** | A curated context snippet the leader / structure injects into each driver prompt once. | Cuts directly against "base prompts are short" (§8.2) and re-introduces the dynamic prompt injection dropped from claude-forge. It also needs an **owner**: someone keeps the snippet accurate, and a stale injected summary is *worse* than no summary (it misleads with authority). This is the lossy hand-embed, promoted to a standing mechanism — it makes the workaround permanent instead of removing the need for it. |
| **2. Rely on each vendor CLI's prompt cache** | Let claude / codex CLIs cache their own context across invocations; fleet adds nothing. | Zero fleet machinery, which is attractive. But the behavior is **vendor-specific and not fleet-controlled**, and multi-vendor is a fixed pillar (§1.2) — fleet must not assume one vendor's caching. I will not assert specifics of what each CLI caches across separate process invocations; that is the vendor's domain and varies by version. Framed correctly: any cross-invocation caching is the **vendor's responsibility**, an opportunistic win where it exists, never a guarantee fleet designs around. fleet stays correct whether or not it happens. |
| **3. Do nothing (status quo)** | Drivers re-read; the leader hand-embeds a summary into the task description when a specific task needs it. | The cheapest option and the current one. Its cost is §13.2's small bounded re-read plus occasional manual leader effort. Its virtue: it adds no mechanism, no second source of truth, no staleness surface, and keeps each driver's context honest (it reads the live files, not a possibly-stale snippet). The leader's hand-embed is lossy but **targeted** — it carries exactly the task-relevant context, decided in-context, which is §1.3 principle 4 ("AI decides") working as intended. |
| **4. Pointer, not payload** *(added)* | A shared read-only artifact the driver is *pointed at* and reads itself, rather than *injected with*. | This is the option that actually fits "drivers are independent." Crucially, **fleet already has it** — see §13.4. It is not a new mechanism to build; it is the pattern fleet already uses, and the recommendation is to recognize it as the answer rather than add Option 1 on top. |

### 13.4 Pointer vs Payload — fleet Already Chose Pointer

The decisive observation: fleet's existing design already answers this question,
and it answered "pointer."

- **Fleet memory (§6)** is a per-project, vendor-neutral, read-only shared
  artifact. The driver base prompt carries "only a 1–2 line entry point… the
  driver reads them itself. The base prompt is not fattened" (§6, Delivery to
  the driver). That is precisely a pointer-not-payload shared-context mechanism —
  shared across all drivers, outside the worktree, read on demand.
- **The startup handshake (§4.1)** pastes "a single pointer line, not the full
  prompt" referencing `driver-prompt.md`. Again: point the driver at a file,
  let it read.
- **CLAUDE.md / AGENTS.md** are themselves pointer-shaped — CLAUDE.md in this
  repo is a *pointer* to AGENTS.md, not an embedded copy.

The pattern is consistent and principled: fleet shares context by **pointing
independent drivers at small, vendor-neutral, read-on-demand artifacts**, never
by injecting payloads into prompts. A driver re-reading a pointed-at file is not
a defect to amortize away — it is the isolation working. The token cost of a
driver reading a small shared file it was pointed at is the price of that
file being *live and vendor-neutral* rather than a cached, possibly-stale,
possibly-vendor-specific blob.

This also reframes the original feedback. The leader's hand-embedded summary is
lossy specifically *because* it is a payload (a frozen paraphrase). The
mission-consistent improvement, where one is wanted, is not to inject a better
payload (Option 1) but to **point at a better artifact** — e.g. ensure the
shared, re-read context a project cares about lives in a small file (a doc, a
fleet-memory entry) the driver is already pointed at, so the leader does not
need to paraphrase it into the prompt at all.

### 13.5 Mission Consistency — the Tension

Hold each option against the pillars and principles:

- **§1.2 multi-vendor pillar.** Option 2 (vendor cache) cannot be a *designed*
  mechanism without assuming a vendor; it can only be an opportunistic, unowned
  win. Options 3 and 4 are vendor-neutral by construction.
- **§8.2 / §6 short base prompt; claude-forge dropped dynamic injection.**
  Option 1 violates this head-on. Options 3 and 4 honor it — 4 is *how* fleet
  keeps the prompt short while still sharing context.
- **§1.3 principle 1 (cutting scope) + principle 2 (real harm).** A new
  injection/caching mechanism must clear "the workflow cannot run without it."
  The workflow runs today; the harm is small and bounded (§13.2). The bar is
  not met.
- **§1.3 principle 6 (no second source of truth).** An injected snippet (Option
  1) is a curated paraphrase of context that *also* lives in the real files — a
  second source of truth that can drift. The pointer pattern (Option 4) has one
  source: the file itself.
- **§1.3 principle 8 (human can intervene).** A driver carrying live,
  self-read context is a coherent agent for the user to attach to. A driver
  carrying an injected summary that disagrees with the live files is a confusing
  one.

Every principle points the same way: away from injection, toward
pointer-and-read, which fleet already does.

### 13.6 Recommendation

**Recommended: do not build a shared/warm-context injection mechanism. Keep the
status quo (Option 3), and treat fleet's existing pointer-not-payload pattern
(Option 4, §13.4) as the sanctioned way to share context** — extending it only
by making sure the context a project re-reads lives in a small, pointed-at
artifact, never by injecting payloads.

Concretely:

1. **Reject Option 1 (injected snippet).** It violates the short-base-prompt
   pillar, revives the dropped dynamic injection, and creates a staleness-prone
   second source of truth (§13.5). It promotes the lossy workaround into
   permanent machinery instead of removing the need for it.
2. **Treat Option 2 (vendor cache) as the vendor's responsibility, not fleet's
   design.** Where a CLI caches across invocations, fleet benefits for free;
   fleet must stay correct and vendor-neutral whether or not it does, and must
   not build around any specific caching behavior (multi-vendor pillar). Do not
   assume specifics not verified per vendor/version.
3. **Keep Option 3 (status quo) as the baseline.** The re-read cost is small and
   bounded for fleet's real usage (§13.2), and no dogfooding signal marks it as
   harm (§1.3 principle 2). The leader's in-context hand-embed remains the right
   tool for the occasional task that needs targeted context — that is "AI
   decides" working, not a gap.
4. **Recognize Option 4 as already-present, and prefer it for any real need.**
   If a project finds itself re-embedding the same context into many task
   descriptions, the mission-consistent fix is to move that context into a small
   pointed-at artifact (a doc or a fleet-memory entry, §6) the driver already
   reads — fixing it at the *pointer* layer, with no new mechanism.

The thread tying this together: **"drivers re-pay context" is partly by design.**
Isolation is the point (§3.1, §4.2), and any shared-context mechanism trades
against the lightweight-leader and short-prompt pillars. fleet already resolved
this tension with the pointer-not-payload pattern; this study recommends leaning
on that rather than adding injection or caching machinery on top.

**Owner decision (made).** The owner accepted this study: **no new mechanism,
status quo retained**, with the pointer pattern acknowledged as the sanctioned
context-sharing route. Issue #152 is closed as resolved — the answer to the open
question is "no mechanism." If the owner later observes a *measured* re-read cost
that does cross the real-harm bar — e.g. genuinely many parallel drivers on a
large shared context — this chapter should be revisited; the option that would
earn its keep first is still Option 4 (a richer pointed-at artifact), not Option
1's injection.
