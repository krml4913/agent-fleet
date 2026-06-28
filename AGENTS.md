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

## branching & releases

agent-fleet uses **trunk-based development** with lightweight milestone tags:

- Feature branches → PR → **`main`**. There is no long-lived `develop` branch.
  Keep `main` green and releasable at all times.
- Distribution is **clone-from-`main`**: agent-fleet runs by `git clone` + `./fleet`
  (no `pip install`/published artifact). So merging to `main` immediately reaches
  anyone who clones — `main` is effectively a continuous release; there is no gate
  between "merged" and "available".
- **Tags are bookmarks, not a deploy gate.** Cut a milestone tag (`vX.Y.Z`) plus a
  GitHub Release only at meaningful checkpoints — not on every merge — to give a
  pin-able reference point and a CHANGELOG boundary. The leader cuts the tag/Release
  after the merge; drivers do not tag.
- **CHANGELOG fragments** are the normal path for new changes. Each PR adds one
  `changelog.d/<task-id>.md` file containing its entry instead of editing
  `CHANGELOG.md` `## [Unreleased]` directly. Use the task id for the filename so
  parallel PRs create different files and GitHub can merge them without a
  changelog conflict. A fragment is regular Markdown, typically:
  `### feat|fix|change|chore|test: short summary`, followed by a short body.
- **Assembling fragments** is on-demand, not a daemon. Run
  `./fleet changelog` to concatenate `changelog.d/*.md` under
  `## [Unreleased]` and delete the fragment files, or
  `./fleet changelog --version X.Y.Z --date YYYY-MM-DD` to create a versioned
  section at a milestone. Existing `[Unreleased]` entries from before the
  fragment convention remain as history; do not convert them during ordinary PRs.
- **CHANGELOG `## [Unreleased]`** means "merged to `main` but not yet folded into a
  named milestone tag" (since `main` itself is already available). New entries
  reach it through `changelog.d/` fragments; when a milestone tag is cut, assemble
  the remaining fragments and move entries under `## [X.Y.Z] - <date>`.
- **CHANGELOG conflict fallback.** `CHANGELOG.md` remains marked `merge=union` in
  `.gitattributes` as belt-and-suspenders for old branches or manual edits, but
  drivers should not rely on direct `## [Unreleased]` edits for new work.

## memory (write project knowledge to fleet memory, not your vendor's own)

When you — driver **or** leader — are asked to remember something, write it to the
**fleet memory**, never to your vendor's own auto-memory (e.g. Claude Code's project
memory). fleet is multi-vendor: a leader or driver may run on Claude, Codex, or
another agent, and one vendor's private memory is invisible to the others — siloing
knowledge there defeats the shared, vendor-neutral memory the project exists to provide.

- **Project knowledge** → `fleet-agent memory write <name> [--type project|reference|feedback]`
  (the per-project store at `<state>/memory/`, read by every vendor's drivers and the
  leader). Separate from any vendor's own auto-memory — do not double-manage the two.
- **Cross-project leader rules / user preferences** → the global leader-memory
  (`global/leader-memory/`), injected into every leader prompt regardless of vendor.
- Which tier: does it travel with the leader across **all** projects (global) or with
  **one** project across all sessions (per-project)?
- If you record project knowledge in a vendor's own auto-memory by reflex, move it to
  fleet memory and delete the vendor-side copy.

## role-specific discipline

Additional discipline specific to each role is documented in `docs/prompts/roles/<role>.md`.
Only the project-wide git discipline is written here.
