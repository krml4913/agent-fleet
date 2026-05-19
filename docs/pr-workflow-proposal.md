# pr-based-workflow plugin 方針提案

> 作成: 2026-05-20 / driver: task-pr-workflow
> ステータス: 議論用ドラフト (実装前)
> 関連: `docs/design.md` §8 §11 priority 8、 `docs/backlog.md`、
>      `src/fleet/plugins/git_worktree.py` / `bare.py` / `__init__.py`、
>      `src/fleet/commands/spawn.py` / `done.py` / `cleanup.py` / `workflow.py`、
>      `docs/role-structure-proposal.md` (§11 priority 4)、
>      `docs/archive-retention-proposal.md` (backlog)

---

## 0. TL;DR

`pr-based-workflow` は **driver done 時に push + PR 作成まで自動で打つ** workflow
plugin。 §11 priority 8 「workflow plugin 具体」 の本丸であり、 backlog 項目
「driver done 時に自動 commit / push / `gh pr create` までやる workflow」 に正面から
答える。

推奨は **案 B (新規 `pr_based.py`、 worktree ロジックは git_worktree に委譲)**。

確定させたい論点を 3 つに絞ると:

1. **commit 責務 (§11 priority 3)** — commit は **driver が持つ**。 workflow は
   commit を奪わず、 done 時に **取りこぼし救済の catch-all commit** だけ打つ。
   つまり「driver が milestone ごとに semantic commit、 workflow は push + PR +
   未 commit の保険」 という分界にする。
2. **auto-merge** — **やらない**。 design.md §8.3 の「PR 作成 + auto-merge」 の
   うち auto-merge は memory の運用ルール (PR マージは leader 判断、 大きな変更は
   user 確認、 force push 禁止) と正面衝突する。 pr-based-workflow は
   **PR を作って止まる**。 merge は人間 / leader の判断に残す。
3. **hook API 拡張** — 新 hook は **足さない**。 既存 3 hook
   (`on_pre_spawn` / `on_post_done` / `on_cleanup`) で足りる。 ただし
   `done.py` が `on_post_done` に渡す `ctx` が薄すぎる (state_dir / task_id /
   task のみ) ので、 `project_root` と `task_dir` を **足すだけ** の最小 core 改修を
   する。

design.md §8.3 の文言「`pr-based-workflow`: PR 作成 + auto-merge」 は
**「PR 作成 (merge は別判断)」 に修正** すべき ── §4 で詳述。

---

## 1. 現状の整理

### 1.1 workflow plugin 機構

- plugin は `WORKFLOW_NAME` / `DESCRIPTION` と 3 つの optional hook
  (`on_pre_spawn` / `on_post_done` / `on_cleanup`) を持つ Python module。
- built-in は `src/fleet/plugins/*.py`、 custom は
  `<state_dir>/plugins/<name>.py` (custom が built-in を shadow)。
- project ごとに `project.yaml` の `workflow:` で 1 つだけ選ぶ
  (`fleet workflow set <name>`、 default `bare`)。
- 現状 built-in は `bare` (全 no-op) と `git_worktree` の 2 つ。

### 1.2 `git_worktree` の現状

| hook | 動作 |
|---|---|
| `on_pre_spawn` | `<state_dir>/worktrees/task-<id>` に branch `task/<id>` の worktree を作成。 `ctx["task_extra"]` に `worktree` / `branch` を載せ、 `ctx["cwd"]` を worktree に上書き |
| `on_post_done` | **no-op** (docstring に「safe-by-default。 teardown は cleanup CLI に寄せる」 と明記) |
| `on_cleanup` | worktree 削除 + branch `-D`。 失敗は warn のみ |

**つまり commit / push / PR は誰も打たない。** worktree と branch までは出来るが、
そこから先 (commit して push して PR) は **driver の自律判断か leader の代行** に
丸投げされている。

### 1.3 hook に渡る `ctx` の非対称

| 呼び出し元 | ctx の中身 |
|---|---|
| `spawn.py` → `on_pre_spawn` | `state_dir` `task_id` `topology` `role` `agent` `description` `title` `project_root` `dry_run` (リッチ) |
| `done.py` → `on_post_done` | `state_dir` `task_id` `task` (薄い。 **`project_root` が無い**) |
| `cleanup.py` → `on_cleanup` | `state_dir` `task_id` `task` `project_root` |

