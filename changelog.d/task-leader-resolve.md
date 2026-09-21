### fix: a renamed or recreated leader tab no longer strands leader notifications; a stuck queue is visible in `fleet status` / `fleet sessions`

The leader notifier addressed the leader pane only as `fleet-<label>:leader`. Closing the
leader tab and starting the leader again leaves a tab with the multiplexer's default name
(`Tab #3`), so every poll failed with "tab not found" and two driver tasks waited in
`awaiting_orders` for a day while the queue sat in `leader-pending.jsonl`.

- **Find the leader by its pane title.** When `capture` fails and the `leader` window is
  missing, the notifier looks for the pane titled with the leader agent's session name
  (`<label>-leader`, the name `fleet leader` launches it with; it survives a window rename),
  renames that window back to `leader`, logs it to `leader-notifier.log`, and carries on. It
  only acts on a `fleet-<label>` session, matches the name as a whole token (so a driver
  named `<project>-<task>-<role>` is never taken for the leader), and does nothing when no
  pane or more than one window matches. Works on tmux and zellij: the `Mux` interface gains
  `list_panes(session)` (window name, window id, pane title) and `rename_window(session,
  window_id, new_name)`. A leader agent that does not put its session name in the pane title
  (e.g. codex) is not found this way; the queue still stays pending and is re-armed as before.
- **Show what is pending.** `fleet status` (project view and `--all`) and `fleet sessions`
  print `N leader notifications pending (oldest 3h ago)` per session whose
  `leader-pending.jsonl` is not empty; nothing is printed in the normal, empty case.
  `fleet sessions` lists a session that has only a stranded queue too, and the dashboard
  snapshot (`collect_sessions`) carries the same `pending` summary.

Closes #302 (items 1 and 2; a `fleet leader --adopt` / `fleet sessions repair` command, item
3, is not part of this).
