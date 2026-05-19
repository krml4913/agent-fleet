# inbox の read/ack 機構 方針提案

> 作成: 2026-05-20 / driver: task-inbox-ack
> ステータス: 議論用ドラフト (実装前)
> 関連: `docs/design.md` §5.3 §7 §11 priority 6、 `docs/backlog.md`
>      (「driver への指示はファイルベース統一」)、 `docs/cli-split-proposal.md`、
>      `docs/role-structure-proposal.md`、 `docs/dialogue-trace-proposal.md`、
>      `src/fleet/commands/inbox.py`、 `src/fleet/commands/spawn.py`、
>      `src/fleet/events.py`、 `tasks/task-*/inbox.md`、 `docs/prompts/driver-base.md`

---

## 0. TL;DR

`fleet-agent inbox <id> "..."` で leader が driver に投げた指示は inbox.md
に追記され `inbox_message` event も発火するが、 **driver が読んだか否か** を
返す return path が無い。 結果、 leader は 「inbox に投げた、 でも driver は
気づいてない」 状態を区別できない。

inbox.md は今 free-form markdown (timestamp ヘッダ + body) で **message id
が無い**。 driver の read 経路は driver-base.md の rule (「check it each
turn」) 一行のみで、 実体は driver の `cat inbox.md` 直読み任せ。

推奨は **案 A の段階導入 (helper 経由 auto-ack + watermark event)**:

- **phase 1 (最低ライン)**: `fleet-agent inbox` を post / read の **dual
  mode** に拡張。 引数なしで叩くと当該 task の inbox.md 全文を stdout に
  出し、 副作用で `inbox_seen` event を発火 (watermark = inbox.md 末尾
  block の timestamp)。 driver-base.md に 「inbox は CLI 経由で読め、 cat
  直読み禁止」 rule を 1 行追加。 これで 「driver が一度でも CLI を叩け
  ば、 そこまでは確実に読んだ」 が events.jsonl に残る
- **phase 2 (構造化、 dialogue-trace-proposal phase 2 と同期)**:
  inbox.md を `tasks/task-<id>/messages/<seq>-<dir>-<topic>.md` に統合
  する流れに乗せ、 message_id 単位の ack に格上げ。 watermark から
  per-message ack に粒度を上げる

**やらないこと**: inbox.md を即座に `inbox.jsonl` 化する案 (案 B 単独)
は **dialogue-trace-proposal phase 2 の messages/ 統合と二重作業になり**、
中途半端な構造変更を残す。 案 B 単独は却下。 case 3 (in-place ✓ ack 追
記) は inbox.md 自体に副作用が走り race / 混乱の元、 採用しない。

dialogue-trace-proposal (片方向対話の双方向化) と本書 (leader→driver
の return path) はスコープが分かれているが、 **phase 2 で同じ
`messages/` ストリームに合流する**。 phase 1 段階で構造を作り込みすぎ
ないことで、 phase 2 の刷新を素直に受け入れられるようにする。

役割の値域、 watermark の粒度、 leader UX、 後方互換の各論は §2-§6 で詰
める。

本書は方針合意までで止まる。 実装は次フェーズ。

---

## 1. 現状の整理

### 1.1 leader → driver の現フロー

```
leader pane          $ fleet-agent inbox 42 "再現手順を outbox に貼ってくれ"
                            │
                            v
src/fleet/commands/inbox.py
  inbox_path.write_text(existing + block)              # ファイル追記
  append_event(..., "inbox_message", message=body)     # events.jsonl
                            │
                            v
tasks/task-42/inbox.md
  ### 2026-05-20T03:45:11Z

  再現手順を outbox に貼ってくれ
                            │
                            v ★ ここで途切れる
driver pane          (driver は次 turn で cat inbox.md する、 か、 しないか)
                     leader / events.jsonl には何も返らない
```

(`src/fleet/commands/inbox.py:49-60` を見て確認)

### 1.2 「読んだか分からない」 の具体的症状

| 症状 | 起きる場面 |
|---|---|
| 二度送ってしまう | leader が反応が無いので再投函、 driver は同じ指示を 2 回受ける |
| 待つだけになる | leader が 「読んだ前提」 で待つ、 driver が実は inbox を見てない |
| 固まり検知の判断材料が無い | heartbeat だけだと 「動いてるが inbox を見てない」 を判別不可 |
| audit が片肺 | events.jsonl に `inbox_message` はあるが対の receipt が無い |

### 1.3 inbox.md の format と message 同定