`on_post_done` だけ `project_root` を持たない。 PR 自動化を `on_post_done` に
載せるなら、 ここの ctx を埋める必要がある (§2.1)。 なお worktree path と branch 名は
`task` dict (= `task.yaml`、 `task_extra` 由来の `worktree` / `branch` フィールド)
から取れる ── 実例として本 task の `task.yaml` にも
`worktree:` / `branch:` が載っている。

### 1.4 dogfooding (2026-05-20) で観測した不安定挙動

- driver は **commit までやらないことが多い** ── leader が代行 commit していた。
- `claude:opus` driver は途中から **自走で commit + PR まで作る** ようになった。
- 結果、 「誰が commit するか」 が driver の気分と model に依存して **再現しない**。

これは §11 priority 3 (driver の commit / workflow 責務分界) そのもの。
pr-based-workflow の設計はこの分界の決着と不可分 ── §4.3 で扱う。

### 1.5 done.py の post_done は「失敗しても done は通る」

`done.py` は `on_post_done` を `try/except` で囲み、 例外は `warn:` 表示のみで
`done` 自体は成功扱いにする。 これは **PR 作成失敗が done をブロックしない** という
良い性質。 pr-based-workflow はこの設計に乗る ── PR 作成に失敗しても task は
completed になり、 失敗は event + 警告で残す (§3.4)。

---

## 2. 観点

### 2.1 hook API を拡張すべきか

**結論: 新 hook は足さない。 `on_post_done` の ctx を埋めるだけ。**

PR 自動化は「driver が done を叩いた後」 に走るので `on_post_done` に自然に乗る。
新しい hook (`on_pre_pr` 等) を足す動機は無い。 問題は ctx が薄いこと。

`pr_based` の `on_post_done` が必要とする情報:

| 必要なもの | 現状の取得可否 |
|---|---|
| worktree path | ○ `ctx["task"]["worktree"]` |
| branch 名 | ○ `ctx["task"]["branch"]` |
| project_root (PR の base repo) | ✗ **ctx に無い** |
| task title / description (PR title/body 素材) | △ `task["title"]` はある。 `description` は task.yaml に無く outbox / driver-prompt 側 |
| outbox.md / events (PR body 素材) | △ `task_dir` が分かれば読める。 ctx に `task_dir` が無い |

→ **最小 core 改修**: `done.py` の post_done ctx に `project_root` と `task_dir`
を足す (`cleanup.py` は既に `project_root` を渡しており、 整合性の面でも妥当)。
これだけで `pr_based` は自己完結できる。 hook 契約 (`__init__.py` docstring) の
「ctx は mutable dict、 plugin が自由に読む」 という緩い規約はそのまま。

### 2.2 pr-based-workflow が自動化する範囲

driver が `fleet-agent done` を叩いた瞬間、 `on_post_done` で:

1. **未 commit の変更を catch-all commit** (worktree が dirty なら `git add -A`
   + 定型 message で commit。 §3.3)
2. **push** (`git push -u origin task/<id>`。 force push はしない)
3. **`gh pr create`** (base = project の default branch、 head = `task/<id>`、
   title/body は §3 で生成)
4. PR URL を **`progress` event と outbox に記録**

**やらないこと**:

- **auto-merge しない** (§4.2 で明確に推奨)。
- **branch 削除しない** (cleanup の領分。 PR がある間は branch を残す)。
- **force push しない** (memory の運用ルール。 既存 PR への追従 push が
  non-fast-forward になったら fail させて warn、 人間に委ねる)。

### 2.3 commit message / PR body の生成

選択肢:

- (a) driver が commit を書く ── driver-prompt に「milestone ごとに commit しろ」。
- (b) workflow が template から自動生成。
- (c) driver の outbox.md / events から組み立てる。

→ **commit は (a) を主、 workflow の catch-all commit は (b)。 PR body は (c)。**
詳細は §3。

### 2.4 driver の commit 責務 (§11 priority 3) との関係

