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
| **3. team formation 定義** | project ごとに team 編成 (driver 1 人 / driver + reviewer / 多段) を YAML で選択 |

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

- **leader は軽い**: 会話と `fleet-agent start` のみ、 状態 polling や awaiting_orders 検知はしない
- **driver は直接 user に届く**: events.jsonl + 通知 + dashboard 経由、 leader を中継しない
- **user は driver と直接話せる**: tmux attach すれば pane に介入できる (これが forge から継承する独自性)
- **fleet 自体は開発フロー非依存**: worktree / PR / changelog 等は **plugin** で外付け

---

## 4. 責務分担

### 4.1 leader の責務

- ユーザーとのタスク依頼会話
- `fleet-agent start` でタスク開始 (どの agent vendor / model / formation で起動するか決める)
  - 長い task description は `fleet-agent start <id> --prompt-file PATH` でファイルから渡せる
- driver-prompt 注入は `start` 本体では直接 paste しない。driver pane と agent CLI を起動した後、
  標準 library だけの小さな detached prompt deliverer を切り離して `start` は即 return する。
  deliverer は tmux `capture-pane` を短い間隔で polling し、agent adapter ごとの ready 正規表現
  (claude / codex 各 1 本) に一致したら `driver-prompt.md` を指す pointer を tmux buffer 経由で
  paste し、paste settle 後に Enter を送って submit してから終了する (paste するのは prompt 全文ではなく pointer 1 行)。
  submit 後は pane の文字面を解釈せず、driver-prompt 冒頭の `fleet-agent inbox-read` 実行によって
  発火する task-scoped `inbox_seen` event を待って配達完了 ack とする。
  interactive boot gate (update / trust / login など) を検出した場合は `awaiting_orders` event と通知を一度出すが、
  polling は hard timeout まで続ける。人間が pane で gate を片付ければ次の ready 検出で自動 paste される。
  timeout では `error` event を出し task を `failed` にする。これは起動時の一回きりの handshake であり、
  heartbeat や継続監視には使わない。
- 必要に応じてユーザーへの高レベル進捗報告

leader は driver の状態を polling したり、 awaiting_orders を検知したりは **しない**。 これらは構造 (events / dashboard / 通知) が user に直接届ける。

codex driver は初回起動時に directory trust prompt を出すため、 `fleet-agent start` は codex の初段 driver を起動する前に git repo root が `~/.codex/config.toml` 上で trusted かを read-only に確認する。 未信頼なら worktree / task state / prompt を作らず中断し、 user にその repo で一度 `codex` を起動して承認してから再実行するよう誘導する。 `fleet preflight` も同じ trust 状態を optional check として表示する。

codex の起動時 update prompt は、Codex CLI の per-invocation config override `-c check_for_update_on_startup=false` を付けて発生源で抑止する。 fleet は `~/.codex/config.toml` には書き込まない。 `fleet preflight` は `codex --version` と npm registry の `@openai/codex` latest を比較し、さらに npm global install の package version と PATH 上の `codex` がずれていれば optional warning として表示する。

### 4.2 driver の責務

- 与えられたタスクの実装 (member subagent への委譲含む)
- 進捗を events.jsonl に追記
- ユーザー入力が必要になったら **`fleet-agent ask`** 専用 CLI を呼んで届ける (詳細は §7)
- 完了時に自己クリーンアップ

### 4.3 ユーザーの責務

- leader に対してタスクを依頼する (tmux 上)
- 通知 / dashboard で driver の状態を把握する
- 必要に応じて driver pane に直接 attach して対話する
- driver の質問に答える、 マージ判断をする (formation による)

---

## 5. project / state 配置

> **2026-05-21 決定**: §5.1〜§5.3 を全面改訂。旧設計 (repo 内 `.fleet-state/`、
> global metadata なし) は撤回。GitHub Issue #67 の確定設計に差し替え。

### 5.1 配置方針 (確定: 2026-05-21)

state は **`<agent-fleet クローン>/fleet-state/`** に中央化する。

- project repo の中には fleet 関連物を一切置かない。全 state は中央に集約。
- `fleet-state/` は gitignore 対象 (`fleet-state/` をエントリに追記)。
  ただし dotfolder (.付き) にはしない — 見えるフォルダとして扱う。
- ホーム配下 (`~/.fleet/` 等) には置かない。`$FLEET_HOME` 環境変数で上書き可能。
- fleet は `__file__` から clone root を解決 (`src/fleet/state.py` の 3 階層上)。
  `$FLEET_HOME` が set されていればそちらを優先する。
- **per-project leader を維持**。shared leader 方式は却下済み。

### 5.2 multi-project 同時起動 (確定: 2026-05-21)

