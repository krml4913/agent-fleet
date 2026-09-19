### feat: Windows-compatible core (no tmux yet)

fleet now imports and runs its non-multiplexer commands on Windows: a portable
file lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) with a bounded
`os.replace` retry, explicit UTF-8 for file I/O, subprocess output and CLI
stdout/stderr, a `spawn_detached()` helper for the prompt deliverer and leader
notifier, `os.pathsep` in the driver `PATH`, and case-insensitive prompt-file
project inference. New `fleet.cmd` / `fleet-agent.cmd` shims run the CLIs from
PowerShell / cmd, and agent prompts embed the forward-slash `fleet-agent.cmd`
path on Windows. CI gains a `windows-latest` unittest job. POSIX behavior is
unchanged.
