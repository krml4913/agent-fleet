### test: live zellij backend suite + Linux CI job; fix zellij temp client and Ctrl+C on POSIX

`tests/test_mux_zellij_live.py` drives `ZellijMux` end to end against a real zellij with a
plain `/bin/sh` as the pane command (no agent CLI, no API key): session create / kill (and
the resurrectable entry), tabs created detached and with a client attached, tab / pane
listing, per-pane env and cwd through `pane_launch` (agent markers stripped), `send_text` /
`send_key` (Ctrl-u, Ctrl-c) / `paste` / `capture` round-trips, closing a tab (waits for the
pane's processes), and the behaviours fleet relies on: an `action` on a missing session or
tab exits 0 (so the listing-based errors), and a stage advance run from a caller inside the
tab it closes completes. Sessions are `fleet-citest-<random>`, torn down even on failure;
nothing else is touched.

It is opt-in like the tmux live tests (`FLEET_LIVE_ZELLIJ=1 python -m unittest
tests.test_mux_zellij_live -v`, `FLEET_ZELLIJ` for a zellij off `PATH`) and skips cleanly
without zellij, so the default `python tests/run_parallel.py` stays hermetic. A new
`unittest-zellij-linux` CI job installs zellij 0.45.1 (pinned, checksum-verified) on
ubuntu-latest and runs it. Refs #258 (a live driver E2E with a real agent CLI on
macOS / Linux is still open).

The Linux run found and fixed two backend bugs:

- **fix:** the #5594 temporary client (`zellij attach S`) was started with stdio on
  `/dev/null`; without a controlling terminal (CI, the detached deliverer / notifier) it
  never registers as a client, so every detached `new_window` on 0.45.x failed with
  "temporary zellij client did not attach". On POSIX it now runs on a pseudo-terminal.
- **fix:** `pane_launch` ignored SIGINT *before* spawning the agent, and an ignored signal
  survives exec, so on POSIX the agent and the commands it starts could never be
  interrupted (`send_key Ctrl-c` to a shell in a pane did nothing). It now ignores SIGINT
  only after the spawn there (Windows unchanged).
