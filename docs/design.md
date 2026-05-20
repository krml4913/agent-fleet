# agent-fleet 設計資料

> claude-forge を後継する新システム `agent-fleet` の初期設計資料。
> **確定済みの設計判断** を記載する。 未確定の設計課題 / open question は
> GitHub Issues で管理する (2026-05-20 移行、 旧 §11)。
>
> 最終更新: 2026-05-20

---

## 1. 概要

### 1.1 mission

> **ユーザーはタスク依頼は leader、 タスク内の要件詰めは driver と直接対話する。**
> **leader は multi-vendor agent (claude / codex) を組み合わせて、**
> **プロジェクトごとに定義された team 編成で開発を進めるシステム。**

### 1.2 3 つの柱

| 柱 | 内容 |
|---|---|
| **1. 階層対話 UI** | タスク依頼は leader、 タスク内の要件詰めは driver と直接 (両方 tmux 上) |
| **2. multi-vendor agent** | claude / codex を組み合わせて使える (MVP は 2 vendor、 OpenAI / Gemini は後段) |
| **3. team topology 定義** | project ごとに team 編成 (driver 1 人 / driver + reviewer / 多段) を YAML で選択 |

### 1.3 既存 claude-forge との位置づけ

- claude-forge は **作り直し対象**、 新 repo `agent-fleet` で 0 から構築する
- claude-forge の memory / vault / museum 等の蓄積知見は移植対象 (詳細は §10)
- claude-forge 固有の自律サイクル運転は agent-fleet では **mission に含めない**
  - あれは claude-forge 自身を開発するための特殊用途
  - agent-fleet では 「ユーザーがタスク依頼する」 を主軸にする

---

### 1.4 設計原則

新機能・新提案は、 まずこの原則に照らせ。 原則に反するものは、 どれだけ魅力的でも入れない。
claude-forge は機能肥大と技術的負債で作り直しになった —— agent-fleet はその逆を行く。

1. **scope を削ることに価値がある。** 機能は「あったら便利」 では足さない。
   「無いと task が回らない」 ものだけ入れる。 迷ったら入れない。 forge は
   「便利そう」 を足し続けて死んだ。

2. **実害ベースで判断する。** 機能の魅力や「綺麗さ」 ではなく、 「解決すべき
   実害が現に在るか」 で決める。 実害の無い課題は解かない。
   (例: 「role が二重管理」 という proposal は、 実害が誤認だったため却下した)

3. **提案の前提を疑う。** 「どう実装するか」 の前に「その問題は本当に存在するか」
   を問う。 proposal や課題設定そのものが誤っていることがある。 鵜呑みにしない。

4. **判断は AI、 配線は仕組み。** driver (AI) が判断し、 orchestrator (プログラム)
   が決定論的に配線する。 判断のために AI を増やさない (task ごとの "owner AI" は
   作らなかった —— 進行は state machine で足りる)。 仕組みは判断しない。

5. **例外の多い仕事は AI に寄せ、 定型は仕組みに。** git の commit / push /
   conflict 解決は例外だらけ → driver (AI) がやる。 worktree の作成/削除は定型
   → 仕組みがやる。 プログラムで全例外を捌こうとしない。

6. **構造でシンプルさを守る。** 1 task = 1 file、 多重 SOT を作らない、 daemon を
   持たない、 polling しない。 規律を人の注意力ではなくプログラム構造で担保する。

7. **leader は軽い。** 会話と spawn だけ。 状態追跡・進行管理・polling を背負わせ
   ない。

8. **人間の介入路を常に残す。** 完全自律にはしない。 user は driver と直接対話
   できる —— これが forge から継承する独自性であり mission の核。

9. **core は最小、 開発フローは外付け。** fleet core は coding 専用の機能を
   持たない。 git / PR / changelog 等は driver と plugin の領分 (§8)。

並列実行・エージェント相互通信・race・dynamic prompt injection を agent-fleet が
持たないのは、 すべてこの原則の帰結である (詳細は §9 anti-scope)。

---

