# Changelog

All notable changes to agent-fleet are recorded here. Entries are grouped by
development **Phase** (per `docs/design.md`) until the first tagged release.

## [Unreleased]

### prompt delivery ack (Issue #107)

- Replaced pane-visual submit confirmation with task-scoped `inbox_seen` ack from `fleet-agent inbox-read`.
- Driver prompts now instruct drivers to run `fleet-agent inbox-read` before other task work, and missing `inbox.md` is treated as an empty inbox for first-boot ack.

### awaiting_orders rename (Issue #108)

- Renamed the user-input wait status/event to `awaiting_orders` across state transitions, driver prompts, CLI output, dashboard rendering, docs, and tests.
- Updated the related user-facing callouts to "awaiting orders".

### formation template + `fleet init` UX (Issue #105)

- formation: `src/fleet/presets/` → `src/fleet/templates/` に移動。"preset formation" の用語を廃止し、fleet 同梱の雛形は "formation template"、project が持つ実体は "formation" と呼び分ける。
- formation: `fleet init` に `--formation` / `--no-formation` / `--non-interactive` オプションを追加。TTY では番号・名前入力の対話ピッカーを表示し、選択した formation template を `<state>/formations/` にコピーする。非 TTY (CI / pipe) では `--formation` 未指定時は formations/ 空で完了。
- formation: `fleet formation init --from <template> [--name <name>]` サブコマンドを新設。既存 project に後から formation template をコピーできる。
- formation: `fleet formation list` の見出しを "preset formations:" → "template formations:" に更新。
- formation: `fleet-agent start --formation` のデフォルトを `solo` から `None` に変更。解決ルール: 明示指定 → `<state>/formations/` から strict ロード (template fallback なし)。未指定 + 1 件のみ → 自動採用。未指定 + 空 → `leader-session.json` の agent で即興 `_leader_solo` 合成。未指定 + 複数 → 曖昧エラー。
- leader: `fleet leader` が起動時に `<state>/leader-session.json` を書くようになった。start の formation fallback がこれを読んで leader の agent で solo formation を合成する。

### prompt deliverer submit retry (Issue #98)

- Detached prompt deliverer now waits briefly after paste, sends Enter, and verifies submit via adapter-specific working markers or by checking that the prompt pointer no longer remains in the active input line.
- If the pointer is still sitting at the Codex/Claude prompt, the deliverer retries Enter a bounded number of times before failing the task with an error event.

### Codex update prompt 抑止 (Issue #93)

- Codex driver 起動時に `-c check_for_update_on_startup=false` を渡し、update prompt が driver prompt を吸い込む事故を発生源で抑止。
- `fleet preflight` に `codex-update` optional check を追加。`codex --version` と npm latest、npm global install の version を比較し、古い CLI や PATH ズレを警告する。

### topology → formation 改称 (Issue #88)

- 概念名 `topology` を `formation` に全面改称

### race formation を廃止 (2026-05-20)

**Migration note**: `race` formation preset および `candidates` shape は削除された。
Issue #29 結論 E の実装 (root 大改修 段階 1)。

- `src/fleet/presets/race.yaml` を削除
- formation の valid shape は `roles` / `stages` のみ (`candidates` は無効)
- preset は `solo` / `pair_review` / `multi_stage` の 3 つ

`candidates` を使っていた formation YAML は `roles` / `stages` に書き直すこと。

### task.yaml スキーマ刷新 (2026-05-20)