グローバルレジストリ (`fleet-state/projects.yaml`) で全 project を一元管理する。

- project 識別子は **name** のみ。registry で一意強制。
- `fleet init` に `--name` を省略すると repo ディレクトリの basename を使用。
- tmux session 名 `fleet-<name>` はレジストリで name 一意性が保証されるため、
  同名衝突の footgun が構造的に消える。

```bash
fleet init /path/to/image-gallery        # name は basename "image-gallery"
fleet init --name api /path/to/api-repo  # 明示指定
fleet status --all                       # 全 project 横断サマリ
# tmux ls
#   fleet-image-gallery: 1 windows
#   fleet-api: 1 windows
fleet leader --project image-gallery     # 特定 project の leader へ
```

不要になった project は `fleet rm <name>` で registry から削除し state ツリーも一括削除する。

### 5.3 state 構造 (確定: 2026-05-21)

```
<agent-fleet クローン>/
  src/  fleet  fleet-agent  ...        ← コード (git 管理)
  fleet-state/                         ← gitignore 対象。dotfolder にしない
    projects.yaml                      ← グローバルレジストリ (name → repo map)
    projects.yaml.lock                 ← flock 用
    projects/
      <name>/                          ← 1 project の state_dir
        project.yaml     # name / repo / created_at / version / workflow
        events.jsonl     # append-only audit log
        dashboard.md     # read-only view (自動生成、直接編集禁止)
        notify.yaml      # 通知設定
        memory/
          MEMORY.md      # 知見インデックス (各 driver が都度追記)
          GUIDE.md       # fleet memory 規律
          *.md           # 個別 memory ファイル
        tasks/
          task-<id>/
            task.yaml         # task 状態
            inbox.md          # leader → driver の指示
            outbox.md         # driver → leader の報告
            driver-prompt.md  # spawn 時に展開済みの prompt (agent が自分で読む)
        worktrees/
          task-<id>/     # git_worktree plugin 使用時のみ
```

#### project 解決ロジック

`resolve_state_dir(cwd, *, project_name=None)` が以下の優先順で解決する:

1. **`project_name` 明示** (`--project <name>`): registry の name で直接解決。
2. **cwd が fleet-state ツリー内** (`fleet-state/projects/<name>/…`):
   その `<name>` に解決。worktree / task dir から fleet コマンドを叩いた場合に効く。
3. **cwd が登録 repo パス配下**: registry の全 `repo` のうち、cwd の祖先で
   **パス長が最長のもの**を選ぶ (monorepo のネスト登録を自然に捌く)。

`FLEET_STATE_DIR` 環境変数 (driver pane に必ず注入) は `resolve_state_dir` より
さらに優先される (driver 向け `task_context.resolve` のみ)。

### 5.4 race 対策

forge では `yq -i` / `sed -i` の partial update + lock 不徹底が race の温床だった。 agent-fleet では Python の構造で堅牢化する:

| 対策 | 内容 |
|---|---|
| **flock 排他取得** | `fcntl.flock(fd, LOCK_EX)` で write lock 取得 |
| **atomic rename** | `tmp` ファイルに全文書き出し → `os.replace(tmp, final)` で atomic 置換 |
| **partial update 禁止** | 既存ファイルへの `sed` / `>>` 等は禁止、 必ず全文 rewrite |
| **1 task = 1 file** | cross-task の同時更新が原理的に起きない構造 |
| **events.jsonl** | append-only、 POSIX `O_APPEND` で atomic、 lock 不要 |
| **registry RMW** | `atomic_update(projects.yaml, mutate)` で flock + read + write を1区間に収める |

書き込みは必ず `locking.atomic_write` / `locking.atomic_update` 経由。
registry の read-modify-write は `atomic_update` がフルロック区間でカバーする。

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

## 6. team formation

### 6.1 設計方針

- **YAML** で定義 (シンプルに始める)
- **formation template** (fleet 同梱の雛形) + **formation** (project が持つもの) の二段構成
- **count なし** (必要に応じて leader が動的に並列起動を判断する)
- **user_approval** を表現できる (人間の承認ポイントを stage 属性で明示)

### 6.2 formation の例

