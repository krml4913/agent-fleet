### feat: claude drivers relay leader notifications with SendMessage instead of typing into the leader pane

The leader notifier delivered driver `done` / approval-gate / `ask` notifications by
typing into the leader's composer. When the human was typing in the same place, the
two texts could merge (#329). Now, when a claude driver reports to a claude leader,
`fleet-agent done` / `ask` print a `[fleet] leader relay` block: the leader's session
name (`<label>-leader`) and the rendered notification. The driver sends it verbatim
with Claude Code's `SendMessage` tool (the rule is also in `driver-base.md`). It
arrives as a cross-session message, not through the composer, so a draft there is
never touched. `fleet leader` launches claude leaders with the launch-only
`--settings '{"crossSessionInbound":"accept"}'`, so the message is never held behind
an approval dialog, and records `delivery: send_message` / `agent_name` in
`session.json`.

Relayed records stay in `leader-pending.jsonl` as the record (marked
`delivery: send_message`). The pane notifier never types them, and they do not count
as pending. Codex drivers, non-claude leaders, `fleet-agent done` run outside Claude
Code, and `fleet config set leader_delivery pane` keep the pane-typing path. A leader
started before this change has no `delivery` field, so it keeps pane typing until it
is restarted with `fleet leader`.
