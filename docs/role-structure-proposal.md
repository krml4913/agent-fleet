# role の構造化 方針提案

> 作成: 2026-05-20 / driver: task-role-structure
> ステータス: 議論用ドラフト (実装前)
> 関連: `docs/design.md` §6 §11 priority 4、 `docs/cli-split-proposal.md` §7-4、
>      `src/fleet/driver_prompt.py`、 `src/fleet/state.py`、 `src/fleet/topology.py`、
>      `src/fleet/commands/spawn.py`、 `src/fleet/presets/*.yaml`、
>      `docs/prompts/driver-base.md`

---

## 0. TL;DR

driver が自分の role (implementer / reviewer / designer / ...) を知る経路が今は **散文 prompt 任せ** で、 §10.2 の 「dynamic prompt injection 廃止」 方針と矛盾しかけている。

実態をよく見ると、 構造化情報は **既に `task.yaml` の `role:` フィールドと `driver-prompt.md` の front matter `role:` 行に二重で書かれている**。 問題は 「保管が無い」 ではなく 「driver が機械的に取り出す API が無い」 こと。

推奨: **task.yaml の `role:` を SOT に固定し、 spawn 時に pane env `FLEET_DRIVER_ROLE` を注入、 取り出し用に `fleet-agent role` を追加する**。 driver-base.md には散文 role 説明を入れない (現状そうなっている) を維持し、 task description 側に 「お前は implementer だ」 等の散文を書かないガイドラインを leader-handoff.md に足す。

役割の値域は **fix しない** (topology が任意に決められる、 ただし conventional 名は docs に列挙)。 role 切り替えは 「同 task 内で再起動」 ではなく 「次 role を別 driver として spawn」 で扱う (§11 priority 2 topology orchestration の領分)。

`FLEET_ROLE` (cli-split-proposal §7-4 で議論された env) は用途が違うので別名 (`FLEET_DRIVER_ROLE`) にする。

本書は方針合意までで止まる。 実装は次フェーズ。

---

## 1. 現状の整理

### 1.1 「role 情報」 が今どこに書かれているか

| 場所 | 内容 | 書き手 | 機械可読 |
|---|---|---|---|
| `task.yaml` の `role:` フィールド | spawn 時に topology から自動解決 (`spawn.py` line 161-167) | `fleet-agent spawn` | ◎ 構造化 |
| `driver-prompt.md` の front matter `role:` 行 | spawn 時に `driver_prompt.render()` が `---role: ...---` で埋め込む | `fleet-agent spawn` | △ regex で抜けるが parser 要 |
| `driver-prompt.md` の **本文 (Task description 部分)** | 「お前は implementer だ」 等、 task を作る人間が散文で書きがち | leader / user | ✗ 自然文 |
| `driver-base.md` (固定 prompt 本体) | role 言及なし (今は 「You are a fleet driver」 のみ) | repo 内固定 | — |

つまり 「構造化情報は二重で保管されているが、 取り出し API が無い」 状態。

### 1.2 §10.2 「dynamic prompt injection 廃止」 との関係

driver-base.md には role の散文は無い (確認済み)。 一方で task description (人間が手書きする部分) には 「## お前の役割 ...」 のような散文がしばしば混入する。 task description は本来 「タスクの内容」 だけを書く場所で、 「driver の役割」 は構造側で渡すべき。

これは厳密には dynamic prompt injection (driver-base 側を膨らませる病) ではないが、 「役割が散文として prompt 本文に流れ込む」 という意味では同じ系統の症状。

### 1.3 driver が今 role を取り出す手段

- env: `FLEET_TASK_ID` / `FLEET_STATE_DIR` のみ。 role は無い (`spawn.py` line 230-234)
- CLI: `fleet-agent` に role を返すコマンドは存在しない
- file: `task.yaml` を `cat | grep` すれば取れるが driver-prompt にその案内が無い
- 結果: 散文 prompt を 「読む」 ことが事実上の取得手段になっている

### 1.4 既に確定している方針との照合