## 2. 命名

| 項目 | 名前 | 備考 |
|---|---|---|
| repo 名 | `agent-fleet` | multi-vendor 前提を表現、 claude- prefix なし |
| CLI 名 | `fleet` | 5 文字、 typing 体感は forge と同等 |
| leader | leader | 維持 |
| driver | driver | 維持。 「agent」 と呼ぶと leader と区別がつかなくなるため |

その他の用語 (museum / vault / corpus / dna / breed / ghost / quest XP / personality 等) は §10 で棚卸しする。

---

## 3. アーキテクチャ全体像

```
[user] <--tmux--> [leader: 会話 + start のみ]
                       |
                       v fleet-agent start
                  [driver pane] (tmux window)
                       |
                       +--> events.jsonl (append-only)
                       +--> notification (macOS / slack)
                       +--> dashboard (read-only view)
                       |
                       v 必要時に user が介入
                  [user が driver pane に直接 attach して対話]
```

### 3.1 重要な思想

- **leader は軽い**: 会話と `fleet-agent start` のみ、 状態 polling や needs_input 検知はしない
- **driver は直接 user に届く**: events.jsonl + 通知 + dashboard 経由、 leader を中継しない
- **user は driver と直接話せる**: tmux attach すれば pane に介入できる (これが forge から継承する独自性)
- **fleet 自体は開発フロー非依存**: worktree / PR / changelog 等は **plugin** で外付け

---

## 4. 責務分担

### 4.1 leader の責務

- ユーザーとのタスク依頼会話
- `fleet-agent start` でタスク開始 (どの agent vendor / model / topology で起動するか決める)
- 必要に応じてユーザーへの高レベル進捗報告

leader は driver の状態を polling したり、 needs_input を検知したりは **しない**。 これらは構造 (events / dashboard / 通知) が user に直接届ける。

### 4.2 driver の責務

- 与えられたタスクの実装 (member subagent への委譲含む)
- 進捗を events.jsonl に追記
- ユーザー入力が必要になったら **`fleet-agent ask`** 専用 CLI を呼んで届ける (詳細は §7)
- 完了時に自己クリーンアップ

### 4.3 ユーザーの責務

- leader に対してタスクを依頼する (tmux 上)
- 通知 / dashboard で driver の状態を把握する
- 必要に応じて driver pane に直接 attach して対話する
- driver の質問に答える、 マージ判断をする (topology による)

---

## 5. project / state 配置

### 5.1 配置方針

- 各 project repo 内 `.fleet-state/` にすべて閉じる
- **global metadata なし** (ホーム配下 `~/.fleet/` 等には何も置かない)
- これにより複数 project の state が衝突しない、 PJ 削除時に state も自然に消える

### 5.2 multi-project 同時起動

- 各 project ごとに `fleet init --name <project-name>` で初期化
- tmux session 名 `fleet-<project-name>` で project ごとに分離
- 複数 PJ 同時起動可能、 衝突しない設計

```bash
fleet init --name image-gallery /path/to/image-gallery
fleet init --name learn-xgboost /path/to/learn-xgboost
# tmux ls
#   fleet-image-gallery: 1 windows
#   fleet-learn-xgboost: 1 windows
fleet attach image-gallery   # 特定 project の leader へ
```

### 5.3 state 構造 (確定)

```
<project-root>/
  .fleet-state/
    project.yaml          # project name / 全体 config / active topology 等
    events.jsonl          # append-only audit log
    dashboard.md          # read-only view (state から自動生成、 直接編集禁止)
    memory/
      MEMORY.md           # 知見インデックス (各 driver が都度追記)
      GUIDE.md            # fleet memory 規律 (read/write のルール)
      *.md                # 個別 memory ファイル (driver が書く)
    tasks/
      task-<id>/
        task.yaml         # task 状態 (status / title / progress / assignee 等)
        inbox.md          # leader → driver の指示
        outbox.md         # driver → leader の報告
        driver-prompt.md  # spawn 時に展開済みの prompt
        heartbeat         # mtime で活動検知
```

