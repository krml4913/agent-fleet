### fix: accept `--project` after the subcommand of grouped commands

`fleet formation show solo --project P` (and `formation list`, `workspace list|set`)
failed with `unrecognized arguments: --project P`; `--project` was only accepted
before the subcommand for these groups (`fleet formation --project P show solo`).
Every subcommand of `formation`, `workspace` and `role` now accepts `--project` in
both places; when both are given, the one after the subcommand wins. The
`argparse.SUPPRESS` pattern from `fleet formation|role seed` is factored into a
shared helper (`commands/_project_arg.py`), and a parser-audit test fails if a
future grouped command adds a group-level `--project` without repeating it on its
subcommands. Closes #279.
