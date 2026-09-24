### feat: Windows toast — fleet-owned sender name and click-to-attach (phase 1)

`fleet notify setup-windows` / `teardown-windows` register (HKCU only, no admin)
a fleet-owned AUMID (`AppUserModelId\agent-fleet`, so toasts show as
"agent-fleet" instead of "Windows PowerShell") and the `fleet://` URL protocol.
`fleet.notify.send` now accepts optional `project` / `task_id` context (passed
by driver `done` / `ask` and the orchestrator's `awaiting_orders` / approval
paths); once setup has run, a toast carrying that context gets a
`fleet://attach?project=…&task=…` launch target. The new hidden `fleet
url-handler <uri>` subcommand — invoked by Windows under `pythonw`, no console
of its own — validates the URI against the project/task registry and opens a
visible terminal (Windows Terminal when present) running `fleet attach`.
Unconfigured machines are unaffected: toasts keep using PowerShell's borrowed
AUMID and carry no launch target. `fleet preflight` reports setup status
(informational only). Approve/reject toast buttons are phase 2 (#318).
