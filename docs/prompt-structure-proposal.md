# prompt 構造 方針提案

> 作成: 2026-05-20 / driver: task-prompt-structure
> ステータス: 議論用ドラフト (実装前)
> 関連: `docs/design.md` §10.2 §11 priority 7、 `docs/prompts/driver-base.md`、
>      `src/fleet/driver_prompt.py`、 `src/fleet/commands/spawn.py`、
>      `tests/test_driver_prompt.py`、
>      `docs/role-structure-proposal.md`、 `docs/dialogue-trace-proposal.md`、
>      `docs/inbox-ack-proposal.md`、 `docs/cli-split-proposal.md`

---

## 0. TL;DR

`prompt-md-split` (PR #11) で **driver-base.md** の本文切り出しは既に終わってる。
残ってる priority 7 の論点は 「収容先」 ではなく 「政策」、 すなわち
**何を base に書いて、 何を書かないか、 どう短く保つか** だ。

ところが先行 3 つの方針提案 (role-structure / dialogue-trace / inbox-ack) は
**全部** 「driver-base.md に 1 行案内を足してほしい」 と書いている。
このまま 1 行ずつ受け入れると base は当然肥大化する。 priority 7 の本質は
**この雪だるまを止める規律を決める** ことだと判断する。

推奨は **案 D (規約 + cap + 単一ファイル)** :

- driver-base.md は **1 ファイル固定** (role 別に分岐しない)
- render() の **dynamic 注入は今の 4 変数 (`task_id / topology / role / agent`) で lock**、
  追加するときは proposal で justify する
- `docs/prompts/README.md` を新設して **何を書く / 書かない / どう増やすか** の規約を明文化
- 既存 test の **line budget を 40 → 32 に締める** (現状 22 行 + 先行 3 proposal の 3 行追加でも 25 行に収まる、 cap 32 で trip wire を 1 ノッチ厳しくする)
- role 別 prompt が **どうしても必要になったら** `docs/prompts/driver-<role>.md` を **継承** ではなく **追加** で増やす (合成は spawn 時に render が責任を持つ)、 ただし 「どうしても」 の閾値を README に書く
- 走行中 driver の prompt は spawn 時 snapshot のまま据え置く (re-render しない)
- 言語は **base 英語 / task description は user 言語** の混在を許容、 統一しない
- rule 違反検出は **構造側** (CLI 必須化 / events) で担保、 prompt 命令だけに頼らない

逆に **やらない** こと:

- role 別 prompt ファイルの先回り分岐 (案 B) — 必要が出てから増やす
- driver-prompt.md の都度 re-render (案 C 派生) — snapshot の利点を捨てる割に得が薄い
- prompt 内に rule を増やして守らせる方向 — agent は破る、 構造で縛れ
- base.md の本文を完全テンプレ化 (`{{...}}` 倍増) — dynamic injection 廃止の核思想に逆行

本書は方針合意までで止まる。 実装は次フェーズ。

---

## 1. 現状の整理

### 1.1 prompt の収容構造 (PR #11 後)

```
docs/prompts/driver-base.md    本文 (22 行、 英語、 固定文)
    ↓ _load_base()
src/fleet/driver_prompt.py
    render(task_id, description, topology_name, role, agent)
        base + "\n---\n" + front matter (4 変数) + "\n---\n\n" + description
    ↓
tasks/task-<id>/driver-prompt.md
    spawn 時 snapshot。 走行中の driver はこれを読む。
```

`driver_prompt.py` の docstring に既に書いてある:

> Kept intentionally small. Design doc §10.2 calls out claude-forge's bloated
> 1000-line driver-prompts as the root cause of boot timeouts; this module
> must resist accumulating optional context. New context belongs in a
> plugin hook, not here.

つまり 「dynamic injection は plugin hook に逃がせ、 render はここで太らせるな」
という原則は **既に code 側に書かれている**。 ただし docs 側にこの原則を明文化した
場所は無い。 結果、 提案ドキュメントを書く driver / レビューする leader が
「base に 1 行足すだけ」 を反射的に提案している。

### 1.2 driver-base.md (22 行) の内訳

| 区分 | 行 | 内容 | 削れるか |
|---|---|---|---|
| 役割宣言 | 1-2 | 「You are a fleet driver」 / 「Work it to completion」 | × 一文目は agent への文脈付け、 削ると意図が伝わらない |
| Environment | 4-7 | `FLEET_TASK_ID` / `FLEET_STATE_DIR` の説明 + 「`--task-id` 不要」 案内 | × CLI 利用前提なので必須 |
| Communication | 9-13 | inbox.md / outbox.md / ask / event の最低限 4 経路 | × どれも 「読まないと task が回らない最低限」 |
| Rules | 15-22 | dashboard 書き換え禁止 / ask 必須 / heartbeat / done | × 全部 「破ると task が完結しない」 系 |

→ 結論: **削れない**。 現状の 22 行は既に骨だけ。 priority 7 の主訴は
「行数を更に削る」 ではなく 「これ以上 grow させない規律を作る」 にある。

### 1.3 先行 proposal が要求している base.md 追記

| proposal | 追記要請 | 行数 |
|---|---|---|
| role-structure §5.2 / §5.3 | 「Your role: `$FLEET_DRIVER_ROLE` (or `fleet-agent role`)」 を Environment 末尾に | +1 |
| dialogue-trace phase 1 | (明示は弱いが) 「user の回答は `fleet-agent answer` で記録しろ」 系の rule | +1 |
| inbox-ack phase 1 | 「inbox は `fleet-agent inbox` (引数なし) 経由で読め、 `cat` 直読み禁止」 系の rule | +1 |

3 つ全部受け入れても 22 → 25 行で済む。 ここまでは健全。
だが **次の波** が来たとき (workflow plugin の hook 通知、 leader pane と
の双方向、 etc.) に同じ調子で 1 行ずつ膨らませると、 半年で 50 行 → 100 行
コースに乗る。 §10.2 の 「base prompt を 300 行以下に」 は緩い目標で、
実用上は **base が 「1 画面で読める」 (32 行 ≒ 80x24 ターミナルの上半分)** に
収めると agent 側の読み落としが少なく済む。

### 1.4 既存 test の guardrail

`tests/test_driver_prompt.py:test_keeps_prompt_under_budget` で **40 行未満**
を assert している。 今は 22 行ベース + front matter 6 行 + description 1 行 = 30 行弱
が最小ケース。 余白がやや大きい。 cap を締める余地はある。

### 1.5 dynamic injection の現境界

`render()` が差し込んでいるのは **4 変数だけ** (task_id / topology / role / agent)。
これ以上の dynamic は plugin hook (`on_pre_spawn`) 側で task description を
書き換えるか、 `task_extra` に詰めるかで処理する。 つまり code 上 「base prompt
への dynamic injection」 は実質既に 4 変数 cap が効いている。 ただしこれを
**rule として明文化していない** ので、 後で 「ちょっと変数足したい」 が通る素地はある。

### 1.6 走行中 driver の prompt 寿命

`spawn.py:174` で `dp.render(...)` の結果を `driver-prompt.md` に
**writeText** している。 これはあくまで spawn 時 snapshot で、 base.md を後で
書き換えても **走行中 driver の prompt は変わらない**。 driver pane を
attach し直しても、 既に context に乗ってる prompt は同じ。
これは feature であって bug ではない (中断・再開時のブレを防ぐ)。

### 1.7 言語の現状

- driver-base.md: 英語固定
- front matter: 英語固定 (`task id:` / `topology:` 等)
- task description: user が書く部分、 日本語が多い
- inbox / outbox: 中身は free-form、 leader-handoff.md が日本語なので日本語混在

claude / codex CLI は両方多言語対応、 prompt 内の混在で性能が大きく落ちる
証拠は無い。 user 側の自然な言語で task を書ける方が重要なので、 統一せず
混在許容のままが妥当。

---

## 2. 観点 (本提案で詰める論点)

タスク依頼で提示された 8 つの論点を 1 つずつ整理する。

### 2.1 base prompt の最小化

§1.2 で見たとおり 22 行は既に骨だけ。 これ以上削るより **これ以上 grow させない**
方向に倒す。 ガード:

- 行数 cap を 40 → **32** に締める (現実的に届く範囲、 trip wire として有効)
- 「base に追加するときは proposal を出して justify する」 を README に書く
- 個別追加要請を 「本当に base に書く必要があるか / 構造で担保できないか / task description に逃がせないか」 で逐一判定する

### 2.2 dynamic injection の境界

候補:

- **A**: 今の 4 変数 (task_id / topology / role / agent) で lock、 追加禁止
- **B**: 増やしてもよいが render() の引数として明示しろ、 `{{...}}` 文字列差し替えは禁止
- **C**: plugin hook 経由なら base 直接差し込みも可

A が一番厳しい、 B はちょっと緩い、 C は 「dynamic injection 廃止」 の趣旨と微妙にずれる。

→ **A を採用**。 ただし 「追加禁止」 ではなく 「追加するなら proposal」 とする
(完全 freeze は将来の正当な拡張をも止める)。 `driver_prompt.py` の docstring に
これを書き加える。

「あるなら base に最初から書く、 無いなら触らない」 という原則は採用する。
具体的には:

- 「全 driver で常に true」 な事実 → base.md
- 「topology / role / agent で変わる」 事実 → render の 4 変数 (front matter)
- 「task ごとに違う」 事実 → task description (user/leader が書く)
- 「workflow plugin が決める」 事実 → `task_extra` 経由で description に追記

これで **dynamic を base に injection したくなる動機が消える**。

### 2.3 role 別の prompt 分岐

candidates:

- 案 1: base.md 1 つ、 role 別の差は task description に書く
- 案 2: `docs/prompts/driver-base.md` (共通) + `docs/prompts/driver-<role>.md` (role 固有) を render が **結合**
- 案 3: 全 role 個別ファイル (`driver-implementer.md` / `driver-reviewer.md`)

判断材料:

- 現状 role が増えるかは未定。 [[role-structure-proposal]] では値域 fix せず、
  custom topology で project が任意に決められる方針
- 「role 別 prompt が必要」 になる根拠が今ない。 implementer / reviewer の
  差は 「task description で 『お前は reviewer だ。 ○○ を review しろ』 と
  書く」 で 90% 賄える
- 案 3 は base を捨てる方向で、 共通 rule の重複が出る (DRY 破綻)

→ **案 1 を default、 案 2 を escape hatch として README に書く**。
今は base 1 ファイル、 役割固有は task description で渡す。 もし将来
「特定 role に常に追加したい instruction が出てきた」 ら、 案 2 で
`driver-<role>.md` を **追加**、 render を `base + role 固有 + front matter +
description` の順に結合する。 ただし採用閾値を README に書く
(「3 task 以上で同じ instruction を反復したら案 2 を検討」 等)。

### 2.4 prompt 規約の文書化場所

candidates:

- A: `docs/prompts/README.md`
- B: `docs/design.md` の独立節 (§11 priority 7 を確定節に昇格)
- C: `docs/prompts/driver-base.md` 冒頭 (HTML コメント)

評価:

- A は読みやすい、 prompts/ 直下にいるので 「prompt を触ろうとした人」 が
  まず読む。 grep 性も良い
- B は設計議論なら適切、 だが運用ガイドラインの細目までは design.md に
  入れたくない (design.md が肥える)
- C は base.md 自体を汚す、 render が prompt 末尾に front matter を付ける
  ので **HTML コメントが driver に流れる** (大した害は無いが noise)

→ **A を採用**。 README に書いて、 design.md §11 priority 7 から
「→ `docs/prompts/README.md` 参照」 とだけ書いて確定節化する。

### 2.5 言語 / トーンの統一

§1.7 で見たとおり混在 OK。 ただし base.md は英語固定の方が:

- code / docstring と並んだとき視覚的に区別しやすい
- 将来 non-Japanese ユーザーが触る場合も差分が小さい
- claude / codex の CLI 自体が英語前提の help / error を返すので、
  base 英語の方が同じ語彙圏に収まる

→ **base.md は英語固定**、 task description / inbox / outbox は free-form
(user 言語) のまま。 README にも明記。

### 2.6 rule 違反検出

「driver は `fleet-agent ask` を必ず使え」 等の rule が守られなかった時に
prompt 側で検出するのは原理的に難しい (agent が prompt rule を破ったとき、
prompt 自身は破ったことを知らない)。

§7.1 で既に書かれてる方針 (「pane に書いただけではどこにも届かない構造的
圧力」) が正解。 つまり:

- **「使わないと届かない」 を CLI 側で物理的に作る** (構造担保)
- prompt 側の rule は **agent への気づかせ** だけが責務、 violation 検出は
  期待しない

具体的に分担すると:

| ケース | prompt 側 (base.md) | 構造側 (CLI / events / dashboard) |
|---|---|---|
| user input 取得 | 「question を pane に書くだけでは届かない」 と教える | `fleet-agent ask` 以外でユーザー通知が走らない |
| heartbeat | 「長 tool call の合間に emit せよ」 と教える | dashboard が `Last seen` 列で fresh/stale を視覚化、 user が気づく |
| 完了通知 | 「終わったら `fleet-agent done`」 と教える | done が呼ばれないと task status が `in_progress` のまま、 leader が気づく |
| inbox 読了 | (将来) 「inbox は CLI 経由で読め」 と 1 行教える | inbox-ack proposal の `inbox_seen` event で leader が確認可能 |
| dialogue 記録 | (将来) 「user 回答は `fleet-agent answer` で」 と 1 行教える | answer 経由でない user 発話は records に残らない、 archive 時に気づく |

→ **prompt 側の rule は最低限の orientation のみ、 検出 / 強制は構造側**。
これは inbox-ack / dialogue-trace の方針と一致する。

### 2.7 prompt のバージョニング

driver-prompt.md は spawn 時 snapshot。 base.md を変更しても走行中 driver は
古い prompt のまま。 これは:

- 中断・再開時に prompt が変わらない安心感 (feature)
- 走行中 task が base.md 更新で振る舞いを変えない予測可能性 (feature)

両方望ましいので **snapshot のままで OK**、 都度 re-render はしない。

ただし leader が明示的に 「新 base で再 spawn したい」 場合のために、
`fleet-agent send-prompt` が現状 driver-prompt.md を再注入する (これは
snapshot を **再貼り付け** する仕組み、 re-render ではない)。 これは
スコープ外 (`docs/cli-split-proposal.md` で扱う)。

`docs/prompts/README.md` には 「driver-prompt.md は snapshot、 base.md 変更は
**次 spawn 以降のみ反映**」 と明記する。

### 2.8 `docs/prompts/` 配下の整理

candidates:

- A: `docs/prompts/` には driver-base.md だけ。 leader 用 prompt
  (leader-handoff.md) は docs/ 直下に置く現状を維持
- B: `docs/prompts/` 配下に leader 用も移動 (`docs/prompts/leader-handoff.md`)
- C: 役割で深掘り (`docs/prompts/driver/base.md`、 `docs/prompts/leader/handoff.md`)

評価:

- A: 現状維持、 leader-handoff.md は手動貼り付け前提なので使う側 (leader pane)
  からの読み込み path がべったり書いてある。 移動は破壊変更
- B: prompts/ に集約すれば 「prompt を探す人」 の発見性は上がる、 ただし
  leader-handoff.md は **leader が手動で attach 時に貼る** だけで spawn パイプ
  ラインを通らないので、 driver-base.md と性質が違う
- C: 早すぎる構造化。 ファイル数が少ない (現状 1 つ) のに階層を切る理由が薄い

→ **A を採用** (現状維持)。 ただし README で 「`docs/prompts/` は **spawn 時に
  agent へ自動投入される prompt** の置き場、 手動投入 prompt (leader-handoff)
  は `docs/` 直下」 と性質の違いを明記する。

命名規約は:

- `driver-base.md` — 全 driver 共通のベース
- `driver-<role>.md` — 将来 role 固有 prompt が必要になった場合 (現状なし)
- `driver-<workflow>.md` — workflow plugin が独自 prompt を持つ場合 (現状なし、 backlog)

leader 用 prompt は `docs/leader-handoff.md` を維持。 必要が出れば
`docs/prompts/leader-base.md` に移すが、 leader は spawn パイプライン外なので
急がない。

---

## 3. 案の比較

### 案 A. driver-base.md 1 ファイル固定、 role 別は task description のみ

- driver-base.md は 1 つ、 role 別の指示は task description 側で書く
- role-structure-proposal の handoff ガイドライン (「description に role を散文で書かない」)
  と矛盾するように見えるが、 「ガイドラインの趣旨は **role 名を散文で書かない** こと、
  role 固有の作業指示は OK」 と整理すれば衝突しない
- メリット: 構造が単純、 base が 1 ファイルで grep / diff しやすい
- メリット: role 増減で prompt ファイルを増やさないので脱メンテ
- デメリット: 「全 reviewer に共通の review チェックリスト」 のような role 共通
  instruction を書く場所がない

### 案 B. driver-base.md (共通) + driver-<role>.md (role 固有) を render が結合

- 案 A の発展、 base + role 固有を spawn 時に結合
- メリット: role 共通 instruction の置き場が明確
- メリット: 案 A から拡張可能 (後付け OK)
- デメリット: 結合順序や front matter の挟み方の仕様化が要る
- デメリット: 「base にも role にも書ける」 状態は規律が無いと混乱する
- デメリット: 現状 role 別 instruction の need が立証されてない (YAGNI 寄り)

### 案 C. driver-prompt.md を都度 re-render

- snapshot をやめて、 driver pane が context を読み直すたびに base.md 最新を反映
- メリット: base.md 更新が即時反映
- デメリット: 走行中 task の振る舞いが base.md 編集で変わる (副作用大)
- デメリット: render の呼び出しタイミング設計が要る (毎 turn 走らせる? 起動時?)
- デメリット: claude / codex の context window は固定、 二重に読ませると無駄
- デメリット: snapshot の安心感を失う (中断 / 再開時にずれる)

### 案 D. 規約 + cap + 単一ファイル (推奨)

- driver-base.md は 1 ファイル固定、 役割別は task description (= 案 A の path)
- 将来 role 別が必要になったら案 B に escape できる余地は残す (README に書く)
- `render()` の dynamic 変数を **4 で freeze、 追加は proposal で justify**
- `docs/prompts/README.md` を新設し、 base に書く / 書かない / 増やし方 / cap /
  言語規約 / バージョニングを明文化
- test の line budget を 40 → 32 に締める
- 走行中 driver の prompt は snapshot のまま据え置く (案 C 不採用)
- rule 違反検出は構造側に任せ、 prompt の rule は最小化

### 比較表

| 軸 | A: 1 ファイル | B: base + role | C: 都度 re-render | **D: 規約 + cap (推奨)** |
|---|---|---|---|---|
| §10.2 (injection 廃止) 整合 | ◎ | ○ | ✗ (render hot path 化) | ◎ |
| 実装コスト | 極小 | 中 | 大 | 小 (docs 主体) |
| role 共通指示の置き場 | △ description | ◎ role md | ○ | △ (escape hatch あり) |
| base 肥大化耐性 | △ (規律次第) | △ (規律次第) | ✗ | ◎ (cap + 規約) |
| snapshot 安心感 | ◎ | ◎ | ✗ | ◎ |
| 言語 / トーン | ◎ | ○ | ◎ | ◎ |
| 先行 3 proposal の +1 行追記吸収 | ○ | ○ | ○ | ◎ (cap 内で測定して通す) |
| 走行中 task への影響 | なし | なし | あり (副作用) | なし |
| YAGNI 度 | ◎ | ✗ (role 別 need 未立証) | ✗ | ◎ |
| 将来 role 別 / workflow 別への余地 | △ (要拡張) | ◎ (既に対応) | ○ | ◎ (escape hatch 文書化済み) |

却下: 案 C (snapshot の利点を捨てる、 §10.2 の hot path 化リスク)、 案 B 単独
(role 別 need が未立証、 先回り構造化)。
案 D (案 A 路線 + 規約 + cap + 案 B への escape hatch) を採用。

---

## 4. 推奨

**案 D (規約 + cap + 単一ファイル、 案 B への escape hatch あり)** を推奨。

### 4.1 全体像

```
docs/prompts/
  README.md          # 規約 (新規)
  driver-base.md     # 1 ファイル固定、 全 driver 共通
  driver-<role>.md   # (将来 escape hatch、 現状なし)

src/fleet/driver_prompt.py
  render(task_id, description, topology_name, role, agent)
    # dynamic 変数は 4 つで freeze
    # 追加は proposal で justify
    # docstring に上記の cap を明記

tests/test_driver_prompt.py
  test_keeps_prompt_under_budget
    # 40 → 32 に締める

docs/design.md §11 priority 7
  # 「→ docs/prompts/README.md 参照」 のリンクのみ、 確定節に昇格
```

### 4.2 `docs/prompts/README.md` の骨子

README に書く規約 (概要、 実装フェーズで文面詰め):

1. **目的**: spawn 時に agent CLI に投入する prompt の置き場と規約
2. **配下のファイル**:
   - `driver-base.md` — 全 driver 共通、 1 ファイル固定、 英語
   - `driver-<role>.md` — escape hatch (現状未使用)、 採用閾値あり
3. **base に書くもの**:
   - 全 driver で常に true な事実 (環境変数 / CLI 経路 / 必須 rule)
   - 行数 cap: **32 行**
   - 言語: 英語
4. **base に書かないもの**:
   - role / topology / agent で変わる事実 → render の front matter (4 変数)
   - task ごとに違う事実 → task description (leader/user が書く)
   - workflow plugin が決める事実 → `task_extra` 経由
   - 励まし / トーン / personality (claude-forge ghost dream 路線、 §10.2 で廃止)
5. **dynamic injection 境界**:
   - render() に dynamic 変数を追加するときは proposal で justify
   - 現在の 4 変数 (task_id / topology / role / agent) で freeze
   - plugin hook (`on_pre_spawn`) で description を書き換える方が望ましい
6. **追記要請の判定基準**:
   - 「本当に base に書く必要があるか」 — 構造で担保できないか確認 (§2.6)
   - 「役割 / topology で変わるか」 — 変わるなら front matter / description へ
   - 「task ごとに違うか」 — task description へ
   - これらを通った場合のみ base.md に追記、 cap 32 を超えるなら escape hatch
     (案 B 路線) を検討
7. **escape hatch (driver-<role>.md)** の採用閾値:
   - 「同じ instruction を 3 task 以上で繰り返したら」 を目安にする
   - 採用時は render を `base + role 固有 + front matter + description` の順で結合
8. **言語規約**: base.md は英語、 task description は user 言語 OK、 混在許容
9. **バージョニング**: driver-prompt.md は spawn 時 snapshot、 base.md 更新は
   次 spawn 以降のみ反映、 走行中 driver は触らない
10. **rule 違反検出**: prompt の rule は orientation のみ、 検出 / 強制は CLI
    必須化 / events / dashboard 側で構造的に担保

### 4.3 line budget の締め

`tests/test_driver_prompt.py::test_keeps_prompt_under_budget` を 40 → **32**
に変更する。 現状ベース 22 行 + 先行 3 proposal の +3 行 = 25 行で収まる見込み。
更に +7 行の余裕がある cap として、 次の波が来たときの trip wire になる。

### 4.4 driver_prompt.py docstring 更新

```python
"""Build the initial prompt that a freshly-spawned driver reads.

Kept intentionally small. Design doc §10.2 calls out claude-forge's bloated
1000-line driver-prompts as the root cause of boot timeouts; this module
must resist accumulating optional context. New context belongs in a
plugin hook, not here.

Dynamic injection budget (frozen at the values below):
  - task_id / topology / role / agent  (front matter)
Adding a new variable here requires a proposal under docs/prompts/README.md.
"""
```

### 4.5 先行 3 proposal との同期

各 proposal が要求している 「base.md +1 行」 は、 本提案の案 D の cap 内で
吸収する。 順序は問わないが、 1 PR にまとめる方が cap が見えて健全:

| 先行 proposal | base.md 追記内容 (案) | 設置場所 |
|---|---|---|
| role-structure | `Your role: $FLEET_DRIVER_ROLE (or fleet-agent role).` | Environment 末尾 |
| inbox-ack phase 1 | Communication 内 inbox 行を 「read via `fleet-agent inbox` (no args); raw `cat` is not tracked.」 に書き換え | 既存行の更新で +0 行も可 |
| dialogue-trace phase 1 | Rules に 「Record user answers via `fleet-agent answer`.」 | Rules 末尾 |

書き換えで吸収できる項目もあるので、 +3 行未満で済む可能性が高い。

---

## 5. 移行戦略

破壊変更なし。 段階的に積めば走行中 task に影響なし。

| 段階 | 変更 | 影響 |
|---|---|---|
| 0 (現状) | base.md 22 行、 cap 40、 README なし、 規約は docstring 1 行のみ | base に「ちょっと足したい」 が通る素地あり |
| 1 | `docs/prompts/README.md` 新設 (規約 §4.2) | 後続提案の判定基準が明確化 |
| 2 | test の line budget を 40 → 32 に変更 | trip wire 強化、 cap 内なら CI green |
| 3 | `driver_prompt.py` docstring に dynamic 変数 freeze を明記 | code 側にも規律が残る |
| 4 | design.md §11 priority 7 を確定節化、 README へリンク | open question から確定方針へ |
| 5 | 先行 3 proposal の base.md 追記を 1 PR で吸収 (cap 内で測定) | base.md は 25 行前後に着地、 cap 32 まで余裕 7 行 |

段階 1-4 を 1 PR にまとめる想定 (docs + test 微調整のみ)。 段階 5 は各先行
proposal の実装 PR に組み込む。

走行中 driver pane について: 段階 1-4 は走行中の prompt を変えない (snapshot)、
段階 5 も新規 spawn 以降のみ反映。 影響なし。

---

## 6. open questions

実装フェーズで決める / コメント募集:

1. **line budget cap 32 の妥当性**: 現状 22 行 + 先行 +3 で 25 行、 cap 32 で
   余裕 7 行。 将来の正当な追加で破る可能性はある。 破ったら都度 escape hatch
   (案 B) を発動する運用で OK か、 cap を 40 に戻すかは議論余地
2. **escape hatch 採用閾値 「3 task 以上で繰り返したら」 の数字**: 経験則、
   実運用が回り出してから調整。 まずは目安として置く
3. **render の dynamic 変数 freeze を 「proposal で justify」 する手続き**:
   軽い追加 (例: workflow 名を front matter に追加) でも proposal を要求するか、
   docstring の 「TODO で 1 行コメント残せば OK」 までは緩めるか
4. **driver-<role>.md (escape hatch) の結合順序**: base + role 固有 + front matter +
   description で問題ないか。 role 固有を base の前に置くべきケース (override) は
   現状想定しないが、 想定外の用途が出たとき再検討
5. **base.md を非英語化する要件**: non-Japanese user / 海外コントリビューター
   が増えたら i18n 検討の余地。 今は英語固定でほぼ問題ない見込み
6. **leader 用 prompt (leader-handoff.md) の prompts/ 配下移動**: §2.8 で
   現状維持にしたが、 leader pane の attach フロー (`fleet leader --attach`) が
   spawn パイプラインに統合される将来があれば移動する。 今は急がない
7. **走行中 driver の prompt re-render を 「明示要求時のみ」 許す API**:
   `fleet-agent send-prompt --re-render` のような escape hatch を入れるか。
   現状 `send-prompt` は snapshot 再貼り付けだが、 base.md 更新を即時反映したい
   局所ケースが出てきたら検討
8. **`docs/prompts/README.md` を 「規約」 ではなく 「ADR / RFC」 形式にするか**:
   将来複数の prompt 関連 ADR が並んだとき `docs/prompts/adr/` に分けるかは TBD
9. **rule 違反検出を 「構造側で担保」 と書いたが、 全 rule が構造化できるわけでは
   ない**: 例えば 「dashboard.md を書き換えるな」 を物理的に防ぐには
   ファイル権限 / git ignore 等が要る。 prompt rule + ベストエフォート構造の
   ハイブリッドで現状は受け入れる
10. **言語混在の影響評価**: claude / codex の prompt quality に対する英 / 日
    混在の影響を計測したい。 §1.7 では証拠なしと書いたが、 dogfooding データが
    溜まったら再確認

---

## 7. anti-scope

本書で扱わないこと:

- **実装**。 本書は方針合意までで止まる
- 先行 3 proposal (role-structure / dialogue-trace / inbox-ack) の中身。 各々
  別 driver の領分、 本書は 「base.md への追記要請を cap 内で吸収する」 ところ
  までしか触らない
- driver-prompt.md / driver-base.md の **書式仕様** (markdown / yaml / plain text)
  — 現状 markdown 維持、 構造化が必要になったら別途 ADR
- workflow plugin が prompt を流し込む API 設計 — `task_extra` で description
  に追記する経路はあるが、 「workflow 専用 prompt ファイル」 の規約は §11
  priority 8 (workflow plugin 具体) の領分
- leader-handoff.md の構造化 — 別系統 (手動投入 prompt)、 必要なら別 ADR
- driver-prompt.md の re-render API — open question #7、 必要が出たら別タスク
- prompt の i18n / 非英語化 — open question #5、 海外利用が増えてから
- prompt 内容に対する CI 静的解析 (lint) — overkill、 README + test cap で足りる
- claude-forge の dna / personality / ghost dream の **可否ではなく方針** —
  §10.2 で既に廃止確定、 本書は方針を裏切らないところまでしか触らない

---

## 8. 議論履歴

- 2026-05-20 初稿: 案 D (規約 + cap + 単一ファイル、 escape hatch あり) を推奨。
  「収容構造」 は既に `prompt-md-split` (PR #11) で整っている前提で、 残りの
  論点は **政策 (何を書く / 書かない / どう grow させない)** に絞った。
  driver-base.md は 1 ファイル / 英語固定 / 32 行 cap、 render の dynamic 変数は
  4 つで freeze、 規約は `docs/prompts/README.md` に集約。 先行 3 proposal
  (role-structure / dialogue-trace / inbox-ack) の +1 行追記要請は cap 内で
  吸収する。 走行中 driver の prompt は snapshot のまま据え置き (案 C 不採用)。
  §10.2 「dynamic prompt injection 廃止」 の核思想は崩さず、 むしろ規約として
  明文化することで雪だるま防止に効かせる方向。 §11 priority 7 に正面から取り組み、
  priority 1-6 や先行 4 proposal とは方向衝突しない範囲で整理。
