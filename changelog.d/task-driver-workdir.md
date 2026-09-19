### fix: driver prompt states the working directory; `CLAUDE_EFFORT` no longer leaks into panes

- The driver prompt now has a short "Working directory" section: with
  `workspace=worktree` it names the task worktree (absolute path) and branch,
  with `workspace=none` the project root, and it forbids editing the fleet
  state (`fleet-state/`, `$FLEET_STATE_DIR`, the task dir) directly — only
  through `fleet-agent` commands, plus appending to the task's `outbox.md`.
  Previously a driver whose task description did not say where to work could
  commit inside its task dir in the agent-fleet clone.
- The zellij pane launcher now also strips `CLAUDE_EFFORT` (the creating
  Claude Code session's effort level, which was silently forced onto every
  driver/leader pane) and `AI_AGENT` (the creating agent's identity).
