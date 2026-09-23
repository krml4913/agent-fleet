### fix: on macOS, fleet refuses to run a Gatekeeper-quarantined zellij

A zellij binary downloaded with a browser (or extracted by `tar`) can carry the
`com.apple.quarantine` xattr. The first exec then pops a Gatekeeper dialog —
with a "Move to Trash" button that deletes the binary — even from `fleet
preflight` or an unattended driver pane (#309).

`fleet preflight` now resolves every tool it is about to probe and checks
that xattr first (`fleet.macos_quarantine`, ctypes `getxattr`, no
subprocess). A quarantined binary fails the check immediately — `zellij
--version` (or `tmux -V`, or an agent CLI) is never run — with a hint to
`brew install <formula>` or, after confirming where the binary came from,
`xattr -d com.apple.quarantine <path>`. The zellij backend
(`fleet.mux.zellij.ZellijMux`) shares the same guard: its own `--version`
probe and every exec path raise an explicit error instead of running a
quarantined binary. The killed-by-signal and timeout hints added in #308 are
refined to name Gatekeeper quarantine specifically (re-signing does not
help) instead of a vague "macOS may have blocked the binary".

No primary source (Apple open-source headers/docs) could be confirmed for
the meaning of individual bits in the quarantine value (the "user approved"
bit some blog posts cite) — see the module docstring — so this only checks
*presence* of the xattr, not its flags. The xattr is never written or
removed automatically; that stays the user's call. Off macOS, behavior is
unchanged (no ctypes load).
