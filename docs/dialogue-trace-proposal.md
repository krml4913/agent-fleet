# dialogue trace 方針提案

> 作成: 2026-05-20 / driver: task-dialogue-trace
> ステータス: 議論用ドラフト (実装前)
> 関連: `docs/design.md` §1.2 §7 §11 priority 5、 `docs/backlog.md`
>      (「driver への指示はファイルベース統一」「task archive 後の成果物保存」)、
>      `docs/cli-split-proposal.md`、 `docs/role-structure-proposal.md`、
>      `src/fleet/events.py`、 `src/fleet/commands/ask.py` / `inbox.py` /
>      `event.py`、 `tasks/task-*/inbox.md` / `outbox.md` / `questions.md`

---

## 0. TL;DR

agent-fleet の核心は 「user は driver と直接対話する」 (§1.2)。 ところがその対話のうち **user 側の発話** は何ひとつ events.jsonl / file に残らない。

| 経路 | 現状の trace |
|---|---|
| leader → driver の inbox.md 投函 | ◎ `inbox_message` event + inbox.md に file 保管 |
| driver → user の `fleet-agent ask "..."` の **question** | ◎ `needs_input` event + questions.md に file 保管 |
| user → driver の **answer** (pane で打って返す) | ✗ どこにも残らない |
| user → driver の pane 生入力 (ask 由来でない自発発話) | ✗ どこにも残らない |
| driver → user / outbox.md への追記 | △ outbox.md に file 残るが event 化されてない |

問題は **片側のみ記録** で対の半分が抜けていること。 audit / 別セッション引き継ぎ / archive 後の振り返りが全部効かない。

推奨は **案 C (段階導入の折衷案)**:

- **phase 1 (足元の最低ライン)**: `fleet-agent answer "<text>"` CLI と outbox event 発火を入れる。 driver が user の回答を受け取った瞬間に明示記録、 outbox.md の write も event 化。 tmux pipe-pane は使わない、 一切の生 stream 監視なし。 「ask に対する answer だけは確実に残す」 の最低ラインを満たす
- **phase 2 (ファイルベース統一)**: backlog の 「driver への指示はファイルベース統一」 と接続。 inbox.md / outbox.md を `tasks/task-<id>/messages/<seq>-<dir>-<topic>.md` の per-message file に発展、 ask/answer もこの message ストリームに乗せる。 「対話」 が file の連番として diff/review/archive できる構造になる
- **phase 3 (任意の raw 補足)**: ask/answer/messages では拾えない user 自発発話 (pane で `# あ、 やっぱり ...` のように直接打つ) を後追いで補足したい project だけ、 opt-in で `tmux pipe-pane` を `dialogue-raw.log` に流す。 core 機能ではなく workflow plugin の責務寄り

**やらないこと**: 全 pane 生 stream を default で記録する重量案 (案 B) はコストとリスク (PII / secret / TUI escape / 巨大化) に対して便益が薄い。 phase 3 として opt-in 余地は残すが default 不採用。

役割の値域、 inbox/outbox との関係、 archive、 PII の各論は §2-§5 で詰める。

本書は方針合意までで止まる。 実装は次フェーズ。

---

## 1. 現状の整理

### 1.1 「対話」 が今どこに記録されているか

| 方向 | 経路 | 記録媒体 | event |
|---|---|---|---|
| leader → driver | `fleet-agent inbox <id> "..."` | `tasks/task-<id>/inbox.md` (timestamped block) | ◎ `inbox_message` |
| driver → user (質問) | `fleet-agent ask "..."` | `tasks/task-<id>/questions.md` (timestamped block) | ◎ `needs_input` |
| user → driver (回答) | pane で driver が prompt から読む | **どこにも残らない** | ✗ |
| user → driver (自発発話) | pane で直接打つ | **どこにも残らない** | ✗ |
| driver → leader / user (報告) | driver が outbox.md に追記 | `tasks/task-<id>/outbox.md` | ✗ event 連動なし |
| その他 (進捗 / heartbeat 等) | `fleet-agent event emit ...` | events.jsonl のみ | ◎ |

