# Global Leader Memory Guide

This directory (`fleet-state/global/leader-memory/`) is the **global tier** of the
leader's two-tier memory (design `§6`). It is loaded at **every session start** and
holds knowledge that spans projects. It is vendor-neutral — a leader of any vendor
(claude, codex, or other) reads and writes here.

---

## The two tiers — what belongs where

Leader memory is split by one axis (design `§6`):

- **How the leader relates to the *user* → here (global).** User-global
  preferences and the leader's cross-project operating rules.
- **How a *project's output* should be → per-project**
  (`projects/<name>/memory/`). Project operating policy.

Concretely: *tone* is global; *"English docs/issues"* is per-project. *"ask the
user before a destructive op"* is global (router rule); *"this project's merge
authority is delegated to the leader"* is per-project.

> When in doubt, ask: "does this travel with **me across all projects**, or with
> **one project across all sessions**?" The first goes here; the second goes in
> that project's memory.

---

## Memory types

There are three types in this tier. Save only what fits one of them.

### user

**What**: Who the user is and how the leader should relate to them — tone,
communication style, standing personal preferences. This is the cross-project
*persona*: it follows the user, not any one project.

**When to save**: When you learn a durable preference about how the user wants to
be addressed or worked with that holds regardless of which project is in focus.

**How to use**: Keep this voice and these preferences in every session, across all
projects, so the user never has to restate them.

**Body structure**: Lead with the preference, then a **Why:** line (the reason or
the moment it was stated) and a **How to apply:** line (where it kicks in).

**Example**:

```
user prefers terse, direct replies — no long preambles, no over-politeness.
Why: stated repeatedly; values speed over ceremony.
How to apply: every user-facing reply and progress report, in every project.
```

> Note: the *per-project* store excludes the `user` type on purpose (a persona
> spans projects, so it does not bind to one project, design `§6`). This tier is
> exactly where that knowledge now lives.

---

### feedback

**What**: Router operating rules — cross-project guidance on how the leader runs
the fleet. Delegation defaults, when to ask the user vs. decide, approval
discipline, notification habits — the rules that hold no matter which project a
dispatch targets.

**When to save**: When a session reveals a leader-operating rule that applies
across projects ("don't X when routing", "default to Y before spawning").
Corrections *and* confirmed approaches both count.

**How to use**: Let these guide how you route and dispatch so the user does not
have to repeat the same operating guidance every session.

**Body structure**: Lead with the rule, then a **Why:** line and a **How to
apply:** line.

**Example**:

```
confirm with the user before spawning more than a couple of drivers at once.
Why: a burst of parallel drivers is hard to supervise and costly to unwind.
How to apply: when a request would fan out into many concurrent dispatches.
```

> A rule that is specific to one project's output ("this project ships English
> docs") is **not** a router rule — it belongs in that project's memory.

---

### reference

**What**: Pointers to cross-project external resources and where information
lives — systems that span the user's work rather than a single project.

**When to save**: When you learn about a durable external resource and its purpose
that is not tied to one project.

**How to use**: When a task references an external system that may hold relevant
information.

**Example**:

```
the user's running design notes live in a shared doc, not any repo.
→ check it when a request references prior design discussion.
```

---

## What NOT to save

- **Per-project operating policy** — how a project's output should be, its merge
  authority, its doc-language convention. That belongs in
  `projects/<name>/memory/` (the per-project tier), not here.
- Code patterns, conventions, architecture, file paths — derivable from code.
- Git history, recent changes, who changed what — `git log` / `git blame` are
  authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the commit message
  has context.
- Anything already documented in `design.md` or other docs.
- Ephemeral session details: in-progress work, transient routing state, the
  conversational active project.

These exclusions apply even when a user explicitly asks to save such things.

---

## How to save a memory (2-step process)

**Step 1** — write the memory to its own file (e.g. `user_tone.md`,
`feedback_dispatch.md`) using this frontmatter format:

```markdown
---
name: short-kebab-case-slug
description: one-line summary — used to decide relevance in future sessions, so be specific
metadata:
  type: user | feedback | reference
---

Memory content here. For user/feedback types: preference/rule first, then **Why:**
and **How to apply:** lines. Link related memories with [[their-name]].
```

**Step 2** — add a pointer to `MEMORY.md` (the index). One line per entry, under
~150 characters: `- [Title](file.md) — one-line hook`. Never write memory content
directly into `MEMORY.md`.

---

## When to access memory

- At **session start**: the global `MEMORY.md` index is injected for you; read the
  bodies of the entries that look relevant.
- When relating to the user (tone, preferences) or routing a dispatch (operating
  rules), let the relevant entries guide you.

---

## Staleness

Memory records can become stale. Before acting on a memory that names a specific
file, function, or flag, verify it still exists. If a recalled memory conflicts
with current reality, trust what you observe now — update or remove the stale
memory.

---

## Memory and the per-project tier

This global tier is for knowledge that travels with the user/leader across every
project. The per-project tier (`projects/<name>/memory/`, design `§6`) is for
knowledge that travels with one project across every session. Keep each kind in
its own tier; cross-link with `[[name]]` only within a tier (the indexes are
separate).
