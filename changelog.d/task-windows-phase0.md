### chore: record Windows Phase 0 verification results

Updates `docs/windows-support.md` with the Phase 0 results on Windows +
zellij 0.45.1. Verified: claude pointer paste + `Enter`, `Ctrl u`,
`new-tab --no-focus`, sole-client attach focus, and pane env inheritance.
Raises the minimum zellij to 0.45.0, because 0.44.3 has no
`new-tab --no-focus`. Also records that the pane launcher must strip
inherited Claude Code session markers, and that `claude --no-chrome` avoids
the Chrome-extension startup dialog.
