# Backlog

実装 TODO の置き場。設計判断が要る open question は `docs/design.md` §11 へ。
完了したら削除 (履歴は git log と CHANGELOG.md で十分)。

## priority

- [ ] **spawn のデフォルト挙動見直し** — 現状 `--auto-prompt` を明示しないと
      driver-prompt は tmux buffer にロードされるだけで手動 paste が要る。
      実運用では煩雑なので、`--auto-prompt` をデフォルトにする/しないを判断する。
      関連: `src/fleet/commands/spawn.py`, `fleet send-prompt`
- [ ] **`fleet --help` の整理** — user 向け / driver 向けが混ざってて分かりづらい。
      cli-split task で方針提案中 (2026-05-20 spawn)。
- [ ] **`pr-based-workflow` plugin の実装** — driver done 時に自動 commit / push /
      `gh pr create` までやる workflow。design.md §8.3 で想定済み、§11 優先 2
      「workflow plugin 具体」の一部。現状 git_worktree は worktree + branch までで
      止まる。

## someday

(空)
