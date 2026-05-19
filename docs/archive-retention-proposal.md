# task archive 後の成果物保存 方針提案

> 作成: 2026-05-20 / driver: task-archive-retention
> ステータス: 議論用ドラフト (実装前)
> 関連: `docs/design.md` §5.3 §8.3 §11、 `docs/backlog.md` (本項目)、
>      `docs/dialogue-trace-proposal.md`、 `docs/inbox-ack-proposal.md`、
>      `docs/role-structure-proposal.md`、 `docs/prompt-structure-proposal.md`、
>      `src/fleet/commands/cleanup.py`、 `src/fleet/plugins/git_worktree.py`、
>      `src/fleet/state.py`、 `.fleet-state/tasks/_archive/`

---

## 0. TL;DR

`fleet-agent cleanup --archive` は task の **state ディレクトリ** (`task.yaml` / `inbox.md` / `outbox.md` / `driver-prompt.md` / `questions.md`) を `tasks/_archive/task-<id>/` に退避するが、 並行して **worktree と branch は完全に消える**。

| 残るもの | 消えるもの |
|---|---|
| `task.yaml` 等の state file | `branch task/<id>` (= `git branch -D`) |
| `inbox.md` / `outbox.md` の append-only テキスト | worktree (= `git worktree remove --force`) |
| `events.jsonl` (project 単位、 archive 対象外) | branch tip commit (squash merge 前の commit 列) |
| | uncommitted state |
| | PR / review コメント (GitHub 側にはあるが対応関係不明) |

dogfooding で実感した穴:

- driver が commit せず done した task を leader が代行 commit するパターン (§11 priority 3) で、 cleanup 後 「driver が何を作りかけたか」 を辿る術が無くなる
- 自走で PR まで作る driver の review 痕跡が cleanup 一回で消える
- topology orchestration (§11 priority 2) が動き出した時、 「あの reviewer がなぜ approve したか」 の git log 復元が不能になる

推奨: **案 B (detached ref `refs/fleet-archive/task-<id>` で branch tip だけ git 側に残す)**。

- 軽量: `git update-ref` 1 発、 容量はほぼゼロ (commit object は既に store にある)
- git の世界に閉じる: tarball / 独自フォーマット無し、 `git log refs/fleet-archive/task-<id>` で直接読める
- `git branch -a` を汚さない: 通常の branch listing には出ず、 必要な時だけ `git for-each-ref refs/fleet-archive/` で列挙
- `tasks/_archive/task-<id>/task.yaml` に `archive_ref:` 行を一行足して **state ↔ git の対応** を貼る
- uncommitted state の保全は **しない** (= driver/workflow の commit 責務に委ねる、 §11 priority 3)

**phase 1 で入れる**: detached ref + `task.yaml` への `archive_ref:` / `archive_tip:` 記録。 cleanup.py の archive ロジックを 1 ブロック差し替えるだけ。

**phase 2 (将来)**: dialogue-trace-proposal phase 2 の `messages/` ストリームと inbox-ack-proposal の ack watermark が確定したら、 それらも archive 対象に同居させる (現状 inbox.md / outbox.md / questions.md は既に同居しているので拡張は素直)。

**やらないこと**:

- worktree tarball (案 C)。 容量と整合性 (lock file / `.venv` / build artifact 混入) に対して、 「uncommitted state を archive で救う」 利得が薄い。 そもそも driver が commit せず done する事自体が §11 priority 3 で修正対象、 ここで救済路を作ると priority 3 の修正を delay させる
- 過去 archive (既に branch を消した task) の救済。 諦め、 phase 1 以降の cleanup から適用
- レビュー履歴 (GitHub PR コメント) の取り込み。 GitHub 側に既に残っており、 archive_ref から PR 番号を辿れる程度で十分

役割の値域、 retention policy、 参照 CLI、 dialogue-trace / inbox-ack との分担は §2-§7 で詰める。

本書は方針合意までで止まる。 実装は次フェーズ。

---

## 1. 現状の整理

### 1.1 cleanup --archive の現在の動作

`src/fleet/commands/cleanup.py` の `run()` を読むと、 archive で行われる事は以下:

```
1. terminal status (completed / failed / cancelled) でなければ refuse (--force で override)
2. workflow plugin の on_cleanup hook を呼ぶ
     git_worktree の場合:
       - git worktree remove --force <worktree>
       - git branch -D task/<id>        ← 失敗は warn 止まり
3. tmux window / paste buffer を drop
4. --archive が立っていれば:
     archive_root = state_dir/tasks/_archive
     archive_root.mkdir(exist_ok=True)
     os.rename(state_dir/tasks/task-<id>, archive_root/task-<id>)
5. events.jsonl に cleanup event (archived=True/False)
6. dashboard rebuild + 通知
```

(`src/fleet/commands/cleanup.py:54-130` を見て確認、 `src/fleet/plugins/git_worktree.py:60-100` で worktree / branch 削除)

### 1.2 archive 後に残るもの / 消えるもの (詳細)

実際に残っている archive を見てみる (例: `tasks/_archive/task-prompt-structure/`):

```
$ ls -la .fleet-state/tasks/_archive/task-prompt-structure/
  driver-prompt.md   6460 bytes  spawn 時 snapshot
  inbox.md              0 bytes  leader からの inbox は無し
  outbox.md             0 bytes  driver は outbox 書かず
  questions.md      (無い)       ask 使わず
  task.yaml          289 bytes   id / title / status / workflow / branch
  task.yaml.lock        0 bytes  flock 跡
```

state side で残るのは **これだけ**。 一方:

| 失われる情報 | 取り戻す術 |
|---|---|
| `branch task/<id>` の tip | `git branch -D` 後、 reflog / `git fsck --lost-found` で 30 日 (gc 既定) は救えるが恒久ではない |
| feature commit history (PR squash 前の作業 commit 列) | 同上 (reflog 期限切れで物理消失) |
| worktree の uncommitted state | 完全消失 (`git worktree remove --force` は untracked も消す) |
| PR / review コメント | GitHub 側に残るが task との対応関係は task.yaml に PR 番号が書かれていないので人力突合 |
| driver-prompt.md の改訂履歴 | spawn 時 snapshot 1 個のみ。 driver-prompt は spawn 後 re-render しない方針 (prompt-structure-proposal) なので **そもそも改訂は起きない**、 archive で救う必要は無い |

### 1.3 「task.yaml に branch が記録されている」 のに復元できない理由

`task.yaml` には spawn 時の `branch: task/<id>` 行があるが、 cleanup で `git branch -D` した時点で **その名前が指す ref は消える**。 残るのは:

- git の object database に **commit object が orphan として** 残る (gc されるまで)
- reflog (これも有限期間)

要は 「branch 名は task.yaml に残るが、 git 側に対応する ref が消えるので解決不能」 という状態。 これが本提案の解こうとする穴。

### 1.4 PR merge 経路との関係

PR が main にマージされている task は、 main の git log を辿れば該当 commit 群に到達できる。 ただし:

- **squash merge** が default なので、 driver が刻んだ作業 commit 列は 1 個に潰れる。 細かい思考プロセスは消える
- **未マージで done** した task (例: 提案だけ書いて PR を作らなかった、 もしくは PR が close された) は main から辿れない
- 「どの commit が task-X 由来か」 を引く index が無い (commit message の `(#PR)` 表記から逆引きはできるが PR とは別系統)

### 1.5 既に確定 / 検討中の方針との照合

| 方針 | 整合性 |
|---|---|
| §5.3 state 構造 (`tasks/task-<id>/`) | archive 先 `tasks/_archive/task-<id>/` は既存、 拡張対象として整合 |
| §10.2 dynamic prompt injection 廃止 | 直接の関係は薄い |
| §8.3 plugin 想定 (`git-worktree-workflow` / `pr-based-workflow`) | archive 保全は **git_worktree 側に閉じる**、 plugin の責務分担を強化する方向 |
| §11 priority 3 driver / workflow 責務分界 | 「driver が commit するか workflow がするか」 の答えが出る前に **archive は worktree commit を前提とする** ことを明示し、 priority 3 の修正を促す形にする |
| dialogue-trace-proposal | dialogue は **driver ↔ user の対話** 領域、 archive-retention は **driver の git work 出力** 領域、 機能重複しない |
| inbox-ack-proposal | inbox.md (phase 2 で messages/) は archive に既に含まれる、 本書は 「inbox 構造化の archive 影響」 を考慮するだけで OK |
| role-structure-proposal | role は task.yaml に書かれており archive に既に含まれる、 影響なし |
| prompt-structure-proposal | driver-prompt.md は spawn snapshot で archive に既に含まれる、 影響なし |

