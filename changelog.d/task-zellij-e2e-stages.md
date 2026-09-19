### fix: multi-stage handoff and peer-review launch on Windows/zellij

- A cross-stage advance run from a driver's own tab (`fleet-agent done`, or an
  in-pane `approve` / `reject`) no longer kills itself. On Windows, closing a
  zellij tab ends every process on its console, so `done` died inside
  `kill_window` before the next stage's tab was opened, and the task was left
  on a stage with no window. When the backend reports
  `window_close_kills_caller` (zellij on Windows only; tmux is unchanged), the
  launch now runs in a detached helper (`fleet.deferred_launch`) after `done`
  exits (`stage_launch_deferred` event, log in `<task>/stage-launch.log`).
- The zellij backend drops `ZELLIJ`, `ZELLIJ_SESSION_NAME` and `ZELLIJ_PANE_ID`
  from the environment of the clients it spawns. A fleet command started from
  a pane of the same session (a driver's `done` that opens the reviewer or the
  next stage, or a leader's `start`) could not attach the zellij#5594 temp
  client when no client was attached: zellij refuses to attach to "the current
  session".
- Verify output is captured as bytes and decoded line by line: UTF-8 first,
  then (on Windows) the OEM code page that `cmd.exe` built-ins use, so
  localized cmd messages (such as cp932) no longer turn into U+FFFD in the
  driver's inbox.
- `fleet preflight` takes the zellij minimum version and the #5594 workaround
  range from the backend constants (`MIN_VERSION`,
  `FIXED_DETACHED_TAB_VERSION`), so its warning matches what the backend does.