§4.3 で詳述。 結論だけ: **commit は driver が持つ。 pr-based-workflow は commit を
「奪わない」**。 workflow がやるのは push + PR、 そして driver が commit し忘れた
分の **保険 (catch-all)** のみ。 これで §11 priority 3 は
「driver = commit、 workflow = push/PR/保険」 と決着する。

### 2.5 topology orchestration (§11 priority 2) との関係

`pr-based-workflow` は **topology 非依存** に保つ。 PR を作るトリガは
「task の done」 であって 「topology の進行」 ではない。

問題は multi-role topology (`pair_review` 等):

- `pair_review` で implementer が done を叩いた時点で PR を作ると、 reviewer が
  まだ走っていない ── reviewer 承認後に PR を作りたい。
- だが §11 priority 1 (completed の定義: role 単位か task 単位か) と
  priority 2 (topology orchestration) が **未決**。 現状 done は task 単位で
  completed にしてしまう。

→ pr-based-workflow 側で topology を解釈しようとすると priority 1/2 を
先取りしてしまう。 **本 proposal では深入りしない**。 方針:

- **solo topology**: done = PR 作成、 で問題ない。 今回の対象。
- **multi-role topology**: 「どの role の done が PR を作るか」 の判断は
  将来の **topology runner (priority 2)** が持つ。 pr-based-workflow には
  将来 `ctx["pr_trigger"]` 的な真偽フラグを topology runner が立てる前提で、
  **今は「フラグが無ければ作る」 = solo 相当の挙動** にしておく。
- これは open question として §6 に残す。

### 2.6 既存 git_worktree との関係

`pr_based` が必要とする `on_pre_spawn` / `on_cleanup` は **git_worktree と完全に
同じ** (worktree + branch を作る / 消す)。 差分は `on_post_done` だけ。

→ 案 A / B / C の比較は §3 案比較に集約。 結論は **案 B (委譲)**。

### 2.7 登録・選択の仕組み

- 選択は既存どおり `fleet workflow set pr_based` で足りる
  (`workflow.py` の `_resolve` は built-in も custom も引ける)。
- **設定値** (PR の base branch、 draft PR か、 将来の auto-merge フラグ) を
  どこに置くか ── project ごとに変わるので `project.yaml` に
  `workflow_config:` block を新設するのを推奨 (§3.5)。
- custom plugin (`.fleet-state/plugins/<name>.py`) の spec は現状の緩い
  契約のままで良い。 `pr_based` を built-in に置けば「自作 spec」 の参照実装に
  もなる。

### 2.8 §8.3 の他 plugin への目配り

`monorepo` / `design-doc` / `research` は今回スコープ外。 ただし hook API を
今 confirm するので将来それらが乗るか確認:

- `monorepo`: `on_pre_spawn` で subdir を見て `ctx["cwd"]` を変える ──
  既存 ctx で足りる。
- `design-doc` / `research`: git 関与しない / read-only ── `bare` の派生で
  hook 不要。

→ **3 hook API のままで §8.3 の 6 種すべて表現できる。** 新 hook 不要の裏付け。

---

## 3. 案の比較

### 3.1 案 A: git_worktree を拡張 (flag で post_done を on/off)

`git_worktree.py` の `on_post_done` に commit/push/PR を実装し、
`project.yaml` のフラグ (例 `git_worktree_pr: true`) で有効化。

### 3.2 案 B: 新規 `pr_based.py`、 git_worktree に委譲 (推奨)

```python
# src/fleet/plugins/pr_based.py
from . import git_worktree

WORKFLOW_NAME = "pr_based"
DESCRIPTION = "git_worktree + done 時に push & gh pr create (merge はしない)"

on_pre_spawn = git_worktree.on_pre_spawn   # worktree + branch をそのまま流用
on_cleanup   = git_worktree.on_cleanup     # teardown も流用

def on_post_done(ctx):
    ...  # catch-all commit → push → gh pr create → event/outbox 記録
```

`on_pre_spawn` / `on_cleanup` は git_worktree の関数を **そのまま再エクスポート**
するだけ。 重複コードはゼロ。 差分は `on_post_done` の 1 関数。

### 3.3 案 C: 共有 helper module を切り出す折衷