---

## 2. 観点

タスク依頼で挙げられた 7 つの論点を順に整理する。

### 2.1 何を保存するか

優先順位:

| 優先 | 対象 | 保存価値 | 既存救済 |
|---|---|---|---|
| 高 | **branch tip (commit hash)** | driver が触った最新 work tree の同定子。 これさえあれば `git show` / `git log` / 後続 cherry-pick が全部可能 | なし、 本提案で救う |
| 高 | **branch tip 含む commit history** | tip があれば自動付随 (commit object は parent を辿れる)。 ただし ref で reachable にする必要あり | 同上 |
| 中 | inbox.md / outbox.md / questions.md / task.yaml | 「対話と状態」 の archive | 既に `tasks/_archive/` に退避済み (現状維持) |
| 中 | driver-prompt.md (spawn snapshot) | driver の出発点を後で再現できる | 既に退避済み |
| 低 | worktree の uncommitted state (tarball) | 「driver が commit せず done した」 場合の救済。 ただし §11 priority 3 で根本対策される問題、 ここで救うと priority 3 を delay させる | 救わない (案 C の理由) |
| 低 | driver-prompt.md の改訂履歴 | そもそも spawn 後 re-render しない (prompt-structure-proposal 確定) ので **改訂は起きない**、 救う必要なし | — |
| 低 | dialogue trace (driver ↔ user 対話) | dialogue-trace-proposal の領域。 同 proposal が file SOT (events.jsonl + dialogue 系 file) を整備すれば archive に同居できる | dialogue-trace-proposal に委譲 |
| 低 | PR / review コメント | GitHub 側に既に残る。 task.yaml に **PR 番号** を 1 行記録すれば人力突合のコストは下がる | task.yaml に `pr:` 行を足すだけで十分 |

→ **第一目標は branch tip 復元**。 副次として PR 番号を task.yaml に記録する小改善も併走。

### 2.2 どこに保存するか

候補 (主に branch tip / commit history の保存先):

| 候補 | 保存先 | git world との関係 | listing 汚染 |
|---|---|---|---|
| α | task.yaml に `archive_tip: <hash>` 行追記、 git ref は作らない | ref を作らないので gc で commit object が消える可能性 | なし |
| β | `refs/fleet-archive/task-<id>` に detached ref (`git update-ref`) | 通常 branch では無いので `git branch -a` に出ない、 `git for-each-ref refs/fleet-archive/` で列挙、 gc から protect される | なし (refs/heads/ に出ない) |
| γ | branch 名を `archive/task-<id>` に rename (`git branch -m`) | branch 扱いなので `git branch -a` に並ぶ | あり (時間と共に膨らむ) |
| δ | worktree tarball を `.fleet-state/tasks/_archive/task-<id>/worktree.tar.gz` に | git の外 | なし |
| ε | git bundle (`git bundle create`) を `.fleet-state/tasks/_archive/task-<id>/work.bundle` に | git の外 (ただし `git fetch` 経由で復活可能) | なし |

判断:

- **β (detached ref)** が圧倒的に素直。 git にとっての 「commit を gc から protect する」 公式手段で、 listing も汚さない
- α は ref が無いので gc race を負う、 採用しない
- γ は branch listing 汚染が 「task が増える度に重くなる」 という時限負債、 採用しない
- δ は容量問題と整合性問題 (build artifact / lock file / venv 混入) で重い、 案 C のメイン論点
- ε は β より物々しく利点が少ない (object 重複保持)、 不採用

**phase 1 では β + α (両方)** を採る:

- detached ref で実体を保護
- task.yaml にも `archive_tip:` / `archive_ref:` を文字列で書く (state file だけで完結する側面と、 git ref の両方を持つ)
- 二重化に見えるが、 ref は git の都合 (gc 防御 + `git log <ref>` で reachable)、 task.yaml の文字列は **fleet が grep する都合** (`fleet-agent show-archive <id>` 等の実装が ref 直読みより楽)

### 2.3 retention policy

| 候補 | 内容 | 評価 |
|---|---|---|
| ρ-1 | 全部永久保存 (default) | 提案。 git ref は容量ほぼゼロ (commit object は元から store にあり、 ref は 41 byte の symlink-like file)、 state file は数 KB |
| ρ-2 | N days で auto-expire | 早すぎる最適化。 容量問題が出てから入れれば良い |
| ρ-3 | `fleet-agent forget <id>` で明示削除 | 提案。 個別 purge の path を確保 (mis-archive した時用) |
| ρ-4 | 「最近の N task」 だけ keep | LRU の policy 化が複雑、 今は不要 |

