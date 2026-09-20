### change: zellij is now the default multiplexer on every platform; new global config (`fleet config`)

> **Behaviour change for existing macOS / Linux users.** The built-in default
> multiplexer used to be tmux everywhere except Windows; it is now **zellij on
> every platform**. If you run fleet on tmux and want to keep it, use **either**:
>
> - `fleet config set mux tmux` — persistent (writes `fleet-state/global/config.yaml`)
> - `FLEET_MUX=tmux fleet ...` — per shell / per command
>
> Do this before your next fleet command if tmux sessions are still running:
> fleet only looks for sessions in the selected backend, so under the new
> default it will not see them. zellij on macOS / Linux is less exercised than
> tmux there (#258).

New global config file `fleet-state/global/config.yaml` (cross-project, next to
`global/leader-memory/`), first key `mux: zellij|tmux`, read through the new
`fleet.config` module (cached per process; a missing file means the defaults, an
unreadable or invalid file only warns and is ignored). Backend selection is now
`FLEET_MUX` env > global config > built-in default; `FLEET_NO_MUX` /
`FLEET_ZELLIJ` are unchanged. `fleet.mux.backend_selection()` returns the name
and its source; `WINDOWS_DEFAULT_BACKEND` is gone (`DEFAULT_BACKEND` is
`zellij`).

`fleet config` prints every key with its source, `fleet config get <key>` prints
one value, and `fleet config set <key> <value>` writes it through the locked
atomic-write helpers; an unknown key or invalid value is rejected with the valid
ones listed. `fleet preflight` gains a `mux` line showing the selected backend
and where the choice came from (`env` / `config` / `default`; the default case
names the two ways back to tmux). Documented in README (en/ja) and
`docs/design.md` (§5.3). Tests no longer assume tmux is the default off Windows
(backend-selection tests pin `FLEET_HOME` and are platform-independent). Closes
#299.