`_worktree.py` (内部 helper) に worktree 作成/削除ロジックを抽出し、
`git_worktree.py` と `pr_based.py` の両方が import する。

### 3.4 比較表

| 観点 | 案 A (flag 拡張) | 案 B (新 plugin + 委譲) | 案 C (helper 抽出) |
|---|---|---|---|
| user の意図表明 | フラグで暗黙。 `fleet workflow show` で見えにくい | `fleet workflow set pr_based` で**明示的**。 名前が仕様 | 案 B と同等 |
| git_worktree の純度 | post_done が「no-op か PR か」 で**揺れる**。 docstring の "safe-by-default" が崩れる | git_worktree は no-op post_done のまま**不変** | 不変 |
| コード重複 | 無 (1 ファイル) | 無 (再エクスポートで委譲) | 無 (helper 共有) |
| ファイル数 | 1 | 2 | 3 (helper 追加) |
| 「PR 無し worktree」 を選ぶ手段 | フラグを false | `git_worktree` を選ぶ | `git_worktree` を選ぶ |
| 将来 3 つ目の worktree 系が来たとき | フラグが増えて破綻 | pr_based が git_worktree に依存。 やや窮屈 | helper 共有で素直に増やせる |
| core 改修 | post_done ctx 拡張 (共通) | post_done ctx 拡張 (共通) | post_done ctx 拡張 (共通) |
| 実装コスト | 小 | **小** | 中 |

### 3.5 設定 (`workflow_config`)

案を問わず、 PR 周りの project 固有設定は `project.yaml` に集約:

```yaml
workflow: pr_based
workflow_config:
  pr:
    base: main          # PR の base branch (省略時は repo の default branch)
    draft: false        # draft PR で作るか
    # auto_merge は意図的に置かない (§4.2)。 将来議論で足すなら ここ
```

`on_post_done` は `ctx["state_dir"]` から `project.yaml` を読んで
`workflow_config.pr` を参照する。

---

## 4. 推奨

### 4.1 案の推奨: 案 B (新規 `pr_based.py` + 委譲)

理由:

- **意図が名前に出る**。 `fleet workflow set pr_based` は「この project は done で
  PR を作る」 という宣言そのもの。 案 A のフラグは `project.yaml` を開かないと
  分からず、 `fleet workflow show` でも見えにくい。
- **git_worktree を汚さない**。 git_worktree の `on_post_done` no-op は
  docstring で「safe-by-default」 と明言された設計判断。 案 A はここに PR 副作用を
  混ぜ、 「PR を作りたくないだけの worktree 利用者」 にとって紛らわしくなる。
  案 B なら git_worktree = 「worktree のみ」、 pr_based = 「worktree + PR」 と
  selection の段階で分かれる。
- **委譲で重複ゼロ**。 案 C の helper 抽出は綺麗だが、 worktree 系がまだ 2 つの
  段階では 3 ファイルは過剰。 案 B は再エクスポート 2 行で済む。
- **§8.3 の plugin リストと 1:1**。 §8.3 は `git-worktree-workflow` と
  `pr-based-workflow` を**別エントリ**として列挙している。 案 B はその構造に素直。

将来 3 つ目の worktree 系 workflow (例 `monorepo` が worktree を使う) が来たら、
**その時に案 C へリファクタ** すればよい (§5)。 案 B → 案 C の移行は
内部 helper の抽出だけで、 plugin 名や `project.yaml` に影響しない。

### 4.2 auto-merge: やらない (明確な推奨)

**pr-based-workflow は PR を作って止まる。 merge はしない。**

design.md §8.3 は「`pr-based-workflow`: PR 作成 + **auto-merge**」 と書くが、
これは memory に記録された運用ルールと衝突する:

- **PR マージは leader 判断** (大きな変更は user 確認)。
- **force push 禁止**、 破壊操作は要確認。

driver が done を叩いた瞬間に無条件 merge すると:

- review 機会がゼロ。 dogfooding 中の 6 proposal はすべて PR review を経て
  main に入った ── auto-merge はこの実績ある運用を壊す。
- `pair_review` topology の reviewer 承認を**飛び越える** (§2.5)。
- 失敗 task でも done さえ叩けば merge される事故が起きうる。

したがって:

- **default = PR 作成のみ**。 `gh pr create` まで。
- `workflow_config.pr` に **`auto_merge` フィールドを今は置かない**
  (将来どうしても必要になったら §6 の open question として議論)。
- 仮に将来足すとしても **`gh pr merge --auto --squash`** 形式 (= branch
  protection / required review を**尊重した上で** 条件成立時に merge) に限り、
  即時無条件 merge は禁止。 これも opt-in。
- **design.md §8.3 の文言を修正** する: `pr-based-workflow`: PR 作成
  (merge は leader / user 判断、 auto-merge は既定で行わない)。

これは「自動化を諦める」 ではない ── driver の done から **PR が立つところまで**
を自動化すれば dogfooding で観測した「driver が commit しない / leader が代行」 の
痛点は解消する。 merge という不可逆操作だけ人間に残すのが妥当な線。

### 4.3 commit 責務 (§11 priority 3) の決着

**commit は driver が持つ。 pr-based-workflow は commit を奪わない。**

| 担い手 | 責務 |
|---|---|
| **driver** | 作業の **意味のある単位** で commit する (driver-prompt が指示)。 commit message は driver が書く ── 作業内容を一番知っているのは driver |
| **pr-based-workflow (`on_post_done`)** | (1) **catch-all**: done 時点で worktree が dirty なら、 取りこぼしを `git add -A` + 定型 message (`chore(task-<id>): uncommitted changes at done`) で commit し、 **`warn` event を emit** (driver が commit し忘れたシグナル)。 (2) push。 (3) `gh pr create` |

なぜ workflow に commit を**全部**持たせない (= driver は一切 commit しない) の
ではないか:

- workflow が done 時に一括 `git add -A` すると履歴が **1 個の巨大 commit** に
  潰れる。 semantic な履歴 (= review しやすさ、 revert 粒度) が死ぬ。
- commit message を workflow が書くと、 task title からの定型文しか作れない。
  driver が書いた方が情報量が高い。
- dogfooding で `claude:opus` driver は既に自走で commit している ── driver に
  commit 能力はある。 問題は**やったりやらなかったり**する一貫性の欠如。

→ catch-all を保険として置くことで、 **driver が commit しなくても PR は立つ**
(一貫性を workflow が担保)。 同時に **driver が commit すれば綺麗な履歴になる**
(driver-prompt の指示 + warn event で commit を促す)。 一貫性は構造で、 品質は
driver で取る ── これは prompt-structure-proposal の「rule は構造側で担保、
prompt 命令だけに頼らない」 と同じ思想。

§11 priority 3 への回答としてはこう書ける:

> **driver が commit、 workflow が push/PR。 workflow は driver の commit 漏れに
> 対する catch-all を持つが、 通常運転では driver が semantic commit する。**

なお `bare` / `git_worktree` を使う project では従来どおり commit は driver / leader
の手作業 ── この決着は **pr_based を選んだ project にのみ** 強制力を持つ。
priority 3 の「workflow が無い場合どうするか」 は別途 driver-prompt の規約で
扱う (本 proposal のスコープ外)。

### 4.4 PR title / body の生成

- **PR title**: `task["title"]` をそのまま (spawn 時に description 1 行目から
  生成済み)。
- **PR body**: workflow が `task_dir/outbox.md` (driver が milestone で書く
  報告) + `events.jsonl` の当該 task の `progress` / `done` event を読んで
  組み立てる。 雛形:

  ```
  task-<id> の成果。

  ## 概要
  <task.yaml の title>

  ## driver 報告 (outbox.md)
  <outbox.md の中身。 空なら "(報告なし)">

  ---
  🤖 fleet pr_based workflow / driver: task-<id>
  ```

- driver が outbox を書いていれば body は厚くなり、 書いていなくても title +
  定型で PR は立つ ── catch-all と同じ「無くても回る、 あれば良くなる」 設計。
- body の言語は task description の言語に追従しない (混在許容)。
  prompt-structure-proposal の言語方針と同じ。

### 4.5 まとめ (推奨セット)

1. **案 B** で `src/fleet/plugins/pr_based.py` を新設、 worktree 系 hook は
   git_worktree に委譲。
2. `on_post_done` で **catch-all commit → push → `gh pr create`**。
   **auto-merge はしない**。
