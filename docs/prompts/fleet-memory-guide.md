# Fleet Memory Guide

This directory (`.fleet-state/memory/`) is the **project-level shared memory store** for all drivers.
Any driver — regardless of vendor (claude, codex, or other) — reads and writes here.

---

## Purpose

Each driver accumulates project knowledge while working on tasks. Fleet memory captures
insights that should persist across tasks and be visible to future drivers of any vendor.

## Memory types

There are three types. Save only what fits one of these types.

### feedback

**What**: Guidance about how to approach work in this project — corrections and confirmed
approaches. Record from failure AND success: corrections are easy to spot; also save
non-obvious choices the user validated without pushback.

**When to save**: Any time a task reveals that a particular approach was wrong ("don't do X")
or right ("yes, exactly that"). Save if it applies to future tasks — not one-off task details.

**How to use**: Let these memories guide approach so users and leaders don't have to repeat
the same guidance. Apply the rule; use the Why to judge edge cases.

**Body structure**: Lead with the rule, then a **Why:** line (the reason — often a past
incident or strong preference) and a **How to apply:** line (when this guidance kicks in).

**Example**:

```
user said: don't test with mocked state — the last time mocked tests passed but prod failed
→ save: integration tests must use real state files, not mocks.
   Why: prior incident where mock/prod divergence masked a broken behavior.
   How to apply: any test that touches state.py or task files.
```

---

### project

**What**: Information about ongoing work, goals, decisions, bugs, or incidents that is not
otherwise derivable from the code or git history.

**When to save**: When you learn who is doing what, why, or by when. Convert relative dates
to absolute dates when saving (e.g. "Thursday" → "2026-03-05") so the memory stays
interpretable after time passes.

**How to use**: Use to understand the broader context and motivation behind tasks. Helps
future drivers make better-informed decisions aligned with current project direction.

**Body structure**: Lead with the fact or decision, then a **Why:** line (the motivation —
often a constraint, deadline, or stakeholder ask) and a **How to apply:** line. Project
memories decay fast; the why helps future drivers judge whether the memory is still relevant.

**Example**:

```
merge freeze begins 2026-03-05 for mobile release cut.
Why: mobile team cutting a release branch.
How to apply: flag any non-critical PR work scheduled after that date.
```

---

### reference

**What**: Pointers to where information can be found in external systems.

**When to save**: When you learn about a resource in an external system and its purpose —
e.g. which Linear project tracks a kind of bug, which Slack channel has feedback,
which dashboard oncall watches.

**How to use**: When a task references an external system or information that may be there.

**Example**:

```
pipeline bugs are tracked in Linear project "INGEST"
→ when investigating pipeline issues, check Linear INGEST first.
```

---

## What NOT to save

- Code patterns, conventions, architecture, file paths, or project structure — derivable from
  current code.
- Git history, recent changes, who changed what — `git log` / `git blame` are authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the commit message has context.
- Anything already documented in design.md or other docs.
- Ephemeral task details: in-progress work, temporary state, current task context.

These exclusions apply even when a user or leader explicitly asks to save such things.

---

## How to save a memory (2-step process)

**Step 1** — write the memory to its own file (e.g. `feedback_testing.md`, `project_release.md`)
using this frontmatter format:

```markdown
---
name: short-kebab-case-slug
description: one-line summary — used to decide relevance in future tasks, so be specific
metadata:
  type: feedback | project | reference
---

Memory content here. For feedback/project types: rule/fact first, then **Why:** and
**How to apply:** lines. Link related memories with [[their-name]].
```

**Step 2** — add a pointer to `MEMORY.md` (the index). One line per entry, under ~150
characters: `- [Title](file.md) — one-line hook`. Never write memory content directly
into `MEMORY.md`.

---

## When to access memory

- At **task start**: read `MEMORY.md` to load the index and identify relevant memories.
- When working on something where prior guidance might apply.
- When a task reference suggests a known external resource.

---

## Staleness

Memory records can become stale. Before acting on a memory that names a specific file,
function, or flag, verify it still exists (`grep` or `Read`). If a recalled memory conflicts
with current reality, trust what you observe now — update or remove the stale memory.

---

## Memory and other persistence

Fleet memory is for knowledge that should survive across tasks and be visible to other
vendor drivers. It is not for:

- In-task planning or step tracking — use notes in outbox.md or task context.
- Design decisions — those go in `docs/design.md` once confirmed.
- Audit trail — events.jsonl is the authoritative append-only log.