### 5.4 race 対策

forge では `yq -i` / `sed -i` の partial update + lock 不徹底が race の温床だった。 agent-fleet では Python の構造で堅牢化する:

| 対策 | 内容 |
|---|---|
| **flock 排他取得** | `fcntl.flock(fd, LOCK_EX)` で write lock 取得 |
| **atomic rename** | `tmp` ファイルに全文書き出し → `os.replace(tmp, final)` で atomic 置換 |
| **partial update 禁止** | 既存ファイルへの `sed` / `>>` 等は禁止、 必ず全文 rewrite |
| **1 task = 1 file** | cross-task の同時更新が原理的に起きない構造 |
| **events.jsonl** | append-only、 POSIX `O_APPEND` で atomic、 lock 不要 |

書き込みは必ず context manager 経由:

```python
with state_writer(path) as w:
    w.update(...)
# exit 時に flock 解放 + dashboard 自動 rebuild
```

### 5.5 dashboard 更新ポリシー

- **書き込みごとに自動 rebuild** (state writer context manager の exit hook で発火)
- dashboard.md は **read-only**、 人間 / driver / leader 誰も直接編集しない
- 並行多数 driver で rebuild 連発が問題になれば debounce (100ms 以内の連続更新は最後の 1 回だけ) を後段で導入

---

## 5.6 fleet memory (確定: 2026-05-21)

### 動機

fleet は multi-vendor (claude / codex / その他) が柱。 claude driver は claude 自身の auto-memory で PJ 知見を溜められるが、 codex driver はそれを読めない。 vendor をまたいで PJ 知見を共有するには **vendor 非依存の memory ストア**が必要。 これは「あったら便利」ではなく multi-vendor を成立させる実害ベースの課題 (設計原則 §1.4)。

### 確定した設計

- **fleet memory = claude auto-memory の multi-vendor 版**。 project 単位、 `.fleet-state/memory/` に配置 (worktree の外なので全 driver が同じ実体を共有できる)
- markdown ファイル群 + frontmatter (`name` / `description` / `type`) + `MEMORY.md` インデックス + `[[name]]` 相互リンク
- **type は 3 つ**: `feedback` / `project` / `reference`。 claude auto-memory の `user` type は除外 (fleet memory は project 単位であり、 user の人物像は project 横断なので紐づかない)
- **自律保存**: 各 driver が task をこなす中で「保存すべき」と判断して書く。 明示コマンド方式は採らない
- **書き込み手段**: 専用 CLI は作らない。 driver は `$FLEET_STATE_DIR/memory/` に直接ファイルを Write する
- **claude driver との二重化**: claude driver は claude 自身の auto-memory も併用しうるが、 規律で「PJ 知見は fleet memory に書け」と寄せる。 claude auto-memory の無効化はしない (過剰)
- **driver への届け方**: `driver-base.md` には入口 1〜2 行のみ。 インデックス・規律・memory 本体は `.fleet-state/memory/` に置き、 driver が自分で読む。 base prompt を太らせない (§10.2 と整合)

### 「保存しないもの」規律 (forge 化を防ぐ防衛線)

以下はコードや git 履歴から導ける / 揮発的すぎる / 他の場所に置くべきもの:

- コードのパターン・規約・アーキテクチャ・ファイルパス — コードから読める
- git 履歴・最近の変更・誰が何を変えたか — `git log` / `git blame` が権威
- デバッグ解法や修正レシピ — 修正はコードに、 文脈は commit メッセージに
- design.md などのドキュメントに既にある内容
- 揮発的なタスク詳細 (進行中の作業、 一時的な状態)

### 規律の詳細

詳細な読み書きルール (type 定義 / 保存タイミング / 2 ステップ手順 / 陳腐化対策) は `.fleet-state/memory/GUIDE.md` に置く。 `fleet init` が自動生成する。

---

## 6. team topology

### 6.1 設計方針

