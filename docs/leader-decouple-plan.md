# Implementation plan — decouple leader from project (Issue #166)

> **Status: plan, not settled state.** This is the phased, sequenced
> implementation plan for the design recorded in `docs/design.md` §4.1, §5.1,
> §5.2, §5.3, §5.6, §6 (two-tier leader memory), §10.3, §12.8 — the model settled
> in Issue #166 (resolved 2026-06-17). Delete this file once the work has shipped;
> `git log` / `CHANGELOG.md` are enough for history thereafter.
>
> **Why a dedicated doc and not `design.md` / `backlog.md`?** `design.md` records
> the settled *end-state* design only (open questions live in Issues); a migration
> sequence with risk analysis is not end-state design. `backlog.md` is for small,
> parkable TODOs ("delete when done") — a multi-phase, risk-annotated plan would
> swamp it. So the sequence lives here, with a one-line pointer left in
> `backlog.md`.

## Scope

Design-only Issue #166 turned into code. **No design decisions are reopened
here** — see `design.md` and #166 for the *what / why*; this doc is the *order
and risk*.

## Highest-risk surfaces (called out up front)

1. **Notifier routing refactor (Phase 4).** Routing key changes project →
   `owner_session`; the queue + leader-agent record relocate from per-project
   (`projects/<name>/`) to per-session (`global/sessions/<label>/`). The notifier
   is a *detached subprocess* with a persisted never-drop queue and a flock — a
   wrong move here silently drops or misroutes a leader notification. Highest risk
   because failures are silent and the code path is hard to exercise end-to-end.
2. **Leader-prompt assembly change (Phase 2).** `leader_prompt.render` stops
   being per-project: it no longer takes `project_name` / `state_dir` and no
   longer startup-injects `SELECTION.md`; instead it injects the **global**
   leader-memory index and instructs first-touch per-project load. Risk: the
   leader silently loses project context (SELECTION / per-project memory) if the
   first-touch instructions are weak, degrading formation choice and the
   contamination guard (design §4.1).

## Phases (ordered by dependency, then risk)

### Phase 1 — Global leader-memory layer  *(self-contained; ship independently)*

The most isolated piece: it adds a store and one injection, and depends on
nothing else here.

- Create `fleet-state/global/leader-memory/` with `MEMORY.md` (index) + `GUIDE.md`
  (same shape/discipline as the per-project memory store, design §6).
- Scaffold it once (e.g. a `global/` init path analogous to `_init_memory`); the
  parent `fleet-state/global/` is the reserved cross-project namespace (design
  §5.3).
