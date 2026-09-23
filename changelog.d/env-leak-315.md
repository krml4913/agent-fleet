### fix: harden zellij pane env against a leaked leader FLEET_STATE_DIR; normalize `task-` ids

`pane_launch.py` stripped inherited agent-session markers before applying a pane's
`FLEET_*` overrides, but not any inherited `FLEET_*` itself. zellij has no per-pane
environment (every pane inherits the zellij *server's* environment, i.e. whichever
process created the session): when that session was created from a leader's shell
(`FLEET_SESSION` / `FLEET_STATE_DIR` set to the leader's session dir), a driver pane
whose per-pane overrides don't happen to cover a given key could see that leader value
leak through instead. `build_env` now strips every inherited *task-scoped* `FLEET_*`
key (`FLEET_SESSION` / `FLEET_STATE_DIR` / `FLEET_TASK_ID` — the only ones a pane's
overrides ever set) before applying the per-pane overrides, the same way it strips
agent markers — a blanket `FLEET_*` strip would also have dropped user-level switches
such as `FLEET_HOME` / `FLEET_MUX` set in the shell that created the session (#315).

As defense in depth, `task_context.resolve()` (used by `done` / `ask` / `event` /
`approval` / `cleanup` / `merge` / `inbox-read`) now self-heals when `FLEET_STATE_DIR`
resolves to a leader session dir but `FLEET_TASK_ID` is set: it falls back to
cwd-based registry resolution (a driver's cwd is its worktree) and trusts the result
only once confirmed to actually own that task id. A session dir with no usable
`FLEET_TASK_ID` still fails loudly, pointing at `--project`, instead of guessing.

A leading `task-` in an explicit task id (e.g. `fleet-agent done task-q-guard`, copied
from a `task-<id>` directory name) no longer doubles into `task-task-<id>`:
`task_context.normalize_task_id()` strips one leading `task-`, applied in `resolve()`
and at the raw task-id entry points of `log`, `send-prompt`, `inbox` and `status --json`.
Since that stripping is now unconditional, `fleet-agent start` rejects a task id that
itself begins with `task-` (e.g. `task-foo`) up front — such a task would otherwise
live in `tasks/task-task-foo` but become unaddressable by every other command.

The third item in #315 (a notifier polling an absent/empty leader pane in a mixed-mux
setup) turned out to already be covered by the existing `fleet status` stranded-queue
warning (`⚠ session <label>: N leader notifications pending`), added the day before
this issue was filed (#292) — no further change needed there.
