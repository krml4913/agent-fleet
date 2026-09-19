### fix: workspace=none driver panes open in the project root

A task without a worktree used to open its driver pane / window inside its task
dir under `fleet-state/`, even though the driver prompt tells it to work in the
project root — inviting edits to fleet state. `launch_stage_driver` now starts
the pane in the project's `repo` (for both `start` and the orchestrator's later
stages), keeping the task dir only as a fallback when the project has no `repo`
or it is missing on disk. Nothing in the pane relied on the task dir as cwd
(`FLEET_TASK_ID` / `FLEET_STATE_DIR` are in the env; prompt and outbox paths are
absolute). Token usage for such tasks is not read from the shared project root,
since its session logs cannot be attributed to a single task.
