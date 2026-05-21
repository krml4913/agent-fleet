# Backlog

実装 TODO の置き場。 設計課題 / open question は **GitHub Issues** で管理する。
完了したら削除 (履歴は git log と CHANGELOG.md で十分)。

## priority

- `fleet-agent start` に `--prompt-file PATH` を追加する。現状 driver-prompt は
  `"$(cat file)"` で巨大なシェル引数として渡しており、引数長・クォートのエッジケースの
  リスクがある。ファイルを直接食わせられるようにして `$(cat)` を廃止する。
  Issue #67 (multi-project 再設計) が落ち着いてから着手。

## someday

(空)

---

2026-05-20 dogfooding session で旧項目はすべて消化済み (実装済み or
GitHub Issues へ移行)。 設計課題は `gh issue list` を見ろ。

新しい実装 TODO が出たらここに足す。
