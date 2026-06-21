# Formation Guide

> A practical guide for **reading and editing formations**, aimed at leaders
> (claude / codex). For design rationale and finalized decisions, see
> `docs/design.md` §6 — this doc is about how to *use* them.
>
> Audience: leaders only. Users do not edit formations directly; they ask the
> leader to do it. The leader (claude or codex) reads this doc and applies
> the edit.

---

## 1. What is a formation?

A formation is a **YAML definition of "who works a task and how"**.

When you run `fleet-agent start <task-id> --formation <name>`, the orchestrator
walks the formation's `stages` and launches drivers in order. Human approval
points (`user_approval`) and AI peer review (`peer_review`) are expressed in
the same file.

### Template vs. formation (finalized in Issue #105)

| Kind | Path | Role |
|---|---|---|
| **formation template** | `src/fleet/templates/<name>.yaml` (shipped with fleet) | Starter file. Not directly executable. Used as the source for `fleet formation init --from`. |
| **formation** | `<state>/formations/<name>.yaml` (per project) | The runtime source of truth. The orchestrator only resolves these. |

- Fleet ships three templates: `solo`, `pair_review`, `multi_stage`. Do not edit them in place.
- Once copied, a template and the resulting formation are **independent** — there is no inheritance or follow-up.
- A project's formations can be edited freely: swap agents, add stages, drop gates, etc.
- To rebuild from scratch: `rm <state>/formations/<name>.yaml && fleet formation init --from <template>`.

---

## 2. YAML schema

### 2.1 Top level

| field | required | type | description |
|---|---|---|---|
| `name` | yes | string | Formation identifier. Must match the file's stem. |
| `description` | optional | string | Human-readable summary. |
| `stages` | yes | list (≥ 1) | List of stage objects. |

### 2.2 `stages[]` (per stage)

| field | required | type | description |
|---|---|---|---|
| `role` | yes | string | The driver's role name (e.g. `driver`, `implementer`, `designer`, `code-reviewer`). |
| `agent` | optional | string | Agent to launch (e.g. `claude:sonnet`). Falls back to the value passed via `--agent`. |
| `peer_review` | optional | mapping | Nested AI review block (§2.5). |
| `user_approval` | optional | string \| mapping | Human approval gate (§2.6). |

### 2.3 `role` values

`role` is just the **driver's role name**. Fleet core does not enforce an enum
— any string is valid. Conventional values:

- `driver` — generic role for solo formations. Picks up `docs/prompts/roles/driver.md` as a role fragment.
- `implementer` — does the implementation work.
- `designer` — does the design work.
- `code-reviewer` — typical value for `peer_review.role`.

The role name participates in driver-prompt composition: if
`docs/prompts/roles/<role>.md` exists, it is included as a role fragment
(silently ignored if it does not). When introducing a new role, drop in a
matching `<role>.md` fragment so the driver knows what is expected.

### 2.4 `agent` spec

Format: `vendor:model`. MVP supports **claude and codex only** (see
`design.md` §13.2).

| example | meaning |
|---|---|
| `claude:opus` | claude vendor, opus model |
| `claude:sonnet` | claude vendor, sonnet model |
| `codex:gpt-5.5` | codex vendor, gpt-5.5 model |

Resolution order (`fleet-agent start`):

1. If the stage's `agent:` is set, use it.
2. Otherwise use the value from `--agent`.
3. Otherwise error: `no agent for role <role>; pass --agent or set one in the formation`.

`peer_review.agent` follows a different fallback chain (§2.5):
**`peer_review.agent` → the stage's `agent` → `claude:sonnet`**.

### 2.5 `peer_review` (nested AI review)

```yaml
peer_review:
  role: code-reviewer    # required: reviewer's role name
  agent: claude:opus     # optional: defaults to the stage agent, then claude:sonnet
```

Execution order inside a stage:

```
implement → peer_review loop (max 3 iter) → user_approval gate (if any) → stage done
```

