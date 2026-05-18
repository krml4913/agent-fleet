"""``fleet ask "<question>"`` — driver-side: ask the user a question.

Records a ``needs_input`` event, flips the task status to ``needs_input``,
appends to ``questions.md``, and fires a notification. **Does not block.**
The driver re-checks ``inbox.md`` on its own schedule for the answer.
"""
from __future__ import annotations

import argparse
import sys

from .. import notify
from .. import state as state_mod
from .. import task_context
from ..events import append_event, utcnow_iso


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "ask",
        help="Driver-side: ask the user a question",
        description=(
            "Record a needs_input event for the current task, flip its "
            "status, and fire a notification. Pane output alone never "
            "reaches anyone — drivers MUST use this CLI."
        ),
    )
    p.add_argument("question", help="The question to ask the user")
    p.add_argument(
        "--task-id",
        default=None,
        help="Override the auto-detected task id",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        state_dir, task_id = task_context.resolve(explicit_id=args.task_id)
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    task["status"] = "needs_input"
    state_mod.save_task(state_dir, task_id, task)

    qpath = state_mod.task_dir(state_dir, task_id) / "questions.md"
    block = f"### {utcnow_iso()}\n\n{args.question.rstrip()}\n\n"
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + block, encoding="utf-8")

    append_event(
        state_dir / "events.jsonl",
        "needs_input",
        task_id=task_id,
        question=args.question,
    )

    project = state_mod.load_project(state_dir)
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} needs input",
        message=args.question,
    )

    print(f"recorded needs_input for task-{task_id}")
    return 0