(`commands/ask.py:53`, `commands/inbox.py:51`, `commands/event.py:36` を見て確認)

### 1.2 何が困るのか

- **audit**: 「user が何と答えた結果その方針になったか」 が後から辿れない。 git log / events.jsonl だけ見ても 「driver は急にこの結論を出した」 ように見える
- **引き継ぎ**: 別セッションで同 task を再開するとき、 driver は inbox / questions / outbox を読めるが **user の生回答だけは欠落**。 同じ質問を二度することになる
- **archive**: backlog 「task archive 後の成果物保存」 と接続。 archive で worktree を消すなら、 対話の流れも archive 対象に含めたい。 今は対の半分が無いので archive 対象として成立しない
- **review**: driver 出力を後でレビューする場合、 「user が承認 / 否認した会話」 が引けない

### 1.3 既に確定している方針との照合

| 確定方針 | 整合性 |
|---|---|
| §1.2 「user は driver と直接対話する」 | 直接対話の **記録** が抜けている (今回の主訴) |
| §3.1 「leader は中継しない、 構造で user に届ける」 | ask 経路は構造化済み、 answer 側は未構造化 |
| §10.2 dynamic prompt injection 廃止 | 直接の関係は薄いが、 「prompt 本文に対話履歴を書き込んで肥らせる」 という派生病は防ぐ必要あり |
| §5.3 state 構造 (`tasks/task-<id>/`) | 既存ディレクトリに乗せる方向で素直に拡張可能 |
| cli-split-proposal (`fleet-agent` 2 バイナリ路線) | `fleet-agent answer` / 関連 CLI は `fleet-agent` 側に置けば整合 |
| role-structure-proposal (task.yaml SOT) | 今回も file SOT 路線で揃える、 矛盾なし |
| backlog 「driver への指示はファイルベース統一」 | **同じ方向**。 phase 2 で接続させる |
| backlog 「task archive 後の成果物保存」 | **同じ方向**。 dialogue を archive 対象に含める設計を意識 |

### 1.4 「user が pane に直接打つ」 を tmux で捕捉するとどうなるか (技術調査)

選択肢:

- `tmux pipe-pane -O 'cat >> path'` (output stream を流す)、 pane 入出力の両方が混在で raw に落ちる。 cleanup は pane 終了で自動停止
- `tmux capture-pane -p` を周期的に呼ぶ。 polling、 差分計算が必要で漏れの確率が出る
- `tmux send-keys` 経由で送った text は記録できない (送る側からは見える)

判定: pipe-pane が技術的に唯一の現実解。 ただし以下の問題を伴う:

- **TUI escape sequence の混入**: claude / codex CLI は spinner / 部分書き換え / ANSI color を多用、 raw stream には escape 制御文字が大量に混じる。 後で grep / review しづらい
- **secret / PII**: pane に `gh auth login` の token や user の生メッセージが流れる → raw に落ちると流出経路になりやすい
- **粒度過剰**: 1 文字単位 (キー入力) ではなく 「行 / メッセージ」 単位が分析の最低粒度。 raw stream はそれより細かい
- **サイズ**: 大きい session で MB 級に膨らむ、 archive に持っていくと task 1 つで重い

→ **default では使わない**。 必要な project だけ opt-in できる workflow plugin として残す (phase 3)。

---

## 2. 観点 (本提案で詰める論点)

タスク依頼で提示された 7 つの論点を 1 つずつ整理する。

### 2.1 何を record すべきか

優先順位を切ると以下:

1. **ask に対する user の answer** — 対の半分が抜けている、 最重要 (タスク依頼 §7)
2. **driver → leader / user の outbox 追記** — file はあるが event 連動なし、 audit 不足
3. **leader → driver の inbox 投函** — 既に ◎、 追加対応不要
4. **driver → user の ask question** — 既に ◎、 追加対応不要
5. **user が pane で打った自発発話** (ask に紐付かない `# やっぱり...`) — 重要度は (1) より低い、 まずは無くて困る局面を見極めてから

(5) は 「driver 側が `fleet-agent note "..."` 等で要約を書き残す」 で半分代替できる。 全自動の raw 捕捉が必要かは phase 3 で見直す。

### 2.2 どう record するか

候補:

| 案 | 媒体 | 粒度 | 形式 |
|---|---|---|---|
| α | events.jsonl に新 event type | message | 1 行 JSON |
| β | 別 file `tasks/task-<id>/dialogue.jsonl` | message | 1 行 JSON |
| γ | `tasks/task-<id>/messages/<seq>.md` | message | per-file markdown |
| δ | inbox.md / outbox.md 維持、 event だけ追加 | message | file は append-only |

判定:

- α: 既存 events.jsonl に乗せる方が SOT 単一化、 schema は `type: dialogue_user_input` 等で識別可能
- β: dialogue だけ別 file は SOT が増えるだけで利点が薄い。 events.jsonl で `type` で分けられる
- γ: backlog 「driver 指示のファイル統一」 と一致、 message 単位で diff/review/archive が綺麗。 events.jsonl にも meta event を残す (どの message file が増えたか) と両立可能
- δ: 既存 inbox/outbox 維持、 event 追加だけは最小コスト。 phase 1 として現実的

→ **phase 1 は δ + α (既存 file 維持 + events.jsonl に event 追加)**、 **phase 2 で γ に発展** (messages/ への統合) が現実的な segment。

### 2.3 取得経路

| 経路 | 説明 | 採否 |
|---|---|---|
| `fleet-agent ask` の対として `fleet-agent answer "<text>"` | driver が user の回答を読んだ瞬間に明示記録 | ◎ phase 1 採用 |
| user が `fleet user-say "<text>"` を pane で叩く | user に手間。 user が tmux pane に attach した状態で別経路を強要するのは UX 上厳しい | ✗ user 主導の CLI 強要は避ける |
| tmux pipe-pane で pane 全部流す | raw stream、 escape / 巨大 / PII | △ phase 3 opt-in |
| leader が user 発話を 「打つ前に」 inbox.md に転載 | user 発話が leader 経由になる、 §1.2 「直接対話」 と矛盾 | ✗ |

→ **driver 側からの明示記録 (answer CLI) を主、 pipe-pane は opt-in**。

`fleet-agent answer` の drift 懸念 (driver が叩き忘れる) について: driver-base.md と driver-prompt 側で 「user の回答を受けたら必ず `fleet-agent answer` を叩く」 を rule 化する。 ask が `needs_input` を構造的に強制しているのと同じ圧力をかける。 drift は完全には防げないが、 ask 側でも 「pane に書くだけでは届かない」 構造圧力で運用回っているので同レベルは確保できる。

### 2.4 粒度

- **1 文字単位**: pipe-pane で得られるが分析に不向き
- **行単位**: shell 1 行ごと、 まだ細かい
- **メッセージ単位**: ask 1 件 / answer 1 件 / inbox post 1 件 / outbox post 1 件 / 自発発話 1 件、 これが現実的

→ **メッセージ単位を default**。 phase 3 の opt-in raw log は別 layer。

### 2.5 保存範囲とクリーンアップ

- **保存範囲**: タスク完了後も `.fleet-state/tasks/task-<id>/` 配下に残る (現状の挙動)。 archive される未来でも dialogue は archive 対象に含めるべき。 backlog 「task archive 後の成果物保存」 と同じ tarball / snapshot に乗せる
- **PII / secret**: phase 1 (answer CLI) は driver が明示記録、 PII は driver の判断で `[REDACTED]` 化可能。 phase 3 (pipe-pane raw) は流出経路、 default 無効
- **巨大化**: phase 1 + 2 の message 単位なら 1 task で MB に届くケースは稀。 phase 3 を入れた場合は raw log の rotate / 上限 size を別途検討
- **削除**: archive 時に raw log だけ任意削除可能にする (message file は archive 必須)