→ **default 永久 + `fleet-agent forget <id>` で個別 purge**。 容量が問題化したら ρ-2 を後付け。

`forget` の挙動:

- `git update-ref -d refs/fleet-archive/task-<id>` (ref 削除、 commit は gc で順次消える)
- `rm -rf tasks/_archive/task-<id>/` (state file 群削除)
- `events.jsonl` に `archive_forget` event 追記

### 2.4 archive されたものの参照経路

候補:

| 候補 | 経路 | 評価 |
|---|---|---|
| ν-1 | `.fleet-state/tasks/_archive/` を直 ls で見る前提 + `git log refs/fleet-archive/task-<id>` を git 経由で | 最小実装、 学習コスト低 |
| ν-2 | `fleet-agent show-archive <id>` CLI を追加 | task.yaml + ref + recent commit log をまとめ表示 |
| ν-3 | `fleet list --archive` で listing | 既存 `fleet list` の延長 |

判断: **phase 1 は ν-1 (直 ls + git log) だけで十分**。 ν-2 / ν-3 は使い始めて煩雑になったら追加。 「最小機能でまず入れて、 UI は後付け」 路線。

`fleet status` などへの archive 件数表示は不要 (dashboard を汚す)。

### 2.5 既存 archive ディレクトリとの関係

既存:

```
.fleet-state/tasks/_archive/task-<id>/
  task.yaml
  inbox.md
  outbox.md
  driver-prompt.md
  questions.md (あれば)
```

phase 1 で増やす:

- `task.yaml` に `archive_tip:` / `archive_ref:` / `archived_at:` の 3 行を追記
- git side で `refs/fleet-archive/task-<id>` ref を追加

**新ディレクトリは作らない**。 「`grave/` 等の別系統」 は分断のコスト (CLI が 2 か所を見る必要) > 利得。

### 2.6 後方互換

既に `cleanup --archive` を叩いた task の worktree は **失われている** (reflog 期限内なら救えるが恒久ではない)。

判断: **過去分は諦め、 phase 1 以降の cleanup から保存対象**。 backlog にもその想定で書かれている。 過去分の救済を組むコストは現実の効用より高い (大半は再現性のないやり捨て task)。

`tasks/_archive/task-*/task.yaml` に `archive_tip:` が無い古い archive は、 「ref も無いので git 側から復元不可」 を許容する。

### 2.7 leader / driver の責務

cleanup が archive を作る現状の責務分担:

- **leader** (もしくは user) が `fleet-agent cleanup --archive <id>` を叩く
- cleanup.py が workflow plugin の `on_cleanup` を呼ぶ
- `git_worktree.on_cleanup()` が worktree / branch を消す

phase 1 の改修案:

| 何を | どこに | 理由 |
|---|---|---|
| `archive_tip` の解決 (`git rev-parse refs/heads/task/<id>`) | **git_worktree plugin の `on_cleanup` 内、 branch 削除の直前** | git world の知識は plugin の責務 |
| `archive_tip` の ctx 返却 | `ctx["archive_tip"] = "<hash>"` で plugin から cleanup.py に渡す | core ↔ plugin の責務分離 |
| `refs/fleet-archive/<id>` の作成 | **git_worktree plugin 内** | git world の操作 |
| `task.yaml` への `archive_tip` / `archive_ref` 書き戻し | **cleanup.py の archive 処理内**、 `os.rename` 前に task.yaml を update | state file の責務は core |
| `archived_at` timestamp | cleanup.py 内 | 同上 |

これは §11 priority 3 (driver / workflow 責務分界) と整合的:

- driver は **commit する責務** (priority 3 で明示化される予定)
- workflow plugin は **git world の操作** (本提案で git_worktree に閉じることを示す)
- core (cleanup.py) は **state file の責務**

→ 本提案は priority 3 の方向性を補強する。 矛盾しない。

---

## 3. 案の比較

### 3.1 案 A: archive_tip メタデータのみ (軽量)

#### 概要

- `task.yaml` に `archive_tip: <commit-hash>` / `archived_at: <iso8601>` の 2 行を追加
- git ref は作らない、 branch は従来通り `git branch -D` で削除
- 容量増は task 1 つあたり数十バイト

