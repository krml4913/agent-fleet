### chore: document the macOS zellij quarantine issue and fix (#309)

README / README.ja gain a short "macOS" section (mirroring "Windows"):
`brew install zellij` as the recommended install (curl-fetched bottles carry
no quarantine attribute), why a browser-downloaded release binary gets killed
by Gatekeeper on first exec, the fix (`xattr -d com.apple.quarantine <path>`
after confirming the binary's origin, or re-download with `curl`; re-signing
does not help; don't disable Gatekeeper), and `xattr -l <path>` for
diagnosis. Docs only — the preflight guard that stops fleet from exec'ing a
quarantined binary in the first place is tracked separately (#309 part 1,
task-q-guard).
