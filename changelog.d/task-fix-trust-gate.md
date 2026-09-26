### fix: first claude task in a newly registered project no longer stalls silently on the trust prompt (#327)

A brand-new project's first claude driver stops at Claude Code's "Is this a project you
created or one you trust?" dialog. The prompt deliverer already parked the task as
`awaiting_orders`, but with a generic "boot gate" message, so nothing said why. fleet
still never answers the dialog (and never writes claude's config); it now makes the wait
loud:

- The deliverer recognises the trust dialog on the pane (new `trust_gate` pattern on the
  claude and codex adapters). The `awaiting_orders` event gains `gate: "trust"`, and the
  question / `questions.md` / notification say the task is waiting on the workspace trust
  prompt, how to clear it, and how to avoid it next time.
- `fleet-agent start` prints a one-time warning before launching a claude driver in a repo
  claude has not trusted yet. It does not abort (unlike codex): claude only waits, and the
  deliverer resumes once the dialog is answered.
- `fleet preflight` gains an optional `claude-trust` check. It resolves a linked worktree
  to the main checkout, since claude keys trust by repo root.

Trust is read-only from `projects["<repo root>"].hasTrustDialogAccepted` in
`~/.claude.json` (`$CLAUDE_CONFIG_DIR/.claude.json` when set); an unreadable config means
"unknown" and stays silent. `docs/windows-support.md` corrects the #259 note: a trusted
ancestor directory does not make a worktree trusted.