#### 実装変更点

- `git_worktree.on_cleanup` の冒頭で `git rev-parse refs/heads/task/<id>` を取って ctx に返す
- cleanup.py の archive 処理で task.yaml に書き戻す

#### メリット

- 実装最小 (CLI / hook 数行)
- task.yaml だけで完結、 git 側に何も足さない
- 既存 archive を読む code への影響ゼロ (新 field を無視すれば従来動作)

#### デメリット

- **gc race**: branch を消した時点で commit は git の orphan、 gc 既定 30 日で消える可能性。 30 日後に archive を見ても tip hash は記録されているが **`git show <hash>` が解決不能** になる
- diff / cherry-pick / `git log` ができない (= 案の主目的を達成しない)
- 結局 「hash の文字列だけが残るが、 中身は失われている」 になりかねない

#### 結論

不採用。 「commit hash を記録するだけ」 では本提案の主目的 (後で work を辿る) を満たさない。

---

### 3.2 案 B: detached ref `refs/fleet-archive/task-<id>` + task.yaml metadata (中量) — **推奨**

#### 概要

- cleanup 時に `git update-ref refs/fleet-archive/task-<id> <branch-tip>` で detached ref を作る
- ref が gc から commit を protect、 commit history は永続化
- task.yaml に `archive_tip: <hash>` / `archive_ref: refs/fleet-archive/task-<id>` / `archived_at: <iso8601>` の 3 行を追加
- branch は従来通り `git branch -D`、 worktree も従来通り削除
- 容量増は ref file 数十 byte + commit object (元からあるので増分ゼロ)

#### 実装変更点

`git_worktree.on_cleanup`:

```python
def on_cleanup(ctx):
    # 既存処理の直前で:
    branch = f"task/{task_id}"
    r = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--verify", branch],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        tip = r.stdout.strip()
        archive_ref = f"refs/fleet-archive/task-{task_id}"
        subprocess.run(
            ["git", "-C", str(project_root), "update-ref", archive_ref, tip],
            check=True,
        )
        ctx.setdefault("archive_meta", {})
        ctx["archive_meta"]["archive_tip"] = tip
        ctx["archive_meta"]["archive_ref"] = archive_ref
    # 以降は既存 (worktree remove + branch -D)
```

`cleanup.py` の archive 処理:

```python
if args.archive:
    archive_meta = ctx.get("archive_meta", {})
    if archive_meta:
        task.update(archive_meta)
        task["archived_at"] = utcnow_iso()
        state_mod.save_task(state_dir, task_id, task)
    # 以降は既存 (os.rename)
```

#### 参照経路

```bash
# 列挙
git for-each-ref refs/fleet-archive/
# 該当 task の log
git log refs/fleet-archive/task-cli-split-impl
# diff (e.g. 何がマージされたか)
git diff main...refs/fleet-archive/task-cli-split-impl
# cherry-pick の起点
git checkout -b retry/task-cli-split-impl refs/fleet-archive/task-cli-split-impl
```

state file 側:

```bash
cat .fleet-state/tasks/_archive/task-cli-split-impl/task.yaml
# id: cli-split-impl
# title: ...
# status: completed
# branch: task/cli-split-impl
# archive_tip: a1b2c3d4...
# archive_ref: refs/fleet-archive/task-cli-split-impl
# archived_at: 2026-05-20T03:30:00Z
```

#### メリット

- **git world に閉じる**: 独自フォーマット / tarball 不要、 `git log` / `git show` / `git diff` がそのまま動く
- **gc から protect**: ref がある限り commit object は gc されない
- **listing 汚染なし**: `refs/heads/` ではないので `git branch -a` / `git branch` に出ない、 `git for-each-ref refs/fleet-archive/` で明示列挙
- **dual record**: ref (git の都合) + task.yaml metadata (fleet の都合) で両世界から引ける
- **後方互換**: 既存 archive 読み取り code は `archive_tip` 等の新 field を無視すれば動く
- **小さい**: ref file は 41 byte、 commit object は元から store にあり増分ゼロ
- **§11 priority 3 と整合**: 「driver が commit する責務」 を archive がリスペクトする (commit していない work は救わない)

#### デメリット