3. core 最小改修: `done.py` の post_done ctx に `project_root` と `task_dir` を
   追加。
4. PR 設定は `project.yaml` の `workflow_config.pr` に置く (`auto_merge` は
   置かない)。
5. §11 priority 3 = 「driver commit / workflow push・PR・catch-all」 で決着。
6. design.md §8.3 の `pr-based-workflow` 行を「PR 作成 (merge は別判断)」 に修正。

---

## 5. 移行戦略

実装はしない前提だが、 合意後の段取り:

1. **core 改修 (先行)** — `done.py` の post_done ctx に `project_root` /
   `task_dir` を追加。 これは git_worktree にも cleanup にも無害な拡張で、
   単独 PR で先に入れられる。
2. **`pr_based.py` 追加** — 案 B。 git_worktree への委譲 + `on_post_done`。
3. **`project.yaml` schema** — `workflow_config` block の load を `state.py`
   に追加 (無ければ空 dict)。
4. **driver-prompt の規約** — 「milestone ごとに commit」 を driver-base.md か
   role 規約に明記 (§11 priority 3 の決着を反映)。 prompt-structure-proposal の
   行数 cap と衝突しないよう 1 行で。
5. **design.md 修正** — §8.3 の `pr-based-workflow` 文言、 §11 priority 8 に
   本 proposal へのリンク。
6. **dogfooding** — agent-fleet repo 自身で `fleet workflow set pr_based` に
   切り替えて検証 (この repo は既に git_worktree 運用、 PR ベースで回している
   ので題材として最適)。
7. **段階導入** — まず solo topology のみ対象。 multi-role topology での
   PR トリガは §11 priority 2 (topology orchestration) の決着を待つ。

前提条件 (実装時に確認):

- driver pane / 実行環境で `gh` CLI が認証済みであること。 未認証なら
  `on_post_done` は push まで済ませて PR 作成失敗を warn event に残す
  (done はブロックしない、 §1.5 の設計に乗る)。
- repo に `origin` remote があること。 無ければ push をスキップして warn。

---

## 6. open questions

1. **multi-role topology での PR トリガ** — `pair_review` で「reviewer 承認後に
   PR」 をどう表現するか。 §11 priority 1 (completed の定義) / priority 2
   (topology orchestration) の決着待ち。 本 proposal は solo 前提で、
   multi-role は将来 topology runner が `ctx` にトリガフラグを立てる想定。
2. **auto-merge を将来足すか** — 足すなら `gh pr merge --auto` 形式 (branch
   protection 尊重) に限定し opt-in。 現時点では置かない。 議論の余地は残す。
3. **PR base branch の決定** — `workflow_config.pr.base` 省略時に repo の
   default branch を自動検出するか、 `main` 決め打ちか。 monorepo workflow との
   兼ね合い。
4. **catch-all commit の挙動** — done 時 dirty を本当に拾うべきか、 それとも
   「dirty なら done を fail させて driver に commit させる」 方が躾として良いか。
   後者は §11 priority 1 (done の定義) と絡む。 本 proposal は「拾って warn」 を
   推奨したが、 ここは運用で再検討の余地あり。
5. **既存 PR への追従 push** — driver が done 後に追加作業して再度 done を
   叩いた場合、 同じ branch への push が non-fast-forward になりうる。
   force push 禁止方針なので fail + warn とするが、 「done は 1 task 1 回」 と
   する規約 (§11 priority 1) が決まれば自然に解消する。
6. **`gh` 不在環境での degrade** — push までで止めるか、 worktree から
   `git format-patch` を outbox に残すか。 §5 では前者を採ったが要確認。
7. **PR body の language** — 混在許容としたが、 project 設定で固定したい需要が
   出るかもしれない。

---

> この proposal は議論用ドラフト。 実装は user / leader の合意後、 §5 の段取りで
> 別 driver が進める前提。 既存 6 proposal (cli-split / role-structure /
> dialogue-trace / inbox-ack / prompt-structure / archive-retention) と
> 方向性は衝突しない ── 特に §11 priority 3 (commit 責務) はここで決着させ、
> priority 1 / 2 には踏み込まず open question に残した。
