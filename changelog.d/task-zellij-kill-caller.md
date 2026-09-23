### fix: zellij treats closing a tab as killing the caller on every platform, not only Windows

`ZellijMux.window_close_kills_caller` returned `True` only on Windows; on macOS / Linux a
`fleet-agent done` run from inside the stage's own tab still called `kill_window` on that tab
directly. A real macOS run (#313, zellij 0.45.1) showed this hangs up (SIGHUP) the tab's
foreground process group just as it ends every console process on Windows: the designer's
`done` closed its own tab and died before the implementer's tab ever opened, stranding the
task on a stage with no window.

The property is now `True` for zellij on every platform, so the orchestrator always hands a
same-pane cross-stage advance to the detached `fleet.deferred_launch` helper (already used on
Windows), which survives via `fleet.proc.spawn_detached`'s `start_new_session=True`. Docstrings
in `zellij.py`, `deferred_launch.py` and `docs/windows-support.md` are corrected: earlier POSIX
testing only checked a caller that had already detached into its own session, not how a real
agent's tool-call shell runs `fleet-agent done` (a plain foreground job).
