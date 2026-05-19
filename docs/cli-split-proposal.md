# fleet CLI 分離方針 提案

> 作成: 2026-05-19 / driver: task-cli-split
> ステータス: 方針合意済み (実装前)
> 関連: `docs/design.md` §7 §13.5, `src/fleet/cli.py`, `src/fleet/commands/*.py`, `README.md`

---

## 0. TL;DR

`./fleet --help` は今 15 sub-command をフラットに並べているが、 **「人間 (user) が打つもの」 と 「システム (leader / driver) が自動で叩くもの」 が混在** していて分かりづらい。

user 方針確認の結果:

- **「人間が使うもの」 と 「システムが自動で使うもの」 を混ぜたくない**
- driver 専用だけでなく **leader 専用も人間 CLI から外す**
- 分離方式は **2 バイナリ路線**、 `fleet` + `fleet-agent` (仮称)
- **driver / leader の階層は付けない**、 内部バイナリは sub-command フラットに並べる
- **後方互換は考慮不要** (WIP / 作り途中 / 外部依存なし)

決定方針:

```text
fleet         # user 用 (8 commands)
  init  preflight  leader  attach  status  log  topology  workflow

fleet-agent   # leader / driver が自動で叩く (7 commands, フラット)
  spawn  inbox  send-prompt  cleanup  ask  event  done
```

`fleet-agent` の `-agent` 部分は仮称、 命名は §7 で別案併記。

本書は方針合意までで止まる。 実装は次フェーズ。

---

## 1. 現状の sub-command 棚卸し

`./fleet --help` の 15 個を主体別に分類:

| # | command | 主体 | 用途 | FLEET_TASK_ID 前提 | 頻度 |
|---|---|---|---|---|---|
| 1 | `init` | **user** | プロジェクト初期化 | — | 1 回 |
| 2 | `preflight` | **user** | 環境チェック | — | 任意 |
| 3 | `leader` | **user** | leader pane 起動 | — | 高 |
| 4 | `attach` | **user** | tmux attach shortcut | — | 高 |
| 5 | `status` | **user** | プロジェクト全体状態 | — | 高 |
| 6 | `log` | **user** (or leader) | events.jsonl tail | — | 中 |
| 7 | `topology list \| show` | **user** (setup) | preset / custom topology | — | 低 |
| 8 | `workflow list \| show \| set` | **user** (setup) | workflow plugin | — | 低 |
| 9 | `spawn` | **agent (leader)** | driver pane 起動 | — | 中 |
| 10 | `inbox` | **agent (leader)** | driver の inbox.md 追記 | task_id 引数 | 中 |
| 11 | `send-prompt` | **agent (leader)** | prompt 再注入 (fallback) | task_id 引数 | 低 |
| 12 | `cleanup` | **agent (leader)** | 終了 task の片付け | task_id 引数 | 中 |
| 13 | `ask` | **agent (driver)** | needs_input event + 通知 | YES (auto) | driver pane 内 |
| 14 | `event emit` | **agent (driver)** | events.jsonl 追記 | YES (auto) | driver pane 内 |
| 15 | `done` | **agent (driver)** | task を completed に | YES (auto) | driver pane 内 |

合計: **user 用 8 個** (日常 5 / setup 3) + **agent 用 7 個** (leader 4 / driver 3)。

---

## 2. 観点

### 2.1 「人間」 と 「システム」 を混ぜない (核)

user 主訴: **`fleet --help` に出るのは、 user が直接打つことを想定したものだけにしたい**。

`spawn` / `inbox` / `cleanup` は leader (claude) が自動で叩く。 `ask` / `event` / `done` は driver (claude) が自動で叩く。 どちらも 「システムが自動で使う」 側で、 user の日常 CLI と混ぜると noise になる。

### 2.2 階層は不要、 フラットで良い

user 方針: **`fleet-agent driver ask` ではなく `fleet-agent ask` で叩けるようにしたい**。

leader と driver で内部用コマンドは綺麗に分かれる (重複なし) ので、 階層を付けず 7 個をフラットに並べて問題ない。 typing が短く、 prompt も読みやすい。

「driver pane で誤って `fleet-agent cleanup` を叩く」 余地は構造的には残るが、 これは driver-prompt 側で 「driver が叩くのは ask / event / done だけ」 と書いておけば足る。 物理的なバリアまでは要らない。

### 2.3 後方互換は考慮不要

user 方針: **WIP / 作り途中、 外部依存なし、 alias 不要**。

→ 移行戦略はシンプル。 一斉切替、 旧コマンド名は残さない。 docs / prompt / tests も一気に書き換え。

### 2.4 driver-only は FLEET_TASK_ID 前提