- When the implementer calls `fleet-agent done --result approved`, the reviewer pane is launched (or woken via inbox handoff).
- Reviewer `--result approved` → fall through to the next gate / next stage.
- Reviewer `--result changes-requested` → inbox handoff to the implementer, iteration counter +1.
- After 3 iterations the task transitions to `awaiting_orders` and the user is escalated.

Within a stage, both the implementer and reviewer agent panes are **kept
alive** across iterations (`design.md` §6.2). This preserves the agent's
context between rounds.

### 2.6 `user_approval` (human approval gate)

Two equivalent forms:

```yaml
# shorthand (string)
user_approval: required    # or "optional"

# full form (mapping)
user_approval:
  required: true           # or false
```

Behavior:

- When a stage with `required: true` finishes, the task transitions to `awaiting_orders`.
- The user tells the leader whether to approve or reject, and the leader relays this via `fleet-agent approve <id>` / `fleet-agent reject <id>`.
- On reject, the stage returns to implementation (if there is a `peer_review`, the implementer pane is woken).

The approval call belongs to the user — **the leader does not self-approve**
(see the `user-approval-gate` memory). A driver calling `fleet-agent done` at
the gate does **not** settle it; the gate moves only on `fleet-agent
approve`/`reject`.

---

## 3. Shipped templates

### 3.1 `solo` — one driver, end to end

```yaml
name: solo
description: One driver works the task end-to-end.
stages:
  - role: driver
    agent: claude:sonnet
```

- One stage, one driver. No review, no approval gates.
- Suitable for prototypes, lightweight tasks, or immediate delegations from the leader.
- `fleet-agent done --result approved` completes the task.

### 3.2 `pair_review` — implementer + AI review + user sign-off

```yaml
name: pair_review
description: Implementer with AI peer review; the user has the final say.
stages:
  - role: implementer
    agent: codex:gpt-5.5
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required
```

- Implementer (codex) → reviewer (claude opus) → user approval, all in one stage.
- This is the showcase multi-vendor formation (`multi-vendor-is-core` memory).
- Reviewer iterations cap at 3; the user is escalated if exceeded.

### 3.3 `multi_stage` — design → implement, with gates at every step

```yaml
name: multi_stage
description: Sequential pipeline with user approval at each stage.
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

- Design stage (claude opus) → user approval → implementation stage (claude sonnet + review + user approval).
- Use this when you want a clear separation between design and implementation for a larger task.

---

## 4. Common edits (leader cookbook)

> Before any edit, run `fleet formation show <name>` to see the current
> contents. After editing, run it again to validate.

### 4.1 Swap an agent (e.g. claude → codex on a rate limit)

```yaml
# before
- role: driver
  agent: claude:sonnet

# after
- role: driver
  agent: codex:gpt-5.5
```

Change only the `agent:` line; keep the stage structure intact.

### 4.2 Add a code reviewer to a solo formation

Promote `solo` by adding a `peer_review`:

```yaml
name: solo
stages:
  - role: driver
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
      agent: claude:opus
```

(Keep the same `name`, or save under a different file if you want a parallel formation.)

### 4.3 Drop or add a `user_approval` gate

```yaml
# drop: remove the whole field (do not set an empty string)
- role: implementer
  agent: claude:sonnet
  peer_review:
    role: code-reviewer
    agent: claude:opus
  # user_approval: required    ← delete this line

# add: the string shorthand is the most readable
- role: driver
  agent: claude:sonnet
  user_approval: required
```

### 4.4 Insert an extra stage (e.g. a design stage before implementation)

```yaml
stages:
  # new
  - role: designer
    agent: claude:opus
    user_approval: required

  # existing
  - role: implementer
    agent: claude:sonnet
    peer_review:
      role: code-reviewer
      agent: claude:opus
    user_approval: required