- **YAML** で定義 (シンプルに始める)
- **preset** (同梱の標準 topology 数種) + **custom** (project ごとに自作可能)
- **count なし** (必要に応じて leader が動的に並列起動を判断する)
- **user_approval** を表現できる (人間の承認ポイントを stage 属性で明示)

### 6.2 topology の例

```yaml
# Topology A: solo driver (一人で PR まで完結)
name: solo
stages:
  - role: driver
    agent: claude:sonnet

# Topology B: pair review (実装者 + AI 査読 + user 承認)
name: pair_review
stages:
  - role: implementer
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
    user_approval: required

# Topology C: 多段 (設計 → 実装 + AI 査読 + user 承認)
name: multi_stage
stages:
  - role: designer
    agent: claude:opus
    user_approval: required
  - role: implementer
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
    user_approval: required
```

各 stage 内の実行順序:
```
implement → peer_review (AI 査読ループ, max 3 回) → user_approval → stage 完了
```

### 6.3 state machine (orchestrator)

- `fleet-agent done --result approved|changes-requested` が呼ばれると `orchestrator.advance()` が次を判断する
- approved: 現 stage を done にして次 stage を launch (次がなければ task completed)
- changes-requested: peer_review の phase に応じてループを回す
- peer_review 上限 (3 回) 超過時は task.status を `needs_input` に変更してユーザーへ通知

### 6.4 topology YAML schema

topology YAML の必須・任意フィールドを以下に明記する。形式言語 (JSON Schema 等) は使わない (§1.4 原則 1)。

**トップレベル**

| フィールド | 必須 | 説明 |
|---|---|---|
| `name` | 必須 | topology の識別名。ファイル名 (stem) と一致すること |
| `description` | 任意 | 人間向けの説明文 |
| `stages` | 必須 | stage オブジェクトのリスト。1 件以上必要 |

**`stages[]` (各 stage)**

| フィールド | 必須 | 説明 |
|---|---|---|
| `role` | 必須 | driver が担う役割名 (例: `driver`, `implementer`, `designer`) |
| `agent` | 任意 | 使用する agent (例: `claude:sonnet`)。省略時は `--agent` 引数の値が使われる |
| `peer_review` | 任意 | AI 査読を挟む場合に指定。`role` サブフィールドで査読者の役割を指定する |
| `user_approval` | 任意 | 人間の承認ポイント。`"required"` / `"optional"` の文字列、またはオブジェクト形式 |

`validate()` はトップレベルの `name` / `stages` 必須チェックと、各 stage の `role` 必須チェックを行う。
それ以上の形式検証 (`peer_review` の構造等) は orchestrator 側に委ねる。

### 6.5 preset / custom

- fleet 同梱 preset: `solo` / `pair_review` / `multi_stage` の 3 つ
- 各 project は `.fleet-state/topologies/` に自前 topology を定義可能 (preset を shadow)
- タスク開始時に `fleet-agent start --topology <name> ...` で選択

**preset は template である。** 同梱 preset は「こう書けば動く」という推奨デフォルト値を示すだけであり、
プロジェクト固有の制約には合わない場合がある。実プロジェクトは `.fleet-state/topologies/` に同名ファイルを
置くことで preset を上書きできるし、新しい名前で独自 topology を追加してもよい。
`agent:` 既定値の変更、`user_approval` の追加・削除など、フィールドの自由な変更を推奨する。

---

## 7. driver 通信プロトコル

### 7.1 driver からの user input 依頼

driver がユーザーに質問したい / 判断を仰ぎたい場合は **専用 CLI を呼ぶ**:

```bash
fleet-agent ask "<question>"
```

これが呼ばれると:
1. `events.jsonl` に `needs_input` event を emit
2. `dashboard.md` を再生成 (needs_input マーク反映)
3. 通知発火 (macOS / slack)

driver が pane に質問を書いただけでは **どこにも届かない**。 rule を守らないと user に届かない構造的圧力で、 prompt 命令だけより堅く担保する。

### 7.2 driver の固まり検知

- 一定時間 driver pane に活動なし → heartbeat 機構が detect
- fallback として 「needs input か?」 を driver に問い合わせる仕組みを別途置く (詳細は別途設計)

