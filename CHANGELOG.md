# Changelog

All notable changes to agent-fleet are recorded here. v0.1.0 is the first tagged
release. Future changes accumulate under `## [Unreleased]` and then move under a
tagged version when released. Older entries are grouped by development **Phase**
(per `docs/design.md`).

## [Unreleased]

### fix: clear a batch of non-blocking review nits (scope / handoff / docs)

Four independent, low-risk polish items deferred as non-blocking nits during the
session-scope (#176) and notify-noise (#178) peer reviews:

- **`fleet leader --scope` validates before side effects.** An invalid `--scope`
  project name is now rejected *before* the tmux session and `session.json` are
  created, instead of after — a typo no longer leaves a half-built leader pane
  with an unpasted prompt. The valid path is unchanged.
- **`fleet scope --add ""` no longer silently no-ops.** The read-only/mutate
  branch tested flag *truthiness*, so a present-but-empty value (`--add ""`) fell
  through to the display path. It now tests flag *presence* and rejects an empty
  project list with an explicit error.
- **`fleet-agent done` desktop notification names the real next driver.** For a
  peer_review `implementer → reviewer` handoff the stage does not advance, so the
  message surfaced the stage's own implementer role and claimed "next stage
  starting". It now derives the actual next role (the reviewer) from the
  peer_review phase and uses accurate handoff wording.
- **`docs/design.md` reflects the shipped session-scope (#172) and unified
  `--project` resolution (#171) as settled design** (§5.3, §5.6).
- Regression tests added for the scope-validation, empty-`--add`, and
  peer_review-handoff paths.

### fix: recover dropped codex submit Enter so prompt delivery doesn't time out

Spawning a `codex` driver could fail prompt delivery: the deliverer pasted the
driver-prompt pointer but codex intermittently swallowed the bare submit Enter
(the same TUI quirk the `/rename` dance already works around), leaving the
pointer sitting in the composer unsubmitted until the 600s timeout fired and the
task was marked `failed` (Issue #179).

- The submit Enter is now re-pressed until the `inbox_seen` ack lands. A bare
  Enter on an already-submitted (empty) composer is a no-op — codex ignores it
  whether idle or mid-turn (verified live) — so a resubmit never double-submits.
- The retry policy lives on the vendor adapter (`submit_retries` /
  `submit_retry_interval_seconds`), so the deliverer stays vendor-agnostic. Only
  codex opts in; claude keeps its single reliable Enter (`submit_retries=0`), so
  the claude path is unchanged.
- Regression tests cover the codex resubmit-until-ack path, the claude
  no-resubmit guarantee, and the adapter defaults.

### fix: suppress noisy intermediate-handoff notifications to the leader pane

`notify_leader_on_driver_done` fired on every `fleet-agent done`, including
internal `implementer ↔ code-reviewer` handoffs inside a `pair_review` or
`multi_stage` task. This caused the leader to investigate false gates repeatedly.

- Leader notifications are now limited to terminal events: task **completed** and
  **awaiting_orders** (user_approval gate). Intermediate stage transitions
  (`status=running`) are silently skipped.
- `render_block` no longer emits `result=approved` in the injected text. That
  field was the driver's self-reported `--result` flag, which looked like a gate
  approval decision and was misleading.
- Regression tests added for both changes.

### fix: archive collision no longer strands the live task dir

`fleet-agent cleanup --archive` (and `merge`, which shares the same teardown)
used to **leave the live `tasks/task-<id>/` in place** whenever the archive
target `tasks/_archive/task-<id>/` already existed — which happens when a task
id is re-spawned (e.g. recreating a dead driver) and cleaned up again. The
stranded dir kept the completed task showing in `fleet status` and required a
manual `rm`/`mv`.

- Teardown now archives the live dir under a deterministic collision-free name
  (`task-<id>-2`, `-3`, …) instead of warning and skipping. The live dir is
  always removed from `tasks/`; the pre-existing archive is never overwritten,
  so history is preserved.
- The no-collision case is unchanged: the dir still archives to `task-<id>`.

### feat: session scope — restrict a leader session's project set (Issue #172)

Adds the concept of a **session scope**: the set of projects a leader session is
responsible for. A session without a declared scope remains unscoped and behaves
exactly as before (backward-compatible).

- **`fleet scope [<label>] [--set/--add/--rm/--clear]`** — new user command to
  view and edit a session's scope. Label defaults to `$FLEET_SESSION`, then
  `main`. `--set a,b,c` replaces the scope; `--add`/`--rm` edit it incrementally;
  `--clear` removes the key (session becomes unscoped). All mutating forms
  validate project names against the registry.
- **`fleet leader --scope a,b,c`** — optionally sets the scope at session launch
  time. The scope is written to `session.json` before the leader prompt is pasted.
  The leader prompt now injects a **Projects in scope** section listing the
  session's assigned projects (or an "unscoped" note when no scope is set).
- **`fleet-agent start` scope guard** — dispatching to a project outside the
  owner session's scope is now a hard error (exit 1). The error message includes
  the current scope and instructions to bypass with `--allow-out-of-scope` or add
  the project via `fleet scope`. Unscoped sessions are unaffected. The guard runs
  even with `--dry-run` so tests can exercise it without tmux.
- **`fleet status --all` scope filter** — now defaults to showing only the current
  session's scoped projects. `--unscoped` shows all registered projects. `--session
  <label>` selects a specific session's scope. `$FLEET_SESSION` is used when
  `--session` is omitted. No filter applied when session is unscoped or unknown.
- **`fleet sessions` scope line** — each session block now shows its scope (or
  `(all projects)` when unscoped). In-flight tasks from projects outside the scope
  are tagged `(out of scope)` for visibility.
- **Storage**: scope is the optional `scope` field in
  `global/sessions/<label>/session.json`. Missing field or missing record → unscoped
  → all projects. `[]` is never persisted (`--clear` deletes the key).
- Refs #172

### fix: unify `--project` resolution and fix silent-fleet no-arg default (Refs #171)

- Introduced `task_context.resolve_project_state_dir` / `_resolve_state_dir_core`
  as the unified `--project` resolver across all commands. Explicit `--project
  <name>` resolves from the registry regardless of `FLEET_STATE_DIR`/cwd (the
  leader path); cwd / `FLEET_STATE_DIR` fallbacks remain for drivers and ad-hoc
  terminal use. Commands with a stray positional name or a previous cwd-only path
  argument were migrated to use the unified resolver.
- Fixed the footgun where `fleet` (no subcommand) would silently apply to an
  unintended project when run without arguments from an unrelated directory.
- Refs #171

### fix: leader notifier delivery — survive a busy leader, clear pending on retire

- **Busy leader stranded the queue.** The detached notifier (`src/fleet/leader_notifier.py`) was a one-shot poller: it waited for the leader pane to go idle and, if the leader stayed busy past its timeout, exited "leave queued" without retrying. With nothing else watching, the implementation-gate (`awaiting_orders`) notification observed on a bmweb multi_stage task was recorded in `global/sessions/<owner>/leader-pending.jsonl` (routing was fine) but never injected into the leader pane. Fix: on a busy-timeout with records still pending and the leader session still alive, the notifier now **re-arms** — it spawns a fresh successor poller (after releasing the session lock) so the next idle boundary is always caught. Each poller stays short-lived; the chain ends when the queue drains or the leader detaches. The poll loop moved into a `_poll_until_idle` helper that reports whether a re-arm is needed.
- **Stale pending after retirement.** `merge` / `cleanup` tore a task down but left its records in the owner session's queue, so a later `done`-triggered notifier would inject a stale "awaiting approval" for an already-merged task (3 retired-task records were seen lingering). Fix: the shared teardown path (`cleanup.teardown`, used by both `merge` and `cleanup`) now evicts the task's records by `task_id` via the new `leader_notifier.clear_task_records` — best-effort, never blocking teardown, no-op when no queue exists.
- Regression tests: busy-then-idle eventually injects within one process; a busy leader re-arms a successor; a detached leader does not re-arm; `clear_task_records` evicts all of one task's records and leaves others; teardown clears pending notifications.

### fix: `test_leader_cmd` no longer kills the live `fleet-main` leader session

- `tests/test_leader_cmd.py::LeaderCmdTests` hard-coded the production default label `main` (`self.session = "fleet-main"`) and its real-tmux tests created/torn down that exact session. Run while a real leader is alive in `fleet-main` (the dogfooding default), `tearDown`'s `kill_session("fleet-main")` terminated the running leader mid-work — observed as the leader session being force-killed. Regression introduced with the `#166` leader-decouple cutover (`9afb42f`), which added the literal `"main"` label to this suite. The sibling suites (`test_tmux`, `test_attach_cmd`, `test_send_prompt_cmd`) were already safe via `os.urandom`-randomized session names.
- Fix: `LeaderCmdTests` now uses an isolated randomized label (`test-<hex>`), and every real-tmux `fleet leader` launch passes an explicit `--name <label>` so the suite never touches `fleet-main`. The "default label is `main`" coverage moves to `test_default_session_label_is_main`, which asserts `leader.DEFAULT_SESSION_LABEL` without spawning a session. `test_custom_label_session` switches its fixed `migration` label to a randomized one too.

- Lands `docs/leader-decouple-plan.md` Phases 5–6, completing the leader-decouple work on top of the Phases 2–4 cutover, and folds in the two non-blocking doc fixes the Phase 2–4 AI review flagged.
- **Phase 5 — `fleet sessions`** (read-only, design `§5.6`, `§10.3`, `§11.5`). New `fleet sessions` lists every known leader session (`global/sessions/<label>/session.json` → label / agent / pane), cross-referenced with tmux for a **live / stale / ?** marker, and under each session its **in-flight tasks**: a scan of `task.yaml` across all registered projects for `owner_session == label` with a non-terminal status (`state.task_owner_session`'s missing ⇒ `main` default applies, so cutover-era tasks never disappear). A label claimed by an in-flight task but lacking a `session.json` still surfaces (flagged `[no session record]`) so no work is hidden. On-demand read only — no polling, no state writes (principle 7). New `src/fleet/commands/sessions.py`, registered on the `fleet` parser in `cli.py`.
- **Phase 6 — leader `--project` / cwd discipline cleanup** (design `§5.3`). The Phase 2–4 cutover left `approve` / `reject` / `cleanup` / `merge` resolving via `task_context` (which gives `FLEET_STATE_DIR` precedence), so from a project-agnostic leader pane — whose `FLEET_STATE_DIR` is the **session** dir (`global/sessions/<label>/`, no `tasks/`) — they dead-ended on "task.yaml missing". Now: `task_context.resolve(project_name=…)` takes an explicit `--project <name>` that resolves the project by registry name **regardless of `FLEET_STATE_DIR`/cwd** (the leader path — `§5.3` priority 1); `approve` / `reject` gain `--project`, and `cleanup` / `merge` repurpose their old `--project` (was a cwd *path*) to a registry **name**. When no `--project` is given and `FLEET_STATE_DIR` points at a leader session dir, resolution refuses with an error that points at `--project` instead of cwd-falling-back (cwd / `FLEET_STATE_DIR` fallbacks stay intact for **drivers** running inside a task dir / worktree and for ad-hoc human use). Audit of the rest of the group: `status` / `inbox` / `send-prompt` already resolve by name via `resolve_state_dir(cwd, project_name=…)` (never reading `FLEET_STATE_DIR`), so they already work cross-project from a leader pane and needed no change.
- **Doc fixes (Phase 2–4 review).** `docs/prompts/leader-base.md` — the command list now shows `cleanup` / `approve` / `reject` (and `merge`) taking `--project <name>`, resolving the tension with the "`--project` mandatory on every dispatch" rule above it. `docs/design.md` `§7.3` — the `_leader_solo` fallback row now cites the owner session's record (`global/sessions/<owner_session>/session.json`) instead of the retired per-project `leader-session.json`.

### leader-decouple core cutover — sessions, owner_session routing (Issue #166, Phases 2–4)

- Lands `docs/leader-decouple-plan.md` Phases 2–4 as one cutover: window placement and notification routing both flip onto the same `owner_session` key (design `§4.1`, `§5.2`, `§5.3`, `§5.6`, `§10.3`, `§12.8`).
- **Phase 2 — project-agnostic leader session.** `fleet leader [--name <label>]` (default label `main`) now starts a project-agnostic session in tmux `fleet-<label>`, cwd pinned to the **clone root**, with `FLEET_SESSION=<label>` injected into the pane env. It drops project resolution (no `--project`, no `load_project`). The per-session leader record relocated from `projects/<name>/leader-session.json` → `global/sessions/<label>/session.json` (label / agent / started_at / pane); new `state.session_dir()` / `state.session_record_path()` helpers. `FLEET_STATE_DIR` in the leader pane now points at the session dir (the leader's own state home), and leader-start events go to `global/sessions/<label>/events.jsonl`. **`leader_prompt.render` is now project-agnostic** (RISK #2): it takes no `project_name` / `state_dir`, no longer startup-injects a project's `SELECTION.md`, and instead injects the **global** leader-memory index (`global/leader-memory/MEMORY.md`, mirroring the driver `MEMORY.md` injection of Issue #114). `docs/prompts/leader-base.md` rewritten to the project-agnostic protocol: `--project` mandatory on every dispatch, cwd = clone root, first-touch per-project load (`projects/<name>/memory/MEMORY.md` + `formations/SELECTION.md`, retained for the session), and the global vs per-project memory split (`§6`).
- **Phase 3 — `owner_session` on the task + driver-window placement.** `fleet-agent start` learns its owner session from `--session <label>` > env `FLEET_SESSION` > default `main` and stamps `owner_session` onto `task.yaml`. `launch_stage_driver` opens the driver window in `fleet-<owner_session>` (was `fleet-<project_name>`); the orchestrator's later-stage launches and peer-review handoffs use the same key. `project_name` is retained only for the session *display* name in the picker. New `state.task_owner_session(task)` helper centralizes the **missing ⇒ `main`** default.
- **Phase 4 — notifier routing by `owner_session`** (RISK #1). `done._maybe_notify_leader` resolves `owner_session` → `fleet-<label>` pane and reads that session's agent spec from `global/sessions/<label>/session.json` for the vendor `ready` regex. The notifier queue + lock relocated from the project `state_dir` to `global/sessions/<label>/` (`queue_path` / `_acquire_lock` now take the session dir), so one notifier per session flushes across all the projects that session spawned. Each pending record now carries its project `state_dir` so the flush-time PR-URL re-scan (Issue #159) targets the right outbox. `formation.read_leader_session(label)` / `synth_leader_solo(owner_session)` read the owner session's relocated record. The never-drop / non-blocking-flock / detached-idle-poll guarantees are unchanged — only the routing key and the queue's home moved (project → session).
- **Coherence (beyond the plan's per-phase Touches lists):** `inbox`, `cleanup`/`merge` teardown, and `send-prompt` resolve the driver's tmux session from the task's `owner_session` rather than `fleet-<project>`, so the leader can nudge / tear down / re-paste drivers that live in a session window after the cutover.
- **Hard cutover** (single-user PoC, clone-from-`main`): no migration shim. **Follow-up (now landed — see the Phases 5–6 entry above):** leader-side `approve` / `reject` / `cleanup` resolved via `task_context` (`FLEET_STATE_DIR` / cwd) and needed `--project` to work cross-project from a project-agnostic leader pane; the `fleet sessions` view (Phase 5) was also not yet built.

### global leader-memory layer — store + scaffold (Issue #166, Phase 1)

- First slice of `docs/leader-decouple-plan.md`: the **global tier** of the two-tier leader memory (design `§6`), shipped as just the *store* (no render wiring / `fleet leader` refactor — those are Phase 2). New `global`-paths helpers in `state.py` — `global_dir()` (`fleet-state/global/`, the reserved cross-project namespace, `§5.3`), `global_leader_memory_dir()` (`global/leader-memory/`), and `global_sessions_dir()` (`global/sessions/`, a **reserved** path constant for later phases — Phase 1 does not build any session state there). All three derive from `fleet_home()`, so they honor `$FLEET_HOME` / clone-root resolution exactly like the per-project paths. New `state.ensure_global_leader_memory()` idempotently scaffolds `global/leader-memory/` with a `MEMORY.md` index stub and a `GUIDE.md` (new `docs/prompts/leader-memory-guide.md`) — same shape/discipline as the per-project memory store one tier up, but scoped to the global split axis (*how the leader relates to the user → global*: user-global preferences like tone, plus router operating rules; `user`/`feedback`/`reference` types, no `project`). The ensure-call is wired into `fleet init` (`commands/init.py`) — a project-agnostic, idempotent entrypoint, since `fleet leader` does not exist until Phase 2; it never clobbers existing content, so re-initializing further projects re-affirms the store harmlessly. No memory content is moved (that migration is an operator action on the gitignored `fleet-state/`, not fleet core). Tests: `tests/test_global_leader_memory.py` (paths honor `$FLEET_HOME`; scaffold creation + idempotency-no-clobber; `sessions/` stays unbuilt; `fleet init` wiring end-to-end).

### re-scan PR URL at inject time in leader_notifier (Issue #159)

- `leader_notifier` scraped each task's PR URL from `outbox.md` only at *enqueue* time (`build_record`, when `done` builds the record). A driver that called `fleet-agent done` a beat before its PR finished landing in `outbox.md` produced a record with `pr_url=None`, so the injected leader notification read `PR=(none yet)` even though the PR appeared seconds later (observed live: `task-infer-project` injected `(none yet)` while PR #157 was created moments after). `_flush_once` now tops up the in-memory render list: for each queued record whose `pr_url` is still falsy, it re-scans `scan_pr_url(state_dir, task_id)` just before `render_block` (new `_refill_pr_urls` helper). Records that already carry a URL are left untouched (never re-scanned/overwritten); a record whose PR genuinely never appears stays null and still renders `(none yet)`. The re-scan is best-effort — wrapped so a `scan_pr_url` error can never block the injection — and only the render-time list is touched (the persisted queue is cleared by nonce right after inject, so no rewrite is needed). `build_record` keeps its enqueue-time scrape as a useful early value.

### `fleet-agent status <id> --json` — one-shot structured task state (Issue #149)

- New `--json` mode on `status` emits a single machine-readable JSON object for one task to stdout (nothing else on stdout, so it parses with `json.loads`), so a leader no longer has to stitch `dashboard.md` + `outbox.md` + `events.jsonl` + `task.yaml` by hand. Fields: `task_id`, `title`, `status`, `formation`, `stages` (role/agent/status per stage), `current_stage` (index, null when no stages), `result` (current stage's gate/approval result — explicit stage `result` or a settled `user_approval`, else null), `pr_url` (scraped from the task `outbox.md` via the existing `leader_notifier.scan_pr_url`, else null), `branch`, `worktree`, `workspace`, and `last_event` (`{type, ts}` of the most recent event for the task, else null). In `--json` mode the positional argument is the **task id** (per the issue title `status <id> --json`); the project is resolved from `--project <name>` or cwd (mirrors `start`/#150) so a leader invoking by absolute path from another repo works. A non-existent task id errors on stderr with a non-zero exit and no half-JSON on stdout. The default table output is unchanged — `--json` is purely additive and reuses the existing state/event helpers (`load_task`, `read_events`, `_current_stage_index`), no parallel state-loading path.

### be lenient when both description and --prompt-file are passed to start (Issue #151)

- `fleet-agent start <id> "<desc>" --prompt-file PATH` no longer hard-fails with "pass either description or --prompt-file, not both" — passing both is a common leader slip, not worth rejecting. Now the `--prompt-file` contents win as the task body (the richer, intentional input) and the stray positional `<desc>` is relegated to the title: with no `--title` it becomes the title (and a `warn:` is printed so the slip is visible, not silent); with `--title T` present, `T` wins and the positional `<desc>` is ignored. `_resolve_description` now returns `(body, fallback_title)` and the title decision lives in one place in `run()` (precedence: `--title` > relegated positional > first line of body), so the prompt-file body never masquerades as the title. The other branches are unchanged: neither given still errors, only one given is unchanged.

### infer project from --prompt-file path instead of erroring (Issue #150)

- Follow-up to #143. `fleet-agent start` now *infers and proceeds* instead of rejecting: when `--project` is omitted and `--prompt-file` resolves under exactly one `projects/<name>/` tree, it infers `<name>` and resolves that project's state dir BEFORE `resolve_state_dir`, rather than resolving from cwd and erroring on a cross-project mismatch. Since the prompt-file path already names the project unambiguously, a leader spawning by absolute path from another repo no longer hits "re-run with --project" on every start. Explicit `--project` still wins (no inference override); a prompt-file outside any project tree falls back to cwd resolution as before; and an inferred but *unregistered* project fails with a clear error rather than silently landing in the cwd project. The #143 `_cross_project_promptfile_error` helper became `_infer_project_from_promptfile` (same path-parsing, returns the name instead of an error string), and the cross-project guard tests now assert the infer-and-proceed behaviour.

### push driver done/gate to the leader pane, opt-in (Issue #147)

- New `project.yaml` flag `notify_leader_on_driver_done` (default `false`). When `true`, `fleet-agent done` enqueues a persisted, idempotent record (`<state>/leader-pending.jsonl`) and spawns a detached `fleet.leader_notifier` that polls the leader pane (`fleet-<project>`, window `leader`; vendor from `leader-session.json`) and injects a coalesced one-line summary **only when the adapter `ready` regex matches** — never mid-turn. Multiple pending records flush as one block; flushed records are cleared by nonce so anything enqueued mid-flush survives; a non-blocking flock keeps a single notifier live (no double-inject); a detached/absent leader leaves records queued for the next `done`. Each record carries `task_id`/`status`/`branch`/`worktree`/PR-URL (scraped from the task `outbox.md`)/`result`/summary, and a `leader_notified` event is emitted on injection. Default `false` is zero behaviour change — no notifier spawned, `done` identical to before. The existing user-facing `notify.send` is untouched.

### `fleet-agent merge <id>` — atomic PR merge + teardown + archive (Issue #148)

- New subcommand that retires a finished task in one correct-ordered pass: merge the task's PR (`gh pr merge <branch>` — merge commit by default, `--squash` to switch; never `--delete-branch`), then run cleanup's teardown (worktree remove + local `branch -D`, kill the tmux window, drop the prompt buffer), then delete the remote branch (`git push origin --delete`, "already gone" is success), then archive the task dir by default (`--keep` opts out). A failed merge (conflict / not mergeable / no PR) stops before any teardown so the worktree survives for conflict resolution — the merge-then-cleanup ordering is now structurally enforced, fixing the dogfooded race where `gh pr merge --delete-branch` then `fleet-agent cleanup` collided over a branch still held by the worktree. Guarded to terminal statuses like `cleanup` (`--force` overrides). The teardown body of `cleanup.run` was refactored into a shared `cleanup.teardown(...)` helper that both commands call (no copy-paste); `cleanup` behaviour is unchanged. As part of the refactor, `teardown` now derives the worktree's repo from project.yaml `repo` (explicit arg > `repo` > `state_dir.parent`) — fixing a latent `cleanup` bug where a project whose state dir lives outside its repo would `git worktree remove` against the wrong directory and fail with `is not a working tree`.

### auto-name agent sessions on spawn (Issue #145)

- Each spawned agent's resumable session display name is now set automatically so the user can tell sessions apart in the picker: `<project>-leader` for the leader, `<project>-<task_id>-<role>` for stage agents (role disambiguates pair_review / multi_stage panes on the same task). The mechanism branches per vendor in the adapter layer: `VendorAdapter` gained `session_name_launch_args` / `session_rename_keys` (default no-op). claude names at launch via `--name`; codex (no launch flag) renames post-ready through its TUI `/rename` popup keystrokes. `start.launch_stage_driver` / `leader.run` append the launch args; `prompt_deliverer.deliver` and the leader send the rename keystrokes before pasting the prompt. No behaviour change for claude beyond the added name.

### fix leader project mis-resolution (Issue #143)

- `leader_prompt.render` now rewrites `fleet-agent` references in the fleet-managed base text to the absolute `fleet_agent_bin()` path (mirroring `driver_prompt.render`, Issue #125), so a leader never needs to `cd` into the agent-fleet clone — the `cd` was what made cwd-based project resolution silently land a task in the `fleet` project. `fleet_agent_bin()` moved to a neutral `fleet.paths` module shared by both prompt builders (re-exported from `driver_prompt` for compatibility). The injected `SELECTION.md` and the footer are project content / metadata and are not rewritten.
- `docs/prompts/leader-base.md` now instructs the leader to always pass `--project <name>` (shown in the footer) to `fleet-agent start`, and the footer surfaces a ready-to-use `start cmd:` hint with the absolute bin + `--project <name>`.
- `start.py` rejects the cwd-resolution trap: when `--project` is omitted (cwd-resolved) and `--prompt-file` lives under a *different* project's `projects/<other>/` subtree, it fails with a message telling the user to pass `--project`. Explicit `--project` bypasses the guard (legit cross-project use); prompt-files outside any project tree and same-project prompt-files pass.

### per-project formation-selection guide (Issue #118)

- `leader_prompt.render` now injects `<state>/formations/SELECTION.md` into the leader prompt under `## Formation selection guide (this project)` when the file exists, and injects nothing when it is absent — mirroring the MEMORY.md index injection (Issue #114). No new command, no schema change, no mechanism that picks a formation: the leader still decides.
- `docs/prompts/leader-base.md` gained a "Choosing a formation" section: formations are per-project (the bundled solo/pair_review/multi_stage are only templates), the leader consults the injected `SELECTION.md` together with the project's real formation files, and co-authors that guide with the user when asked.
- `docs/design.md` §12 updated from "design study" to "decided": added §12.8 recording the adopted per-project, co-authored, leader-injected `SELECTION.md` guide, and corrected the §12.7 framing that implicitly treated selection criteria as universal (they are per-project because formations are per-project).

### design study: formation auto-recommend (Issue #118)

- Added §12 "Design Study: Formation Auto-Recommend" to `docs/design.md`, marked "design study — not yet adopted". It reasons through whether the leader should infer the formation from the task description and how far to automate it, covering rule options (LLM-inferred vs static keyword vs task classes), override semantics, input/output, failure fallback, and consistency with the mission principles. Recommends *not* building a separate mechanism (the leader is already an LLM that picks the formation in-context) while leaving the final adopt/not-adopt decision to the repo owner. No code or schema change.

### richer notifications: status emoji + Slack color (Issue #139)

- `notify.send(...)` gained a `level` arg (`success` / `waiting` / `progress` / `error` / `info`, default `info`). Each transport renders from `(title, message, level)` — no per-call-site channel knowledge.
- Shared: every notification now carries a leading status emoji (✅ / 🟡 / ▶️ / ❌ / ℹ️), so macOS notifications are glanceable.
- Slack: the flat `{"text": …}` payload is replaced with an attachment carrying a level-keyed color bar, the emoji + bold title, the message, and a `project · task` context footer derived from the title. Still best-effort (never raises) and `FLEET_NO_NOTIFY` still short-circuits; `notify.yaml` is unchanged.
- Call sites tagged with fitting levels: done → success/waiting/progress, ask → waiting, orchestrator approval waits → waiting, prompt-deliverer ack timeout → error.

### vendor adapter registry (Issue #116)

- Added `src/fleet/adapters/` with one self-contained file per vendor (`claude.py`, `codex.py`) behind a `VendorAdapter` interface, plus an explicit-import `REGISTRY` in `adapters/__init__.py`.
- `agents.parse_spec` / `agents.cli_command` / `agents.SUPPORTED_VENDORS` and the prompt deliverer's ready/gate detection now derive from the registry instead of hardcoding claude/codex. Adding a vendor is now one new adapter file plus one registry line. Behavior is unchanged.

### richer `fleet status` task display (Issue #117)

- `fleet status` now renders each task as a compact aligned row showing the formation and current stage as `stage N/M (role, agent)` (e.g. `stage 1/2 (implementer, codex:gpt-5.5)`) alongside status and last-seen. `awaiting_orders` tasks are marked with a `▸` and highlighted so they stand out.
- Added `fleet status -v` / `--verbose`, which expands each task to list every stage with its role/agent and per-stage state (done / current / pending) plus the last `inbox_seen` / `heartbeat` ack timestamps.

### vendor-neutral fleet memory entry point (Issue #114)

- Added a `fleet-agent memory` subcommand group (`list` / `read <name>` / `write <name>`) so any vendor driver — claude, codex, or other — can read/write the project-level shared memory at `<state>/memory/` without relying on a vendor's own auto-memory.
- The driver prompt now injects the `MEMORY.md` index (index only, not every body) when present, so every driver starts with the accumulated project knowledge. Absent `MEMORY.md` injects nothing.
- `docs/prompts/driver-base.md` now points drivers at `fleet-agent memory …` and `<state>/memory/GUIDE.md`, and spells out that fleet memory is separate from any vendor's own auto-memory (no double management).

## [0.1.0] - 2026-06-04

### prompt delivery ack (Issue #107)

- Replaced pane-visual submit confirmation with task-scoped `inbox_seen` ack from `fleet-agent inbox-read`.
- Driver prompts now instruct drivers to run `fleet-agent inbox-read` before other task work, and missing `inbox.md` is treated as an empty inbox for first-boot ack.

### awaiting_orders rename (Issue #108)

- Renamed the user-input wait status/event to `awaiting_orders` across state transitions, driver prompts, CLI output, dashboard rendering, docs, and tests.
- Updated the related user-facing callouts to "awaiting orders".

### formation template + `fleet init` UX (Issue #105)

- formation: moved `src/fleet/presets/` → `src/fleet/templates/`. Dropped the term "preset formation"; the templates bundled with fleet are called "formation template", while the actual entities a project owns are called "formation".
- formation: added `--formation` / `--no-formation` / `--non-interactive` options to `fleet init`. On a TTY it shows an interactive picker that accepts a number or name, and copies the chosen formation template into `<state>/formations/`. On a non-TTY (CI / pipe), if `--formation` is not specified it completes with an empty formations/.
- formation: added a new `fleet formation init --from <template> [--name <name>]` subcommand. It lets you copy a formation template into an existing project after the fact.
- formation: updated the heading of `fleet formation list` from "preset formations:" → "template formations:".
- formation: changed the default of `fleet-agent start --formation` from `solo` to `None`. Resolution rule: explicit specification → strict load from `<state>/formations/` (no template fallback). Unspecified + exactly 1 present → auto-adopted. Unspecified + empty → synthesize an ad-hoc `_leader_solo` from the agent in `leader-session.json`. Unspecified + multiple → ambiguity error.
- leader: `fleet leader` now writes `<state>/leader-session.json` at startup. The start formation fallback reads this to synthesize a solo formation with the leader's agent.

### prompt deliverer submit retry (Issue #98)

- Detached prompt deliverer now waits briefly after paste, sends Enter, and verifies submit via adapter-specific working markers or by checking that the prompt pointer no longer remains in the active input line.
- If the pointer is still sitting at the Codex/Claude prompt, the deliverer retries Enter a bounded number of times before failing the task with an error event.

### Suppress Codex update prompt (Issue #93)

- Pass `-c check_for_update_on_startup=false` when launching the Codex driver, suppressing at the source the accident where the update prompt swallows the driver prompt.
- Added a `codex-update` optional check to `fleet preflight`. It compares the version of `codex --version`, the npm latest, and the npm global install, warning about an outdated CLI or a PATH mismatch.

### topology → formation rename (Issue #88)

- Renamed the concept name `topology` to `formation` across the board.

### Remove the race formation (2026-05-20)

**Migration note**: the `race` formation preset and the `candidates` shape have been removed.
This is the implementation of Issue #29 conclusion E (root overhaul, stage 1).

- Removed `src/fleet/presets/race.yaml`.
- The valid shapes for a formation are only `roles` / `stages` (`candidates` is invalid).
- The presets are the 3: `solo` / `pair_review` / `multi_stage`.

Formation YAML that used `candidates` must be rewritten to `roles` / `stages`.

### task.yaml schema overhaul (2026-05-20)

Root overhaul stage 2 (PR #46). The implementation of Issue #28/#29 conclusion D.

- Overhauled `task.yaml` to the new schema "1 task = 1 formation, with multiple stages".
- At task creation time, the formation definition is expanded and copied into task.yaml (snapshot).
- Each stage has a `status` (pending/running/done) and optionally `peer_review` / `user_approval`.
- The task-wide `status` is a cache derived from the stages.

### Rename/role change of `fleet spawn` → `fleet start` (2026-05-20)

Root overhaul stage 3 (PR #47). The implementation of Issue #29 conclusion C.

**Migration note**: `fleet-agent spawn` is gone. Driver launch is `fleet-agent start`.

- `fleet start <id> "<desc>" --formation T` = start a task (create task.yaml + worktree + launch the driver for the first stage).
- Removed the `--role` option (the orchestrator cycles through stages in order).
- Extracted "launch the driver for a given stage" into `launch_stage_driver()` (shared by start and the orchestrator).
- Changed `save_task` to `yaml.safe_dump` — task.yaml no longer breaks even when the title etc. contains `:` or `#`.

### New orchestrator.py + done.py made role-scoped (2026-05-20)

Root overhaul stage 4 (PR #48). The implementation of Issue #29 conclusion A/B.

- New `src/fleet/orchestrator.py` — a done-driven state machine. It has no daemon / polling.
- `fleet-agent done --result <approved|changes-requested>` — completion per role.
- done calls `orchestrator.advance()`, launching the next stage (or marking the task completed if there is none).

### peer_review loop + user_approval gate (2026-05-20)

Root overhaul stage 5 (PR #50). The implementation of Issue #28.

- stage attribute `peer_review` — within a stage, implement → review → if changes, return to the implementer (up to 3 rounds).
- stage attribute `user_approval` — before done, take the user's explicit approval via `fleet-agent ask`.
- Processing order within a stage: implement → peer_review (max 3) → user_approval → stage complete.

### Move git over to the driver (2026-05-20)

Root overhaul stage 6 (PR #51). The implementation of Issue #30.

- The work's git (commit / push / PR / conflict resolution) is done by the driver. The procedure is spelled out in `driver-base.md`.
- fleet core does not touch git other than creating/removing the worktree.
- Updated `docs/design.md` §8 to "the work's git is the driver's; only the worktree boundary is mechanism".

### root overhaul full inspection (2026-05-20)

Root overhaul stage 7 (PR #52).

- Removed the old `src/fleet/commands/spawn.py` (a 289-line leftover that was missed when start.py was created).
- Reconciled README.md / design.md / leader-handoff.md with the new design.
- Confirmed the 3 presets (solo / pair_review / multi_stage) conform to the new schema.

### Remove the `fleet-agent cleanup` notification (2026-05-20)

PR #49. Stopped the macOS / Slack notification on `cleanup`. Task-teardown notifications become noise and
bury the `ask` notification (which requires a user decision). The `done` / `ask` notifications are kept.

### inbox delivery + ack mechanism (2026-05-20)

Strengthened the leader → driver notification to be double-ended.

#### A. delivery — wake the driver pane after appending to the inbox

- In addition to appending to inbox.md and firing the event, `fleet-agent inbox` now sends
  notification text (`[fleet] new message in inbox. check it with fleet-agent inbox-read`) to the
  driver's tmux pane via `send-keys`.
- When the pane does not exist (driver not yet spawned / already terminated), it only warns and still succeeds at appending to the inbox.
- Added an `inbox_ts` field to the `inbox_message` event (the same timestamp as the inbox.md header).

#### B. ack — new `fleet-agent inbox-read` command (driver-side)

- Added `fleet-agent inbox-read`.
  - Prints the task's `inbox.md` to stdout.
  - As a side effect, appends an `inbox_seen` event to events.jsonl.
  - `watermark` = the value of the last `### <ISO8601>` header in inbox.md.
- The driver reads the inbox via `fleet-agent inbox-read` rather than reading it directly with `cat`.

#### C. driver-base.md rule change

- "check inbox each turn" → "read it with `fleet-agent inbox-read` (direct cat/Read reading prohibited)".
- The flow where a driver woken by delivery runs `fleet-agent inbox-read` is spelled out in the rule.

#### D. unread display — `fleet status`

- Added an `[unread inbox]` flag to the task list in `fleet status`.
- Determination: unread if the latest `inbox_message.ts` > the latest `inbox_seen.watermark`.

#### E. `task_context.resolve` improvement

- Changed to use the `FLEET_STATE_DIR` env var as a fallback for state-dir resolution.
  Used only when cwd-based discovery fails (a rescue for when the spawned pane's CWD is outside the project).

#### Migration note

- Driver-side rule update: read the inbox via `fleet-agent inbox-read` (direct cat reading prohibited).
- An `inbox_ts` field was added to the `inbox_message` event (backward compatibility maintained, WIP scope).

#### Tests

- `test_inbox_cmd.py`: added 3 delivery mock tests.
- `test_inbox_read_cmd.py`: new — 4 watermark-calculation + 6 inbox-read command = 10 total.
- `test_status.py`: added 6 unread-determination logic + 1 integration test.
- `test_cli_parsers.py`: agent command count 7 → 8 (inbox-read added).
- Tests: 166 cases pass.

### Fix `fleet-agent spawn` auto-paste so it sends Enter (2026-05-20)

- Passing an empty string to `tmux.send_keys` triggered an error in some tmux versions,
  which got swallowed by the outer `except TmuxError`, returning with Enter unsent.
- Fixed `send_keys`: when `text` is empty, skip the first `_run`, and
  guarantee that Enter is always sent when `enter=True`.
- No change to the spawn auto-paste path (the order `paste_buffer` → `send_keys("", enter=True)` is preserved).
- Added 6 tests: 4 mock-based unit tests for `send_keys` + 2 integration tests for spawn auto-paste.

### Extract the driver prompt into a markdown template (2026-05-20)

- Moved the prompt body that was hardcoded in `src/fleet/driver_prompt.py` to `docs/prompts/driver-base.md`.
- `driver_prompt.py` is now specialized to just template loading + variable substitution. Behavior is unchanged.
- By editing `docs/prompts/driver-base.md` directly, prompt changes become visible as a markdown review.

### `fleet-agent spawn` auto-paste by default (2026-05-20)

**Breaking change** — `--auto-prompt` flag removed; auto-paste is now the default.

- `fleet-agent spawn <id> "..."` alone starts the driver immediately (no manual paste needed).
- Use `--no-auto-paste` to restore the old behaviour (preloads buffer, manual paste via `C-b ]` or `fleet-agent send-prompt`).
- Migration: remove any `fleet-agent send-prompt` calls that immediately follow `spawn` — they are now redundant.

### Phase 13 — CLI split: `fleet` + `fleet-agent` (2026-05-20)

**Breaking change** — all commands are now split across two binaries.
No backwards-compatibility aliases. One-time cutover.

#### What changed

| Before | After | Binary |
|---|---|---|
| `fleet init` | `fleet init` | `fleet` |
| `fleet preflight` | `fleet preflight` | `fleet` |
| `fleet leader` | `fleet leader` | `fleet` |
| `fleet attach` | `fleet attach` | `fleet` |
| `fleet status` | `fleet status` | `fleet` |
| `fleet log` | `fleet log` | `fleet` |
| `fleet formation` | `fleet formation` | `fleet` |
| `fleet workflow` | `fleet workflow` | `fleet` |
| `fleet spawn` | **`fleet-agent spawn`** | `fleet-agent` |
| `fleet inbox` | **`fleet-agent inbox`** | `fleet-agent` |
| `fleet send-prompt` | **`fleet-agent send-prompt`** | `fleet-agent` |
| `fleet cleanup` | **`fleet-agent cleanup`** | `fleet-agent` |
| `fleet ask` | **`fleet-agent ask`** | `fleet-agent` |
| `fleet event` | **`fleet-agent event`** | `fleet-agent` |
| `fleet done` | **`fleet-agent done`** | `fleet-agent` |

#### New file

`./fleet-agent` — shebang script at repo root, same structure as `./fleet`.
Both import the same `src/fleet/` package; entrypoint decides which parser
to build (`build_parser_user()` vs `build_parser_agent()`).

#### PATH setup decision

`fleet-agent spawn` injects `PATH=<repo>:$PATH` into the spawned tmux window's
env. Rationale: repo-local injection keeps the binary self-contained without
requiring the user to modify their shell profile, and avoids conflicts if
multiple agent-fleet repos exist on the machine.

#### Migration note for running driver / leader panes

Any pane that was spawned before this change expects the old `fleet` command.
Stop those panes manually (`fleet-agent cleanup <id>`) and re-spawn. There is
no cutover path; WIP tasks should be restarted.

### Phase 12 — `fleet log` + `fleet send-prompt` (2026-05-19)

Observability + recovery shortcuts for the spawn flow.

- `fleet log [task-id] [-n N] [--type T]` — tail `events.jsonl` with
  optional task / type filters. The `--type` flag is repeatable
  (e.g. `--type spawn --type done`).
- `fleet send-prompt <task-id>` — re-load
  `<state>/tasks/task-<id>/driver-prompt.md` into the named tmux
  buffer and paste it into the task window. Companion to
  `fleet spawn`'s default (manual-paste) behaviour.
- Tests: 130 unittest cases pass (+5 log + 3 send_prompt).

### Phase 11 — `fleet attach` + `fleet inbox` (2026-05-19)

Two everyday-use shortcuts so the leader doesn't have to memorize tmux
syntax or hand-edit `inbox.md`.

- `fleet attach [<target>]`: shortcut for
  `tmux attach -t fleet-<project>:<window>`. Target is `leader`
  (default) or a task id (`fleet attach 1` → window `task-1`).
- `fleet inbox <task-id> "<message>"`: append a timestamped block to
  `<state>/tasks/task-<id>/inbox.md` and emit an `inbox_message`
  event so the dashboard reflects it.
- Tests: 122 unittest cases pass (+4 inbox + 3 attach).

### Phase 10 — README, CI, `fleet preflight` (2026-05-19)

Project hygiene pass — anyone arriving via `git clone` now has a clear
on-ramp.

- `fleet preflight` — verify Python ≥ 3.11, tmux, git, claude, codex.
  Required tools missing → exit 1; optional tools missing → warn.
- `.github/workflows/test.yml` — GitHub Actions runs `unittest` against
  Python 3.11 / 3.12 / 3.13 on `push` and `pull_request`.
- README rewritten: quick-start walkthrough, command catalogue (project
  / leader / tasks / driver-side / configuration), repo layout, and
  state-dir layout.
- Tests: 115 unittest cases pass (+4 preflight).

### Phase 9 — `fleet cleanup` + workflow teardown (2026-05-19)

Physical teardown is split from logical completion (`fleet done`).

- `fleet cleanup [task-id] [--archive] [--force]`:
  * Runs the workflow plugin's `on_cleanup` hook.
  * Kills the task's tmux window (if any) and drops its prompt buffer.
  * Optionally archives `tasks/task-<id>/` to `tasks/_archive/task-<id>/`.
  * Refuses non-terminal statuses (`completed`/`failed`/`cancelled`)
    unless `--force` is passed.
- `git_worktree` plugin gains `on_cleanup`:
  * `git worktree remove --force` the worktree.
  * `git branch -D task/<id>` (best-effort; missing branches are
    tolerated).
- Cleanup emits a `cleanup` event and rebuilds dashboard.md (archived
  tasks disappear from `list_tasks` because `_archive/` is outside the
  `task-*` glob).
- Tests: 111 unittest cases pass (+5 cleanup + 1 git_worktree cleanup).

### Phase 8 — dashboard rebuild + heartbeat (2026-05-19)

Dashboard becomes information-dense; drivers gain a "still alive"
signal — without forge's 6-feature lifecycle daemon.

- `fleet.heartbeat`:
  - `parse_ts(ts)`, `humanize_age(seconds)` ("12s ago", "3h ago"…)
  - `last_per_task(events)` — derive "last seen" per task from
    events.jsonl. Append-only audit log means latest entry wins
    naturally; no extra state needed.
- `fleet.dashboard.render`:
  - "⚠ Awaiting orders" highlight section above the task table when
    any task has status `awaiting_orders`.
  - Task table gains **Workflow** + **Last seen** columns.
  - "Recent events (last 10)" section at the bottom.
  - Workflow shown in the header block too.
- `fleet status` — same enrichment (awaiting-orders call-out + workflow +
  "seen" age + nicer event formatting).
- `driver_prompt`: rule added — "between long tool calls, emit a
  heartbeat (`fleet-agent event emit heartbeat`)". Still under the 40-line
  bloat tripwire.
- Explicit non-goal: no daemon, no auto-cleanup. forge's lifecycle
  layer (heartbeat / liveness / tamagotchi / janitor / custodian /
  leader_context) is replaced with **"surface the data, let the human
  decide"**. (design doc §10.2)
- Tests: 105 unittest cases pass (Phase 8 adds heartbeat 9 + dashboard 5).

### Phase 7 — `fleet leader` (2026-05-19)

The user-facing entry point from design doc §3 lands.

- `fleet leader [--project P] [--agent SPEC] [--attach]`:
  Creates a detached tmux session `fleet-<project>` with a single
  `leader` window in the project root and starts the agent CLI inside.
  Default agent: `claude:sonnet`.
- Single-instance per project: if the session already exists, prints
  the attach command and exits. `--attach` execs into `tmux attach`.
- Leader window inherits `FLEET_PROJECT` and `FLEET_STATE_DIR` env
  vars; cwd is set to the project root.
- `tmux.new_session(..., cwd=..., env=...)` extended to accept both.
- Emits a `leader_start` event with the chosen agent + session name.
- Tests: 90 unittest cases pass (+3 leader tests, 2 skipped if `tmux`
  missing).

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
    formation, role, agent, etc.
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
(design doc §7 — "the leader does not intervene").

- `fleet.task_context.resolve(...)` — figure out which task a driver-side
  CLI call is acting on, from (1) explicit `--task-id`, (2) `FLEET_TASK_ID`
  env var, (3) cwd inspection (`<state>/tasks/task-<id>/`).
- `fleet.notify` — best-effort macOS Notification Center + Slack webhook
  dispatch, configured per-project via `.fleet-state/notify.yaml`.
  Failures warn-only, never raise.
- `fleet ask "<question>"` — record `awaiting_orders` event, flip task status
  to `awaiting_orders`, append to `questions.md`, fire notification.
  Non-blocking; the driver re-checks `inbox.md` on its own cadence.
- `fleet event emit <type> [--field K=V ...]` — append arbitrary audit
  events to `events.jsonl` tagged with the current task id.
- `fleet done [task-id]` — flip task status to `completed`, emit `done`
  event. Real cleanup (worktree / branch / tmux window) is delegated to
  workflow plugins (Phase 5).
- Tests: 69 unittest cases pass total (+ Phase 4: task_context 6,
  notify 5, ask 3, event 3, done 3).

### Phase 3 — formation YAML + `fleet spawn` (2026-05-19)

`fleet` can now actually spawn a driver. End-to-end:
`fleet init` → `fleet spawn <id> "<desc>"` → claude/codex CLI lives in a
tmux window, with task state + prompt prepared on disk.

**Formation layer (3a):**
- `vendor/yaml/`: PyYAML 6.0.3 pure-Python sources, MIT license preserved.
  `./fleet` prepends `vendor/` to `sys.path`, so `import yaml` works
  without `pip install`.
- `src/fleet/presets/`: 4 stock formations — `solo`, `pair_review`,
  `multi_stage`, `race`. Cover roles / stages / candidates shapes.
- `fleet.formation`: `list_presets`, `list_custom`, `load_preset`,
  `load_custom`, `load(name, state_dir=)` (custom wins), `validate`
  (requires `name` + one of roles|stages|candidates).
- `fleet formation list` / `fleet formation show <name>` CLI.

**Spawn layer (3b):**
- `fleet.agents.parse_spec("claude:sonnet")` / `cli_command(...)` —
  resolve `vendor:model` specs into argv. claude + codex only (design
  doc §13.2).
- `fleet.driver_prompt.render(...)` — produces a compact (~30 line)
  initial prompt. Bloat tripwire test ensures it stays small.
- `fleet.tmux` — thin `subprocess` wrap around tmux session/window/keys.
  Mechanism only, no fleet vocabulary.
- `fleet spawn <id> "<description>"`:
  - Resolves formation + role + agent (with `--role` / `--agent` overrides).
  - Writes `task.yaml` (status=spawning, agent, formation, role).
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
  YAML parser lands in Phase 3 when formation files demand nesting).
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