`inbox.md` の append は以下の format (`commands/inbox.py:51` の `block`):

```
### 2026-05-20T03:45:11Z

(本文)

```

- ヘッダは `### <UTC ISO8601>`、 body は free-form markdown
- **message id は無い**。 timestamp が事実上の id だが秒粒度
  なので同 second 2 投函で衝突する可能性
- events.jsonl 側の `inbox_message` event にも message id は付かない
  (`ts` / `type` / `task_id` / `message` のみ)

### 1.4 driver の read 経路

- driver-base.md L9-10:
  > `inbox.md   — instructions from the leader; check it each turn.`
- driver-base.md L11: `outbox.md  — append reports here at milestones.`
- driver 実装上は 「check it each turn」 の文言だけで、 **どう check するかは driver 任せ**。
  実際は claude / codex CLI が `cat tasks/task-<id>/inbox.md` するか、 task ディレクトリの
  `inbox.md` を Read tool 経由で読む

→ 「読んだ」 を観測する hook が現在 一つも無い。

### 1.5 既に確定 / 検討中の方針との照合

| 方針 | 整合性 |
|---|---|
| §5.3 state 構造 (`tasks/task-<id>/`) | 既存ディレクトリに event 追加で素直に乗る |
| §5.4 race 対策 (flock + atomic rename) | inbox.md は append-only だが write は inbox.py が `read_text + write_text` で実装、 ここは別途 flock 化が望ましい (本提案範囲外、 別タスク) |
| §7 driver 通信プロトコル | ask / event の構造圧力と同じ路線で、 inbox 読了を 「構造で記録」 する方向と一致 |
| §10.2 dynamic prompt injection 廃止 | driver-base に 1 行追加のみで済む方向と一致 |
| cli-split-proposal (`fleet-agent` 2 バイナリ) | post / read mode の dual command でも `fleet-agent` 側に収まる |
| role-structure-proposal (task.yaml SOT) | role 同様、 inbox の watermark も既存 file に乗せる方向で揃う |
| dialogue-trace-proposal phase 2 (messages/ 統合) | **phase 2 で同じ stream に合流**、 本提案は phase 1 で軽く先行 |
| backlog 「driver への指示はファイルベース統一」 | 同方向、 phase 2 で接続 |
| backlog 「task archive 後の成果物保存」 | watermark event は events.jsonl に乗るので archive 設計に追加負担なし |

---

## 2. 観点 (本提案で詰める論点)

タスク依頼で提示された 7 つの論点を 1 つずつ整理する。

### 2.1 ack を何のために残すか

優先順:

1. **leader のオペレーション補助** — 「投げた指示が届いたか / 再送が要るか」 の判断材料。 これが無いと leader は heartbeat だけで推測、 user 体感の 「driver が指示を見てない」 不安が解消しない
2. **監査 / リプレイ** — `inbox_message` event の対になる receipt が events.jsonl に残れば audit / replay 整合が取れる
3. **固まり検知の入力** — heartbeat だけだと 「動いてるが inbox を見てない」 状態を判別できない。 watermark と最新 `inbox_message` の差分で 「未読 N 件 / X 時間滞留」 のメトリクスを足せる (§11 priority 10 へのデータ提供)

(1) が一番強い動機。 (2)(3) は (1) を満たした副産物として自然に付いてくる。

### 2.2 ack のトリガ

候補:

| 案 | トリガ | drift リスク | 実装コスト |
|---|---|---|---|
| (a) 能動 ack CLI | driver が `fleet-agent inbox-ack <message-id>` を毎回叩く | 高 (叩き忘れ) | 中 |
| (b) next event 時 auto-ack | driver の他 event (progress / heartbeat) 発火時に最新 inbox を全部 ack 済みとマーク | 低 (受動) | 中 |
| (c) helper 経由 read で auto-ack | inbox を読む経路を CLI に絞り、 helper 内で watermark を進める | 低 (read 経路が ack 経路) | 小 |
| (d) tmux fs watch | inotify / fsevents で driver の cat を観測 | 不可 (cat は file mtime を変えない) | 大 (実機不可) |

判定:

- (a) は ask 側でも 「叩き忘れ」 のリスクが指摘されている。 inbox 側で更に強い rule を増やすと driver-base.md の rule 数が膨張する
- (b) は 「他 event 発火時の副作用」 で、 「inbox を読まずに event を発火」 した場合も ack 済みになる嘘 ack の可能性。 副作用の主体が ambiguous
- (c) は driver が cat 直読みを止めて CLI 経由に揃えるだけで、 read = ack の構造圧力が成立。 ask 側の 「pane に書くだけでは届かない」 圧力と同じ性質
- (d) は cat が file mtime / atime を変えない (atime は relatime / noatime mount で更新されない) ため実装不可