| 確定方針 | 整合性 |
|---|---|
| §10.2 dynamic prompt injection 廃止、 base prompt 短く | 散文 role を本文に書かない方向と一致 |
| §5.3 state 構造 (task.yaml に集約) | task.yaml の `role:` を SOT にする方向と一致 |
| §13.5 / cli-split-proposal `fleet-agent` の責務 (システムが自動で叩く CLI) | `fleet-agent role` 追加なら整合 |
| cli-split-proposal §7-4 `FLEET_ROLE` 用途否認 (user shell guard) | **用途が違う**、 別名 (`FLEET_DRIVER_ROLE`) なら矛盾しない |

---

## 2. 観点 (本提案で詰める論点)

タスク依頼で提示された 7 つの論点を 1 つずつ整理する。

### 2.1 保管場所

候補: env / task.yaml / driver-prompt の front matter / 別ファイル。

- `task.yaml` の `role:` は **既に存在** していて、 spawn / save_task / load_task が走る (state.py)。 SOT として完成している
- driver-prompt 内 front matter は人間 (pane を見る user) が読む用、 機械抽出は二次的
- 別ファイル (`role.yaml`) は SOT を増やすだけで利点が薄い

→ **SOT は `task.yaml` の `role:`**。 別ファイル化は不要。

### 2.2 driver が role を取得する API

候補: env / 専用 CLI / Python 関数。

- driver の実体は claude / codex CLI process。 Python 関数を直接呼ぶ手段が無いので Python API 単体では不足
- env は pane に既に注入する仕組みがある (`FLEET_TASK_ID` の隣に追加するだけ、 spawn.py line 230-234 の dict に追記)
- CLI (`fleet-agent role`) は env を読み、 無ければ `task.yaml` にフォールバックする薄い実装で済む

→ **env + CLI のハイブリッド**。 env で即時参照、 CLI で fallback / 構造化出力。

### 2.3 role の値域

- design.md §6.2 / preset では `driver` / `implementer` / `reviewer` / `designer` / `user_review` が登場
- race topology では `spawn.py` が `candidate0` / `candidate1` … を自動生成 (line 290-294)
- project が custom topology を定義可能 (§6.3)、 そこで自由に role 名を決められる

→ **fix しない**。 ただし conventional 名のリストを `docs/role-structure.md` 等に明記する (typo / 揺れ防止)。 内部実装は role 名を opaque string として扱う。

### 2.4 既存 driver-prompt 散文の扱い

- driver-base.md には現状 role の散文無し → **何もしない**
- driver-prompt.md の front matter `role:` 行は維持 (人間が pane を attach したとき見える)
- task description (人間が書く部分) に 「お前は implementer だ」 と書く慣習は止める方向の **ガイドライン** を leader-handoff.md に追加
- driver-base.md に 「自分の role は `$FLEET_DRIVER_ROLE` または `fleet-agent role` で取れる」 と 1 行案内を追加 (これは新規追加だが 1 行で済み、 §10.2 base prompt 短く保つ趣旨に反しない)

### 2.5 cli-split-proposal §7-4 の `FLEET_ROLE` との関係

cli-split §7-4 で議論された `FLEET_ROLE=leader|driver` は **「user shell からの誤実行 guard」 用途**。 これは pane の主体 (leader / driver / user shell) を区別する env で、 値域が `leader|driver` の 2 値。

今回扱うのは **「driver の自己認識」 用途**。 値域は `implementer|reviewer|designer|...` で、 主体ではなく role。 同じ名前にすると意味が混ざる。

→ **別名 `FLEET_DRIVER_ROLE` を採用**。 cli-split §7-4 の `FLEET_ROLE` を後で導入する余地は残る (任意なので未着手だが、 もし導入する場合も衝突しない)。

### 2.6 role 切り替えの粒度

「同 task 内で implementer done → 同 worktree で reviewer 再起動」 は技術的に可能だが、

