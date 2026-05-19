# Backlog

実装 TODO の置き場。設計判断が要る open question は `docs/design.md` §11 へ。
完了したら削除 (履歴は git log と CHANGELOG.md で十分)。

## priority

- [ ] **spawn のデフォルト挙動見直し** — 現状 `--auto-prompt` を明示しないと
      driver-prompt は tmux buffer にロードされるだけで手動 paste が要る。
      実運用では煩雑なので、`--auto-prompt` をデフォルトにする/しないを判断する。
      関連: `src/fleet/commands/spawn.py`, `fleet-agent send-prompt`
- [ ] **`fleet --help` の整理** — user 向け / driver 向けが混ざってて分かりづらい。
      cli-split task で方針提案中 (2026-05-20 spawn)。
- [ ] **`pr-based-workflow` plugin の実装** — driver done 時に自動 commit / push /
      `gh pr create` までやる workflow。design.md §8.3 で想定済み、§11 優先 8
      「workflow plugin 具体」の一部。現状 git_worktree は worktree + branch までで
      止まる。
- [ ] **task archive 後の成果物保存** — `fleet-agent cleanup --archive` で task state は
      退避されるが、worktree は削除される。レビュー履歴や driver 出力を
      後で振り返るための保存手段が無い。branch tip 保持 or diff スナップショット
      の検討。
- [ ] **driver-prompt の固定文を .py から別 .md に切り出す** — 現状
      `src/fleet/driver_prompt.py` などに prompt 本文が Python 文字列として
      直書きされていて読みづらい。`docs/prompts/` 等に markdown として配置し、
      Python 側はテンプレ読み込み + 変数差し込みに徹する構成にしたい。
      関連: §11 優先 7「prompt 構造」。
- [ ] **driver への指示はファイルベースに統一** — `fleet-agent inbox` や `fleet-agent spawn` の
      description で文字列を直接送る現方式から、`tasks/task-<id>/messages/<n>.md`
      のようなファイルに書き出して driver に参照させる形に寄せる。やり取り全般を
      ファイル化することで diff / 履歴 / レビューが効くようにする。
      関連: §11 優先 5「dialogue trace」、優先 6「inbox ack」。

## someday

(空)