→ **推奨は (c)**。 (a) は drift、 (b) は嘘 ack、 (d) は技術不可。 (c)
は driver-base.md に 1 行 (「inbox は `fleet-agent inbox` で読め、 cat
直読み禁止」) を加えるだけで構造が成立する。

(c) の弱点は 「driver が rule を破って cat で読む」 余地。 ask 側でも
「pane に質問を書いただけでは届かない」 を rule 圧力で運用しており、
完全防止は無理。 同レベルの担保で十分。

### 2.3 メッセージ単位の同定 (message id 設計)

候補:

| 案 | id 形式 | 衝突可能性 | 既存形式変更 |
|---|---|---|---|
| (i) timestamp 単独 | `2026-05-20T03:45:11Z` | あり (同 second multi 投函) | なし |
| (ii) timestamp + hash | `2026-05-20T03:45:11Z-a3f2` | ほぼなし | inbox.md ヘッダに hash 追記 |
| (iii) 採番 seq | `0042` (task 内 monotonic) | なし | inbox.md ヘッダに seq 追記 |
| (iv) UUID v4 | `c4f1...` | なし | 長い |

判定:

- phase 1 (inbox.md 維持) では (i) で十分。 watermark は 「最新 ack 済み
  timestamp」 を保持するだけで、 個別 message id は要らない
- phase 2 (messages/ 統合) で per-message ack を入れる時に (iii) seq が
  自然 (dialogue-trace-proposal §2.6 の `<seq>-<dir>-<topic>.md` と整
  合)。 phase 2 で採番

→ **phase 1 では message id 不要 (watermark で代替)、 phase 2 で seq
採番**。

### 2.4 ack の保管場所

候補:

| 案 | 場所 | SOT 数 | race リスク |
|---|---|---|---|
| (α) events.jsonl に `inbox_seen` event | events.jsonl | 単一 | なし (O_APPEND) |
| (β) 別 file `tasks/task-<id>/inbox-ack.jsonl` | 別 file | 2 つ (events と分離) | 別途 flock 要 |
| (γ) inbox.md 内に `✓ ack at <ts>` 追記 | inbox.md | inbox.md と混在 | あり (read+write の race) |

判定:

- (α) は dialogue-trace-proposal §2.2 の判定と同じ路線 (event sourcing 単一 SOT)、 既存 `append_event` で済む
- (β) は SOT を増やすだけで利点なし。 「inbox 専用」 の意味付けは弱い
- (γ) は inbox.md 自体に副作用、 driver の cat 出力にも `✓ ack` が混じって混乱、 race リスクも増える

→ **推奨は (α) events.jsonl に `inbox_seen` event**。

### 2.5 dialogue-trace-proposal との関係

dialogue-trace-proposal のスコープ:

- ask に対する user の answer trace
- outbox event 化
- phase 2 で `messages/<seq>-<dir>-<topic>.md` に統合

本提案のスコープ:

- leader → driver の inbox.md 読了 (return path)
- phase 2 で同じ `messages/` に合流

両者は **方向が違うが終着点が同じ**:

- dialogue-trace: 「driver → user の質問」 と 「user → driver の回答」 の対称化
- inbox-ack: 「leader → driver の指示」 と 「driver の receipt」 の対称化

phase 2 で `messages/` に統合される時点で、

| 経路 | message kind | 対の receipt |
|---|---|---|
| leader → driver | `kind: inbox` | driver の read で `inbox_seen` |
| driver → user (質問) | `kind: ask` | user の answer (dialogue-trace 担当) |
| driver → leader / user (報告) | `kind: outbox` | (受信側の ack は phase 2 で議論) |

→ phase 2 で 「message stream + receipt event」 という統一構造に合流す
る。 本提案は phase 1 として inbox 側だけ先行する形だが、 dialogue-
trace-proposal phase 2 と整合する設計にしておく。

**役割分担**:

- 本提案 (inbox-ack): leader → driver 方向の read/ack を扱う
- dialogue-trace (ask/answer): driver ↔ user の対話を扱う
- phase 2 で messages/ に統合するときに両方が同じ `dialogue_message` /
  `message_seen` 等の event スキーマに収まる、 設計上 conflict なし