### 2.6 既存 inbox.md / outbox.md との関係 (同期/非同期、 統合か分離か)

- 現状: inbox / outbox は append-only file、 同期は driver の polling (driver-base.md 「check it each turn」)
- 同期/非同期の混在: leader が inbox に書く ⇒ driver が次 turn で読む、 これは asynchronous。 user が pane で打つ ⇒ driver が即読む、 これは synchronous (にしか見えない、 trace が無いので)

→ phase 1 ではこの構造を維持。 phase 2 で `messages/<seq>-<dir>-<topic>.md` に統合し、 同期/非同期の差は 「message を書く側の意図」 だけに薄める (file の構造は同じ)。

統合形式の候補 (phase 2):

```
tasks/task-<id>/
  messages/
    0001-leader-to-driver-spec-question.md
    0002-driver-to-leader-clarification.md
    0003-driver-to-user-ask-naming.md
    0004-user-to-driver-answer-naming.md
    0005-user-to-driver-self-direct.md
    ...
```

- `<seq>` でゆるい順序、 `<dir>` で送受信主体、 `<topic>` で内容 hint
- 内容は markdown、 front matter で構造化 meta (`from:` / `to:` / `kind: ask|answer|note|...`)
- events.jsonl にも `dialogue_message` event を発火 (どの seq の file が増えたかだけ記録)
- inbox.md / outbox.md は phase 2 完了時点で `messages/` の view (concatenation) として再構築するか、 完全に廃止するかは phase 2 開始時の議論で決める

### 2.7 `fleet ask` の answer 記録の最低ライン

タスク依頼の §7 として提示された 「最低でもこれは残したい」 ライン:

- driver は user の回答を読んだ瞬間に `fleet-agent answer "<text>"` を叩く
- これにより `needs_input` event の対として `needs_input_answered` (or `dialogue_user_answer`) event が events.jsonl に追記される
- task.yaml の status は `needs_input` → 元の status (working 等) に戻る
- 同時に `tasks/task-<id>/answers.md` (or questions.md に answer block 追記、 形式は §3 で詰める) に file 保管
- これだけで 「ask だけは ◎、 answer も ◎」 になり、 audit / 引き継ぎが最低限機能する

CLI 名候補: `fleet-agent answer` / `fleet-agent ack-ask` / `fleet-agent reply` の中から `answer` を推奨 (ask の対として自然、 typing 短い)。

---

## 3. 案の比較

最低 2 案 + 折衷の 3 案を比較する。

### 案 A. 軽量案 — `fleet-agent answer` + outbox event のみ

**変更点**:

1. `fleet-agent answer "<text>"` CLI 新規追加
   - questions.md (or 新規 answers.md) に answer block を追記
   - events.jsonl に `needs_input_answered` event を追記
   - task.yaml の status を `needs_input` → working に戻す
2. outbox.md write 時に event 発火 (`outbox_message` event を新設)
3. driver-base.md に 「user の回答は必ず `fleet-agent answer` で記録する」 を追加

**メリット**:

- 実装最小 (新規 CLI 1 個 + event 1 個 + driver-base 1 行)
- 既存 file 構造を変えない、 互換性 100%
- tmux に手を入れない (pipe-pane / capture-pane なし)
- §7 「ask answer の最低ライン」 を構造的に満たす
- archive / 引き継ぎが phase 1 だけで 「ask/answer 対」 については完結

**デメリット**:

- driver の自発記録任せ → 叩き忘れの drift リスク。 ask 側と同じ構造圧力で運用、 完全防止は無理
- user が pane で自発的に打つ message (ask に紐付かない) は依然として trace 不可
- backlog 「ファイルベース統一」 とは方向は揃うが直接の前進にはならない (phase 2 を別途必要)

