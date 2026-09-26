# agent-fleet

*[English README](README.md)*

**agent-fleet** は、ターミナルマルチプレクサ（デフォルトは zellij、tmux も選択可）の
ペイン内で driver エージェント（claude / codex）を動かす、階層的かつ
マルチベンダーなエージェントオーケストレーターである。
あなたは単一の **leader** エージェントと対話し、タスクを軽く投げるだけでよい。
leader はそれぞれ専用のペインで **driver** エージェントを起動して作業を進める。
各プロジェクトごとに YAML で定義された **team formation** に従う。多数のタスクを
同時に走らせられる。すべてマルチプレクサ上のキーボード操作だけで完結し、いつでも
driver の
ペインにアタッチして、何をしているかを読んだり、軌道修正したり、作業の途中で
引き継いだりできる。これは無人の完全自律ではなく、human-in-the-loop な
コーディング作業のために作られている。

**Python ≥ 3.11** とターミナルマルチプレクサが必要: **zellij ≥ 0.45.0**
（全プラットフォームでのデフォルト）または **tmux**（明示的に選ぶ場合。
[マルチプレクサの選択](#マルチプレクサの選択) と [Windows](#windows) を参照）。**`pip install` は
不要** で、repo を clone して `./fleet`（Windows では `fleet.cmd`）を実行するだけで
よい。Python 依存はすべて `vendor/` 配下に同梱してある。

---

## 60 秒で分かる概念

- **Leader** — あなたがチャットする相手のエージェント。プロジェクトごとに 1 つで、
  `fleet-<project>` という名前のマルチプレクサセッション内に常駐する。タスクを割り当て、
  あなたの判断を中継する。自分ではコードを書かず、driver にディスパッチする。
- **Driver** — 単一のタスクを実際に処理するエージェントで、専用のマルチプレクサウィンドウ
  （zellij ではタブ）内で動く。driver は claude でも codex でもよい。任意の driver ペインにアタッチ
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

`preflight` は、`PATH` 上の Python、ターミナルマルチプレクサ（どのバックエンドが
選ばれ、その選択がどこから来たか — `env` / `config` / `default` — も表示する）、git、
そしてエージェント CLI（`claude`、`codex`）をチェックする。Codex CLI が古い場合や、directory trust が
設定されていない場合や、Claude Code がその repo をまだ信頼していない場合（そのままだと最初の
claude タスクが trust プロンプトで止まる）にも警告する。指摘された点は次に進む前に解消すること。

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

これは `fleet-trial` というマルチプレクサセッションを作成し、その中で leader エージェント
（デフォルトは `claude:opus`。永続的に設定を変えたい場合は
[leader のエージェントを選ぶ](#leader-のエージェントを選ぶ) を参照）を動かし、フォアグラウンドでアタッチする。セッションは
プロジェクトごとに単一インスタンスである。いつでもデタッチでき（tmux は `C-b d`、zellij は `Ctrl o` のあと `d`）、leader は
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
~/dev/agent-fleet/fleet dashboard              # 全 PJ 横断 GUI ビュー（ブラウザで開く）
```

driver の肩越しに覗いたり引き継いだりするには、そのペインにアタッチする —— これが
体感の核心だ:

```bash
~/dev/agent-fleet/fleet attach status-json-flag   # このタスクの driver ペイン
~/dev/agent-fleet/fleet attach                     # leader（デフォルトターゲット。別の leader セッションは --session LABEL）
```

ライブのエージェントセッションに直接降り立つ —— 出力を読み、入力し、軌道を修正し、
終わったらデタッチする（tmux は `C-b d`、zellij は `Ctrl o` のあと `d`）。

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
番号ではない。これが branch 名・state ディレクトリ・マルチプレクサのウィンドウ名になるので、
内容が分かる名前を付ける。

これはタスク state を書き込み、`driver-prompt.md` をレンダリングし、最初の stage の
driver を動かす新しいマルチプレクサウィンドウ（zellij ではタブ）を開き、（デフォルトでは）エージェントの準備が
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
タスクのマルチプレクサウィンドウを kill し、そのプロンプトバッファを破棄する。終端状態で
ないタスクに対しては、`--force` を渡さない限り実行を拒否する。

後でプロジェクト全体を fleet から削除するには:

```bash
~/dev/agent-fleet/fleet rm trial --yes
```

これはプロジェクトの登録を解除し、その state を削除する。アクティブなマルチプレクサ
セッションは自動では kill されない。まだ動いているものを見つけると fleet が警告する。

---

## コマンドリファレンス

### `fleet` — 人間用 CLI

| コマンド | 用途 |
|---|---|
| `fleet preflight` | Python / マルチプレクサ（zellij または tmux。どちらが選ばれ、なぜかも表示）/ git / エージェント CLI をチェック（Codex / Claude の trust と Codex のアップデート警告を含む。Windows では追加チェックあり）。 |
| `fleet config [get <key> \| set <key> <value> \| unset <key>]` | グローバル config（`fleet-state/global/config.yaml`）の表示 / 取得 / 設定 / 削除。キーは `mux` = `zellij` \| `tmux`（[マルチプレクサの選択](#マルチプレクサの選択) を参照）、`leader_agent` = `vendor:model` 形式の spec またはエージェントエイリアスで `fleet leader` のデフォルトエージェント（[leader のエージェントを選ぶ](#leader-のエージェントを選ぶ) を参照）、`leader_delivery` = `send_message`（デフォルト）\| `pane` で claude leader への driver 通知の届け方（`fleet leader` 起動時に読む）、`agent_aliases.<name>` = エージェントエイリアス（[エージェントエイリアス](#エージェントエイリアス) を参照）。 |
| `fleet init [path] [--name N] [--formation N] [--no-formation]` | プロジェクトを登録し、その state ディレクトリを作成する。 |
| `fleet leader [--name LABEL] [--agent SPEC] [--attach]` | leader セッション `fleet-<LABEL>` を起動 / アタッチする（デフォルトのラベルは `main`）。エージェントの優先順位: `--agent` > グローバル config の `leader_agent` > 組み込みデフォルト `claude:opus`。 |
| `fleet leader --respawn [--name LABEL] [--agent SPEC]` | 動いているセッション `fleet-<LABEL>` の leader エージェントだけを再起動する。新しい leader ウィンドウを新規の `fleet leader` と同じコマンドラインで起動し（現在の `leader_delivery` 設定が効く）、古い leader ウィンドウを閉じて、leader プロンプトを貼り直す。driver のウィンドウと leader 宛ての未配達通知には触らず、`session.json` の `scope` と `started_at` も残す。エージェントのデフォルトはセッション起動時のもの。leader ペインの中から実行しても安全（エージェントを終了してから打つ、または leader に実行させる）。その場合は処理がバックグラウンドで完了し、ログは `global/sessions/<LABEL>/leader-respawn.log` に出る。 |
| `fleet attach [target] [--project P] [--session LABEL]` | タスクの driver ペイン（そのタスクを所有するセッション `fleet-<owner_session>` 内。`--project` でタスクを特定）、またはデフォルトで `fleet-<LABEL>` の `leader` ペインにアタッチする（`--session`、デフォルトは `$FLEET_SESSION`、なければ `main`）。対象セッションが動いていなければ、稼働中のセッションを一覧表示する。 |
| `fleet status [name] [--all] [--unscoped] [--events N]` | プロジェクト情報、タスク一覧、直近のイベントを表示する。`--all` 時はセッションの scope 内 project のみ表示（`--unscoped` で全件）。 |
| `fleet sessions` | leader セッションと全 PJ の実行中タスクを一覧表示する。 |
| `fleet dashboard [--no-open]` | 全 PJ 横断 HTML ダッシュボード（`fleet-state/global/dashboard.html`）を生成してブラウザで開く。 |
| `fleet scope [label] [--set/--add/--rm/--clear]` | leader セッションが担当する project の集合（scope）を確認・編集する。 |
| `fleet log [task_id] [-n N] [--type T]` | `events.jsonl` を tail し、任意でタスク / タイプでフィルタする。 |
| `fleet formation list \| show <name>` | runtime formation と template seed source を確認する。 |
| `fleet formation seed <name> [--global] [--project P] [--force]` | 同梱の formation seed を project（デフォルト）または global tier にコピーする。既存ファイルは `--force` なしでは上書きしない。 |
| `fleet role seed <name> [--global] [--project P] [--force]` | 同梱の role プロンプト（`docs/prompts/roles/`）を project（デフォルト）または global tier にコピーする。既存ファイルは `--force` なしでは上書きしない。 |
| `fleet workspace list \| set <mode>` | workspace モード（`worktree` / `none`）を表示または設定する。 |
| `fleet notify [--project P] [on\|off\|status]` | オプトインの leader ペインへのプッシュ（`project.yaml` の `notify_leader_on_driver_done`、デフォルト off）を表示（引数なし / `status`）または設定する。on の間は driver の `done` / 承認ゲートに加え `fleet-agent ask` の質問も担当 leader のペインに注入される（ask の行は `fleet-agent inbox <id> "<answer>" --project P` で答えるよう leader に指示する）。次回の `done` / `ask` から有効。claude driver から `fleet leader` で起動した claude leader への通知は、leader のペインに打ち込まれない。`done` / `ask` がメッセージを出力し、driver が Claude Code の `SendMessage` で leader に送るので、leader の入力欄に書きかけの文があってもそのまま残る。`fleet config set leader_delivery pane` で無効にできる。この変更より前から動いている leader は、起動し直すまでペイン入力のまま（`fleet leader --respawn` なら driver を残したまま leader だけ起動し直せる）。 |
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
| `fleet-agent reject <id> [--reason TEXT \| --reason-file PATH]` | ユーザーの却下を中継する。stage は実装に戻る。driver の inbox に理由付きの `[fleet reject]` が届く。 |
| `fleet-agent cleanup <id> [--archive] [--force] [--allow-from-driver]` | 完了したタスクを撤去する。 |
| `fleet-agent merge <id> [--squash] [--keep] [--force] [--allow-from-driver]` | タスクの PR をマージし、撤去とアーカイブまで行う。 |

`merge` / `cleanup` は leader 専用で、driver ペインからは実行を拒否される（`FLEET_TASK_ID` で判定）。`--allow-from-driver` はこのソフトガードを意図的に上書きする（`--force` では上書きされない）。

driver 側（driver ペイン内で実行。`FLEET_TASK_ID` は設定済み）:

| コマンド | 用途 |
|---|---|
| `fleet-agent ask "<question>"` | タスクを `awaiting_orders` に切り替え、質問を記録し、ユーザーに通知する（`fleet notify on` の間は担当 leader のペインにも注入）。 |
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
  global/
    dashboard.html              # 全 PJ 横断 GUI ビュー（自動生成、fleet dashboard で開く）
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

## マルチプレクサの選択

> **デフォルトの変更 — 既存の macOS/Linux ユーザーは必読。** 組み込みのデフォルトの
> マルチプレクサは **全プラットフォームで zellij** になった。以前は Windows 以外では
> tmux だった。tmux を使い続けるには、次の **どちらか一方** を行う:
>
> ```bash
> ./fleet config set mux tmux     # 永続（fleet-state/global/config.yaml に書き込む）
> FLEET_MUX=tmux ./fleet ...      # シェル単位 / コマンド単位
> ```
>
> 動作中の tmux セッションがある場合は、**次に fleet コマンドを実行する前に** これを
> 行うこと。fleet は選択中のバックエンドのセッションしか探さないため、新しい
> デフォルトのままでは稼働中の `fleet-<label>` の tmux セッションが見えなくなる。
> macOS/Linux 上の zellij は tmux ほど検証されていない
> （[#258](https://github.com/krml4913/agent-fleet/issues/258)）。

バックエンドはプロセスごとに一度だけ選ばれ、最初に該当したものが勝つ:

1. 環境変数 **`FLEET_MUX=tmux|zellij`**。
2. グローバル config `fleet-state/global/config.yaml`
   （`$FLEET_HOME/global/config.yaml`）の **`mux:`**。
3. 組み込みのデフォルト: 全プラットフォームで **zellij**。

config は手で編集せず CLI で管理する:

```bash
./fleet config                  # 全キーの値と、その値の出どころを表示
./fleet config get mux          # 値を 1 つ表示
./fleet config set mux tmux     # tmux | zellij。未知のキー / 値は拒否される
```

`fleet config` / `get` は config 層（ファイル、なければデフォルト）を報告する。
`FLEET_MUX` が有効な場合はその旨が注記され、実際に選ばれたバックエンドとその出どころは
`fleet preflight` で確認できる。`FLEET_NO_MUX` と `FLEET_ZELLIJ` は従来どおり。
config ファイルがなければデフォルトを使うだけで、読めない / 不正な config は警告を
出して無視されるだけであり、コマンドが止まることはない。

---

## leader のエージェントを選ぶ

`fleet leader` は leader ペインをエージェント spec（`vendor:model` 形式。例:
`claude:opus`、`claude:claude-opus-5-5`、`codex:gpt-5.5`）で起動する。優先順位は、
最初に該当したものが勝つ:

1. コマンドラインの **`fleet leader --agent <spec>`**。
2. グローバル config `fleet-state/global/config.yaml` の **`leader_agent:`**。
3. 組み込みのデフォルト: **`claude:opus`**。

```bash
./fleet config set leader_agent claude:claude-opus-5-5   # 永続
./fleet leader --agent claude:claude-opus-5-5              # その起動だけの一時指定
```

値は `--agent` と同じ方法で検証される（未知の vendor は、サポート対象を列挙して
拒否される）。config ファイル中の `leader_agent` が欠けている、または不正な場合は、
他のグローバル config キーと同様に警告を出して組み込みのデフォルトにフォールバック
するだけであり、コマンドが止まることはない。`--agent` と `leader_agent` はどちらも
[エージェントエイリアス](#エージェントエイリアス) も受け付ける
（`./fleet config set leader_agent deep`）。エイリアスは起動時に完全な spec へ解決され、
`fleet config` は両方を表示する（`leader_agent: deep -> claude:opus (config)`）。

---

## エージェントエイリアス

エージェントエイリアスは `vendor:model` spec 全体に付ける短い名前で、グローバル config に
一度定義すれば spec を書ける場所ならどこでも使える: formation の stage の `agent` と
`peer_review.agent`、`fleet-agent start --agent`、`fleet leader --agent`、
config キー `leader_agent`。

```bash
./fleet config set agent_aliases.fast claude:sonnet
./fleet config set agent_aliases.deep claude:opus
./fleet config get agent_aliases.deep       # -> claude:opus
./fleet config unset agent_aliases.fast
./fleet config                              # 全エイリアスを表示
```

```yaml
# formation の stage
- role: implementer
  agent: fast
  peer_review:
    role: code-reviewer
    agent: deep
```

- エイリアスは完全な `vendor:model` spec 1 つに対応する。エイリアスからエイリアスへの
  連鎖は拒否される。エイリアス名に使えるのは英数字・`_`・`-` のみ（`:` は不可）なので、
  本物の spec と衝突することはない。
- エイリアスは spec が task / leader の状態に入る時点で **一度だけ** 解決される。
  `task.yaml`、イベント、ダッシュボード、cost/usage は解決済みの spec を持つ
  （エイリアス名は `agent_alias` として横に残る）。後からエイリアスを変えても、
  実行中の task や leader は変わらない。
- 未知のエイリアスは既知のエイリアス一覧付きのエラーになる — `fleet-agent start`、
  `fleet leader`、`fleet config set leader_agent`、formation の検証
  （`fleet formation show`）のいずれでも。
- エイリアスは現状グローバルのみ（プロジェクト単位の層はまだ無い）。

---

## macOS

zellij は `brew install zellij` でインストールする — Homebrew の bottle は curl で
取得されるため quarantine 属性が付かない。

ブラウザでダウンロードした zellij のリリースバイナリには、代わりに Apple の
`com.apple.quarantine` 属性が付く。何も対策しなければ、Gatekeeper は初回実行時に
「開発元を確認できません」という趣旨のダイアログを出してプロセスを kill する —
このダイアログの **ゴミ箱に入れる** ボタンはバイナリを削除する。`fleet preflight`
（および zellij バックエンド自体）は exec する**前**に quarantine 属性の有無を
チェックし、付いていれば実行を拒否して下記の対処法を報告する — fleet からはこの
ダイアログが出ないはず。

- **対処:** バイナリの入手元を確認したうえで、フラグを外す
  （`xattr -d com.apple.quarantine <path>`）か、ブラウザではなく `curl` で
  再ダウンロードする（curl でのダウンロードには quarantine 属性が付かない）。
  `codesign` での再署名は効かない — リリースバイナリはすでに有効な ad-hoc
  署名済みのため。回避策として Gatekeeper を無効化する
  （`spctl --master-disable`）のは避けること。
- **診断:** `xattr -l <path>` で quarantine 属性の有無を確認できる。

---

## Windows

fleet は Windows 上でネイティブに（WSL なしで）動く。tmux の代わりに
[zellij](https://zellij.dev/) を使う。leader と driver のペインは `fleet-<label>`
という名前の zellij セッション内に置かれ、各 driver ウィンドウは zellij の
**タブ** になる。それ以外 —— formation、state ファイル、`fleet` / `fleet-agent`
コマンド —— は macOS/Linux と同じである。

### 必要なもの

- **Python ≥ 3.11**（`py` ランチャー、または `PATH` 上の `python`）。
- **zellij ≥ 0.45.0** のネイティブ Windows ビルド。それより古いバージョンは
  弾かれる（0.44.x には `new-tab --no-focus` がない）。
- **Git for Windows**。
- 使用するエージェント CLI（`claude`、`codex`）が **`PATH` 上にあること**（後述）。

zellij は `winget install Zellij.Zellij` でインストールするか、
[zellij のリリース](https://github.com/zellij-org/zellij/releases) から Windows
ビルドの zip を取得して `PATH` 上のディレクトリに展開する。

### セットアップ

```powershell
git clone <this-repo-url> D:\dev\agent-fleet     # スペースを含まないパス
git config --global core.longpaths true
D:\dev\agent-fleet\fleet.cmd preflight
```

- **fleet は `fleet.cmd` / `fleet-agent.cmd` 経由で実行する**（PowerShell または
  cmd から）か、`python fleet …` として実行する。拡張子のない `fleet` /
  `fleet-agent` スクリプトは Windows では直接実行できない。`.cmd` シムは
  `py -3` を優先し、なければ `python` にフォールバックし、`PYTHONUTF8=1` を
  設定する。エージェントは `fleet-agent.cmd` を自分で呼ぶ: fleet はそのパスを
  プロンプトに埋め込む。
- **スペースを含まないパスに clone する。** `fleet-agent` のパスはプロンプトに
  クォートなしで埋め込まれるため、スペースがあるとエージェントからの呼び出しが
  壊れる。
- **`core.longpaths`** は、`fleet-state/projects/<p>/worktrees/` 配下の深い
  worktree パスが `MAX_PATH` に達するのを防ぐ。
- **エージェント CLI は `PATH` 上に置くこと。** Windows は `PATH` 内の `~` を
  展開しないので、`~/.local/bin` のようなエントリは Git Bash では効くが、
  PowerShell、cmd、zellij のペインでは効かない。代わりに実際のディレクトリ
  （例: `%USERPROFILE%\.local\bin`）を追加すること。
- **`FLEET_MUX=tmux|zellij`** でバックエンドを上書きできる（
  [マルチプレクサの選択](#マルチプレクサの選択) を参照）。デフォルトは全
  プラットフォームで zellij。

### Windows で `fleet preflight` がチェックすること

通常のチェックに加えて: zellij のバージョン（0.45.0 未満は失敗。zellij#5594 の
回避策が有効になる 0.45.0–0.45.1 では ⚠）、スペースを含まない clone パス、
`core.longpaths`（修正コマンド付き）、そして `fleet-agent.cmd` の存在。
`claude` / `codex` は解決済みの絶対パスとともに表示される。`PATH` の外
（`%USERPROFILE%\.local\bin`、または `~` で始まる `PATH` エントリ経由）でしか
見つからない CLI は ⚠ で示される。エージェントのペインからは見つからない
可能性があるためだ。

### zellij でのアタッチ

`fleet leader --attach` と `fleet attach [<task>]` は `zellij attach
fleet-<label>` を実行する。デタッチは zellij の `Ctrl o` のあと `d`。zellij の
クライアントはそれぞれ自分のフォーカスを持つので、アタッチしても他の誰かの
表示が動くことはない。ただし fleet がクライアントを特定のタブへ移動させられるのは、
そのクライアントが唯一アタッチ中のときだけである:

- 他にアタッチ中のクライアントがなければ、`fleet attach <task>` はそのタスクの
  タブに着地する。
- 他のクライアントがアタッチ中なら（例: 別のターミナルで leader を見ている）、
  fleet は代わりにタスクのタブ番号を表示するので、自分で切り替える:
  `Ctrl t` のあとその番号。

### 既知の制限

- **zellij 0.45.0–0.45.1:** クライアントが 1 つもアタッチしていない間に作られた
  タブは破棄される（[zellij#5594](https://github.com/zellij-org/zellij/issues/5594)）。
  fleet は driver タブを開く間だけ非表示のクライアントを一時的にアタッチして
  回避する（`FLEET_ZELLIJ_TEMP_CLIENT=0|1` で回避策を強制的にオフ / オンにできる）。
- **claude の workspace-trust ダイアログ。** 新しく登録したプロジェクトの最初の
  claude タスクは、claude の「Is this a project you created or one you trust?」
  ダイアログで止まる（claude は答えを repo root 単位で覚える）。fleet はこれに
  答えないが、黙って止まらないようにしてある: `fleet-agent start` と
  `fleet preflight` が事前に警告し（`~/.claude.json` を読むだけ）、それでも
  ペインがそこで止まればタスクは `awaiting_orders` になり、workspace trust
  プロンプト待ちだと通知する。repo で一度 `claude` を起動して承認するか、
  アタッチして "Yes, I trust this folder" を選べば、プロンプトは自動で配送される。
- **verify コマンドは Windows ではデフォルトで `cmd.exe` で実行される** ので、
  `verify` コマンドは cmd の構文として正しくなければならない。ただし formation で
  `verify.shell`（`bash` = Git Bash、`pwsh`、`powershell`、`sh`、`cmd`）を指定した
  場合は、そのシェルで実行される（[docs/formations.md](docs/formations.md) §2.5 を参照）。
- **zellij 上の codex はまだ検証されていない。** claude の driver は検証済み。

デスクトップ通知は Windows のトーストを使う（デフォルトで有効。無効にするには
プロジェクトの `notify.yaml` に `windows: {enabled: false}` を設定する）。
初期状態ではトーストの送信元は **「Windows PowerShell」** と表示され（PowerShell
の AppUserModelID を間借りしている）、クリックしても何も起きない。ユーザーごとに
一度、次を実行すると fleet 専用の送信元名とクリックでの attach を有効にできる:

```powershell
D:\dev\agent-fleet\fleet.cmd notify setup-windows
```

これは管理者権限不要の `HKCU` のみのレジストリキーを 2 つ書き込む:
`AppUserModelId\agent-fleet` キー（トーストが「Windows PowerShell」ではなく
「agent-fleet」と表示されるようになる）と `fleet://` URL プロトコルハンドラー
（タスクを伴う `done` / `ask` / 承認トーストをクリックすると、そのタスクの
ペインにアタッチしたターミナルが開くようになる）。`fleet.cmd notify
teardown-windows` は setup が作成したものだけを取り除く。`fleet preflight` は
setup 済みかどうかを報告する（情報表示のみで、未設定でも従来どおり動作する）。
書き込まれる内容の詳細は
[`fleet.windows_notify_setup`](src/fleet/windows_notify_setup.py) を参照。

この移植の背景となった調査と残りのフォローアップは
[docs/windows-support.md](docs/windows-support.md) にある。

---

## ライセンス

MIT。[LICENSE](./LICENSE) を参照。