### 7.3 leader は介在しない

driver → events / dashboard / 通知 → user の経路は **leader を経由しない**。 leader は会話と `fleet-agent start` だけで忙しくないように構造で分離する。

---

## 8. core / plugin 境界

### 8.1 設計思想

> **fleet 自体に開発フロー (git / worktree / PR / changelog) に関わる機能は極力持たせない。**
> **ただし複数の開発フローを選べる plugin 機構は持つ。**

これにより coding 以外 (research / monitoring / data analysis 等) の用途も plugin 次第で乗る。

### 8.2 git 操作の責務分界 (確定: 2026-05-20)

git は例外が多い (conflict / push reject / detached HEAD / 認証切れ / rebase 失敗)。 Python コードで全例外を捌くのは破綻するため、 操作を 2 種類に分けて担い手を明確にする。

| 種別 | 操作 | 担い手 | 理由 |
|---|---|---|---|
| **作業の git** | commit / push / PR 作成 / conflict 解決 / rebase | **driver (AI)** | 例外が多く AI が柔軟に対応できる |
| **ライフサイクル境界の git** | `worktree add` / `worktree remove` | **plugin (仕組み側)** | 定型操作で例外がほぼ無い; driver が自分の作業場所を自分で作れない (鶏と卵) |

- driver は作業完了後に `commit → push → gh pr create → fleet-agent done` を実行する。 手順は `docs/prompts/driver-base.md` に明記。
- fleet core の Python コードは作業の git (commit / push / PR) を一切叩かない。
- PR のマージは driver が行わない。 leader / user の判断に委ねる。

### 8.3 配置

| 機能 | 配置 | 備考 |
|---|---|---|
| tmux pane 起動 | core | fleet の基盤 |
| inbox / outbox file 通信 | core | text-based async は本質 |
| driver-prompt 注入 | core | 起動の一部 |
| state DB 更新 | core | 全 plugin が共通利用 |
| events.jsonl 記録 | core | 監査 log は core |
| dashboard 生成 | core | view layer |
| `on_pre_start` / `on_post_done` hook 機構 | core | plugin がここに乗る |
| worktree 作成 / 削除 | **plugin** | ライフサイクル境界の git |
| commit / push / PR 作成 | **driver (AI)** | 作業の git; core は関与しない |
| changelog 更新 | **driver (AI)** | 開発フローの一部; 作業の git と同様 |
| review-request | **driver (AI)** | topology と workflow に応じて role が判断 |

### 8.4 想定 workflow plugin

plugin の責務は worktree ライフサイクル (作成/削除) のみ。 PR / commit は plugin の担当ではない。

- `git_worktree`: worktree + branch 作成/削除 (現行実装)
- `bare`: worktree なし、 直接編集 (git 非依存)

plugin 機構そのもの (フラグ化など) の議論は Issue #37 に委ねる。

---

## 9. やらないこと (anti-scope)

明示的に **やらない / 含めない** ものを記録する。 範囲拡大の防衛線。

| 項目 | 理由 |
|---|---|
| coding 以外の汎用 orchestrator 化 | core は coding 想定、 他用途は plugin で扱う |
| 完全自律 (人間介入を最小化する方向) | mission に反する、 介入できることが本質 |
| cost-based routing | MVP 不要、 必要になったら後で |
| OpenAI / Gemini / local LLM 対応 | MVP は claude + codex のみ |
| global metadata の集中管理 | ホーム配下に何も置かない、 repo 内完結 |
| leader による driver 状態 polling | 構造 (events / dashboard / 通知) で代替 |
| no-code / GUI ベース | terminal native、 power user 向け |

---

## 10. claude-forge からの引き継ぎ判断

### 10.1 引き継ぐ概念

- worktree per task (plugin として)
- inbox.md / outbox.md による text-based async
- events.jsonl の append-only event sourcing
- tmux pane での human-fallback
- dashboard.md の view (ただし read-only に厳格化)
- preflight check