別々の CLI に分けるか統合 (例: `fleet-agent message-ack`) するかは
phase 2 開始時に再決定。 phase 1 段階では `fleet-agent inbox` (read
mode) で独立。

### 2.6 backlog 「driver への指示はファイルベース統一」 との整合

backlog (`docs/backlog.md` priority 3):

> driver への指示はファイルベースに統一 — `fleet-agent inbox` や
> `fleet-agent spawn` の description で文字列を直接送る現方式から、
> `tasks/task-<id>/messages/<n>.md` のようなファイルに書き出して
> driver に参照させる形に寄せる。 やり取り全般をファイル化することで
> diff / 履歴 / レビューが効くようにする。 関連: §11 優先 5「dialogue
> trace」、 優先 6「inbox ack」。

backlog 自身が本提案 (§11 priority 6) と dialogue-trace (§11 priority
5) を関連項目として明記している。 phase 2 で `messages/<n>.md` に統合
する方向は backlog 方針と完全一致。

phase 1 で inbox.md format を変えずに watermark event だけ足すのは、
phase 2 の刷新を二重作業にしないための **意図的な手抜き** であり、
backlog の方針と矛盾しない (phase 2 で messages/ 統合と一緒に刷新す
る)。

### 2.7 後方互換

`commands/inbox.py` と `driver-prompt.md` の rule 一行が現状の inbox
読み書きの全て (確認済み、 §1.4)。 dogfooding 中の WIP repo なので後方
互換不要 (cli-split-proposal §2.3 と同じ判断)。

- 既存走行中 driver pane: 旧 driver-base.md の rule (`check it each
  turn`) で動いている。 phase 1 で rule が更新されても、 既存 driver-
  prompt.md は spawn 時 snapshot 済みなので追従しない。 cutoff date を
  切って既存 task は手動 close (cli-split-proposal §5 と同じ判断)
- 既存 inbox.md format: phase 1 では変えないので走行中 task も影響なし
- events.jsonl: 新 event type (`inbox_seen`) を追加するだけで破壊変更
  なし

---

## 3. 案の比較

タスク依頼で提示された 3 案を整理 + 中間案 (D) を追加。

### 案 A. 軽量 — helper 経由 auto-ack + watermark event

**変更点**:

1. `fleet-agent inbox` を post / read の **dual mode** に拡張
   - `fleet-agent inbox <id> "<msg>"` (引数あり) → 現状の post 動作
   - `fleet-agent inbox` (引数なし、 task は env から解決) → read 動作:
     - 該当 task の inbox.md 全文を stdout に出す
     - 副作用で `inbox_seen` event を events.jsonl に append (watermark
       = inbox.md 末尾の timestamp block)
2. driver-base.md L9-10 を更新: 「inbox は cat ではなく `fleet-agent
   inbox` で読め」 と 1 行追加
3. 既存 inbox.md format (timestamp ヘッダ + body) は変えない
4. 新 event `inbox_seen` schema:
   `{"ts": "...", "type": "inbox_seen", "task_id": "...", "watermark": "<最新 ack 済み timestamp>"}`

**メリット**:

- 実装最小: `commands/inbox.py` の `run()` を 「task_id 引数の有無で
  分岐」 / `cli.py` parser を nargs 調整 / driver-base.md 1 行追加
- 既存 inbox.md format / events.jsonl 既存 event 不変、 後方互換最大
- watermark で 「未読 N 件」 を leader / `fleet status` 側で計算可能
  (最新 `inbox_message.ts` > 最新 `inbox_seen.watermark` なら未読あり)
- dialogue-trace-proposal phase 2 への移行時にも、 watermark → per-
  message ack に粒度を上げるだけで概念は同じ
- driver の rule 違反 (cat 直読み) があった場合でも、 inbox.md 自体は
  動くので 「動かない事故」 にはならない (ack が記録されないだけ)
- §10.2 dynamic prompt injection 廃止と方向衝突なし (driver-base 1 行
  追加のみ)

**デメリット**:

- driver が cat で直読みすると ack が記録されない (rule 圧力で運用、
  ask 側と同レベル)
- watermark 粒度なので 「個別 message に対する ack」 は phase 1 では出
  ない (per-message reaction が必要なら phase 2)
- 同 second multi 投函で timestamp 衝突しても watermark は 「以下全
  部」 で扱えば実害なし、 ただし leader の集計で 1 件として扱われる可
  能性

