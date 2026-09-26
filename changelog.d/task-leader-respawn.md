### feat: `fleet leader --respawn` restarts only the leader agent of a live session

A leader session's drivers live in the same multiplexer session as its leader, so
"kill the session and run `fleet leader` again" was not an option once any task ran,
and `fleet leader` on an existing session only printed "leader session already exists".
`fleet leader --respawn [--name LABEL] [--agent SPEC]` now replaces just the leader
window (zellij and tmux): it starts a new window with exactly the command line a fresh
`fleet leader` builds (incl. the #329 relay flags when `leader_delivery` is
`send_message`), updates `delivery` / `agent_name` / `agent` in `session.json` through
the locked state writer (keeping `scope` and `started_at`), closes the old leader
window, renames the new one to `leader` and re-pastes the leader prompt. Driver
windows and `leader-pending.jsonl` are untouched. The agent defaults to the one the
session was started with. A leader window renamed by hand is found by its pane title.
Run from inside the session (e.g. in the leader pane after quitting the agent, or by
the leader itself), the work is handed to a detached helper that waits for the calling
command to exit, logging to `global/sessions/<LABEL>/leader-respawn.log`.