- 同 pane で agent CLI を kill → 再起動するか、 別 pane を開くか
- task.yaml の `role:` を rewrite する責務はどこ (driver 自身 / leader / orchestrator)
- pair_review / multi_stage の進行制御は **§11 priority 2 (topology orchestration)** の領分

→ **今回は要らない**。 1 driver = 1 role 固定。 次 role は 「別 driver として spawn」 で扱う方が clean (worktree / pane / state が綺麗に分離する)。 同 worktree での再起動は §11 priority 2 を解いた後で再検討する。

### 2.7 後方互換

env を追加するだけ、 既存ファイルの形式は変えない (front matter 維持)、 base prompt は 1 行追加のみ。 後方互換はほぼ自動で取れる。

- 既存の走行中 driver pane には `FLEET_DRIVER_ROLE` が無い → `fleet-agent role` は `task.yaml` から取り直す fallback で動く
- driver-prompt.md の front matter は変更なし → 既存 task の再開も問題なし
- preset YAML の `role:` 表記は既存形式のまま → 何も書き換えない

---

## 3. 案の比較

### 案 A. env var (`FLEET_DRIVER_ROLE`) 単体

spawn 時に pane env に `FLEET_DRIVER_ROLE=implementer` を注入、 driver は `$FLEET_DRIVER_ROLE` で読む。

- 実装: `spawn.py` の `driver_env` dict に 1 行追加 (3 行未満)
- メリット: 最短 typing (`$FLEET_DRIVER_ROLE`)、 implementation cost 最小
- デメリット: SOT が env (揮発) になる。 attach 再開 / 再 spawn 時にずれるリスク (※ tmux `new-window -e` は window scope なので window 生存中は持続するが、 window kill → 再 spawn で消える)
- デメリット: CLI から構造化アクセスする手段がない

### 案 B. task.yaml の既存 `role:` フィールド + `fleet-agent role` コマンド

`task.yaml` を SOT とし、 driver は `fleet-agent role` を叩いて取り出す。

- 実装: `commands/role.py` 新規 (5-10 行)、 `cli.py` parser builder に entry 1 行
- メリット: SOT が永続 (file)、 既存構造に乗る、 `task.yaml` の他フィールド (agent / topology) と同経路
- メリット: driver 以外 (debug / lint) からも引きやすい
- デメリット: 都度 file I/O (現実的には role 取得は頻繁ではないので問題なし)
- デメリット: env 1 つ叩くより遅い (ms order)

### 案 C. driver-prompt.md の front matter を更に構造化 + parser を追加

front matter を YAML として parse し、 `fleet-agent role` がそれを読む。

- 実装: front matter parser + コマンド
- メリット: 1 ファイルに集約
- デメリット: driver-prompt は 「読むだけ」 のファイルが原則 (人間も AI も)、 parse 対象にすると役割増
- デメリット: task.yaml と front matter で二重保管が固定化
- デメリット: 散文との混在で誤抽出リスク

### 案 D. 別ファイル `tasks/task-<id>/role.yaml`

`role.yaml` を新設し、 そこに role 情報を集約。

- 実装: 新ファイル + state.py / spawn.py / load API
- メリット: 専用ファイルで意味明確
- デメリット: SOT が task.yaml と 2 つになる、 §5.3 の構造に余計な entry を増やす
- デメリット: 利点が薄い (task.yaml にすでに `role:` がある)

### 案 E. 推奨 — **B + A のハイブリッド**

- SOT: `task.yaml` の `role:` フィールド (案 B)
- 即時参照用: spawn 時に pane env `FLEET_DRIVER_ROLE` を注入 (案 A)
- 取り出し用 CLI: `fleet-agent role` (env を読み、 無ければ `task.yaml` にフォールバック)
- driver-base.md: 「自分の role は `$FLEET_DRIVER_ROLE` または `fleet-agent role` で確認できる」 と 1 行案内
- task description 散文への対策: leader-handoff.md にガイドライン (description に role を散文で書かない)

---

## 4. メリデメ比較表