### 案 B. 構造化 — inbox を inbox.jsonl 化 + per-message inbox-ack

**変更点**:

1. inbox.md を `inbox.jsonl` に置換 (1 行 1 message + 採番 id)
2. `fleet-agent inbox <id> "..."` を inbox.jsonl に append、 events.jsonl
   の `inbox_message` に `message_id` field を追加
3. driver は `fleet-agent inbox-list` 等で読む、 個別 ack は
   `fleet-agent inbox-ack <message-id>` か helper 内自動
4. events.jsonl に `inbox_ack` event (per-message)
5. driver-base.md rule を更新 (inbox.jsonl + ack CLI の流れに)

**メリット**:

- message 単位 ack、 「未読 message X 件」 が正確
- backlog 「ファイルベース統一」 と直接前進
- audit / replay が message id baseline で揃う

**デメリット**:

- **dialogue-trace-proposal phase 2 の messages/ 統合と二重作業**。
  inbox.jsonl を作って数週間後に messages/<seq>-...md に置換するのは
  中途半端な構造変更が残る
- inbox.md format 変更で driver-prompt の rule / docs を一斉に書き換え
- driver が cat で直読みするケースで message id が見えない (markdown
  ヘッダ + body の方が pane で見て可読、 jsonl は raw 見にくい)
- 実装コスト中 (新規 CLI 2-3 個 + format 変更 + tests)
- helper 経由を強制しないと per-message ack が drift する

### 案 C. その他 — 案 A + 案 B のハイブリッド (inbox.jsonl 化しつつ helper 経由 ack)

inbox.jsonl 化と helper 経由 auto-ack を両方入れる。

**メリット**: 案 B の構造化 + 案 A の helper 経由で drift も減らす

**デメリット**: 実装コストが案 B + 案 A。 dialogue-trace-proposal phase 2
で再度書き換えるなら 2 度手間度合いがさらに増える

### 案 D. in-place ✓ ack (タスク依頼 §4 の参考案)

inbox.md に `✓ ack at <ts>` を driver が書き込む。

**メリット**: file 1 個で完結

**デメリット**: race (read+write 同時)、 pane 表示で混乱 (driver の cat
出力に ack マーカが混入)、 in-place update は §5.4 「partial update 禁
止」 と方向不一致

---

## 4. メリデメ比較表

| 軸 | **A: watermark ★** | B: inbox.jsonl | C: A+B 全部 | D: in-place ✓ |
|---|---|---|---|---|
| 読了の return path 提供 | ◎ | ◎ | ◎ | ◎ |
| 個別 message ack 粒度 | △ watermark | ◎ per-message | ◎ | ◎ |
| dialogue-trace-proposal phase 2 整合 | ◎ phase 2 で自然合流 | △ 二重作業 | △ 二重作業 | △ |
| backlog 「ファイル統一」 整合 | ◎ phase 2 で接続 | ◎ 直接前進 | ◎ 直接前進 | ✗ format 維持 |
| 既存 inbox.md format 維持 | ◎ | ✗ | ✗ | ○ 追記のみ |
| 既存 events.jsonl 後方互換 | ◎ event 追加のみ | ○ schema 拡張 | ○ schema 拡張 | ◎ |
| race リスク | なし | 低 | 低 | あり (read+write) |
| 実装コスト | 小 (CLI 拡張 + 1 行 docs) | 中-大 | 大 | 中 |
| driver drift リスク (rule 違反) | 中 (cat 直読み余地) | 中 (ack 叩き忘れ) | 低 (helper 強制) | 高 (✓ 追記忘れ) |
| pane attach 時の可読性 | ◎ inbox.md 据え置き | △ jsonl raw | △ | ✗ ack 混在 |
| §10.2 dynamic prompt injection 廃止 | ○ (1 行追加のみ) | ○ | ○ | ○ |
| 段階導入の柔軟性 | ◎ phase 2 で刷新 | △ 一斉刷新 | △ | △ |

却下:
- **案 B (単独)**: dialogue-trace-proposal phase 2 で二重作業になる、 phase 2 まで待って一斉刷新が筋
- **案 C**: 案 B のコスト + 案 A のコスト、 phase 2 で結局再刷新、 利点に対してコスト過剰
- **案 D**: race / pane 混乱 / §5.4 方針不一致、 3 重で不利

比較対象は案 A (推奨) と 案 B (構造化単独)。 phase 2 で messages/ 統合
を控えているという文脈で、 phase 1 を軽量化する案 A が素直。

