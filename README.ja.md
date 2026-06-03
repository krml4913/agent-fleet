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

## チュートリアル: clone から最初のタスクまで

このウォークスルーは、新規 checkout から、観察したり介入したりできる稼働中の
タスクまでを案内する。コマンドは agent-fleet を `~/dev/agent-fleet` に clone した
前提なので、パスは自分の環境に合わせて調整すること。

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
`agent-fleet/fleet-state/projects/trial/` 配下に作成する。formation テンプレートの
コピーも提案される。`--formation solo,pair_review` を渡せば（または
`--no-formation` で）対話的なピッカーをスキップできる。残りのコマンドは
プロジェクトディレクトリ内から実行できる。fleet は cwd からプロジェクト名を
解決する。

### 3. workspace モードを選ぶ（任意）

デフォルトではタスクはその場で実行される。各タスクに専用の git branch/worktree を
与えるには:

```bash
~/dev/agent-fleet/fleet workspace set worktree
~/dev/agent-fleet/fleet workspace list   # アクティブなモードを確認
```

`worktree` の場合、`fleet-agent start` は、branch が既知の upstream より遅れて
いると警告する（ただし続行する）。fetch は決して行わず、オフラインでの start を
ブロックすることもない。

### 4. leader を起動する

```bash
~/dev/agent-fleet/fleet leader --attach
```

これは `fleet-trial` という tmux セッションを作成し、その中で leader エージェント
（デフォルトは `claude:opus`）を動かし、フォアグラウンドでアタッチする。セッションは
プロジェクトごとに単一インスタンスで、既に存在する場合は fleet がアタッチ用の
コマンドを表示するだけである。エージェントは `--agent claude:sonnet` などで
上書きできる。

これで通常の tmux セッションに入った状態になる。いつでも `C-b d` でデタッチでき、
leader は動き続ける。

### 5. タスクをディスパッチする

通常は *チャットで leader に欲しいものを伝え*、leader が代わりに
`fleet-agent start` を発行する。仕組みを直接見るには、2 つ目のシェルから自分で
実行する:

```bash
cd /tmp/trial
~/dev/agent-fleet/fleet-agent start 1 "Implement a hello-world script." --formation solo
```

これはタスク state を書き込み、`driver-prompt.md` をレンダリングし、最初の stage の
driver を動かす新しい tmux ウィンドウを開き、（デフォルトでは）エージェントの準備が
できたらプロンプトへのポインタをペインに自動ペーストする。team の形は
`--formation`（`solo`、`pair_review`、`multi_stage`、または任意のカスタム）で選び、
最初の stage のエージェントは `--agent` で上書きする。長い説明をインラインではなく
ファイルから渡すには `--prompt-file PATH` を使う。

### 6. 進捗を見る

プロジェクト内の任意のシェルから:

```bash
~/dev/agent-fleet/fleet status                 # プロジェクト + タスク一覧 + 直近のイベント
~/dev/agent-fleet/fleet log 1                   # このタスクのイベントを tail
cat fleet-state/projects/trial/dashboard.md     # 人間が読めるロールアップ
```

`status` は各タスクの状態（`in_progress`、`awaiting_orders`、`done` …）と最新の
イベントを表示する。`dashboard.md` は自動で再生成され、開きっぱなしにしておくと
一目で状況を把握できる良いビューになる。

### 7. driver にアタッチして介入する

これがこの体験の核心である。driver の肩越しに覗いたり、引き継いだりするには:

```bash
~/dev/agent-fleet/fleet attach 1      # タスク 1 の driver ペインにアタッチ
~/dev/agent-fleet/fleet attach        # leader にアタッチ（デフォルトターゲット）
```

エージェントのペインに直接降り立つ。出力を読み、入力し、軌道を修正できる。これは
ライブのエージェントセッションである。終わったら `C-b d` でデタッチする。アタッチ
する代わりに driver へ非同期のメモを残すには、leader が inbox にメッセージを
投げ込める:

```bash
~/dev/agent-fleet/fleet-agent inbox 1 "Use argparse, not sys.argv parsing."
```

### 8. 質問と承認ゲートに答える

driver があなたを必要とするときは `fleet-agent ask` を呼び、タスクを
`awaiting_orders` に切り替えて通知を発火する。ペインの出力だけではあなたに届かない。
`user_approval` ゲートを持つ formation は、stage が完了したときに同じように一時停止
する。判断を下すのはあなたで、**それを中継するのは leader** である（leader は決して
自分で承認しない）:

```bash
~/dev/agent-fleet/fleet-agent approve 1     # 保留中のゲートを承認
~/dev/agent-fleet/fleet-agent reject 1      # 却下。stage は作業に戻る
```

`pair_review` formation では、implementer が自動的に AI reviewer に引き継ぐ。あなたが
必要なのは最終のユーザー承認ゲートだけである。

### 9. 終了して片付ける

タスクが完了したら、それを撤去する（任意で state をアーカイブする）:

```bash
~/dev/agent-fleet/fleet-agent cleanup 1 --archive
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
