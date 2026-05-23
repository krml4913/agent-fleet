# formation guide

> formation を **読む / 編集する leader 向け** の実用ガイド。
> 設計の背景・確定済み判断は `docs/design.md` §6 に書く。 ここはその使い方。
>
> 想定読者: leader (claude / codex 両 vendor)。 user は formation を直接編集せず、
> leader に依頼するのが標準フロー (`user-types-prompts-not-commands` memory)。

---

## 1. formation とは

formation は **「1 タスクを誰がどう進めるか」 の YAML 定義** 。

`fleet-agent start <task-id> --formation <name>` で task を起動すると、
formation の `stages` に従って driver が順次起動される。
人間の承認ポイント (`user_approval`) や AI 査読 (`peer_review`) も formation で表現する。

### template と formation の二項対立 (Issue #105 で確定)

| 区分 | 配置 | 役割 |
|---|---|---|
| **formation template** | `src/fleet/templates/<name>.yaml` (fleet 同梱) | 雛形。直接実行できない。 `fleet formation init --from` でコピーする source |
| **formation** | `<state>/formations/<name>.yaml` (project ごと) | 実体。runtime はこれだけを解決対象にする |

- template は fleet 同梱で 3 つ (`solo` / `pair_review` / `multi_stage`)。 編集禁止。
- コピーした瞬間に template と formation は独立する (追従なし)。
- project の formation は自由に編集してよい — agent 差し替え、stage 追加、gate 削除など。
- 大規模に作り直したいなら `rm <state>/formations/<name>.yaml && fleet formation init --from <template>` で再生成。

---

## 2. YAML スキーマ

### 2.1 トップレベル

| field | 必須 | 型 | 説明 |
|---|---|---|---|
| `name` | ✅ | string | formation 識別名。ファイル名 (stem) と一致させる |
| `description` | optional | string | 人間向け説明 |
| `stages` | ✅ | list (1 件以上) | stage オブジェクトのリスト |

### 2.2 `stages[]` (各 stage)

| field | 必須 | 型 | 説明 |
|---|---|---|---|
| `role` | ✅ | string | driver の役割名 (例: `driver` / `implementer` / `designer` / `code-reviewer`) |
| `agent` | optional | string | 起動 agent (例: `claude:sonnet`)。省略時は `--agent` で渡された値が使われる |
| `peer_review` | optional | mapping | AI 査読を挟む場合の入れ子定義 (§2.4) |
| `user_approval` | optional | string \| mapping | 人間承認ゲート (§2.5) |

### 2.3 `role` の値

`role` は **driver の役割名** に過ぎず、 fleet core が enum で縛っていない。
任意の文字列を書ける。 慣習的に使われている値:

- `driver` — solo formation の汎用役。`docs/prompts/roles/driver.md` の役割断片が読まれる
- `implementer` — 実装担当
- `designer` — 設計担当
- `code-reviewer` — `peer_review.role` でよく使う

`role` 文字列は driver-prompt の合成に使われる:
`docs/prompts/roles/<role>.md` があれば role 断片として混ぜる (なければ無視)。
新 role を増やしたい時は同名の `docs/prompts/roles/<role>.md` を用意するのが推奨。

### 2.4 `agent` spec

書式は `vendor:model`。 MVP は **claude / codex の 2 vendor のみ** (`design.md` §13.2)。

| 例 | 解釈 |
|---|---|
| `claude:opus` | claude vendor の opus モデル |
| `claude:sonnet` | claude vendor の sonnet モデル |
| `codex:gpt-5.5` | codex vendor の gpt-5.5 モデル |

解決規則 (`fleet-agent start`):

1. stage の `agent:` が指定されていればそれを使う
2. なければ `--agent` 引数の値を使う
3. それもなければエラー (`no agent for role <role>; pass --agent or set one in the formation`)

`peer_review.agent` の解決は別ルール (§2.4):
**peer_review.agent → 同 stage の agent → `claude:sonnet`** の順でフォールバック。

### 2.5 `peer_review` (入れ子の AI 査読)

```yaml
peer_review:
  role: code-reviewer    # 必須。査読者の役割名
  agent: claude:opus     # optional。省略時は同 stage の agent → claude:sonnet
```