**案 A が user 主訴 (「driver がその指示を見たか」 を leader / user に届
ける) と既存方針 (cli-split / role-structure / dialogue-trace / backlog)
を全部素直に飲み込む**。 phase 1 だけ取れば実装コスト最小、 phase 2 で
dialogue-trace と一緒に messages/ 統合と per-message ack に格上げできる。

---

## 5. 推奨

**案 A (watermark + helper 経由 auto-ack)** を **phase 1 として確定方
針** に推奨。 phase 2 (per-message ack) は dialogue-trace-proposal
phase 2 と同期して別タスクで設計。

### 5.1 全体像

```
SOT (phase 1):
  tasks/task-<id>/inbox.md               既存、 format 不変 (timestamp ヘッダ + body)
  events.jsonl                            既存、 新 event `inbox_seen` を追加

CLI (phase 1):
  fleet-agent inbox <id> "<msg>"         既存、 post mode (動作不変)
  fleet-agent inbox                       新規 read mode (env から task 解決)
                                         → inbox.md を stdout 出力 + inbox_seen event 発火

driver-base.md (phase 1):
  「inbox は cat ではなく `fleet-agent inbox` で読め」 1 行追加

leader / fleet status (phase 1):
  events.jsonl から `inbox_message.ts` (最新) と `inbox_seen.watermark` (最新) を
  比較して 「未読 N 件 / 滞留時間」 を計算 (将来、 本提案では UI まで作らない)

phase 2 (dialogue-trace-proposal phase 2 と同期、 別タスク):
  tasks/task-<id>/messages/<seq>-<dir>-<topic>.md に統合
  message_id (seq) ベースで per-message ack に格上げ
  inbox.md → messages/ の concatenation view または廃止
```

### 5.2 各論

- **read mode の引数仕様**: `fleet-agent inbox` (引数なし、 env から
  task 解決) を read mode、 `fleet-agent inbox <id> "<msg>"` (引数あ
  り) を post mode。 `<id>` だけ与えて message が無い場合は 「post
  modeのつもりで間違えた」 と区別がつかないので、 「message 省略時は
  read mode for that id」 とする (env で解決できない leader pane でも
  特定 task の inbox を覗ける)
- **read mode の出力**: inbox.md 全文を stdout に出す (markdown その
  まま)。 `--since <ts>` で差分のみ、 `--json` で構造化出力は phase
  2 / open question
- **watermark の意味**: read mode 発火時点での inbox.md 末尾 block の
  timestamp。 「ここまで読んだ」 を意味する。 inbox.md が空なら
  watermark = 直近 read の ts (空 ack を許容)
- **inbox.md が phase 1 で format 不変**: 既存 `### <ts>\n\n<body>\n\n`
  を維持。 message id は phase 2 で導入
- **events.jsonl の `inbox_seen` event**: `append_event(state_dir /
  "events.jsonl", "inbox_seen", task_id=..., watermark=<ts>)`、 既存
  `append_event` で済む
- **driver-base.md の文面**: 「inbox.md ── instructions from the
  leader; read with `fleet-agent inbox` each turn (auto-records that you
  read it).」 程度に書き換え。 「cat 禁止」 を強く書くと rule 数が増え
  るので 「`fleet-agent inbox` で読め」 のポジティブ形が良い
- **leader への通知**: phase 1 では `inbox_seen` event を events.jsonl
  に流すだけ。 leader pane で `fleet log -f` をしていれば見える。 専用
  通知 (push) は §11 priority 12 (通知経路) の領分、 本提案範囲外
- **`fleet status` の未読カウント**: 新規実装は phase 1 後半 / 別タスク
  で OK (本書 anti-scope)。 events.jsonl の最新 `inbox_message.ts` と最
  新 `inbox_seen.watermark` を比較する単純な計算

### 5.3 phase 1 実装作業項目 (参考、 本書では着手しない)

1. `src/fleet/commands/inbox.py` の `add_parser` / `run` を post / read
   mode 両対応に拡張
   - `message` を `nargs="*"` に (空なら read mode)
   - `task_id` 省略時は `task_context.resolve()` で env から解決 (ask.py
     の `--task-id` 拡張と同じ機構)
   - read mode の動作: inbox.md を `read_text()` で取得 → stdout → `inbox_seen`
     event 発火 (watermark = inbox.md 末尾の `### <ts>` ヘッダから抽出、
     空なら現在時刻)
2. `src/fleet/events.py` には変更不要 (`append_event` 汎用 helper のま
   ま)
