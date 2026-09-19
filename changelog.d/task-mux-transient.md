### fix: a transient multiplexer error no longer makes the prompt deliverer or the leader notifier give up

One inconsistent zellij pane listing (`tab not found` while another tab is being closed)
used to fail a task's prompt delivery and strand a leader notification for an hour, with
nothing in the logs.

- **Prompt deliverer:** a `MuxError` from capture / rename / paste / submit Enter is
  retried with a short backoff until the delivery deadline; it fails the task only if it
  persists or the session is confirmed gone. Closes #289.
- A successful delivery (including `fleet-agent send-prompt`) of a task the deliverer had
  marked `failed` moves it back to its stage-derived status and emits
  `prompt_delivery_recovered`.
- With `notify_leader_on_driver_done` on, a deliverer failure is also pushed to the owning
  leader (`kind: "delivery_failed"`: "delivery failed, retry with `fleet-agent send-prompt`").
- **Leader notifier:** `MuxError` from `capture` and a single `session_exists() == False`
  keep polling until the deadline, then re-arm like the busy case; it gives up only when
  the session is confirmed gone. #290's idle/confirm/submit logic and #291's ask records are
  unchanged. Closes #292.
- The notifier now logs its decisions (spawn pid/timeout, lock contention, wait reasons,
  flush result, deadline/re-arm, exit reason) to `leader-notifier.log`, at low volume.