- ref を消し忘れると 「無限に溜まる」 (mitigation: `fleet-agent forget <id>` で個別 purge、 必要なら ρ-2 を後付け)
- ref が project repo 内に増えるので、 `.git/packed-refs` が肥大化する可能性 (ただし 1 task = 1 ref で、 数万 ref まで実害なし)
- driver が commit していない work (uncommitted state) は救われない (これは意図、 §11 priority 3 の方向と整合)

#### 結論

**推奨**。 軽量実装で主目的を達成、 後方互換、 git world と整合。

---

### 3.3 案 C: worktree tarball + ref (重量)

#### 概要

- 案 B の ref に加え、 worktree 全体を tarball (`.tar.gz`) にして `.fleet-state/tasks/_archive/task-<id>/worktree.tar.gz` に保存
- uncommitted state も含めて 「driver が触ったもの全部」 を保全
- 容量は 1 task あたり数 MB - 数十 MB (`.venv` / `node_modules` / build artifact 次第)

#### 実装変更点

- 案 B の改修に加え、 `git_worktree.on_cleanup` の worktree remove 直前で `tar -czf` を実行
- `.gitignore` 相当 (= worktree 内の untracked + ignored) も含めるか否かを policy 決定
- tarball の path を task.yaml の `archive_worktree:` に追記

#### メリット

- **uncommitted state も救う** (driver が commit せず done した時の救済)
- 完全な再現性 (build artifact / config / 環境込み)

#### デメリット

- **容量爆発**: `.venv` / `node_modules` / build cache 込みで数十 MB - GB 級。 数百 task 溜まると `.fleet-state/` が見るに耐えない
- **mitigation の複雑化**: ignore rule (どの path を除く?) を仕様化する必要、 project 固有事情も入る
- **§11 priority 3 を delay させる**: 「commit せず done」 を archive で救えるなら driver は commit を怠ったままになる、 構造的圧力が抜ける
- **secret 混入リスク**: `.env` / credentials / token が tarball に入り、 archive ディレクトリが secret store 化する
- **整合性問題**: tarball の中身は cleanup 時点の snapshot、 git ref と二重 SOT (どちらが正?)
- 案 B より重い実装、 ignore policy 議論、 secret scrubbing、 容量管理など派生 work が多い

#### 結論

不採用。 救う対象 (uncommitted state) は §11 priority 3 の根本対策で消えるべき問題、 ここで救済路を作ると priority 3 修正が delay する。 必要なら **将来 opt-in の workflow plugin** (`bare-workspace-workflow` 等の派生) として個別実装する余地は残す。

---

### 3.4 メリデメ比較表

| 観点 | 案 A: metadata only | 案 B: ref + metadata **(推奨)** | 案 C: tarball + ref |
|---|---|---|---|
| commit history 復元 | ✗ (gc race) | ◎ ref で永続 | ◎ ref で永続 |
| uncommitted state 復元 | ✗ | ✗ | ◎ |
| 実装コスト | 最小 (数行) | 小 (10-20 行) | 中 (50-100 行 + policy 議論) |
| 容量増 | ほぼゼロ | ほぼゼロ (ref 41 byte) | 数 MB - GB / task |
| `git branch -a` 汚染 | なし | なし | なし |
| `git log <ref>` で読める | ✗ | ◎ | ◎ |
| `git diff` で対比できる | ✗ | ◎ | ◎ |
| secret 混入リスク | なし | なし | 高 |
| §11 priority 3 との整合 | 中立 | ◎ 補強する | ✗ delay させる |
| 後方互換 (既存 archive) | ◎ | ◎ | ◎ |
| 過去 archive の救済 | ✗ | ✗ (諦め) | ✗ (諦め) |
| **総合** | 主目的未達 | **主目的達成 + コスト小** | 主目的達成だが副作用大 |

---

## 4. 推奨

### 4.1 採用案

**案 B: detached ref `refs/fleet-archive/task-<id>` + task.yaml metadata**。

### 4.2 仕様の詳細

#### 4.2.1 ref naming

- 形式: `refs/fleet-archive/task-<id>`
- `refs/heads/` でも `refs/tags/` でも `refs/remotes/` でも `refs/notes/` でもない、 fleet 専用 namespace
- 列挙: `git for-each-ref refs/fleet-archive/`

理由:

- `refs/heads/archive/` 等の branch 扱いは `git branch -a` を汚す
- `refs/tags/` は immutable で副作用が違う (`git tag --list` に並ぶ、 push 時に default 同期される ref も pollute する)
- `refs/notes/` は git-notes 用途と意味が衝突
- `refs/fleet-archive/` は完全に独自 namespace で、 push / fetch の default に乗らない (= remote pollute なし)

#### 4.2.2 task.yaml に追加する field

| field | 値 | 例 |
|---|---|---|
| `archive_tip` | branch tip の commit hash (40 桁) | `a1b2c3d4e5f6...` |
| `archive_ref` | ref の完全名 | `refs/fleet-archive/task-cli-split-impl` |
| `archived_at` | archive 時刻 (UTC ISO8601) | `2026-05-20T03:30:00Z` |

ref が作れなかった場合 (branch が unborn 等):

- `archive_tip` / `archive_ref` は **省略** (key 自体を書かない)
- `archived_at` は書く

#### 4.2.3 events.jsonl

既存の `cleanup` event に `archive_tip` / `archive_ref` を field として追加。 新 event type は作らない。

```json
{"ts":"...","type":"cleanup","task_id":"cli-split-impl","archived":true,"archive_tip":"a1b2...","archive_ref":"refs/fleet-archive/task-cli-split-impl"}
```

#### 4.2.4 forget CLI

```bash
fleet-agent forget <task-id>
```

挙動:

1. `tasks/_archive/task-<id>/task.yaml` を読んで `archive_ref` を取得
2. `git update-ref -d <archive_ref>` で ref 削除
3. `rm -rf tasks/_archive/task-<id>/` で state file 群削除
4. `events.jsonl` に `archive_forget` event 追記
5. `dashboard` rebuild

`archive_ref` が無い古い archive は ref 削除をスキップ、 state file のみ削除。

#### 4.2.5 参照 CLI (phase 1 では実装しない)

`fleet-agent show-archive <id>` の追加は将来検討項目。 phase 1 では直 `cat tasks/_archive/.../task.yaml` + `git log <ref>` で十分。

### 4.3 責務分担

| 責務 | 担当 |
|---|---|
| `git rev-parse` で tip 取得 | `git_worktree` plugin |
| `git update-ref` で ref 作成 | `git_worktree` plugin |
| `task.yaml` への metadata 書き戻し | `cleanup.py` (core) |
| `events.jsonl` への field 追加 | `cleanup.py` (core) |
| `forget` CLI | core (新 command) |
| ref 削除 | `forget` command → ただし git 操作なので将来 plugin 経由化の余地あり |

`forget` の ref 削除を plugin 経由にするか core 直叩きにするかは §11 priority 3 / §8.3 (plugin 機構) 議論の後に決める。 phase 1 では core 直叩きで素朴に。

### 4.4 dialogue-trace-proposal / inbox-ack-proposal との分担

| 領域 | 担当 proposal | archive で同居 |
|---|---|---|
| driver の git work (commit / branch) | **本提案** | refs/fleet-archive + task.yaml metadata |
| driver ↔ user 対話 (ask/answer/messages) | dialogue-trace-proposal | dialogue-trace phase 2 の `messages/` ファイル群が tasks/_archive/ に既に同居する想定 |
| leader → driver inbox の ack | inbox-ack-proposal | inbox-ack phase 2 で messages/ に統合される時、 watermark event は events.jsonl に残る (events.jsonl は project 単位で archive 対象外) |

→ 機能重複はない。 dialogue-trace / inbox-ack が phase 2 で `messages/` ファイル群に進化したら、 そのまま `tasks/_archive/task-<id>/messages/` に同居する。 本提案の改修は不要。

---

## 5. 移行戦略

### 5.1 phase 1 (本提案で合意したい範囲)

1. `git_worktree.on_cleanup` を改修: branch 削除前に `archive_tip` 解決 + `refs/fleet-archive/task-<id>` 作成、 ctx に返す
2. `cleanup.py` を改修: ctx から受け取った `archive_meta` を `task.yaml` に書き戻し
3. `events.jsonl` の `cleanup` event に `archive_tip` / `archive_ref` を field 追加
4. `fleet-agent forget <id>` を新規追加
5. 既存 `fleet-agent cleanup --archive` の振る舞いは **後方互換**: 新 field が増えるだけ、 既存読み手は無視できる
6. tests: `tests/test_cleanup.py` (既存 or 新規) で archive_ref の作成と task.yaml への書き戻しを検証

