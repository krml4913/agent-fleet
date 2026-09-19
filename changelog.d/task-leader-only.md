### feat: `merge` / `cleanup` refuse to run from a driver pane

`fleet-agent merge` and `fleet-agent cleanup` are leader-only, but that was
enforced by driver-prompt discipline alone (Refs #188). They now refuse with a
"leader-only action" error when run from a driver pane, detected by the
`FLEET_TASK_ID` that `launch_stage_driver` injects into every driver pane (tmux
and zellij alike); the leader pane and a user's own terminal never carry it and
are unaffected. `--allow-from-driver` overrides the guard on purpose — `--force`
does not, since it only skips the terminal-status check. `docs/prompts/driver-base.md`
now lists `merge`, `cleanup`, `approve`, `reject` and `start` as leader-only.
This is a soft guard, not role enforcement: the state-machine / role-gating
option in #188 stays open.
