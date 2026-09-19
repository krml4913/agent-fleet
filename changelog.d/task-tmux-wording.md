### fix: make user-facing help and messages multiplexer-neutral

Since the multiplexer became tmux or zellij, `fleet --help`, the `fleet-agent`
`start` / `cleanup` / `merge` / `done` / `inbox` / `rm` / `attach` help text, the
`--dry-run` output ("dry-run: multiplexer step skipped."), the related docstrings
and the README prose still said "tmux". They now say "multiplexer" (tmux window /
zellij tab), and the tmux-only hints (`C-b ]` manual paste, `C-b d` detach) are
labelled as tmux-only, with the zellij equivalent (`fleet-agent send-prompt`,
`Ctrl o` then `d`) alongside. Wording only; no behavior change. Closes #275.