stage の実行順序:

```
implement → peer_review loop (max 3 iter) → user_approval gate (あれば) → stage done
```

- 実装者が `fleet-agent done --result approved` を呼ぶと reviewer pane が立ち上がる。
- reviewer が `done --result approved` なら次へ進む。
- reviewer が `done --result changes-requested` なら実装者 pane に inbox handoff が飛んで iteration が +1。
- iteration 3 回を超えたら task は `awaiting_orders` になって user に escalate される。

stage 内では実装者 / reviewer の agent CLI pane は閉じずに **保持** される (`design.md` §6.2)。
これにより agent context が iteration をまたいで残る。

### 2.6 `user_approval` (人間承認ゲート)

2 つの書き方:

```yaml
# 短縮形 (string)
user_approval: required    # or "optional"

# 完全形 (mapping)
user_approval:
  required: true           # or false
```

挙動:

- `required: true` の stage が完了した時点で task の status が `awaiting_orders` に遷移。
- user が leader に承認 / 却下を伝え、 leader が `fleet-agent approve <id>` / `fleet-agent reject <id>` を中継する。
- reject されると stage が implementation に戻る (peer_review がある場合は実装者 pane を起こす)。

ゲートの判断は **user に届く** — leader は自己承認しない (`user-approval-gate` memory)。

---

## 3. 同梱 template の解説

### 3.1 `solo` — 一人で完結

```yaml
name: solo
description: One driver works the task end-to-end.
stages:
  - role: driver
    agent: claude:sonnet
```

- 1 stage / 1 driver。 承認ゲートも査読もなし。
- 試作 / 軽量タスク / leader からの即座委譲向け。
- `fleet-agent done --result approved` で task 完了。

### 3.2 `pair_review` — 実装 + AI 査読 + user 承認

```yaml
name: pair_review
description: Implementer with AI peer review; the user has the final say.
stages:
  - role: implementer
    agent: codex:gpt-5.5
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required
```

- 実装者 (codex) → 査読者 (claude opus) → user 承認 で 1 stage を完結。
- multi-vendor の中核 formation (`multivendor-is-core` memory)。
- 査読 iteration max 3、 超過時は user escalate。

### 3.3 `multi_stage` — 設計 → 実装 + 査読、各段で user 承認

```yaml
name: multi_stage
description: Sequential pipeline with user approval at each stage.
stages:
  - role: designer
    agent: claude:opus
    user_approval: required
  - role: implementer
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required
```

- 設計段 (claude opus) → user 承認 → 実装段 (claude sonnet + 査読 + user 承認)。
- 大きめのタスクで設計と実装を分離したい時に使う。

---

## 4. 編集の典型ケース (leader 向け cookbook)

> 編集前に必ず `fleet formation show <name>` で現状を読む。
> 編集後も同じコマンドで validate を回す。

### 4.1 agent を入れ替える (例: rate limit で claude → codex)

```yaml
# Before
- role: driver
  agent: claude:sonnet

# After
- role: driver
  agent: codex:gpt-5.5
```

`agent:` 行だけ書き換える。 stage 構造は変えない。

### 4.2 solo に code-reviewer を足す

`solo` を `peer_review` 付きに格上げ:

```yaml
name: solo            # 名前は維持してよい。 別名にしたいなら別ファイルに分ける
stages:
  - role: driver
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
      agent: claude:opus
```

### 4.3 user_approval gate を抜く / 付ける

```yaml
# 抜く: field ごと削除する (空文字列にはしない)
- role: implementer
  agent: claude:sonnet
  peer_review:
    role: code-reviewer
    agent: claude:opus
  # user_approval: required  ← この 1 行を削除

# 付ける: string 短縮形が読みやすい
- role: driver
  agent: claude:sonnet
  user_approval: required
```

### 4.4 stage を 1 段増やす (実装の前に設計段を挟む)

```yaml
stages:
  # 追加
  - role: designer
    agent: claude:opus
    user_approval: required

  # 既存
  - role: implementer
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required
```

順序は YAML の上から下。 設計段が approved されてから実装段が起動する。

### 4.5 peer_review の agent だけ替える

```yaml
peer_review:
  role: code-reviewer
  agent: codex:gpt-5.5    # claude:opus から差し替え
```

