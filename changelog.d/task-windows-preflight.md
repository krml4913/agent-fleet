### feat: `fleet preflight` checks the configured multiplexer backend and Windows setup

- The multiplexer check follows the backend (`FLEET_MUX`, else zellij on
  Windows / tmux elsewhere). tmux: `tmux -V` as before. zellij: parses
  `zellij --version` and fails below 0.45.0 (0.44.x lacks
  `new-tab --no-focus`); 0.45.0–0.45.1 pass with a ⚠ note that the detached
  new-tab workaround for zellij#5594 is active.
- Windows only: warns when the clone path contains spaces (the fleet-agent
  path is embedded unquoted in prompts), when `core.longpaths` is not true
  (with the fix command), and fails when `fleet-agent.cmd` is missing.
- claude / codex are reported with their resolved absolute path. On Windows,
  when `PATH` lookup fails, `%USERPROFILE%\.local\bin` and `~`-prefixed
  `PATH` entries are tried, and a CLI found only that way is flagged
  (agent panes may not find it on `PATH`).
- A check can now be "ok with a warning" (⚠, exit code unaffected).
