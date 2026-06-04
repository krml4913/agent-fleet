# agent-fleet

*[English README](README.md)*

**agent-fleet** は、tmux のペイン内で driver エージェント（claude / codex）を
動かす、階層的かつマルチベンダーなエージェントオーケストレーターである。
あなたは単一の **leader** エージェントと対話し、タスクを軽く投げるだけでよい。
leader はそれぞれ専用のペインで **driver** エージェントを起動して作業を進める。
各プロジェクトごとに YAML で定義された **team formation** に従う。多数のタスクを
同時に走らせられる。すべて tmux 上のキーボード操作だけで完結し、いつでも driver の
ペインにアタッチして、何をしているかを読んだり、軌道修正したり、作業の途中で
引き継いだりできる。これは無人の完全自律ではなく、human-in-the-loop な
コーディング作業のために作られている。

**Python ≥ 3.11** と **tmux** が必要。**`pip install` は不要** で、repo を
clone して `./fleet` を実行するだけでよい。Python 依存はすべて `vendor/` 配下に
同梱してある。

---

## 60 秒で分かる概念

- **Leader** — あなたがチャットする相手のエージェント。プロジェクトごとに 1 つで、
  `fleet-<project>` という名前の tmux セッション内に常駐する。タスクを割り当て、
  あなたの判断を中継する。自分ではコードを書かず、driver にディスパッチする。
- **Driver** — 単一のタスクを実際に処理するエージェントで、専用の tmux ウィンドウ
  内で動く。driver は claude でも codex でもよい。任意の driver ペインにアタッチ
  できる。
- **Formation** — *誰がどうタスクを処理するか* を記述した YAML ファイル。stage の
  並び、各 stage を担当するエージェント、AI による peer review の有無、人間の承認
  ゲートの位置を定める。`solo`、`pair_review`、`multi_stage` の 3 つを同梱している。
  [docs/formations.md](docs/formations.md) を参照。
- **Workspace** — タスクの作業ツリーをどう分離するか。`worktree` は各タスクに専用の
  git worktree/branch を与え、`none` はその場で作業する。プロジェクトごとに設定する。

leader と driver は、プロジェクトの state ディレクトリ内のファイル
（`inbox.md`、`outbox.md`、`questions.md`、追記専用の `events.jsonl`、自動生成される
`dashboard.md`）を通じて通信する。あなたは leader を動かし、leader は `fleet-agent`
CLI を通じて driver を動かす。

---

## クイックスタート: user の歩む道

fleet を使う実際の体感はこうだ。一度きりのちょっとしたセットアップを済ませたら、
あとはほぼ **チャットで leader に話しかけるだけ**。per-task の細かいコマンドを自分で
打つことはまずない —— それは leader が代わりに発行する。コマンドは agent-fleet を
`~/dev/agent-fleet` に clone した前提なので、パスは自分の環境に合わせて調整すること。

### 1. clone して環境を検証する

```bash
git clone <this-repo-url> agent-fleet
cd agent-fleet

./fleet preflight
```

`preflight` は、`PATH` 上の Python、tmux、git、そしてエージェント CLI
（`claude`、`codex`）をチェックする。Codex CLI が古い場合や、directory trust が
設定されていない場合にも警告する。指摘された点は次に進む前に解消すること。

### 2. プロジェクトを初期化する

エージェントに作業させたい任意の git リポジトリに fleet を向ける。ここでは
使い捨てのものを作る:

```bash
mkdir -p /tmp/trial && cd /tmp/trial
git init -b main
echo hi > README.md
git add -A && git -c user.email=t@x -c user.name=t commit -m init

~/dev/agent-fleet/fleet init --name trial .
```

`init` はプロジェクトを fleet のレジストリに登録し、その state を
`agent-fleet/fleet-state/projects/trial/` 配下に作成する。残りのコマンドは
プロジェクトディレクトリ内から実行できる。fleet は cwd からプロジェクト名を
解決する。

任意で、各タスクに専用の git branch/worktree を与えることもできる（デフォルトは
その場での作業）:

```bash
~/dev/agent-fleet/fleet workspace set worktree
```

### 3. leader を起動する

```bash
~/dev/agent-fleet/fleet leader --attach
```

これは `fleet-trial` という tmux セッションを作成し、その中で leader エージェント
（デフォルトは `claude:opus`）を動かし、フォアグラウンドでアタッチする。セッションは
プロジェクトごとに単一インスタンスである。いつでも `C-b d` でデタッチでき、leader は
動き続ける。あなたが常駐するのはこの 1 つのペインだ。

### 4. leader に話しかける

ここが fleet を使う体験の核心だ。**あなたはやりたいことを自然な散文で leader に
チャットで伝える** だけで、あとは leader がやってくれる —— formation を選び、
エージェントを決め、driver を起動する。