| 軸 | A: env のみ | B: task.yaml + CLI | C: front matter + parser | D: role.yaml 新設 | **E: B + A (推奨)** |
|---|---|---|---|---|---|
| dynamic prompt injection 廃止整合 | ◎ | ◎ | △ (parse 経路追加) | ◎ | ◎ |
| SOT 永続性 | ✗ env 揮発 | ◎ file | ○ file | ○ file | ◎ task.yaml |
| 既存 state 構造との整合 | ○ | ◎ 既存 field 流用 | △ 二重保管 | ✗ 構造追加 | ◎ |
| 取り出し速度 | ◎ env 即値 | △ file I/O | △ parse | △ file I/O | ◎ env hot path |
| typing 短さ | ◎ `$FLEET_DRIVER_ROLE` | ○ `fleet-agent role` | ○ | ○ | ◎ + ○ 両用 |
| 拡張余地 (構造化出力) | ✗ env は string | ◎ CLI で `--json` 等 | △ | ○ | ◎ |
| 実装コスト | 極小 | 小 | 中 | 中 | 小+極小 |
| 既存方針との衝突 | なし | なし | §10.2 微妙 | なし | なし |

却下: 案 C (parser 経路を増やすのは §10.2 の趣旨に反する方向)、 案 D (利点が薄い、 SOT を増やすだけ)。
比較対象: 案 A (env 単体)、 案 B (CLI 単体)、 案 E (両方)。

**案 E が user 主訴 (driver の自己認識を構造化、 dynamic prompt injection 廃止と整合) を最も素直に満たす**。 案 A 単体だと SOT が揮発、 案 B 単体だと typing と速度が惜しい。 両方足しても実装コストは小さい。

---

## 5. 推奨

**案 E (task.yaml SOT + env 即値 + `fleet-agent role` CLI)** を推奨。

### 5.1 全体像

```
SOT:           tasks/task-<id>/task.yaml  の  role: implementer
              ↑ spawn 時に topology から自動解決して書き込む (現状の挙動)

即時参照:      pane env  FLEET_DRIVER_ROLE=implementer
              ↑ spawn.py の driver_env dict に追加

取り出し API:  fleet-agent role
              ↑ env 優先、 fallback で task.yaml を読む

base prompt:   docs/prompts/driver-base.md  に 1 行案内追加
              ↑ 「Your role: $FLEET_DRIVER_ROLE (or `fleet-agent role`)」

散文の流出止め: docs/leader-handoff.md にガイドライン
              ↑ task description に role を散文で書かない、 構造で渡す
```

### 5.2 各論

- **値域**: fix しない。 conventional 名 (`driver` / `implementer` / `reviewer` / `designer` / `user_review` / `candidate<N>`) を docs に列挙
- **role 切り替え**: 同 task 内では行わない。 別 role は別 driver として spawn する (§11 priority 2 で扱う)
- **`FLEET_ROLE` との関係**: 別名 `FLEET_DRIVER_ROLE` を採用、 cli-split §7-4 の `FLEET_ROLE` (主体識別 guard) と衝突しない
- **既存散文**: driver-base.md は role 散文を持たない現状を維持 (1 行案内のみ追加)、 task description 側のガイドラインを leader-handoff.md に追加

### 5.3 実装作業項目 (参考、 本書では着手しない)

1. `src/fleet/commands/spawn.py` line 230-234 の `driver_env` dict に `"FLEET_DRIVER_ROLE": role_name` を追加
2. `src/fleet/commands/role.py` を新規追加 (env 優先、 task_context.resolve() で fallback)
3. `src/fleet/cli.py` の `build_parser_agent()` に `role` sub-command を登録
4. `docs/prompts/driver-base.md` に 1 行追加 (「Your role: `$FLEET_DRIVER_ROLE` (or `fleet-agent role`)」)
5. `docs/leader-handoff.md` に 「task description に role を散文で書かない」 ガイドライン
6. `docs/role-structure.md` (新規) に conventional role 名一覧
7. `tests/` に env 注入と `fleet-agent role` の最小 test
8. `CHANGELOG.md` に追記

---

## 6. 移行戦略

