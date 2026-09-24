### feat: Windows toast — Approve / Reject / Open buttons on approval gates (phase 2)

With `fleet notify setup-windows` done, the two approval-gate toasts (`done`'s
"stage N/M awaiting approval" and the orchestrator's "needs approval") now carry
**Approve**, **Reject** and **Open** buttons. Plain `ask` toasts, handoffs and
unconfigured machines are unchanged. Approve/Reject use `fleet://approve|reject`
URIs with a single-use nonce (`toast-nonces.json` in the task dir, bound to
project + task + stage, 24h expiry); the handler refuses a missing, unknown,
used or expired nonce, or a task no longer at that gate, and logs every refusal.
Approve asks for confirmation in a native message box; Reject asks for the reason
in a native input box (cancel / empty does nothing). Both run the same code path
as `fleet-agent approve` / `reject --reason`, record events with `source: toast`,
and queue a `toast_decision` note for the owning leader (opt-in
`notify_leader_on_driver_done`) so it does not re-approve; merging stays with the
leader. Open is the phase-1 attach.