`ask` / `event` / `done` は `task_context.resolve()` で `FLEET_TASK_ID` → cwd → `--task-id` の順に解決する。 driver pane の外で叩いても基本動かない / 動くと事故 (誤った task_id を completed にする)。

`fleet-agent` 1 バイナリ内に同居しても、 leader pane では `FLEET_TASK_ID` が無いので `fleet-agent done` は引数なしで叩いた瞬間にエラーで止まる。 構造的事故は env が無いことで防げる。

### 2.5 design.md §13.5 「単一 script」 の更新

design.md §13.5 は今 「単一 script `./fleet`」 と言い切っている。 2 バイナリ化に伴い、 ここを **「2 entrypoint (`fleet` + `fleet-agent`)、 内部 module は共有」** に書き換える必要がある。 §6 で具体差分を提示。

---

## 3. 分離方式の候補

### 案 1. 1 バイナリ + help suppress (最小コスト)

`argparse.SUPPRESS` で agent 系 7 個を `fleet --help` から隠す。 機能は残す。

- メリット: 工数最小、 後方互換 100% (考慮不要だが副作用として)
- デメリット: **物理分離なし**。 user 主訴 「混ぜたくない」 に対して構造的に弱い。 同一バイナリに同居している以上、 typing 上の混在は残る

### 案 2. 1 バイナリ + sub-command 階層 (`fleet agent ask` 等)

```text
fleet agent ask "..."
fleet agent spawn 1 "..."
```

- メリット: namespace で意図明示
- デメリット: **user が 「階層不要」 と明言**、 typing 長くなる。 却下

### 案 3. 2 バイナリ + フラット (`fleet` + `fleet-agent`)  **★ 推奨**

```text
fleet                    # user 用 (8 commands)
  init  preflight  leader  attach  status  log  topology  workflow

fleet-agent              # leader / driver 自動呼び出し用 (7 commands, フラット)
  spawn  inbox  send-prompt  cleanup  ask  event  done
```

ファイル構成:

```text
agent-fleet/
  fleet                  # 既存 entrypoint (user 向け)
  fleet-agent            # 新 entrypoint (システム自動呼び出し用)
  src/fleet/
    cli.py               # build_parser_user() / build_parser_agent() を分離
    commands/            # 全コマンド共有 (実装は 1 か所)
```

2 つの shebang script は同じ `src/fleet/` を import。 entrypoint script 1 個 + `cli.py` の parser builder 分割のみで済む。

- メリット:
  - **user の `fleet --help` が完全に clean** (8 個)
  - **フラットな CLI で typing 最短** (`fleet-agent ask "..."`、 階層なし)
  - leader / driver の pane に `PATH` 細工で `fleet-agent` を通せば prompt 短い
  - 内部用コマンドが将来増えても、 user CLI が膨れない
- デメリット:
  - バイナリ 2 つ (実体は shebang script 2 行、 重さ無視可)
  - design.md §13.5 更新 (本提案 §6)
  - docs / prompt / tests の名前置換 (後方互換考慮不要なので一気に)

### 案 4. 3 バイナリ (`fleet` + `fleet-leader` + `fleet-driver`)

- メリット: leader / driver で物理的に分離、 driver pane で leader CLI が見えない
- デメリット: **user が 「driver / leader 階層不要」 と明言**。 バイナリ 3 つは過剰。 却下

---

## 4. メリデメ比較表

| 軸 | 1: help suppress | 2: 階層化 | **3: 2 バイナリ ★** | 4: 3 バイナリ |
|---|---|---|---|---|
| 「混ぜない」 主訴 | △ help 上だけ | ○ 文法レベル | ◎ 完全分離 | ◎ 完全分離 |
| 階層なし主訴 | ◎ | ✗ 違反 | ◎ | ◎ |
| 構造的事故防止 | △ | ○ | ○ | ◎ |
| typing 長さ | 短 | 長 | 短 | 短 |
| 実装コスト | 小 | 中 | 中 | 中 |
| バイナリ数 | 1 | 1 | 2 | 3 |
| 拡張余地 | △ | ○ | ◎ | ◎ |

却下: 案 2 (階層化主訴に反する) / 案 4 (3 バイナリ過剰)。
比較対象: 案 1 (現状ほぼ維持) と 案 3 (推奨)。 user 主訴を満たすのは案 3 のみ。

---

## 5. 後方互換 (考慮不要、 移行は一斉切替)

WIP / 外部依存なしの前提で、 旧コマンド alias は残さない。 実装フェーズで以下を一気に更新する:

- `src/fleet/driver_prompt.py` の文字列を `fleet ask` → `fleet-agent ask` 等に置換
- `docs/leader-handoff.md` の leader 引き継ぎ prompt 内 `fleet spawn` → `fleet-agent spawn` 等
- `docs/design.md` §7 / §13.5 更新 (§6 に差分案)
- `README.md` Commands セクションを 2 ブロックに再構成
- `tests/*.py` のうち `fleet.cli.build_parser()` を呼ぶ箇所を新 parser に追従
- `CHANGELOG.md` に migration note

すでに走っている driver / leader pane が古いコマンドを期待する問題は、 cutoff date を切って手動で終了させる。 WIP なので影響範囲は小さい。

---

## 6. 推奨

**案 3 (2 バイナリ + フラット)** を推奨。

理由:
- user 主訴 3 つ (「混ぜない」 / 「階層不要」 / 「後方互換不要」) を素直に満たす唯一の案
- バイナリ 2 個は実装コスト的にも控えめ (案 4 ほどではない)
- user 主訴を満たした上で、 typing が最短

### 実装作業項目 (参考、 本書では着手しない)

1. `src/fleet/cli.py` を `build_parser_user()` / `build_parser_agent()` に分離
2. `fleet-agent` shebang script を repo 直下に追加 (3 行程度)
3. `src/fleet/driver_prompt.py` の文字列を `fleet-agent <cmd>` に置換
4. `src/fleet/commands/spawn.py` で driver / leader pane の env に `PATH=<repo>:$PATH` を仕込む (もしくは `fleet-agent` を `/usr/local/bin` 等に置く前提なら不要)
5. `docs/leader-handoff.md` の文字列を `fleet-agent` に置換
6. `docs/design.md` §7 / §13.5 を更新 (下記差分)
7. `README.md` Commands セクションを `fleet` / `fleet-agent` の 2 ブロックに再構成
8. tests を新 parser 構成に追従
9. `CHANGELOG.md` に migration note

### design.md §13.5 の更新案 (差分イメージ)

```diff
- 単一 script `./fleet` (shebang `#!/usr/bin/env python3`)
+ 2 entrypoint script:
+   - `./fleet`        — 人間 (user) が打つ (init / preflight / leader / attach /
+                        status / log / topology / workflow)
+   - `./fleet-agent`  — システム (leader / driver agent) が自動で叩く
+                        (spawn / inbox / send-prompt / cleanup / ask / event / done)
+ 2 つは同じ `src/fleet/` module を import する shebang script。
+ 「人間が打つもの」 と 「システムが自動で叩くもの」 を物理的に分離する設計。
```

### design.md §7 の更新案 (差分イメージ)

```diff
- fleet ask "<question>"
+ fleet-agent ask "<question>"
```

---

## 7. open questions (実装フェーズで決める)

1. **`fleet-agent` の命名**: 別案として `fleet-internal` / `fleet-sys` / `fleet-bot` / `fleeta` 等。 推奨は `fleet-agent` (「agent が叩く」 が直球で意味が伝わる)。 ただし design.md §2 で 「agent と呼ぶと leader と区別がつかない」 と書いた手前、 用語の整合性に若干注意 (bin 名としての agent は「leader / driver の総称としての agent CLI」 と読めば矛盾しない)
2. **PATH 仕込み**: `spawn.py` が pane の env に `PATH=<repo>:$PATH` を渡すか、 repo 直下に置いて user の `PATH` 標準依存にするか
3. **`log` の所属**: 現状 user 用に置いているが、 leader が裏で `fleet log` を叩く想定もある。 user 用 `fleet log` と内部用 `fleet-agent log` の両方に置くか、 user 専用に統一するか
4. **env による安全策**: `fleet-agent` が `FLEET_ROLE=leader|driver` 等を要求して、 user shell から手で叩けないようにする強化策。 やるなら案、 やらなくても構造的に大事故は起きないので任意

---

## 8. anti-scope

- 実装。 本書は方針合意までで止まる
- 配布 (`pip install` パッケージ化) ── design.md §13.5 で MVP 不要
- 旧コマンド名の alias (移行期間含めて作らない)
- 通知 / dashboard 周りの CLI 拡張
- 案 2 (階層化) / 案 4 (3 バイナリ) ── user 主訴により却下

---

## 9. 議論履歴

- 2026-05-19 初稿: 案 E (1 バイナリ + help カテゴリ + SUPPRESS) を推奨
- 2026-05-19 user 1 回目: 「別バイナリにしたい」 「driver だけでなく leader も人間 CLI から分離」 → 案 4 (3 バイナリ: `fleet` + `fleet-leader` + `fleet-driver`) に書き直し
- 2026-05-19 user 2 回目: 「2 バイナリで行きたい」 「driver / leader 階層不要、 フラットで」 「後方互換考慮不要、 WIP だから」 → **案 3 (2 バイナリ + フラット: `fleet` + `fleet-agent`)** に書き直し ← 現状
