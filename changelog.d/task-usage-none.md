### fix: record token usage for workspace=none tasks

Since #262 a workspace=none driver pane runs in the project root, but
`state.record_task_usage` still looked for the session logs under the task dir,
so these tasks recorded no usage. The root is shared with the leader and other
tasks, so the directory alone cannot identify a task's logs. The lookup now also
narrows to the sessions the task's own prompt pointer (the first thing fleet
pastes into the pane) started: `VendorAdapter.usage_from_session` takes an
optional `pointer`, and both the claude and codex adapters keep only the
session logs whose first pointer mention is that task's. Worktree tasks and the
task-dir fallback (a project with no usable `repo`, or panes that predate #262)
are unchanged. Closes #264.
