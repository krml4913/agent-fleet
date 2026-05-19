# Backlog

実装 TODO の置き場。 設計判断が要る open question は `docs/design.md` §11 へ、
方針議論 ドラフトは `docs/proposals-summary.md` からたどれ。
完了したら削除 (履歴は git log と CHANGELOG.md で十分)。

## priority

- [ ] **`pr-based-workflow` plugin の実装** — driver done 時に自動 commit / push /
      `gh pr create` までやる workflow。 design.md §8.3 で想定済み、 §11 priority 8
      「workflow plugin 具体」 の一部。 現状 git_worktree は worktree + branch まで。
      ※ 提案ドキュメント未作成。 実装に先んじて方針 proposal が必要。

## someday

(空)

## proposal で議論中 (backlog からは外したもの)

これらは `docs/proposals-summary.md` から各 proposal を参照:

- ~~task archive 後の成果物保存~~ → `docs/archive-retention-proposal.md` で議論中
- ~~driver への指示はファイルベースに統一~~ → `docs/dialogue-trace-proposal.md` +
  `docs/inbox-ack-proposal.md` で議論中
- ~~spawn のデフォルト挙動見直し~~ → 実装済み (#9 + #12 + #15)
- ~~`fleet --help` の整理~~ → 実装済み (#7)
- ~~driver-prompt の固定文を .py から別 .md に切り出す~~ → 実装済み (#11)