### 10.2 引き継がない / 棚卸し対象

下記は claude-forge で実装されたが、 agent-fleet では **再設計 or 廃止** を検討する:

| 機能 | 判断方針 |
|---|---|
| ghost dream 注入 | 廃止 (driver-prompt 肥大化の元凶) |
| breed (driver crossover + mutation) | 廃止 (実運用効果薄い) |
| quest XP / level / title | 廃止 (演出機能) |
| dna (driver profile) | 簡素化 (profile に降格) or 廃止 |
| personality | 効果検証後判断、 当面は廃止候補 |
| tamagotchi mode | 機能名に降格 (idle-watchdog 等)、 機能自体は再検討 |
| museum / vault / corpus / dna / memory の 5 重 knowledge layer | **1 つの knowledge store に統合**、 query は 1 本化 |
| swarm / wars / mesh / relay の 4 並列モード | topology の概念に吸収、 個別 mode は廃止 |
| lifecycle 6 features (heartbeat / liveness / tamagotchi / janitor / custodian / leader_context) | root cause 修正で半分消える想定、 残りは core hook 化 |
| state.yaml + dashboard.md + task.yaml の 3 重 SOT | task ごとに 1 file の YAML に整理、 dashboard は自動生成 view、 race は Python の flock + atomic rename で防ぐ (§5.3-5.5 で確定) |
| dynamic prompt injection (Ghost + Vault + DNA + lint-rules + personality) | 大幅削減、 base prompt を 300 行以下に |
| spawn-team.sh 等の bash monolith | Python に書き換え (§13 で確定) |

---

## 11. 議論の流れ (履歴)

将来の自分が振り返れるように要点だけ残す。

1. **2026-05-19**: claude-forge は機能膨らみすぎ + 技術的負債 (bash monolith / 多重 SOT / dynamic prompt 肥大化) で作り直し決定
2. **2026-05-19**: 命名 `forge` → `fleet` (multi-vendor 前提、 `agent-fleet` repo / `./fleet` CLI)
3. **2026-05-19**: 用語、 driver は維持 (agent だと leader と区別不能)
4. **2026-05-19**: mission を 「ユーザーは leader と話す + multi-vendor + team topology」 で確定
5. **2026-05-19**: 開発フローは plugin 化、 fleet core は orchestrator に徹する
6. **2026-05-19**: multi-project 同時起動可能、 state は repo 内に閉じる、 global metadata なし
7. **2026-05-19**: leader は会話 + spawn 専念、 driver の状態通知は構造的に user 直行
8. **2026-05-19**: 言語は Python 3.11+ のみ、 bash 廃止、 agent SDK 不採用、 git clone で即動く依存ゼロ路線
9. **2026-05-19**: state は file-based 継承 (forge 流)、 SQLite 不採用、 race は Python flock + atomic rename + 1 task 1 file で構造的に防ぐ
10. **2026-05-19**: dashboard.md は state 書き込みごとに自動 rebuild、 read-only view に厳格化
11. **2026-05-20**: dogfooding 開始。 CLI 整理 (fleet / fleet-agent 2 バイナリ化) の方針合意、 実装着手
12. **2026-05-20**: dogfooding で 6 つの穴が露呈 (completed の定義 / topology orchestration / driver の commit 責務 / role 構造化 / dialogue trace / inbox ack)。 §11 を再構成、 「足元固め」を機能追加より先行させる方針に転換
13. **2026-05-20**: preset の codex agent を一時的に全部 claude に置換。 codex は動作未検証で、 まず claude スタックで安定化に集中する判断。 codex CLI / parse 自体は残し、 explicit に指定可能
14. **2026-05-20**: dogfooding auto-pilot で §11 priority 4〜8 の方針提案ドラフトを作成 (role-structure / dialogue-trace / inbox-ack / prompt-structure / pr-workflow + archive-retention)。 `docs/proposals-summary.md` が入り口
15. **2026-05-20**: §11 priority 4 「role の構造化」 を **却下**。 driver-prompt は task.yaml から render される揮発的派生物であり SOT は task.yaml 一本、 「二重管理」 という proposal の問題設定が誤り。 role は task description と同格の本質的変数で prompt に出て当然 (forge 的 dynamic injection 肥大化とは別物)。 解くべき実害が無いと判断。 proposal の前提を鵜呑みにせず実害ベースで却下した例
16. **2026-05-20**: 設計課題の管理を **GitHub Issues に一本化**。 旧 §11 (未確定論点) と各 `*-proposal.md`、 `proposals-summary.md` を削除し、 1 論点 = 1 Issue として移行。 docs に課題を書くと毎回 commit が要る運用負担を解消する判断。 `design.md` は **確定済みの設計のみ** を保持する。 dialogue trace (旧 §11-5) はこの整理に伴い議論し、 却下 (記録は Issue 側)