```

Stages run top to bottom. The designer must be approved before the implementer is launched.

### 4.5 Change only the `peer_review` agent

```yaml
peer_review:
  role: code-reviewer
  agent: codex:gpt-5.5    # was claude:opus
```

If `peer_review.agent` is omitted, it falls back to the stage's `agent`, then
to `claude:sonnet` — which may not match the vendor you want for review. When
the vendors should differ, set it explicitly.

---

## 5. Validation and behavior

### 5.1 `fleet formation show <name>`

- Loads `<state>/formations/<name>.yaml` and runs `formation.validate()`.
- Schema errors are printed to stderr as `warn: formation validation failed: <reason>`, but the YAML body is still emitted to stdout for inspection.

### 5.2 What `validate()` checks (`src/fleet/formation.py`)

- Top level has a non-empty `name`.
- Top level has `stages`, a list with at least one entry.
- Each stage is a mapping and has a `role` field.

Stricter checks — `peer_review` structure, `agent` parseability,
`user_approval` shape — are deferred to **runtime in the orchestrator**
(`design.md` §6.4). They are not done statically.

### 5.3 Possible errors

| situation | error / behavior |
|---|---|
| `formations/<name>.yaml` missing | `fleet-agent start --formation <name>` raises `ResolutionError`. No template fallback. |
| `formations/` empty + no `--formation` | A synthetic `_leader_solo` 1-stage formation is built from the leader-session agent. |
| `formations/` has 2+ entries + no `--formation` | Ambiguity error; tells you to pass `--formation <name>`. |
| YAML parse failure | `formation file must be a YAML mapping: <path>` |
| missing `name` | `formation missing required field: name` |
| missing / empty `stages` | `formation 'stages' must be a non-empty list` |
| stage missing `role` | `formation stages[i] missing required field: role` |
| bad `agent` spec | At runtime: `unsupported vendor` or `agent spec must be 'vendor:model'`. |
| unsupported vendor (e.g. `openai:gpt-4`) | `unsupported vendor 'openai'; supported: ['claude', 'codex']` |

---

## 6. Best practices

- **Read the current file first.** Run `fleet formation show <name>` before editing — someone may have customized it already.
- **Validate after editing.** Run `fleet formation show <name>` again. It is a cheap top-level check.
- **For big rewrites, regenerate.** `rm <state>/formations/<name>.yaml && fleet formation init --from <template>` is faster than reshaping a heavily edited file.
- **Avoid relying on `agent:` omission.** The `--agent` fallback is convenient but easy to forget. Be explicit per stage, especially for `peer_review.agent`.
- **Do not put vendor-specific notes in `role` or `description`.** Formations are read by both claude and codex leaders; keep vendor-specific guidance in the driver prompt (role fragment).
- **Keep `role` names aligned with `docs/prompts/roles/<role>.md`.** If the role fragment is missing, the driver starts without a clear sense of its role.

---

## 7. CLI reference (short form)

| command | description |
|---|---|
| `fleet formation list` | List both templates and custom formations. |
| `fleet formation show <name>` | Print a custom formation's YAML and run validate. |
| `fleet formation init --from <template> [--name <name>]` | Copy a template into `<state>/formations/<name>.yaml` (defaults to the template name). |
| `fleet-agent start <task-id> --formation <name>` | Resolve the formation and launch the task. |
| `fleet-agent approve <task-id>` | Relay user approval for the current `user_approval` gate. |
| `fleet-agent reject <task-id>` | Relay user rejection; the stage returns to implementation. |

For full flags, see `fleet formation --help` and `fleet-agent start --help`.

---

## 8. Out of scope

This guide intentionally does not cover:

- Skills or CLI helpers like `fleet formation edit`. Skills are a Claude Code-specific feature, which conflicts with the multi-vendor pillar.
- Partial overlay or inheritance between formations. Declined in Issue #105 and not part of the current spec.
- A `count` field or dynamic parallel launching. `design.md` §6.1 finalized **no count**.
