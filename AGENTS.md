# agent-fleet driver 規律

> これは agent-fleet repo 向けの規律。
> 他 PJ の driver は自分の PJ の AGENTS.md / CLAUDE.md を読め。

このリポジトリで fleet driver として作業するときの規律。
worktree を切って動く前提 (workspace=worktree)。

## git workflow (作業の git は driver が担う)

作業完了後は必ず以下の手順を実行してから `fleet-agent done` を呼ぶ:

1. `git add` / `git commit` — 変更を commit する
2. `git push -u origin <branch>` — remote に push する
3. `gh pr create` — PR を作成する (タイトル・本文を適切に記述)
4. `fleet-agent done` — 最後に done を呼んで orchestrator に通知

- PR のマージは driver が行わない。マージは leader / user の判断に委ねる。
- conflict が発生した場合は driver (AI) が自力で解決する:
  `git fetch origin main` → `git rebase origin/main` (または `git merge`) →
  conflict を手動編集して解消 → `git rebase --continue` → `git push --force-with-lease`
- push reject された場合も driver が原因を調べて対処する (force-with-lease / rebase 等)。
- 作業の git (commit / push / PR) は fleet core ではなく driver (AI) の責務。
  fleet core が git commit / push / PR を自動実行することはない。

## role 固有の規律

各 role 固有の追加規律は `docs/prompts/roles/<role>.md` に記載。
ここには PJ 共通の git 規律のみ書く。