3. `src/fleet/cli.py` の `build_parser_agent()` で `inbox` parser を上
   記に追従 (引数 nargs 調整)
4. `docs/prompts/driver-base.md` L9-10 を 「inbox は `fleet-agent inbox`
   で読め」 形に書き換え (1 行差分)
5. `docs/leader-handoff.md` L110 周辺 (「fleet-agent inbox <id>
   "<message>"」) の説明に 「driver が読むときは引数なしで叩く」 旨を 1
   行追記
6. `tests/` に
   - read mode の出力 = inbox.md 全文を確認
   - read mode で `inbox_seen` event が events.jsonl に append される
   - post mode の挙動が従来通り (regression なし)
7. `CHANGELOG.md` に追記
8. (任意・本提案外) `fleet status` 等で 「未読 N 件」 を表示する UI 拡
   張 — 別タスク

### 5.4 phase 2 実装作業項目 (参考、 dialogue-trace-proposal phase 2 と同期)

dialogue-trace-proposal §5.4 と内容統合を想定:

- `tasks/task-<id>/messages/<seq>-<dir>-<topic>.md` ディレクトリ規約決定
- inbox.md を messages/ の view (concatenation) として再構築、 または
  廃止 (dialogue-trace §6 open question #10 と同期)
- message_id (seq) ベースの per-message ack: `inbox_seen` →
  `message_seen --field message_id=<seq>`
- driver-base.md の rule を messages/ 経路に書き換え (helper CLI 統合)
- archive 設計と同期 (backlog 「task archive 後の成果物保存」)

---

## 6. 移行戦略

破壊変更なし。 phase ごとに止められる。

| 段階 | 変更 | 影響 |
|---|---|---|
| 0 (現状) | inbox.md 据え置き、 read/ack なし | leader は受領を確認できない、 driver 任せ |
| phase 1a | `fleet-agent inbox` の read mode 追加 | 既存 post mode は動作不変、 新 spawn 以降の driver が利用 |
| phase 1b | `inbox_seen` event 追加 | events.jsonl 既存 reader は ignore (open schema)、 後方互換 |
| phase 1c | driver-base.md 1 行更新 | 新規 spawn 以降の driver-prompt に反映、 既存 driver-prompt.md は snapshot 済みなので変えない |
| phase 1d (任意) | `fleet status` で 「未読 N 件」 を表示 | UI 拡張、 別タスク |
| phase 2a | messages/ 規約決定 (dialogue-trace と同期) | docs 更新のみ、 既存 task に影響なし |
| phase 2b | 新規 task のみ messages/ 採用、 per-message ack | 既存 task は inbox.md 維持で動き続ける |
| phase 2c | inbox.md / outbox.md / questions.md / answers.md を messages/ に統合 (新規 task) | 新規 spawn のみに反映 |

phase 1 は 1 PR で完結 (phase 1a + 1b + 1c を bundle)。 phase 1d は任意、
phase 2 は dialogue-trace-proposal phase 2 と一緒に別 PR 群。

走行中 driver pane への影響: 既存 driver-prompt.md には 「cat ではなく
`fleet-agent inbox` で読め」 rule が無いので、 既存 task では cat 直読
みのまま (= ack なし) で動き続ける。 CLI 自体は使えるので driver が
気づけば自主的に CLI 経由に切り替え可能。 cutoff date を切って既存
task は手動 close する方針 (cli-split-proposal §5 と同じ判断)。

---

## 7. open questions (実装フェーズで決める)

1. **read mode の引数衝突**: `fleet-agent inbox <id>` (message なし)
   は read mode for that id、 `fleet-agent inbox` (env 解決) も read
   mode、 `fleet-agent inbox <id> "<msg>"` は post mode。 この三分岐は
   分かりやすいか / `--read` flag を required にすべきか。 推奨は前者
   (CLI 自然推論)、 ただし誤解釈 (post のつもりで message を空文字に)
   は要注意
2. **watermark の精度**: 秒単位 timestamp なので同 second multi 投函で
   一括 ack されるが、 inbox は high-frequency でないので実害なし。
   phase 2 で seq に切り替えれば自然解消
3. **空 inbox での read**: inbox.md が空 (まだ post なし) のとき、
   watermark をどう記録するか。 推奨は 「`inbox_seen` event を発火しな
   い」 (空 ack に意味なし) / 「watermark=null で発火」 のどちらか
4. **read mode の冪等性**: 同じ watermark で複数回 `inbox_seen` event
   が連発する可能性 (driver が毎 turn read mode を叩く)。 events.jsonl
   は append-only なので 冪等で良い (集計時に `max(watermark)` で十分)、
   driver も特に困らない。 但し event noise を減らしたければ 「前回
   `inbox_seen` から watermark 不変なら skip」 を CLI 側で判定する選
   択肢
5. **leader 側の未読カウント UI**: `fleet status` / `dashboard.md` で 「未
   読 N 件」 をどう見せるか。 phase 1 では event ベースで集計可能だが、
   UI 拡張は別タスク (本提案 anti-scope)
6. **inbox.md write 時の flock**: 現状 `commands/inbox.py:52-53` は
   `read_text + write_text` で race リスクあり (§5.4 「partial update
   禁止」 の例外)。 read mode 追加で並列度が上がる可能性に備え、
   `state_writer` 経由か `O_APPEND` write に書き換えるのが望ましい
   (本提案 anti-scope、 別タスク)
7. **inbox-ack と dialogue-trace の CLI 統合**: phase 2 で messages/ に
   統合する時、 `fleet-agent message-read` のような統一 CLI に集約する
   か、 `fleet-agent inbox` / `fleet-agent ask-list` のように送信元別に
   分けるか。 phase 2 開始時に再決定
8. **race / pair_review topology での ack 主体**: pair_review で
   implementer と reviewer が両方居る場合、 leader → implementer の
   inbox を reviewer が読んでも ack 扱いになるべきか。 phase 1 では
   task 単位 watermark なので driver の役割は区別しない、 phase 2 で
   role 別ストリームにするか議論 (§11 priority 2 topology orchestration
   と接続)
9. **`fleet-agent inbox` read mode の `--since` / `--json` オプション**:
   差分のみ / 構造化出力。 phase 1 では markdown 全文だけで良いか、 駆
   け足で足すか
10. **driver が cat で直読みした場合の検出**: cat 直読みは ack されな
    い、 これを leader / user に warning するか (静かに sample monitor
    で 「最終 inbox_seen から 30 分超かつ未読あり」 等で alert)、 静か
    に運用するか。 phase 1 では静か、 alert は §11 priority 10 (heart
    beat / 固まり検知) で扱う

---

## 8. anti-scope

- 実装。 本書は方針合意までで止まる
- §11 priority 1 (completed の定義) — done 周り、 本タスク外
- §11 priority 2 (topology orchestration) — multi-role 進行制御、 phase
  2 で messages/ と接続するが本タスクでは触らない
- §11 priority 3 (driver commit 責務) — commit と inbox は独立
- §11 priority 4 (role 構造化) — role-structure-proposal 側、 本タスク
  では独立
- §11 priority 5 (dialogue trace) — dialogue-trace-proposal 側、 phase 2
  で合流するが本タスクでは inbox 側のみ
- §11 priority 7 (prompt 構造) — driver-base.md は 1 行更新のみ、 大改
  修は別タスク
- §11 priority 10 (固まり検知) — `inbox_seen` を入力に使う UI は本タス
  ク外
- §11 priority 12 (通知経路) — `inbox_seen` を専用通知する話は本タスク
  外
- 既存 inbox.md の `read_text + write_text` race の修正 — 別タスク (§5.4
  方針との整合は別途)
- `fleet status` の 「未読 N 件」 UI — 別タスク
- inbox.md の per-message ack (phase 2) — 別タスク、 dialogue-trace
  phase 2 と同期
- backlog 「task archive 後の成果物保存」 で `inbox_seen` event を
  archive する話 — events.jsonl に乗るので追加対応不要、 archive 設
  計側で吸収

---

## 9. 議論履歴

- 2026-05-20 初稿: 案 A (watermark + helper 経由 auto-ack) を phase 1
  として推奨。 inbox.md format を変えず `fleet-agent inbox` を post /
  read dual mode に拡張、 read mode の副作用で `inbox_seen` event を
  発火。 driver-base.md に 1 行追加で 「cat ではなく CLI で読め」 を
  rule 化、 helper 経由を強制することで read = ack の構造圧力を確立。
  phase 2 で dialogue-trace-proposal phase 2 と同期して messages/ 統合
  + per-message ack に格上げ。 §11 priority 6 に正面から取り組み、
  priority 1-5 / 7-12 とは衝突しない範囲で整理。 cli-split-proposal /
  role-structure-proposal / dialogue-trace-proposal とも矛盾なし。
