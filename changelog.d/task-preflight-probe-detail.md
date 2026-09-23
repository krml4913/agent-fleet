### fix: `fleet preflight` explains why a tool's `--version` probe failed

A zellij binary downloaded from a GitHub release and killed by macOS on launch only
showed `✘ zellij required TimeoutExpired` — neither the exception name nor a bare
`non-zero from \`zellij\`` said what actually happened.

`_check_command` now reports the concrete failure:

- Timeout: `timed out after 5s running \`zellij --version\``.
- Killed by a signal (negative returncode): `killed by SIGKILL running
  \`zellij --version\``; on macOS this also notes that Gatekeeper (quarantine /
  code signing) may have blocked the binary and that installing via Homebrew
  avoids it.
- Non-zero exit: `exit 2 running \`zellij --version\`: <first stderr line>`.

Required/optional and the `ok` flag are unchanged.
