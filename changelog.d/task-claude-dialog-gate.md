### fix: selection dialogs no longer count as a ready prompt

An un-numbered selection menu (e.g. claude's "Claude in Chrome extension
detected" startup dialog, `❯ No, keep browser tools off`) matched the adapter's
`ready` regex, so the prompt deliverer pasted the driver-prompt pointer into the
dialog and the leader notifier could inject into it. Adapters now expose
`is_ready` / `is_gated` / `is_dialog`: a dialog is detected structurally at the
bottom of the pane (a `Enter to confirm` / `Esc to …` footer, or an indented
un-numbered cursor option with an aligned sibling option), vetoes readiness, and
is reported as a boot gate (`awaiting_orders`). Keyword matches in conversation
history (e.g. "login") still do not veto readiness.