### 案 B. 重量案 — tmux pipe-pane で pane I/O 全部 file へ

**変更点**:

1. `fleet-agent spawn` 時に `tmux pipe-pane -t <window> -O 'cat >> .../dialogue-raw.log'` を仕込む
2. pane の入出力 (raw stream) が `dialogue-raw.log` に流れる
3. cleanup / archive 時に rotate / 廃棄

**メリット**:

- 完全 trace、 driver 任せの漏れ無し
- user 自発発話も自動で取れる
- 「何が起きたか」 の原本が残るので debug 用途で強い

**デメリット**:

- TUI escape sequence で raw stream は壊れる (parse / grep 困難)
- PII / secret 流出経路 (token / 個人情報が raw に落ちる)
- 巨大化、 1 task で MB 級
- driver pane の TUI 描画と pipe-pane stream が干渉するケースあり (実機検証要)
- 粒度過剰、 message 単位 audit には変換 layer が必要
- archive 時のサイズ問題、 cleanup ポリシー要設計

### 案 C. 折衷 / 段階導入 — **推奨**

**phase 1: 案 A 相当 (最低ラインを満たす)**

1. `fleet-agent answer "<text>"` CLI 追加
2. outbox event 追加
3. driver-base.md 1 行追加 (answer rule)

**phase 2: backlog 「ファイルベース統一」 と接続**

4. `tasks/task-<id>/messages/<seq>-<dir>-<topic>.md` を新設
5. inbox.md / outbox.md を message stream のビューとして再構成 (または廃止) 、 ask/answer もここに乗せる
6. events.jsonl には `dialogue_message` event (どの seq が増えたか) を発火
7. backlog 「task archive 後の成果物保存」 で messages/ ごと archive

**phase 3 (任意の opt-in): pipe-pane raw log**

8. workflow plugin として `dialogue-raw-capture` を定義 (default 無効)
9. enable した project だけ `dialogue-raw.log` を取る、 archive 時に rotate / 削除
10. message stream とは独立、 raw は debug 用 / audit 補強

**メリット**:

- phase 1 だけで 「ask answer の最低ライン」 が確保 (§7)
- phase 2 で backlog 2 項目と一気に整合
- phase 3 は要望が出てから後付けで足せる、 default は default のままで軽い
- 段階的に積めるので一気に大きい PR にしなくていい
- §10.2 dynamic prompt injection 廃止と方向衝突なし (prompt は触らない、 file 構造で解決)

**デメリット**:

- phase 1 → phase 2 で file 名規約が変わる、 移行手順が必要 (本書 §5 で記述)
- phase 2 着手まで user 自発発話 trace は依然欠落

---

## 4. メリデメ比較表

| 軸 | A: 軽量 | B: pipe-pane 全部 | **C: 段階導入 ★** |
|---|---|---|---|
| ask/answer 最低ライン (§7) | ◎ | ◎ | ◎ (phase 1) |
| user 自発発話 trace | ✗ | ◎ | △ (phase 3 opt-in) |
| 実装コスト | 小 | 中-大 | 段階分散 (phase 1 は A と同じ) |
| TUI escape / 粒度問題 | なし | あり | phase 3 のみ影響、 default 無し |
| PII / secret リスク | 低 (driver redact 可) | 高 | 低 (default)、 phase 3 で考慮 |
| サイズ / archive 負担 | 小 | 大 | 小 (default)、 phase 3 で rotate |
| backlog 「ファイル統一」 接続 | △ 方向は揃う | ✗ raw stream は別物 | ◎ phase 2 で接続 |
| backlog 「archive 成果物保存」 接続 | ○ | △ raw は archive 負担 | ◎ phase 2 で messages 込み archive |
| 既存方針との衝突 | なし | TUI 周りリスク | なし |
| driver 任せの drift | あり | なし (自動) | phase 1 はあり、 phase 2-3 で軽減 |
| 配信 / cutoff の柔軟性 | ◎ | △ 一括導入 | ◎ phase ごとに止められる |

