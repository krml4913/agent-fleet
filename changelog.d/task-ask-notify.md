### feat: `fleet-agent ask` also reaches the owning leader pane when `fleet notify` is on

With `notify_leader_on_driver_done` on (`fleet notify --project P on`), a driver's
`fleet-agent ask "<q>"` now enqueues a `kind: "ask"` record (status `awaiting_orders`,
carrying the question) into the owner session's leader queue and spawns the detached
notifier, exactly as `done` does — the shared enqueue/spawn logic moved from
`done._maybe_notify_leader` into `leader_notifier.push_to_leader`. The injected line
tells the leader to answer with `fleet-agent inbox <id> "<answer>" --project <P>`
(or relay to the user) instead of "pull the diff and run the gate"; a mixed batch
keeps the gate instruction for done entries. An ask and a later done for the same
task are independent records and never suppress each other. The OS notification
`ask` sends is unchanged. Documented in design §10.1/§10.3, README (en/ja) and the
leader prompt. Closes #287.