実装規模感: 改修 30-50 行 + test 50 行程度。 1 driver で 1 サイクル収まる。

### 5.2 phase 2 (将来)

dialogue-trace / inbox-ack の `messages/` 統合が確定したら:

- 本提案改修不要 (messages/ は既に task ディレクトリ配下にあり、 archive で同居)
- ただし `fleet-agent show-archive <id>` で messages も含めて表示する CLI を **その時に** 追加するのは自然

### 5.3 過去 archive の扱い

phase 1 適用前に cleanup --archive 済みの task は **諦める**。 task.yaml に `archive_tip` が無いものは 「git 側から復元不可」 を許容。

将来コストが許せば `.git/logs/HEAD` (reflog) / `git fsck --lost-found` を漁って復元する救済 CLI を後付けする余地はあるが、 phase 1 では入れない。

### 5.4 plugin 跨ぎの整合性

将来 `bare-workspace-workflow` / `pr-based-workflow` 等の新 plugin が増えた時:

- 「branch tip を ref で保護する」 という archive 行動は **git ベースの workflow plugin の責務**
- bare-workspace は worktree を持たないので archive の動作も違う (state file 退避だけで OK)
- pr-based-workflow は branch + PR 番号を持つので、 task.yaml に `archive_pr:` 行を追加する派生がありうる

phase 1 では git_worktree のみ対応、 他 plugin は plugin 側の責務として後付け。 cleanup.py 側は `ctx["archive_meta"]` を受け取って task.yaml に merge するだけで、 plugin に依存しない。

---

## 6. open questions

### 6.1 ref naming

- `refs/fleet-archive/task-<id>` で良いか? `refs/fleet/archive/<id>` の方が階層的だが、 namespace の親 (`refs/fleet/`) を 1 階層挟むメリットが現時点で薄い
- multi-project (`.fleet-state/` を持つ別 project) が同じ project repo を使うことは想定外なので project name は ref name に含めない

→ 提案: `refs/fleet-archive/task-<id>` 固定。 異論があれば §11 で議論。

### 6.2 forget の semantics

- `fleet-agent forget <id>` は **完全削除**? それとも 「ref だけ削除して state file は残す」 のような中間モード?
- 提案: phase 1 は完全削除のみ。 中間モードは需要が出てから

### 6.3 list -a (archived 含む) の CLI

- `fleet list` を `--archive` 付きで archived 一覧出すべきか?
- 提案: phase 1 では入れない。 `ls .fleet-state/tasks/_archive/` で見える、 必要なら後で

### 6.4 task.yaml の `branch:` 行はどうする

- 現状 archive 後も task.yaml には spawn 時の `branch: task/<id>` が残る (削除した branch 名)
- `archive_ref` を増やすので情報冗長、 残しておいて良いが 「これは生きてる branch ではない」 を明示するべきか?
- 提案: 残す (歴史情報として有用)、 注釈は要らない (`archive_ref` の存在で archived と分かる)

### 6.5 PR 番号の記録

- task.yaml に `pr: <number>` を加えるべきか? GitHub 側との突合が楽になる
- 提案: 本提案のスコープ外、 別 backlog 項目として切り出す。 PR 作成 plugin (§11 priority 8 = pr-based-workflow) と一緒に議論したい

### 6.6 worktree 内の uncommitted state を救う opt-in 経路

- 案 C を「全 task default」 では入れないが、 「特定 task だけ tarball を残す」 opt-in flag (`fleet-agent cleanup --archive --keep-worktree-tarball <id>`) はあって良いか?
- 提案: phase 1 では入れない。 §11 priority 3 (driver commit 責務) が固まってから判断、 そこで 「commit を強制する側に倒れる」 か 「opt-in 退避路を残す側に倒れる」 が決まる

### 6.7 events.jsonl 自体の archive

- events.jsonl は project 単位で 1 ファイル、 archive 対象外。 大量の task が回ると無限に肥大化する
- これは本提案のスコープ外、 別 backlog 項目 (rotation policy) として切り出し

### 6.8 既存 cleanup の `--archive` flag を default に倒すか

- 現状 `--archive` は opt-in、 default は archive せず削除のみ
- 「archive せず削除」 が default で良いのか? cleanup 後にやっぱり振り返りたい case が普通にある
- 提案: 本提案のスコープ外 (UX 議論)。 backlog に切り出す