```
you ▸ status コマンドに --json フラグを足して、テストでカバーして。
      pair_review formation で。

leader ▸ `status-json-flag` を pair_review で開始する（codex が実装、
         claude がレビュー）。承認が要るタイミングで知らせる。
```

`fleet-agent start` を自分で打つことは **通常ない** —— leader があなたの依頼を
そのコマンドに翻訳する。ここから先はシェルコマンドではなく、ほぼ散文を打つだけだ。
作業は leader とのチャットで舵を取る。

### 5. 観察し、介入し、承認する

プロジェクト内の任意のシェルから進捗を見られる:

```bash
~/dev/agent-fleet/fleet status                 # タスク一覧 + 直近のイベント
cat fleet-state/projects/trial/dashboard.md    # 人間が読めるロールアップ（自動更新）
```

driver の肩越しに覗いたり引き継いだりするには、そのペインにアタッチする —— これが
体感の核心だ:

```bash
~/dev/agent-fleet/fleet attach status-json-flag   # このタスクの driver ペイン
~/dev/agent-fleet/fleet attach                     # leader（デフォルトターゲット）
```

ライブのエージェントセッションに直接降り立つ —— 出力を読み、入力し、軌道を修正し、
終わったら `C-b d` でデタッチする。

driver が判断を必要とするときは通知を発火する（ペインの出力だけではあなたに
届かない）。`user_approval` ゲートを持つ formation も、同じように一時停止する。
判断を下すのはあなたで、それを **leader に伝える** —— leader が中継する（leader は
決して自分で承認しない）。チャットで「いいね、出して」「いや、まず X を直して」と
言うだけでよい —— 実際の approve/reject は leader が代わりに実行する。

これが全体のループだ: **init → leader を起動 → チャット → 観察 / 承認。**
`fleet-agent start / inbox / approve / cleanup` を自分で触ることは通常ない ——
それらは leader の仕事だ。次のセクションでは、仕組みを理解したい人や手動でタスクを
動かしたい人のために、それらを一通り示す。

---

## 内部の仕組み: 手動で動かす

> 通常はこれらを自分で打つことはない。あなたが leader とチャットすると、leader が
> 代わりに実行する。このセクションは動く部品を理解するための —— あるいは leader
> なしで手動でタスクを動かすための —— リファレンスである。

以下はすべて `hello-world` というタスク id を例に使う。

### タスクをディスパッチする

```bash
cd /tmp/trial
~/dev/agent-fleet/fleet-agent start hello-world "Implement a hello-world script." --formation solo
```

第 1 引数（ここでは `hello-world`）は自分で付ける **タスク id** である。短い
kebab-case の slug（小文字英字・数字・ハイフン）でタスクに名前を付ける。自動採番の
番号ではない。これが branch 名・state ディレクトリ・tmux ウィンドウ名になるので、
内容が分かる名前を付ける。

これはタスク state を書き込み、`driver-prompt.md` をレンダリングし、最初の stage の
driver を動かす新しい tmux ウィンドウを開き、（デフォルトでは）エージェントの準備が
できたらプロンプトへのポインタをペインに自動ペーストする。team の形は
`--formation`（`solo`、`pair_review`、`multi_stage`、または任意のカスタム）で選び、
最初の stage のエージェントは `--agent` で上書きする。長い説明をインラインではなく
ファイルから渡すには `--prompt-file PATH` を使う。

### driver に非同期のメモを残す

アタッチする代わりに、driver の inbox にメッセージを投げ込める:

```bash
~/dev/agent-fleet/fleet-agent inbox hello-world "Use argparse, not sys.argv parsing."
```

これはタスクの `inbox.md` にタイムスタンプ付きのメモを追記し、ペインを起こす。

### ゲートを承認 / 却下する

driver が `fleet-agent ask` を呼んだとき、または `user_approval` ゲートを持つ stage
が完了したとき、タスクは `awaiting_orders` に切り替わる。判断を中継する:

```bash
~/dev/agent-fleet/fleet-agent approve hello-world   # 保留中のゲートを承認
~/dev/agent-fleet/fleet-agent reject hello-world    # 却下。stage は作業に戻る
```

`pair_review` formation では、implementer が自動的に AI reviewer に引き継ぐ。人間が
必要なのは最終のユーザー承認ゲートだけである。

### 終了して片付ける

タスクが完了したら、それを撤去する（任意で state をアーカイブする）:

```bash
~/dev/agent-fleet/fleet-agent cleanup hello-world --archive
```

これは workspace のクリーンアップフックを実行し（worktree を使っていれば削除し）、
タスクの tmux ウィンドウを kill し、そのプロンプトバッファを破棄する。終端状態で
ないタスクに対しては、`--force` を渡さない限り実行を拒否する。

後でプロジェクト全体を fleet から削除するには:

```bash
~/dev/agent-fleet/fleet rm trial --yes
```

これはプロジェクトの登録を解除し、その state を削除する。アクティブな tmux
セッションは自動では kill されない。まだ動いているものを見つけると fleet が警告する。

---

## コマンドリファレンス

### `fleet` — 人間用 CLI

| コマンド | 用途 |
|---|---|
| `fleet preflight` | Python / tmux / git / エージェント CLI をチェック（Codex の trust + アップデート警告を含む）。 |
| `fleet init [path] [--name N] [--formation N] [--no-formation]` | プロジェクトを登録し、その state ディレクトリを作成する。 |
| `fleet leader [--project P] [--agent SPEC] [--attach]` | leader ペインを起動 / アタッチする（デフォルトエージェント `claude:opus`）。 |
| `fleet attach [target] [--project P]` | leader（デフォルト）またはタスク driver ペインにアタッチする。 |
| `fleet status [name] [--all] [--events N]` | プロジェクト情報、タスク一覧、直近のイベントを表示する。 |
| `fleet log [task_id] [-n N] [--type T]` | `events.jsonl` を tail し、任意でタスク / タイプでフィルタする。 |
| `fleet formation list \| show <name> \| init --from <template>` | formation を確認または作成する。 |
| `fleet workspace list \| set <mode>` | workspace モード（`worktree` / `none`）を表示または設定する。 |
| `fleet rm <name> [--yes]` | プロジェクトの登録を解除し、その state を削除する。 |

### `fleet-agent` — エージェント用 CLI

leader と driver が実行する。日常的に人間が直接使うことを意図したものではないが、
動く部品を理解するのに役立つ。

leader 側:

| コマンド | 用途 |
|---|---|
| `fleet-agent start <id> "<desc>" [--formation F] [--agent A] [--title T] [--prompt-file P]` | タスクを開始する: state を書き込み、プロンプトをレンダリングし、最初の driver ペインを開く。 |
| `fleet-agent inbox <id> "<msg>"` | driver の `inbox.md` にタイムスタンプ付きのメモを追記し、ペインを起こす。 |
| `fleet-agent send-prompt <id>` | `driver-prompt.md` のポインタをタスクペインに（再）配信する。 |
| `fleet-agent approve <id>` | 保留中の `user_approval` ゲートに対するユーザー承認を中継する。 |
| `fleet-agent reject <id>` | ユーザーの却下を中継する。stage は実装に戻る。 |
| `fleet-agent cleanup <id> [--archive] [--force]` | 完了したタスクを撤去する。 |

driver 側（driver ペイン内で実行。`FLEET_TASK_ID` は設定済み）:

| コマンド | 用途 |
|---|---|
| `fleet-agent ask "<question>"` | タスクを `awaiting_orders` に切り替え、質問を記録し、ユーザーに通知する。 |
| `fleet-agent inbox-read` | `inbox.md` を読み、`inbox_seen` の ack を発行する。 |
| `fleet-agent event emit <type> [--field K=V ...]` | 監査イベントを追記する。 |
| `fleet-agent done [--result approved\|changes-requested]` | stage を done としてマークする。オーケストレーターがタスクを進める。 |

---

## 同梱の formation

| Formation | 形 |
|---|---|
| `solo` | 1 つの driver がタスクを最初から最後まで処理する。review もゲートもなし。 |
| `pair_review` | Implementer → AI peer review（最大 3 ラウンド）→ ユーザー承認。マルチベンダーの目玉フロー（例: codex が実装し、claude がレビュー）。 |
| `multi_stage` | 設計 stage → ユーザー承認 → review と承認を伴う実装 stage。 |

formation はプロジェクトごとに編集できる素の YAML である。エージェントを差し替え、
reviewer を追加し、ゲートを外せる。完全なスキーマと leader 向けのクックブックは
[docs/formations.md](docs/formations.md) にある。

---

## プロジェクト state のレイアウト

`fleet init --name trial` の後、state は agent-fleet の checkout 配下に置かれる:

```
agent-fleet/fleet-state/
  projects.yaml                 # 既知のプロジェクトのレジストリ
  projects/trial/
    project.yaml                # name / workspace モード / created_at
    events.jsonl                # 追記専用の監査ログ
    dashboard.md                # 自動生成される読み取り専用ビュー
    formations/                 # このプロジェクトの formation（YAML）
    tasks/
      task-1/
        task.yaml               # status / title / agent / formation / ...
        driver-prompt.md        # レンダリングされた初期プロンプト
        inbox.md                # leader -> driver
        outbox.md               # driver -> leader
        questions.md            # `fleet-agent ask` がここに記録する
      _archive/                 # cleanup --archive がここに着地する
```

---

## ライセンス

MIT。[LICENSE](./LICENSE) を参照。
