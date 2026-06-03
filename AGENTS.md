# agent-fleet driver discipline

> This discipline applies to the agent-fleet repo.
> Drivers for other projects must read their own project's AGENTS.md / CLAUDE.md.

Discipline for working as a fleet driver in this repository.
Assumes you operate on a checked-out worktree (workspace=worktree).

## git workflow (the driver is responsible for the work's git)

After completing work, always run the following steps before calling `fleet-agent done`:

1. `git add` / `git commit` — commit the changes
2. `git push -u origin <branch>` — push to the remote
3. `gh pr create` — create a PR (write an appropriate title and body)
4. `fleet-agent done` — finally, call done to notify the orchestrator

- The driver does not merge the PR. Merging is left to the judgment of the leader / user.
- If a conflict occurs, the driver (AI) resolves it on its own:
  `git fetch origin main` → `git rebase origin/main` (or `git merge`) →
  manually edit and resolve the conflicts → `git rebase --continue` → `git push --force-with-lease`
- If a push is rejected, the driver also investigates the cause and deals with it (force-with-lease / rebase, etc.).
- The work's git (commit / push / PR) is the responsibility of the driver (AI), not fleet core.
  fleet core never runs git commit / push / PR automatically.

## role-specific discipline

Additional discipline specific to each role is documented in `docs/prompts/roles/<role>.md`.
Only the project-wide git discipline is written here.