破壊変更なし。 段階的に積めば走行中 task に影響なし。

| 段階 | 変更 | 影響 |
|---|---|---|
| 0 (現状) | task.yaml に `role:` 既存、 env に role なし、 CLI なし | driver は散文か手 grep で role を知る |
| 1 | spawn.py の `driver_env` に `FLEET_DRIVER_ROLE` 追加 | 新規 spawn 以降の pane で env 参照可、 既存 pane は無影響 |
| 2 | `fleet-agent role` 追加 | env が無い既存 pane でも file 経由で取れるようになる |
| 3 | driver-base.md に 1 行追加 | 新規 spawn 以降の prompt で案内が見える、 既存 driver-prompt.md は手付かず |
| 4 | leader-handoff.md ガイドライン更新 | 今後の task description から散文 role を排除する習慣 |

段階 1-3 を 1 PR にまとめる想定。 段階 4 は別 PR で良い (docs 単体)。

走行中 driver pane について: 既存 pane は `FLEET_DRIVER_ROLE` が無いまま動き続けるが、 段階 2 で CLI fallback が入るので `fleet-agent role` は機能する。 driver-base.md の更新は新規 spawn のみに反映 (既存 driver-prompt.md は spawn 時に snapshot 済みなので変えない)。

---

## 7. open questions (実装フェーズで決める)

1. **`fleet-agent role` の出力形式**: plain text (`implementer\n`) が default、 `--json` で `{"role": "implementer", "topology": "pair_review", "agent": "claude:opus"}` を返すか
2. **conventional role 名の lock 度合い**: docs に列挙するだけか、 lint で warning を出すか。 lint は overkill か (custom topology が任意名を使えるべき)
3. **driver-base.md の 1 行案内の文面**: 英語ベース prompt の中で 「Your role: ...」 がどこに入るか (Environment ブロックの末尾が妥当)
4. **`FLEET_DRIVER_ROLE` を env で書き換える誘惑への対処**: pane で `export FLEET_DRIVER_ROLE=reviewer` と書くと CLI 表示は変わるが task.yaml は変わらない。 これを 「desync = bug」 として扱うか 「local override は許容」 とするか。 推奨は前者 (CLI が `task.yaml` と env を比較して mismatch を warn する)、 ただし実装コストとのトレードオフ
5. **race topology の `candidate<N>` 名前**: 自動生成名のままで良いか、 topology 定義側で `role:` を required にするか。 これは §11 priority 9 (preset topology) の範囲とも被る
6. **role 切り替えを future に許す場合の準備**: task.yaml の `role:` を mutable とするか、 履歴を `role_history:` で残すか。 今は要らないが、 §11 priority 2 (topology orchestration) を解く段で再訪
7. **task description の lint**: 「お前は implementer だ」 等の散文を CI で機械検出するかどうか。 false positive が多そうなので docs ガイドライン止まりが現実的

---

## 8. anti-scope

- 実装。 本書は方針合意までで止まる
- §11 priority 2 (topology orchestration) — role 切り替え機構は別タスク
- §11 priority 1 (completed の定義) — done を role 単位にする話は本タスク外
- §11 priority 3 (driver の commit 責務) — commit 主体の話は本タスク外
- cli-split-proposal §7-4 の `FLEET_ROLE` (主体識別 guard) — 別用途、 必要になったとき別途検討
- driver-prompt.md の front matter format 変更 — 維持 (人間が pane で見たとき可読のまま)
- preset topology schema の刷新 — §11 priority 9 の領分
- task description の機械 lint — open question #7、 docs ガイドライン止まり

---

## 9. 議論履歴

- 2026-05-20 初稿: 案 E (B + A ハイブリッド) を推奨。 SOT を task.yaml に固定し、 env で即値参照、 薄い CLI で fallback。 driver-base.md 1 行案内追加、 task description ガイドラインを leader-handoff.md に。 §11 priority 4 に正面から取り組み、 priority 1-3 / cli-split §7-4 とは衝突しない範囲で整理。
