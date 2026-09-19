### feat: `fleet-agent reject` takes a reason and always tells the driver it was rejected

`fleet-agent reject <id> [--reason TEXT | --reason-file PATH]` (mutually
exclusive; the file is read as UTF-8) relays the user's feedback to the driver.
Every reject now appends a `[fleet reject]` block to the task inbox **before**
the relaunch / peer-review handoff: it says the user rejected the stage, carries
the reason when given, and tells the driver not to re-submit unchanged work (with
no reason it points the driver at `fleet-agent ask`). Previously a stage without
`peer_review` was relaunched with nothing in the inbox but the stale
`[fleet verify]` message, so the driver re-submitted the same work. The reason
is also recorded on the `reject` event. Docs: leader prompt command list, README
(en/ja) command tables, `docs/formations.md`, `docs/design.md`. Closes #285.
