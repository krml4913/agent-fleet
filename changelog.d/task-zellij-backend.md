### feat: zellij multiplexer backend (default on native Windows)

`fleet.mux.zellij.ZellijMux` implements the multiplexer interface on zellij
(>= 0.45): driver windows are tabs in the owner session, every pane runs the
new `fleet.pane_launch` launcher (per-pane env from a JSON file, inherited
Claude Code session markers stripped, agent CLI resolved to an absolute path),
failures are detected explicitly (zellij exits 0 on most of them), and the
zellij 0.45.x detached-tab bug (#5594) is worked around with a hidden
temporary client. Backend selection: `FLEET_MUX` wins, otherwise zellij on
Windows and tmux elsewhere (`fleet.mux.default_backend_name()`; preflight uses
the same rule). Also: driver panes get `FLEET_SESSION`; cleanup / merge close
the task's windows (and wait for the agent to exit) before removing its
worktree; the test suite never touches a real multiplexer unless
`FLEET_LIVE_TMUX` / `FLEET_LIVE_ZELLIJ` is set.