却下: 案 B 単体 (リスクとコストに対して便益が薄い、 ただし opt-in 形では C-phase 3 に組み込み)。
比較対象: 案 A (短期で十分なら) と 案 C (段階導入で長期に揃える)。

**案 C が user 主訴 (audit / 引き継ぎ / archive と矛盾しない設計) と既存方針 (cli-split / role-structure / backlog 2 項目) を全部素直に飲み込む**。 phase 1 だけ取れば実装コストは A と同じ、 phase 2-3 は要望次第で後追い可能。

---

## 5. 推奨

**案 C (段階導入の折衷案)** を推奨。 特に **phase 1 を確定方針として進める** ことを推奨し、 phase 2-3 は方針合意のみで実装は要望次第で別タスク。

### 5.1 全体像

```
SOT (phase 1):
  tasks/task-<id>/questions.md    既存、 ask の question 追記
  tasks/task-<id>/answers.md      新規、 answer 追記 (or questions.md に block 追加でも可、 §6 open question)
  tasks/task-<id>/outbox.md       既存、 ただし write 時に event 発火を追加
  events.jsonl                     既存、 新 event type を追加

SOT (phase 2):
  tasks/task-<id>/messages/<seq>-<dir>-<topic>.md   新規、 統合 stream
  events.jsonl の `dialogue_message` event で meta 記録
  inbox.md / outbox.md / questions.md / answers.md は messages/ の view か廃止

opt-in (phase 3):
  tasks/task-<id>/dialogue-raw.log   workflow plugin が enable 時のみ
```

### 5.2 各論

- **新規 CLI**: `fleet-agent answer "<text>"` 1 個 (phase 1)
- **新規 event type**: `needs_input_answered` / `outbox_message` (phase 1)、 `dialogue_message` (phase 2)
- **driver-base.md 修正**: 「user の回答を受けたら必ず `fleet-agent answer` を叩く」 1 行追加 (phase 1)、 ファイルベース統一に伴う rule 更新 (phase 2)
- **archive 接続**: backlog 「task archive 後の成果物保存」 と一緒に設計、 messages/ を archive 対象に含める (phase 2 と同期)
- **既存散文の流出止め**: ask 側の questions.md 形式は維持、 answer 側も同形式 (timestamp + body) で揃える
- **PII / secret 対策**: phase 1 は driver の judgment、 phase 3 を入れる場合は workflow plugin 側で redaction filter 余地を残す

### 5.3 phase 1 実装作業項目 (参考、 本書では着手しない)

1. `src/fleet/commands/answer.py` 新規追加
   - `task_context.resolve()` で task_id を解決 (ask と同じ機構)
   - `tasks/task-<id>/answers.md` に block append (or questions.md 末尾の 直近 question block に answer subblock を足す、 §6 open question)
   - events.jsonl に `needs_input_answered` event 追記
   - task.yaml の status を `needs_input` → 前の status に戻す (前 status を `previous_status` field に退避 / `ask.py` 側で対応必要)
