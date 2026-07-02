### feat: add stage-level verify gates

Stages can now declare a `verify:` block whose project-declared command runs in
the task worktree, or project root for `workspace: none`, before peer review or
user approval. Non-zero exits and timeouts bounce to the implementer with
captured output; repeated failures escalate to `awaiting_orders`.