- Inject the global index at **session start** (mirrors the driver `MEMORY.md`
  index injection, Issue #114) — see Phase 2 for the render wiring; the *store*
  itself ships here.
- **Data migration (operator/leader, not fleet core):** move user-global memory
  entries currently filed under fleet's per-project memory (e.g. `leader-tone`)
  up into `global/leader-memory/`. fleet core never edits memory content.
- **Touches:** `src/fleet/state.py` (scaffold), a new `global`-paths helper,
  `docs/prompts/` (a global `GUIDE.md`).
- **Ship-independent?** Yes. The store can exist and be hand-loaded before the
  session refactor lands; nothing breaks if routing is still per-project.

### Phase 2 — Session entrypoint, naming, and prompt assembly  *(foundation; contains risk #2)*

Everything downstream needs a session label and a per-session record.

- `fleet leader [--name <label>]`: default label `main`; tmux session
  `fleet-<label>`; **cwd pinned to the clone root** (not a project repo); drop
  project resolution from this command (it is project-agnostic). Inject
  `FLEET_SESSION=<label>` into the leader pane env (alongside `FLEET_STATE_DIR`).
- Relocate the per-session leader record: `projects/<name>/leader-session.json`
  → `global/sessions/<label>/session.json` (label / agent spec / started_at /
  pane). Multiple sessions coexist.
- **Leader-prompt assembly change (RISK #2):** `leader_prompt.render` becomes
  project-agnostic — no `project_name` / `state_dir` args, no `SELECTION.md`
  startup injection. It injects the **global** leader-memory index and the
  first-touch-load + `--project` discipline instructions instead.
- Rewrite `docs/prompts/leader-base.md`: project-agnostic protocol, `--project`
  mandatory on every dispatch, cwd = clone root, first-touch per-project load
  (read `projects/<name>/memory/MEMORY.md` + `formations/SELECTION.md` on first
  touch, retain for the session), global vs per-project memory split (design §6).
- **Touches:** `src/fleet/commands/leader.py`, `src/fleet/leader_prompt.py`,
  `docs/prompts/leader-base.md`, a `global/sessions/<label>/` paths helper.
- **Ship-independent?** Partly. Renaming the session to `fleet-<label>` is a
  visible cutover (see Migration); best landed together with Phase 3/4 so routing
  and window placement follow the same key. The prompt-assembly change is
  behaviourally safe to land early (leader just reads files first-touch).

### Phase 3 — `owner_session` on the task + driver-window placement  *(depends on Phase 2)*

- `fleet-agent start` reads `FLEET_SESSION` (fall back to `--session <label>`, then
  default `main`) and stamps `owner_session` onto `task.yaml`.
- `launch_stage_driver` opens the driver window in **`fleet-<owner_session>`**,
  not `fleet-<project>` (start.py currently hardcodes `fleet-<project_name>`).
  The orchestrator's later-stage launches use the same key.
- **Touches:** `src/fleet/commands/start.py`, `src/fleet/orchestrator.py`,
  `src/fleet/state.py` (task schema doc/comment).
- **Ship-independent?** No — needs Phase 2's session label. Pairs naturally with
  Phase 4 (same `owner_session` key for windows and notifications).

### Phase 4 — Notifier routing by `owner_session`  *(depends on Phase 2 + 3; RISK #1)*

- `done._maybe_notify_leader`: resolve `owner_session` → `fleet-<label>` pane
  (drop the `session = f"fleet-{project_name}"` hardcode); read the session's
  agent spec from `global/sessions/<label>/session.json` for the vendor `ready`
  regex.
- Move the queue + lock to per-session: `leader_notifier.queue_path` /
  `LOCK_NAME` resolve under `global/sessions/<label>/` instead of the project
  `state_dir`. One notifier per session flushes that session's queue across all
  projects.
- `formation` fallback (`_leader_solo`) that reads `leader-session.json` for the
  agent → read the **owner session's** record.
- **Test focus (the surface is hard to exercise live):** unit-test the pure
  functions — session-keyed `queue_path`, `build_record` carrying `owner_session`,
  `owner_session → pane` resolution, and `clear_records` nonce matching against
  the relocated queue. The detached spawn stays best-effort/never-raise.
- **Touches:** `src/fleet/leader_notifier.py`, `src/fleet/commands/done.py`,
  `src/fleet/formation.py`.
- **Ship-independent?** No — the keystone. Land it with Phase 3 so windows and
  notifications switch to `owner_session` together.

### Phase 5 — `fleet sessions` CLI view  *(depends on Phases 2–4)*

- New read-only `fleet sessions`: live leader sessions (label → pane, agent, from
  `global/sessions/` cross-referenced with tmux) + each session's in-flight tasks
  (scan `task.yaml` across projects for `owner_session == label`, non-terminal
  status). On-demand read, no polling (design principle 7).
- **Touches:** new `src/fleet/commands/sessions.py`, `src/fleet/cli.py` (register),
  `docs/design.md` §11.5 already lists it.
- **Ship-independent?** Yes, once the data it reads (per-session records +
  `owner_session`) exists. Purely additive; safe to land last.

### Phase 6 — `--project` / cwd discipline cleanup  *(mostly independent polish)*

- Make `--project` mandatory (or hard-required) on leader-facing dispatch; retire
  cwd-based project resolution for the leader path (drivers/ad-hoc keep it, design
  §5.3). Tighten error messages.
- Update the `leader-cwd-discipline` project memory: the rule changes from "keep
  cwd at repo root" to "leader cwd = clone root; project comes only from
  `--project`" (operator/leader memory edit, not fleet core).
- **Touches:** `src/fleet/commands/leader.py`, `status.py`, related help text;
  `fleet-state` memory (operator action).
- **Ship-independent?** Yes; can trail the rest as hardening.

## Migration / cutover notes

- **Session rename is the visible break.** `fleet-<project>` → `fleet-<label>`
  (default `fleet-main`). Land Phases 2–4 together so window placement and
  notification routing flip on the same key in one cutover.
- **In-flight tasks at cutover** have no `owner_session`. Simplest: drain
  in-flight tasks before cutover. Otherwise backfill `owner_session = main` (the
  default label) and have the notifier treat a missing `owner_session` as `main`.
- fleet is clone-from-`main` continuous release and a single-user PoC, so a hard
  cutover is acceptable — no external-compatibility window is owed.

## Open items to confirm in review (not guesses to bake in)

- **`FLEET_SESSION` env vs `--session` flag** as the channel by which `start`
  learns its owner session. Plan assumes env-injected `FLEET_SESSION` with a
  `--session` override and `main` default; confirm during Phase 3.
- Whether `global/sessions/<label>/` entries are **pruned** when a session ends,
  or kept as durable history for `fleet sessions`. Plan leans keep-and-mark-stale;
  confirm during Phase 5.
