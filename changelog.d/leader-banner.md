### feat: startup banner for `fleet leader`

A new leader session now prints a sailing-fleet + `FLEET` ASCII-art banner, then one
info line (`  leader: main · agent: claude:opus · scope: all` — `scope` is the comma list of
scoped projects, or `all`), ahead of the existing `leader started:` / `attach:` lines. The
art ships as `src/fleet/assets/leader-banner.txt` and is rendered by the new
`fleet.banner` module. With `--attach`, `fleet leader` pauses 2 seconds after printing so
the banner is visible before the multiplexer takes over the screen; there is no wait
without `--attach`, and none on the "leader session already exists" path (no banner there).

Color (white-bold sails/masts, yellow hulls, blue sea, cyan-bold logo) is used only on a
TTY with `NO_COLOR` unset — the same rule as `fleet sessions`. The banner degrades by
terminal width (`shutil.get_terminal_size`): the full art when it fits, the logo alone
when only the logo fits, the info line alone below that. A stdout encoding that cannot
encode the logo's box-drawing characters (e.g. a non-UTF-8 Windows console) skips the art
and uses `|` instead of `·` in the info line. The banner is decoration only: any error
while rendering or writing it is swallowed and never fails `fleet leader`.