root 大改修 段階 2 (PR #46)。Issue #28/#29 結論 D の実装。

- `task.yaml` を「1 task = 1 formation、複数 stage を持つ」新スキーマに刷新
- task 作成時に formation 定義を task.yaml に展開コピー (snapshot)
- 各 stage は `status` (pending/running/done)、任意で `peer_review` / `user_approval` を持つ
- task 全体の `status` は stages から導出するキャッシュ

### `fleet spawn` → `fleet start` 改名・役割変更 (2026-05-20)

root 大改修 段階 3 (PR #47)。Issue #29 結論 C の実装。

**Migration note**: `fleet-agent spawn` は廃止。driver 起動は `fleet-agent start`。

- `fleet start <id> "<desc>" --formation T` = task 開始 (task.yaml 作成 + worktree + 最初の stage の driver 起動)
- `--role` オプション廃止 (orchestrator が stage を順に回す)
- 「指定 stage の driver を起動」を `launch_stage_driver()` に切り出し (start と orchestrator で共用)
- `save_task` を `yaml.safe_dump` に変更 — title 等に `:` `#` を含んでも task.yaml が壊れない

### orchestrator.py 新設 + done.py を role 単位に (2026-05-20)

root 大改修 段階 4 (PR #48)。Issue #29 結論 A/B の実装。

- `src/fleet/orchestrator.py` 新設 — done 駆動の state machine。daemon / polling は持たない
- `fleet-agent done --result <approved|changes-requested>` — role 単位の完了
- done が `orchestrator.advance()` を呼び、次 stage を起動 (無ければ task を completed に)

### peer_review ループ + user_approval ゲート (2026-05-20)

root 大改修 段階 5 (PR #50)。Issue #28 の実装。

- stage 属性 `peer_review` — stage 内で implement → 査読 → changes なら実装担当に戻る (上限 3 巡)
- stage 属性 `user_approval` — done 前に `fleet-agent ask` で user の明示承認を取る
- stage 内処理順序: implement → peer_review (max 3) → user_approval → stage 完了

### git を driver に寄せる (2026-05-20)

root 大改修 段階 6 (PR #51)。Issue #30 の実装。

- 作業の git (commit / push / PR / conflict 解決) は driver が行う。手順は `driver-base.md` に明記
- fleet core は worktree の作成/削除以外の git を叩かない
- `docs/design.md` §8 を「作業の git は driver、worktree 境界だけ仕組み」に更新

### root 改修 総点検 (2026-05-20)

root 大改修 段階 7 (PR #52)。

- 旧 `src/fleet/commands/spawn.py` (start.py 新設時の消し漏れ、289 行) を削除
- README.md / design.md / leader-handoff.md を新設計に整合
- preset 3 つ (solo / pair_review / multi_stage) が新スキーマであることを確認

### `fleet-agent cleanup` の通知を削除 (2026-05-20)

PR #49。`cleanup` 時の macOS / Slack 通知をやめた。task の片付け通知はノイズになり、
`ask` (user の判断が要る) の通知を埋もれさせるため。`done` / `ask` の通知は維持。

### inbox delivery + ack 機構 (2026-05-20)

leader → driver の通知を double-ended に強化。

#### A. delivery — inbox 追記後に driver pane を起こす

- `fleet-agent inbox` が inbox.md への追記・event 発火に加え、driver の tmux pane に
  通知テキスト (`[fleet] inbox に新着メッセージ。fleet-agent inbox-read で確認しろ`) を
  `send-keys` で送信するようになった。
- pane が存在しない場合 (driver 未 spawn / 既終了) は warn のみ出して inbox 追記は成功させる。
- `inbox_message` event に `inbox_ts` フィールド追加 (inbox.md ヘッダと同一タイムスタンプ)。

#### B. ack — `fleet-agent inbox-read` 新コマンド (driver-side)

- `fleet-agent inbox-read` を新設。
  - 当該タスクの `inbox.md` を stdout に出力。
  - 副作用で `inbox_seen` event を events.jsonl に append。
  - `watermark` = inbox.md の最後の `### <ISO8601>` ヘッダの値。
- driver は inbox を `cat` で直読みせず `fleet-agent inbox-read` 経由で読む。

#### C. driver-base.md rule 変更

- "check inbox each turn" → "`fleet-agent inbox-read` で読め (cat/Read 直読み禁止)"。
- delivery で起こされた driver が `fleet-agent inbox-read` を叩く流れを rule に明記。

#### D. 未読表示 — `fleet status`

- `fleet status` の task 一覧に `[unread inbox]` フラグを追加。
- 判定: 最新 `inbox_message.ts` > 最新 `inbox_seen.watermark` なら未読。

#### E. `task_context.resolve` 改善

- `FLEET_STATE_DIR` env var を state-dir 解決のフォールバックとして利用するように変更。
  cwd-based discovery が失敗したときのみ使用 (spawned pane の CWD がプロジェクト外の場合の救済)。

#### Migration note

- driver 側ルール更新: inbox は `fleet-agent inbox-read` 経由で読むこと (cat 直読み禁止)。
- `inbox_message` event に `inbox_ts` フィールドが追加された (後方互換維持、WIP スコープ)。

#### Tests

- `test_inbox_cmd.py`: delivery mock テスト 3 件追加。
- `test_inbox_read_cmd.py`: 新規 — watermark 計算 4 件 + inbox-read コマンド 6 件 = 計 10 件。
- `test_status.py`: 未読判定ロジック 6 件 + 統合テスト 1 件追加。
- `test_cli_parsers.py`: agent コマンド数 7 → 8 (inbox-read 追加)。
- Tests: 166 cases pass。

### `fleet-agent spawn` auto-paste が Enter を送信するよう修正 (2026-05-20)

- `tmux.send_keys` に空文字列を渡すと一部の tmux バージョンでエラーが発生し、
  外側の `except TmuxError` に吸い込まれて Enter が未送信のまま return していた。
- `send_keys` を修正: `text` が空の場合は最初の `_run` をスキップし、
  `enter=True` の場合は必ず Enter を送信するよう保証した。
- spawn の auto-paste パスは変更なし（`paste_buffer` → `send_keys("", enter=True)` の順序は維持）。
- テスト 6 件追加: `send_keys` の mock-based 単体テスト 4 件 + spawn auto-paste の結合テスト 2 件。

### driver-prompt を markdown テンプレートに切り出し (2026-05-20)

- `src/fleet/driver_prompt.py` に直書きされていたプロンプト本文を `docs/prompts/driver-base.md` に移動。
- `driver_prompt.py` はテンプレート読み込み + 変数差し込みのみに特化。振る舞いは不変。
- `docs/prompts/driver-base.md` を直接編集することで markdown レビューとしてプロンプト変更が可視化される。

### `fleet-agent spawn` auto-paste by default (2026-05-20)

**Breaking change** — `--auto-prompt` flag removed; auto-paste is now the default.

- `fleet-agent spawn <id> "..."` alone starts the driver immediately (no manual paste needed).
- Use `--no-auto-paste` to restore the old behaviour (preloads buffer, manual paste via `C-b ]` or `fleet-agent send-prompt`).
- Migration: remove any `fleet-agent send-prompt` calls that immediately follow `spawn` — they are now redundant.

### Phase 13 — CLI split: `fleet` + `fleet-agent` (2026-05-20)

**Breaking change** — all commands are now split across two binaries.
No backwards-compatibility aliases. One-time cutover.

#### What changed

| Before | After | Binary |
|---|---|---|
| `fleet init` | `fleet init` | `fleet` |
| `fleet preflight` | `fleet preflight` | `fleet` |
| `fleet leader` | `fleet leader` | `fleet` |
| `fleet attach` | `fleet attach` | `fleet` |
| `fleet status` | `fleet status` | `fleet` |
| `fleet log` | `fleet log` | `fleet` |
| `fleet formation` | `fleet formation` | `fleet` |
| `fleet workflow` | `fleet workflow` | `fleet` |
| `fleet spawn` | **`fleet-agent spawn`** | `fleet-agent` |
| `fleet inbox` | **`fleet-agent inbox`** | `fleet-agent` |
| `fleet send-prompt` | **`fleet-agent send-prompt`** | `fleet-agent` |
| `fleet cleanup` | **`fleet-agent cleanup`** | `fleet-agent` |
| `fleet ask` | **`fleet-agent ask`** | `fleet-agent` |
| `fleet event` | **`fleet-agent event`** | `fleet-agent` |
| `fleet done` | **`fleet-agent done`** | `fleet-agent` |

#### New file

`./fleet-agent` — shebang script at repo root, same structure as `./fleet`.
Both import the same `src/fleet/` package; entrypoint decides which parser
to build (`build_parser_user()` vs `build_parser_agent()`).

#### PATH setup decision

`fleet-agent spawn` injects `PATH=<repo>:$PATH` into the spawned tmux window's
env. Rationale: repo-local injection keeps the binary self-contained without
requiring the user to modify their shell profile, and avoids conflicts if
multiple agent-fleet repos exist on the machine.

#### Migration note for running driver / leader panes

Any pane that was spawned before this change expects the old `fleet` command.
Stop those panes manually (`fleet-agent cleanup <id>`) and re-spawn. There is
no cutover path; WIP tasks should be restarted.

### Phase 12 — `fleet log` + `fleet send-prompt` (2026-05-19)

Observability + recovery shortcuts for the spawn flow.

- `fleet log [task-id] [-n N] [--type T]` — tail `events.jsonl` with
  optional task / type filters. The `--type` flag is repeatable
  (e.g. `--type spawn --type done`).
- `fleet send-prompt <task-id>` — re-load
  `<state>/tasks/task-<id>/driver-prompt.md` into the named tmux
  buffer and paste it into the task window. Companion to
  `fleet spawn`'s default (manual-paste) behaviour.
- Tests: 130 unittest cases pass (+5 log + 3 send_prompt).

### Phase 11 — `fleet attach` + `fleet inbox` (2026-05-19)

Two everyday-use shortcuts so the leader doesn't have to memorize tmux
syntax or hand-edit `inbox.md`.

- `fleet attach [<target>]`: shortcut for
  `tmux attach -t fleet-<project>:<window>`. Target is `leader`
  (default) or a task id (`fleet attach 1` → window `task-1`).
- `fleet inbox <task-id> "<message>"`: append a timestamped block to
  `<state>/tasks/task-<id>/inbox.md` and emit an `inbox_message`
  event so the dashboard reflects it.
- Tests: 122 unittest cases pass (+4 inbox + 3 attach).

### Phase 10 — README, CI, `fleet preflight` (2026-05-19)

Project hygiene pass — anyone arriving via `git clone` now has a clear
on-ramp.

- `fleet preflight` — verify Python ≥ 3.11, tmux, git, claude, codex.
  Required tools missing → exit 1; optional tools missing → warn.
- `.github/workflows/test.yml` — GitHub Actions runs `unittest` against
  Python 3.11 / 3.12 / 3.13 on `push` and `pull_request`.
- README rewritten: quick-start walkthrough, command catalogue (project
  / leader / tasks / driver-side / configuration), repo layout, and
  state-dir layout.
- Tests: 115 unittest cases pass (+4 preflight).

### Phase 9 — `fleet cleanup` + workflow teardown (2026-05-19)

Physical teardown is split from logical completion (`fleet done`).

- `fleet cleanup [task-id] [--archive] [--force]`:
  * Runs the workflow plugin's `on_cleanup` hook.
  * Kills the task's tmux window (if any) and drops its prompt buffer.
  * Optionally archives `tasks/task-<id>/` to `tasks/_archive/task-<id>/`.
  * Refuses non-terminal statuses (`completed`/`failed`/`cancelled`)
    unless `--force` is passed.
- `git_worktree` plugin gains `on_cleanup`:
  * `git worktree remove --force` the worktree.
  * `git branch -D task/<id>` (best-effort; missing branches are
    tolerated).
- Cleanup emits a `cleanup` event and rebuilds dashboard.md (archived
  tasks disappear from `list_tasks` because `_archive/` is outside the
  `task-*` glob).
- Tests: 111 unittest cases pass (+5 cleanup + 1 git_worktree cleanup).

### Phase 8 — dashboard rebuild + heartbeat (2026-05-19)

Dashboard becomes information-dense; drivers gain a "still alive"
signal — without forge's 6-feature lifecycle daemon.

- `fleet.heartbeat`:
  - `parse_ts(ts)`, `humanize_age(seconds)` ("12s ago", "3h ago"…)
  - `last_per_task(events)` — derive "last seen" per task from
    events.jsonl. Append-only audit log means latest entry wins
    naturally; no extra state needed.
- `fleet.dashboard.render`:
  - "⚠ Awaiting orders" highlight section above the task table when
    any task has status `awaiting_orders`.
  - Task table gains **Workflow** + **Last seen** columns.
  - "Recent events (last 10)" section at the bottom.
  - Workflow shown in the header block too.
- `fleet status` — same enrichment (awaiting-orders call-out + workflow +
  "seen" age + nicer event formatting).
- `driver_prompt`: rule added — "between long tool calls, emit a
  heartbeat (`fleet-agent event emit heartbeat`)". Still under the 40-line
  bloat tripwire.
- Explicit non-goal: no daemon, no auto-cleanup. forge's lifecycle
  layer (heartbeat / liveness / tamagotchi / janitor / custodian /
  leader_context) is replaced with **"surface the data, let the human
  decide"**. (design doc §10.2)
- Tests: 105 unittest cases pass (Phase 8 adds heartbeat 9 + dashboard 5).

### Phase 7 — `fleet leader` (2026-05-19)

The user-facing entry point from design doc §3 lands.

- `fleet leader [--project P] [--agent SPEC] [--attach]`:
  Creates a detached tmux session `fleet-<project>` with a single
  `leader` window in the project root and starts the agent CLI inside.
  Default agent: `claude:sonnet`.
- Single-instance per project: if the session already exists, prints
  the attach command and exits. `--attach` execs into `tmux attach`.
- Leader window inherits `FLEET_PROJECT` and `FLEET_STATE_DIR` env
  vars; cwd is set to the project root.
- `tmux.new_session(..., cwd=..., env=...)` extended to accept both.
- Emits a `leader_start` event with the chosen agent + session name.
- Tests: 90 unittest cases pass (+3 leader tests, 2 skipped if `tmux`
  missing).

### Phase 6 — spawn robustness: tmux env + prompt buffer (2026-05-19)

Sharpens `fleet spawn` for real-world tmux use. The forge-era prompt
race ("cat | head" sometimes lands before the agent TTY is ready) is
structurally removed.

- `fleet.tmux.new_window(..., env=...)` — pass `-e KEY=VAL` so each
  spawned window inherits `FLEET_TASK_ID` and `FLEET_STATE_DIR`. Drivers
  can now call `fleet ask` / `event emit` / `done` without `--task-id`.
- `fleet.tmux.load_buffer` / `paste_buffer` / `delete_buffer` — wrap the
  tmux paste-buffer machinery.
- `fleet spawn` preloads `driver-prompt.md` into a named tmux buffer
  (`fleet-task-<id>`). Default behavior shows manual paste instructions
  (safer). `--auto-prompt [--prompt-delay SEC]` opts into the old
  auto-paste after a delay.
- `driver_prompt`: BASE updated to mention the `FLEET_*` env vars so
  the driver knows it doesn't need `--task-id`.
- Tests: 87 unittest cases pass (Phase 6 adds 4 tmux integration tests,
  skipped if `tmux` isn't on PATH). The driver-prompt bloat tripwire
  still holds (< 40 lines).

### Phase 5 — workflow plugin system + `bare` / `git_worktree` (2026-05-19)

Spawn and done now run through a plugin hook layer (design doc §8). The
core orchestrator stays development-flow-agnostic; git-specific bits
live in an opt-in plugin.

- `fleet.plugins` package:
  - `load_workflow(state_dir)` resolves `project.yaml` ➜ plugin module.
    Custom plugins under `<state>/plugins/<name>.py` shadow built-ins.
  - `run_hook(module, hook_name, ctx)` — silent no-op if the hook
    isn't defined; `ctx` is a mutable dict carrying state_dir, task_id,
    formation, role, agent, etc.
  - `list_builtin()` / `list_custom(state_dir)`.
- Built-in plugins:
  - `bare` — default no-op for non-git projects.
  - `git_worktree` — creates `<state>/worktrees/task-<id>` on branch
    `task/<id>` from the project root; overrides spawn cwd; records
    worktree + branch in `task.yaml`.
- `fleet workflow list | show <name> | set <name>` — inspect and pick
  the active workflow plugin.
- Hooks wired into existing commands:
  - `fleet spawn`: calls `on_pre_spawn` before `save_task`; merges
    `ctx['task_extra']` into the task; honors `ctx['cwd']` as the tmux
    window's working directory.
  - `fleet done`: calls `on_post_done` after status flip (failures
    warn but never block).
- Tests: 83 unittest cases pass (Phase 5 adds 14: plugins 7, workflow
  CLI 5, git_worktree 2 — the last skipped if `git` is missing).

### Phase 4 — driver communication protocol (2026-05-19)

Drivers can now report up to the user without going through the leader
(design doc §7 — "leader は介在しない").

- `fleet.task_context.resolve(...)` — figure out which task a driver-side
  CLI call is acting on, from (1) explicit `--task-id`, (2) `FLEET_TASK_ID`
  env var, (3) cwd inspection (`<state>/tasks/task-<id>/`).
- `fleet.notify` — best-effort macOS Notification Center + Slack webhook
  dispatch, configured per-project via `.fleet-state/notify.yaml`.
  Failures warn-only, never raise.
- `fleet ask "<question>"` — record `awaiting_orders` event, flip task status
  to `awaiting_orders`, append to `questions.md`, fire notification.
  Non-blocking; the driver re-checks `inbox.md` on its own cadence.
- `fleet event emit <type> [--field K=V ...]` — append arbitrary audit
  events to `events.jsonl` tagged with the current task id.
- `fleet done [task-id]` — flip task status to `completed`, emit `done`
  event. Real cleanup (worktree / branch / tmux window) is delegated to
  workflow plugins (Phase 5).
- Tests: 69 unittest cases pass total (+ Phase 4: task_context 6,
  notify 5, ask 3, event 3, done 3).

### Phase 3 — formation YAML + `fleet spawn` (2026-05-19)

`fleet` can now actually spawn a driver. End-to-end:
`fleet init` → `fleet spawn <id> "<desc>"` → claude/codex CLI lives in a
tmux window, with task state + prompt prepared on disk.

**Formation layer (3a):**
- `vendor/yaml/`: PyYAML 6.0.3 pure-Python sources, MIT license preserved.
  `./fleet` prepends `vendor/` to `sys.path`, so `import yaml` works
  without `pip install`.
- `src/fleet/presets/`: 4 stock formations — `solo`, `pair_review`,
  `multi_stage`, `race`. Cover roles / stages / candidates shapes.
- `fleet.formation`: `list_presets`, `list_custom`, `load_preset`,
  `load_custom`, `load(name, state_dir=)` (custom wins), `validate`
  (requires `name` + one of roles|stages|candidates).
- `fleet formation list` / `fleet formation show <name>` CLI.

**Spawn layer (3b):**
- `fleet.agents.parse_spec("claude:sonnet")` / `cli_command(...)` —
  resolve `vendor:model` specs into argv. claude + codex only (design
  doc §13.2).
- `fleet.driver_prompt.render(...)` — produces a compact (~30 line)
  initial prompt. Bloat tripwire test ensures it stays small.
- `fleet.tmux` — thin `subprocess` wrap around tmux session/window/keys.
  Mechanism only, no fleet vocabulary.
- `fleet spawn <id> "<description>"`:
  - Resolves formation + role + agent (with `--role` / `--agent` overrides).
  - Writes `task.yaml` (status=spawning, agent, formation, role).
  - Writes empty `inbox.md` / `outbox.md` + rendered `driver-prompt.md`.
  - Emits a `spawn` event.
  - Opens a tmux window and launches the agent CLI inside.
  - `--dry-run` skips the tmux step (used in tests / CI).
- Tests: 49 unittest cases pass total (+ Phase 3b: agents (8), driver_prompt
  (3), spawn (7)).

### Phase 2 — state writer + dashboard auto-rebuild (2026-05-19)

State updates are now race-safe and `dashboard.md` is regenerated on every write.

- `fleet.locking.atomic_write(path)` — flock-guarded context manager that
  writes via temp file + `os.replace` for atomic, all-or-nothing updates
  (design doc §5.4).
- `fleet.events.append_event(path, type, **fields)` — POSIX `O_APPEND`
  audit log helper for `events.jsonl`.
- `fleet.simple_yaml` — minimal flat `key: value` reader/writer (the real
  YAML parser lands in Phase 3 when formation files demand nesting).
- `fleet.state` extended:
  - `discover_state_dir(start)` walks parents looking for `.fleet-state/`
  - `load_project` / `save_project`
  - `list_tasks` / `load_task` / `save_task`
  - Every state write triggers `dashboard.rebuild`.
- `fleet.dashboard.render` / `rebuild` — render state into Markdown,
  marked **read-only** in the header (design doc §5.5).
- `fleet status [path] [--events N]` — print project info, task list,
  recent events.
- Tests: 22 cases pass (locking concurrency, events, state, dashboard,
  init, status).

### Phase 1 — skeleton + `fleet init` (2026-05-19)

Bootstrap the repository so a minimal `fleet init` works end-to-end.

- Repository skeleton:
  - `./fleet` — executable Python entrypoint (shebang, `git clone` and run, no `pip install`)
  - `src/fleet/` — Python package (`cli`, `state`, `commands/init`)
  - `tests/` — stdlib `unittest`-based smoke tests
  - `docs/design.md` — design document carried over from claude-forge
  - `.gitignore`, `README.md`
- Command:
  - `fleet init --name <name> [path]` — creates `.fleet-state/` with
    `project.yaml`, empty `events.jsonl`, empty `tasks/`. Rejects already-initialized
    projects and non-directory paths.
- Python 3.11+ required; no third-party dependencies (vendored libs land in later phases).
