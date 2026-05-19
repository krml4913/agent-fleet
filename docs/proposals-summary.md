# Proposals Summary

> 2026-05-20 dogfooding session 中に生成された方針提案ドキュメントのインデックス。
> 各 proposal は議論用ドラフトとして main にマージ済み、 実装は user/leader の
> 議論で順次合意してから別 driver で進める前提。
>
> 詳細は各 proposal の本体を読め。 ここは入り口。

---

## 一覧 (status)

| # | proposal | §11 | status | 主な決定要素 |
|---|---|---|---|---|
| 1 | `cli-split-proposal.md` | — | **実装済み** (#7, #9, #12, #15) | `fleet` / `fleet-agent` 2 binaries |
| 2 | `role-structure-proposal.md` | 4 | 議論待ち | task.yaml の `role:` を SOT、 env `FLEET_DRIVER_ROLE` 注入 |
| 3 | `dialogue-trace-proposal.md` | 5 | 議論待ち | user→driver の answer/生入力を events.jsonl + dialogue.md に記録 |
| 4 | `inbox-ack-proposal.md` | 6 | 議論待ち | helper 経由 auto-ack + watermark event の段階導入 |
| 5 | `prompt-structure-proposal.md` | 7 | 議論待ち | 規約 + 行数 cap + base 1 ファイル維持 |

---

## 各 proposal の TL;DR

### 2. role の構造化 (§11 priority 4)

driver が自分の role を知る経路が現状 **散文 prompt 任せ**。 「dynamic prompt
injection 廃止」 方針と矛盾しかけ。 実態は構造化情報が task.yaml と
driver-prompt.md の front matter に二重で書かれているので **保管は既にある**、
問題は driver が機械的に取り出す API が無いこと。

**推奨:** task.yaml の `role:` を SOT、 spawn 時に pane env
`FLEET_DRIVER_ROLE` を注入、 取り出し用に `fleet-agent role` を追加。
役割の値域は fix しない (topology が任意に決められる)。 role 切り替えは
別 driver の spawn で扱う (§11 priority 2 = topology orchestration の領分)。
`FLEET_ROLE` は cli-split-proposal §7-4 で却下した別概念なので **別名** にする。

### 3. dialogue trace (§11 priority 5)

agent-fleet の核心は 「user は driver と直接対話する」 (§1.2)。 ところが対話の
うち **user 側の発話** が events.jsonl にも file にも残らない。 ask に対する
answer も、 pane での自発発話も、 半永久に蒸発する。 audit / 引き継ぎ / archive
振り返りが全部効かない。

**推奨:** ask には対応する answer を `fleet-agent answer "<text>"` で記録する
最低ライン (案 A) を即時導入。 pane 全文 capture (案 B) は task ごとに
opt-in、 全任意で取り回す。 [[inbox-ack-proposal]] と [[backlog "ファイル
ベース化"]] の方向と整合。

### 4. inbox の read/ack 機構 (§11 priority 6)

`fleet-agent inbox` で leader が投げた message を **driver が読んだか** を
返す path が無い。 結果、 leader は 「投げた、 でも driver は気づいてない」
状態を区別できない。 inbox.md は free-form markdown で message id も無い。

**推奨:** 段階導入 (案 A)。 第 1 段階で driver-base.md の rule + helper
(`fleet-agent inbox-read`) 経由読みに変えて auto-ack を吐かせる + watermark
event を導入。 message id は timestamp ベース。 inbox.md は据え置きで
構造化 (案 B の inbox.jsonl) は将来オプション。

### 5. prompt 構造 (§11 priority 7)

`prompt-md-split` (PR #11) で **driver-base.md の収容先は確定**。 残るは
「base に何を書いて、 何を書かないか」 の政策。 先行 3 proposal がすべて
「base.md に 1 行案内を足したい」 と書いていて、 このまま受け入れると base が
肥大化する。 priority 7 の本質は **雪だるまを止める規律**。

**推奨:** 案 D (規約 + cap + 単一ファイル維持)。 driver-base.md は
1 ファイル / 行数 cap (例 50 行) / 「contract に書くべき」 ものだけ。
role / workflow 別の分岐は将来必要になってから判断、 まず単一 base で持つ。
規約は `docs/prompts/README.md` に書く。

---

## 議論の進め方 (user 復帰時の想定)

1. **role-structure (priority 4)** から議論する流れが自然 — env / cli / 命名の話で他 proposal の前提
2. **inbox-ack** と **dialogue-trace** はセットで議論できる (両方が events.jsonl と driver 通信の話)
3. **prompt-structure** は他 3 proposal の集大成 — 1 行ずつ base.md に書きたい要求群に対する規律
4. 合意がついたら **§11 priority 1-3 (root)** にようやく入れる:
   - completed の定義
   - topology orchestration
   - driver / workflow 責務分界

---

## 関連 backlog 項目

- **driver 指示のファイルベース統一** — dialogue-trace / inbox-ack で議論中、 合意次第削除予定
- **`pr-based-workflow` plugin の実装** — §11 priority 8 と同じ論点
- **task archive 後の成果物保存** — dialogue-trace と関連 (dialogue ごと archive 含める設計)

---

## 注意 (auto-pilot で観測した dogfooding 課題)

ここまでの自動運転で driver が hit した穴。 設計判断後に解消すべし:

- driver は **commit までやらない** (前 3 proposal は leader が代行 commit、 inbox-ack 以降は自走) — §11 priority 3
- topology orchestration が無いので pair_review でも leader が手動で reviewer 起動 — §11 priority 2
- `fleet done` が role 単位ではなく task 単位で confirmed になる — §11 priority 1
- auto-paste の paste/Enter race が 2 回 fix された (PR #12, #15) — 動作確認は dogfooding 経由のみ