2. `src/fleet/commands/ask.py` を 「status 退避」 対応に拡張
3. `src/fleet/cli.py` の `build_parser_agent()` に `answer` sub-command を登録
4. `docs/prompts/driver-base.md` に 1 行追加 (「After receiving the user's reply, call `fleet-agent answer "<text>"` so the answer is recorded.」)
5. outbox.md write 時に event 発火: 現状 outbox.md は driver が直接 write してる場合と `fleet-agent inbox` 相当の CLI を経由してない場合があるので、 driver 側で `fleet-agent event emit outbox_message --field body=...` を叩く運用を driver-base.md に明記する (or 新規 `fleet-agent outbox` CLI を追加して file write + event を atomic にする、 §6 open question)
6. `tests/` に answer CLI / outbox event の最小 test
7. `CHANGELOG.md` に追記

### 5.4 phase 2 実装作業項目 (参考、 phase 2 の合意後に詳細化)

- `tasks/task-<id>/messages/` ディレクトリ規約決定 (`<seq>-<dir>-<topic>.md`)
- inbox.md / outbox.md / questions.md / answers.md の messages/ への統合手順
- 既存 task の messages/ への migration (新規 task のみ messages/ 採用 / 既存は file 維持の 2 路線可)
- `fleet-agent message <kind> ...` のような統一 CLI 追加 (or 既存 `inbox` / `ask` / `answer` を内部で messages/ に書き出す薄い変換)
- archive 設計と接続 (backlog 「task archive 後の成果物保存」)

### 5.5 phase 3 実装作業項目 (任意、 opt-in)

- `workflow plugin: dialogue-raw-capture` の API 設計 (pre_spawn hook で pipe-pane を仕込む)
- raw log の rotate / 上限 size / 削除ポリシー
- PII / secret redaction filter
- archive 時の raw log の扱い (含める / 別 archive / 削除)

---

## 6. 移行戦略

破壊変更なし。 phase ごとに止められる。

| 段階 | 変更 | 影響 |
|---|---|---|
| 0 (現状) | ask の question のみ trace、 answer / outbox / 自発発話は trace なし | audit / 引き継ぎ困難 |
| phase 1a | `fleet-agent answer` CLI 追加 | 走行中 task は driver-prompt が古いので叩かない可能性あり、 新 spawn 以降で機能 |
| phase 1b | outbox event 発火 (driver-base 経由) | 同上、 新 spawn 以降で機能 |
| phase 1c | driver-base.md 1 行追加 | 新規 spawn 以降の driver-prompt に反映、 既存 driver-prompt.md は snapshot 済みなので変えない |
| phase 2a | messages/ ディレクトリ規約決定 (docs) | docs 更新のみ、 既存 task に影響なし |
| phase 2b | 新規 task のみ messages/ 採用 | 既存 task は inbox/outbox 形式維持、 cutoff date を切る |
| phase 2c | inbox/outbox/questions/answers を messages/ に統合 (新規 task) | 新規 spawn のみに反映 |
| phase 2d | archive 設計と messages/ 込み archive (backlog 「task archive 後の成果物保存」 と同期) | archive 機能 + messages 同時導入 |
| phase 3 | dialogue-raw-capture workflow plugin (opt-in) | enable した project のみ raw log 取得 |

phase 1 は 1 PR で完結する想定。 phase 2 は backlog 2 項目 (「ファイル統一」「archive」) と一緒に別 PR 群、 phase 3 は需要が出てから。

走行中 driver pane への影響: 既存 driver-prompt.md には 「answer を叩け」 rule が書かれていないので、 既存 task では answer 記録は走らない (CLI 自体は使える)。 cutoff date を切って既存 task は手動 close / 終了させる方針 (cli-split-proposal §5 と同じ判断)。

---

## 7. open questions (実装フェーズで決める)

1. **answer の保管 file**: `tasks/task-<id>/answers.md` に分けるか、 既存 `questions.md` の直近 question block に answer subblock を足すか。 後者は file 1 個で完結するが parse がやや複雑、 前者は対が file 分離。 推奨は 「questions.md に subblock 追加」 (1 ask 1 answer の対応が同 file で見える、 file 数を増やさない、 §10.2 の趣旨にも合う)
2. **status 退避**: ask 時に `task["status"]` を `"needs_input"` に上書きしているので、 元の status (working など) を `task["previous_status"]` 等に退避し、 answer 時に戻す。 退避 field 名と 「複数 ask 連続時の挙動」 を要決定
3. **outbox event の発火経路**: driver が `fleet-agent event emit outbox_message --field body=...` を叩く運用 (driver-base に rule 化) か、 専用 `fleet-agent outbox "<text>"` CLI で write + event を atomic にするか。 後者の方が drift 少ない (推奨)
4. **`fleet-agent answer` の引数**: 単一 text 引数 (`fleet-agent answer "..."`) か、 stdin 経由 (`fleet-agent answer < tmp.md`) も許すか。 driver pane で複数行 answer を扱う場合は stdin が現実的
5. **CLI 名**: `answer` か `ack-ask` か `reply` か。 推奨は `answer` (ask の対として自然、 typing 短い)
6. **questions.md / answers.md の archive**: phase 1 時点でこれらが archive 対象に含まれるか。 backlog 「task archive 後の成果物保存」 が未着手のため、 phase 2 と同期するのが筋
7. **messages/ の seq 採番**: ファイル名先頭の `<seq>` の幅 (`0001` か `001` か `1` か)、 並列 driver 時の race 対策 (state.py の flock + atomic rename と同じ機構を使う)
8. **messages/ の `<topic>` 任意度**: 必須にすると書き手の負担、 任意にすると流出。 推奨は 「任意、 default は kind だけ」 (`0003-driver-to-user-ask.md` のように `<topic>` 省略可)
9. **phase 3 raw log の TUI escape 対策**: `tmux pipe-pane` の出力に sed で escape を剥がす filter を入れるか、 raw のまま落として review 時に変換 layer を別で持つか
10. **inbox.md の扱い**: phase 2 で messages/ に統合した場合、 inbox.md は (a) 廃止 (b) messages/ の concatenation view (c) 別意味で残す のどれにするか。 推奨は (b) (driver が `check it each turn` する rule を維持しやすい)
11. **driver pane の TUI と pipe-pane の干渉実機検証**: phase 3 着手前に macOS + tmux 3.x + claude / codex CLI の組み合わせで動作確認、 動かなければ phase 3 自体を諦める判断もあり
12. **multi-role / multi-driver 時の messages/ 分離**: pair_review / multi_stage で複数 driver が同 task 内に居るとき、 messages/ を共有するか role ごとに分けるか。 §11 priority 2 (topology orchestration) と一緒に詰める

---

## 8. anti-scope

- 実装。 本書は方針合意までで止まる
- §11 priority 1 (completed の定義) — done 周り、 本タスク外
- §11 priority 2 (topology orchestration) — multi-role 進行制御、 messages/ と将来接続するが本タスクでは触らない
- §11 priority 3 (driver commit 責務) — commit と dialogue は独立、 本タスク外
- §11 priority 4 (role 構造化) — role-structure-proposal.md 側、 本タスクでは独立
- §11 priority 6 (inbox ack) — inbox.md 読了通知は messages/ 統合とは別問題、 本タスクでは触らない (関連はあるので注記のみ)
- prompt 構造の刷新 (§11 priority 7) — driver-base.md は 1 行追加のみ、 大改修は別タスク
- workflow plugin の本格設計 (§11 priority 8) — phase 3 で workflow plugin に頼るが、 plugin API 自体は別タスクで決める
- 既存 task の messages/ への migration (phase 2 では新規 task のみ採用、 既存 task の遡及移行は anti-scope)
- 通知経路の刷新 — events.jsonl 経路で既存通知が走るので追加対応不要

---

## 9. 議論履歴

- 2026-05-20 初稿: 案 C (段階導入の折衷案) を推奨。 phase 1 で `fleet-agent answer` + outbox event を入れて 「ask answer の最低ライン」 を構造的に確保、 phase 2 で backlog 「ファイル統一」「archive 成果物保存」 と接続して messages/ に統合、 phase 3 は opt-in で pipe-pane raw log を残す。 §11 priority 5 に正面から取り組み、 priority 1-4 / 6 とは衝突しない範囲で整理。 cli-split-proposal / role-structure-proposal とも矛盾なし。
