### change: put every tmux call behind a multiplexer backend (`fleet.mux`)

`src/fleet/tmux.py` is replaced by `src/fleet/mux/` — a `Mux` backend interface
(`base.py`), the tmux backend (`tmux.py`), and `mux.get()` returning the
process-wide backend (`FLEET_MUX`, default `tmux`; `zellij` raises "not
implemented yet" until the next PR). Every caller (leader, start, attach,
cleanup, done, inbox, rm, send-prompt, sessions, status, the orchestrator,
prompt deliverer and leader notifier) goes through it, so a zellij backend can
be added without touching them. No behavior change on tmux:

- Launch takes argv (`new_window(argv=…)`, `new_session(argv=…)`); the tmux
  backend still opens a shell window with `-e` env and types the
  `shlex`-joined command, so the pane keeps a shell after the agent exits.
- Buffers leave the interface: `paste(text)` uses a one-shot tmux buffer
  (`paste-buffer -d`). The per-task `fleet-task-<id>` buffer for
  `--no-auto-paste` / `C-b ]` is staged by `preload_paste()` and dropped by
  cleanup via `drop_paste()`.
- Keys are explicit: adapters return `Key("Ctrl-u")` (codex `/rename` flow)
  and backends translate normalized key names (tmux `C-u`). Typed text is sent
  with `send-keys -l` (literal).
- `fleet attach`'s grouped view session (Issue #76) moved into the tmux
  backend's `attach()`; `tmux attach -t …` hints come from `attach_hint()`.
- `TmuxError` → `MuxError` (the tmux backend's `TmuxError` subclasses it).
- `FLEET_NO_MUX` disables the multiplexer; `FLEET_NO_TMUX` stays as an alias.
- Tests use a shared recording fake backend (`tests/_fake_mux.py`).