`peer_review.agent` 省略時は同 stage の `agent` → `claude:sonnet` にフォールバックするので、
明示しないと意図しない agent で査読される可能性がある。 vendor を分けたい時は明示する。

---

## 5. validation と挙動

### 5.1 `fleet formation show <name>` の挙動

- `<state>/formations/<name>.yaml` を読んで `formation.validate()` を回す。
- スキーマエラーがあれば stderr に `warn: formation validation failed: <reason>` を出すが、
  本体 YAML は stdout に出す (確認のため)。

### 5.2 validate() が見るもの (`src/fleet/formation.py`)

- top-level に `name` が必要 (空文字列もエラー)
- top-level に `stages` が必要、 1 件以上のリストでないとエラー
- 各 stage は mapping、 `role` が必要

これより細かい構造検証 (`peer_review` の中身 / `agent` spec の解析 / `user_approval` の形) は
**orchestrator が runtime で見る** (`design.md` §6.4)。 静的にチェックしない。

### 5.3 起こり得るエラー

| 状況 | エラー / 挙動 |
|---|---|
| `formations/<name>.yaml` が無い | `fleet-agent start --formation <name>` が `ResolutionError`。 template fallback はしない |
| `formations/` が空 + `--formation` 省略 | leader-session.json の agent で `_leader_solo` を即興合成 (1-stage solo) |
| `formations/` に 2 件以上 + `--formation` 省略 | 曖昧エラー (`--formation <name>` を渡すよう案内) |
| YAML 解析失敗 | `formation file must be a YAML mapping: <path>` |
| `name` 欠落 | `formation missing required field: name` |
| `stages` 欠落 / 空 | `formation 'stages' must be a non-empty list` |
| stage に `role` 無し | `formation stages[i] missing required field: role` |
| `agent` 解析失敗 | runtime で `unsupported vendor` / `agent spec must be 'vendor:model'` |
| 未対応 vendor (例: `openai:gpt-4`) | `unsupported vendor 'openai'; supported: ['claude', 'codex']` |

---

## 6. ベストプラクティス

- **編集前に `fleet formation show` で現状を読む** — 前任者が手を入れている可能性がある。
- **編集後も `fleet formation show` を回す** — top-level validate は最低限通しておく。
- **大規模に作り直す時は再生成** — `rm <state>/formations/<name>.yaml` してから `fleet formation init --from <template>` で雛形をやり直す方が早い。
- **`agent` 省略を多用しない** — `--agent` 引数で上書きできるのは便利だが、 stage ごとに明示した方が事故が少ない。 特に `peer_review.agent` は明示推奨。
- **vendor 固有の話は role 名や description に書かない** — formation は claude / codex 両 vendor の leader が読む前提。 vendor に依存する記述は driver-prompt 側 (role 断片) に追い出す。
- **stage の `role` 名は `docs/prompts/roles/<role>.md` と整合させる** — role 断片が読まれないと driver が役割を理解しないまま起動する。

---

## 7. CLI リファレンス (簡潔)

| コマンド | 説明 |
|---|---|
| `fleet formation list` | template + custom formations を一覧表示 |
| `fleet formation show <name>` | custom formation の YAML を表示 + validate |
| `fleet formation init --from <template> [--name <name>]` | template をコピーして `<state>/formations/<name>.yaml` を作成 (`--name` 省略時は template 名と同じ) |
| `fleet-agent start <task-id> --formation <name>` | formation を解決して task を起動 |
| `fleet-agent approve <task-id>` | `user_approval` gate の承認を中継 |
| `fleet-agent reject <task-id>` | `user_approval` gate を却下、 stage を implementation に戻す |

詳細フラグは `fleet formation --help` / `fleet-agent start --help` を参照。

---

## 8. スコープ外

以下は本 doc では扱わない:

- skill / CLI 補助コマンド (`fleet formation edit` 等) — 別議論。 skill は Claude Code 固有機能で codex で使えず、 multi-vendor の柱と相容れない。
- formation の partial overlay / inherit — Issue #105 で見送り済み、 現状仕様に無い。
- count フィールドや動的並列起動 — `design.md` §6.1 で **count なし** が確定済み。
