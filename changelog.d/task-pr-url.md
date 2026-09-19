### fix: leader notification finds the PR even when the driver wrote `PR #N`

The leader-pane notification showed `PR=(none yet)` whenever the driver's
`outbox.md` said `PR #280` instead of a full PR URL. `scan_pr_url` now tries, in
order: the full `https://github.com/<owner>/<repo>/pull/<n>` URL (unchanged);
a `PR #<n>` / `pull request #<n>` mention expanded with the repo's `origin`
remote (github https / ssh forms); and, at notifier flush time only, a
best-effort `gh pr list --head <branch> --state all --json url` lookup (only
when `gh` is on PATH, 5s timeout, never raises, never runs inside `done`).
`docs/prompts/driver-base.md` now tells drivers to record the full PR URL in
`outbox.md`. Closes #281.
