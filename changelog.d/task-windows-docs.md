### chore: document native Windows support (zellij)

README / README.ja gain a "Windows" section: requirements (Python >= 3.11,
zellij >= 0.45.0, Git for Windows, agent CLIs on `PATH`), installing zellij
(`winget install Zellij.Zellij` or a release zip), `fleet.cmd` /
`fleet-agent.cmd`, a clone path without spaces, `core.longpaths`, `FLEET_MUX`,
what `fleet preflight` checks, attach under zellij, and known limitations. The
intro no longer says tmux is required everywhere. `docs/design.md` describes
the mechanism as a terminal multiplexer (tmux on macOS/Linux, zellij on
Windows) behind `fleet/mux/`, and `docs/windows-support.md` is marked
implemented (#247–#251) with a list of remaining follow-ups.
