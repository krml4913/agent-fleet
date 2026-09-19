### test: de-flake `test_git_worktree` copytree vs. git auto-maintenance (#272)

The shared template repo in `tests/test_git_worktree.py` now sets
`maintenance.auto=false` and `gc.auto=0` right after `git init`, so
`git commit` no longer spawns a background maintenance run that creates and
removes `.git/objects/maintenance.lock` while `shutil.copytree` is copying it.
The per-test copy also ignores `*.lock` as a guard. The template approach (one
build, a copy per test) is unchanged, so there is no speed cost.
