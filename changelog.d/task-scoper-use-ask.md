### change: scoper poses questions via `fleet-agent ask`, not the CLI interactive menu

The scoper role prompt now instructs the scoper to surface every question with
the `fleet-agent ask "<question>"` shell command rather than its CLI's built-in
interactive multiple-choice menu (the AskUserQuestion tool). The in-pane menu
blocks without calling `fleet-agent ask`, so the task stayed `running` (heartbeat
stopped) instead of parking at `awaiting_orders`, hiding the wait from fleet and
the leader — the same class of status lie as #226. Options now go inside the
question text and the user replies in the pane.
