### feat: optional `shell:` field for verify commands

A stage's `verify` block can now set `shell: bash | sh | pwsh | powershell | cmd`
to pick the shell that runs the command. Without it nothing changes (the
platform shell: `/bin/sh`, or `cmd.exe` on Windows), so POSIX-style verify
commands can finally be used in formations on Windows with `shell: bash`. On
Windows `bash` / `sh` resolve to Git Bash (never the WSL launcher in
`System32`); `pwsh` / `powershell` run with `-NoProfile -NonInteractive`.
`formation.validate()` rejects an unknown `shell` value. A shell that is not
installed is reported to the driver as a failed verify run (exit 127) with a
message naming the shell, and the usual `max_iterations` cap escalates it. New
`fleet.verify_shell` module; documented in `docs/formations.md` §2.5. Closes #260.