```yaml
# Formation A: solo driver (一人で PR まで完結)
name: solo
stages:
  - role: driver
    agent: claude:sonnet

# Formation B: pair review (実装者 + AI 査読 + user 承認)
name: pair_review
stages:
  - role: implementer
    agent: codex:gpt-5.5
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required

# Formation C: 多段 (設計 → 実装 + AI 査読 + user 承認)
name: multi_stage
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

各 stage 内の実行順序:
```
implement → peer_review (AI 査読ループ, max 3 回) → user_approval → stage 完了
```

peer_review 付き stage では、実装者と reviewer の agent CLI は stage の間は起動したまま保持する。
初回の reviewer だけ必要に応じて新しい tmux window として起動し、その後の iteration handoff は
既存 pane に inbox 通知を `send-keys` して起こす。これにより agent context を保持し、agent CLI の
boot gate は stage 内の初回起動時だけ通る。multi_stage ではこの長命化は stage-local であり、
stage を跨ぐ通常の advance は次 stage の driver を新しく launch する。

### 6.3 state machine (orchestrator)

- `fleet-agent done --result approved|changes-requested` が呼ばれると `orchestrator.advance()` が次を判断する
- approved: driver / reviewer の作業完了を受け、peer_review / user_approval の次状態を判断する。peer_review handoff は live pane があれば inbox 通知で起こし、なければその role を初回 launch する。user_approval が無ければ現 stage を done にして次 stage を launch (次がなければ task completed)
- changes-requested: peer_review の phase に応じてループを回す。実装者に戻す場合は既存 implementer pane に inbox 通知を流し込み、relaunch しない
- peer_review 上限 (3 回) 超過時は task.status を `awaiting_orders` に変更してユーザーへ通知
- `user_approval.status == asked` のゲートは leader が user の判断を受けて `fleet-agent approve <id>` / `fleet-agent reject <id>` で中継する
  - approve: `user_approval.status` を `approved` にし、stage 完了処理へ進む
  - reject: `user_approval.status` を `pending` に戻し、該当 stage を implementation に戻す。peer_review stage では既存 implementer pane を起こす
- 後方互換として `done --result approved|changes-requested` による asked gate の承認/差し戻し中継は当面残すが、新しい導線では使わない

### 6.4 formation YAML schema

formation YAML の必須・任意フィールドを以下に明記する。形式言語 (JSON Schema 等) は使わない (§1.4 原則 1)。

**トップレベル**

| フィールド | 必須 | 説明 |
|---|---|---|
| `name` | 必須 | formation の識別名。ファイル名 (stem) と一致すること |
| `description` | 任意 | 人間向けの説明文 |
| `stages` | 必須 | stage オブジェクトのリスト。1 件以上必要 |

**`stages[]` (各 stage)**

| フィールド | 必須 | 説明 |
|---|---|---|
| `role` | 必須 | driver が担う役割名 (例: `driver`, `implementer`, `designer`) |
| `agent` | 任意 | 使用する agent (例: `claude:sonnet`)。省略時は `--agent` 引数の値が使われる |
| `peer_review` | 任意 | AI 査読を挟む場合に指定。サブフィールド: `role` (査読者の役割、必須)、`agent` (査読者の agent、任意)。`agent` 省略時は同 stage の `agent` → `claude:sonnet` の順でフォールバックする |
| `user_approval` | 任意 | 人間の承認ポイント。`"required"` / `"optional"` の文字列、またはオブジェクト形式 |

`validate()` はトップレベルの `name` / `stages` 必須チェックと、各 stage の `role` 必須チェックを行う。
それ以上の形式検証 (`peer_review` の構造等) は orchestrator 側に委ねる。

### 6.5 formation template / formation

- **formation template** (`src/fleet/templates/`): fleet 同梱の雛形。`solo` / `pair_review` / `multi_stage` の 3 つ。直実行禁止。
- **formation** (`<state>/formations/<name>.yaml`): project が持つ実体。runtime はこれだけを解決対象にする。
- template は「こう書けば動く」という推奨デフォルト値を示す雛形。`fleet init --formation <name>` や `fleet formation init --from <name>` でコピーすると project の formation になり、以降は独立する (template との追従なし)。
- コピー後は `agent:` 既定値の変更、`user_approval` の追加・削除など自由に変更してよい。

**`fleet-agent start --formation` の解決ルール:**

| 状況 | 結果 |
|---|---|
| `--formation <name>` 明示 | `<state>/formations/<name>.yaml` をロード。不在はエラー (template fallback なし) |
| 省略 + formations/ に 1 件 | その 1 件を自動採用 |
| 省略 + formations/ が空 | `<state>/leader-session.json` の agent で 1-stage solo を即興合成 (`_leader_solo`) |
| 省略 + formations/ に 2 件以上 | 曖昧エラー (`--formation <name>` を渡すよう案内) |

---

## 7. driver 通信プロトコル

### 7.1 driver からの user input 依頼

driver がユーザーに質問したい / 判断を仰ぎたい場合は **専用 CLI を呼ぶ**:

```bash
fleet-agent ask "<question>"
```

これが呼ばれると:
1. `events.jsonl` に `awaiting_orders` event を emit
2. `dashboard.md` を再生成 (awaiting_orders マーク反映)
3. 通知発火 (macOS / slack)

driver が pane に質問を書いただけでは **どこにも届かない**。 rule を守らないと user に届かない構造的圧力で、 prompt 命令だけより堅く担保する。

### 7.2 driver の固まり検知

- 一定時間 driver pane に活動なし → heartbeat 機構が detect
- fallback として 「awaiting orders か?」 を driver に問い合わせる仕組みを別途置く (詳細は別途設計)

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

- driver-prompt は `docs/prompts/driver-base.md` の fleet 共通プロトコルに、workflow plugin が任意で提供する `DRIVER_PROMPT_FRAGMENT`、`docs/prompts/roles/<role>.md` の role 断片、task description を合成して生成する。 `git_worktree` は作業完了後の `commit → push → gh pr create → fleet-agent done` 手順を断片として持ち、`bare` は持たない。
- `git_worktree` の `on_pre_start` は、worktree を切る現在 branch が既知の upstream より遅れている場合だけ警告する。`start` の latency と offline 起動を守るため fetch は行わず、警告して続行する。`bare` workflow には適用しない。
- tmux pane へ paste するのは prompt 全文ではなく、prompt ファイルを指す短い pointer のみ。 `start` / `send-prompt` / `leader` は共通ヘルパで `.driver-prompt.md.paste-pointer` または `.leader-prompt.md.paste-pointer` を生成し、`Read the prompt file at this path before doing anything else, then follow its instructions: <絶対パス>` を1行で paste する。 prompt 本体は agent が起動直後に `Read` / file-reading tool で読む。
- driver-prompt の pointer は `fleet-agent start` / `fleet-agent send-prompt` が直接 paste せず、detached prompt deliverer が agent CLI の ready marker を見てから注入し、submit 確認まで行う (§4.1 参照)。 deliverer が paste するのも prompt 全文ではなく pointer 1 行。
- leader-prompt は `docs/prompts/leader-base.md` の fleet 汎用 leader プロトコル (不変・project 非依存) を土台に、project 名・state dir・memory 入口を `---` footer として合成して生成する。 `fleet leader` が pane 起動時に pointer を注入する (driver-prompt の paste 機構と同様)。 project 固有・揮発なコンテキスト (現在の方針 / handoff) は leader-base.md には入れず、project memory と `leader-handoff.md` 側が持つ。 leader は fleet memory の主要な維持者でもある。
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
| review-request | **driver (AI)** | formation と workflow に応じて role が判断 |

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
| swarm / wars / mesh / relay の 4 並列モード | formation の概念に吸収、 個別 mode は廃止 |
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
4. **2026-05-19**: mission を 「ユーザーは leader と話す + multi-vendor + team formation」 で確定
5. **2026-05-19**: 開発フローは plugin 化、 fleet core は orchestrator に徹する
6. **2026-05-19**: multi-project 同時起動可能、 state は repo 内に閉じる、 global metadata なし
7. **2026-05-19**: leader は会話 + spawn 専念、 driver の状態通知は構造的に user 直行
8. **2026-05-19**: 言語は Python 3.11+ のみ、 bash 廃止、 agent SDK 不採用、 git clone で即動く依存ゼロ路線
9. **2026-05-19**: state は file-based 継承 (forge 流)、 SQLite 不採用、 race は Python flock + atomic rename + 1 task 1 file で構造的に防ぐ
10. **2026-05-19**: dashboard.md は state 書き込みごとに自動 rebuild、 read-only view に厳格化
11. **2026-05-20**: dogfooding 開始。 CLI 整理 (fleet / fleet-agent 2 バイナリ化) の方針合意、 実装着手
12. **2026-05-20**: dogfooding で 6 つの穴が露呈 (completed の定義 / formation orchestration / driver の commit 責務 / role 構造化 / dialogue trace / inbox ack)。 §11 を再構成、 「足元固め」を機能追加より先行させる方針に転換
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

- **YAML** (formation / project config)
- 依存ゼロ路線との両立のため、 PyYAML 6.0.2 の pure Python 部分のみを vendored で同梱
- YAML を選んだ理由: ユーザーが書く formation の表現力 (深いネスト + コメント + 配列) で TOML より優位

### 13.5 CLI entrypoint

- 2 entrypoint script (どちらも shebang `#!/usr/bin/env python3`):
  - `./fleet`       — 人間 (user) が打つ: `init` / `preflight` / `leader` / `attach` /
                      `status` / `log` / `formation` / `workflow`
  - `./fleet-agent` — システム (leader / driver agent) が自動で叩く:
                      `start` / `inbox` / `inbox-read` / `send-prompt` / `cleanup` /
                      `ask` / `event` / `approve` / `reject` / `done`
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