---

## 13. 言語 / 依存 / インストール

### 13.1 言語

- **Python 3.11+ のみ** (bash 廃止、 agent SDK 不採用)
- 3.11 を要求する理由: `tomllib` stdlib (将来 TOML 採用余地)、 match 文、 改善された type hints
- bash は廃止: 「shell 1 行で済む」 と思える tmux / git 操作も実際は error 検知 + pipe で 1 行収まらず、 `subprocess.run([...])` の方が安全 (shell injection なし、 error 明確、 quoting 不要)
- forge の bash monolith 負債は 「bash で何でもやった」 から発生。 agent-fleet は最初から Python only で防ぐ

### 13.2 agent SDK

- **不採用** (Anthropic Agent SDK / LangGraph / 自前 framework)
- 理由:
  - tmux pane で claude / codex CLI を直接起動する forge 流が agent-fleet の独自性 (human fallback) と整合
  - SDK 越しだと pane 視認性が失われる
  - multi-vendor (claude / codex) を SDK 越しで扱うと vendor SDK の互換性問題が出る
- driver = tmux pane 内で起動された claude / codex CLI process、 通信は file + tmux pane

### 13.3 依存

- **標準 lib のみ** が基本路線
- 必要になったら **vendored dependency** (`vendor/` 配下に commit) で対応
- `pip install` / `venv` / `uv` / `poetry` 一切不要、 git clone で即動く

```
agent-fleet/
  fleet                  # CLI entrypoint (shebang #!/usr/bin/env python3)
  fleet/                 # Python package
  vendor/                # 必要時のみ、 PyYAML 等を pure Python で同梱
```

### 13.4 設定 format

- **YAML** (topology / project config)
- 依存ゼロ路線との両立のため、 PyYAML 6.0.2 の pure Python 部分のみを vendored で同梱
- YAML を選んだ理由: ユーザーが書く topology の表現力 (深いネスト + コメント + 配列) で TOML より優位

### 13.5 CLI entrypoint

- 2 entrypoint script (どちらも shebang `#!/usr/bin/env python3`):
  - `./fleet`       — 人間 (user) が打つ: `init` / `preflight` / `leader` / `attach` /
                      `status` / `log` / `topology` / `workflow`
  - `./fleet-agent` — システム (leader / driver agent) が自動で叩く:
                      `start` / `inbox` / `inbox-read` / `send-prompt` / `cleanup` /
                      `ask` / `event` / `done`
- 2 つは同じ `src/fleet/` module を import する shebang script。
  「人間が打つもの」 と 「システムが自動で叩くもの」 を物理的に分離する設計。
- pyproject.toml / setuptools entry_points は **MVP では使わない** (`pip install` 想定しない)
- 将来 distribute する段階で pyproject 化する余地は残す

### 13.6 開発インフラ (MVP)

| 項目 | 採否 |
|---|---|
| pytest | 採用 (test 書く) |
| ruff | 採用 (lint + format) |
| mypy / pyright | 任意 (信頼性に効くが MVP 必須ではない) |
| CI (GitHub Actions) | 採用 (forge 流継承) |

開発者だけが `pip install pytest ruff` する想定、 fleet 本体は依存ゼロを維持。
