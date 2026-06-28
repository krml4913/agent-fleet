### change: replace direct changelog edits with changelog fragments

New PRs now add one `changelog.d/<task-id>.md` fragment instead of editing
`CHANGELOG.md` `## [Unreleased]` directly, removing the GitHub-side mergeability
conflict that parallel changelog edits still hit. A new on-demand
`fleet changelog` command assembles fragments into `CHANGELOG.md` under
`## [Unreleased]` or a versioned heading and deletes the assembled fragment files.
The existing `CHANGELOG.md merge=union` rule stays as a fallback for old branches
or manual edits. Closes #232.
