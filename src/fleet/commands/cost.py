"""``fleet cost`` (alias ``fleet usage``) — on-demand recorded-usage report.

Walks the per-task ``usage`` block the recording path (Issue #209) folds into
``task.yaml`` at a terminal transition and aggregates it across every registered
project, grouped by **session / project / vendor / stage**.

On-demand only — the aggregation runs when this command is invoked; there is no
resident process and no polling (principle P6 / the no-daemon pillar). Read-only:
it never writes usage data or task state (P7). Cost is approximate-or-absent —
fleet records none today, so the cost figure shows ``—`` until a vendor exposes
one; tokens always render.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import state as state_mod
from .. import status_data as status_data_mod

_RESET = "\033[0m"
_BOLD = "\033[1m"

# Grouping dimensions, in display order. Each is derived per task and the task's
# recorded usage is folded into that key's accumulator.
_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("session", "By session"),
    ("project", "By project"),
    ("vendor", "By vendor"),
    ("stage", "By stage"),
)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "cost",
        aliases=["usage"],
        help="Aggregate recorded per-task token usage (and approximate cost)",
        description=(
            "Walk every registered project's task.yaml usage blocks on demand "
            "and aggregate token usage (and approximate cost) grouped by "
            "session, project, vendor, and stage. Read-only; no daemon, no "
            "polling — it only reads what the recording path already wrote."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the aggregation as a JSON object instead of tables.",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    agg = aggregate()
    if getattr(args, "as_json", False):
        print(json.dumps(_json_obj(agg), ensure_ascii=False))
        return 0
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    _print_report(agg, use_color)
    return 0


# ---------------------------------------------------------------------------
# Aggregation (on-demand, read-only)
# ---------------------------------------------------------------------------


def _new_acc() -> dict:
    """A fresh per-group accumulator. ``cost_known`` flips on once any task in the
    group reported a cost — distinguishing a real ``0.0`` sum from absent cost."""
    return {
        "tasks": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "cost_known": False,
    }


def _fold(acc: dict, usage: dict) -> None:
    """Fold one task's normalized ``usage`` into accumulator ``acc``."""
    acc["tasks"] += 1
    acc["input_tokens"] += usage["input_tokens"]
    acc["output_tokens"] += usage["output_tokens"]
    acc["total_tokens"] += usage["total_tokens"]
    cost = usage.get("cost")
    if cost is not None:
        acc["cost"] += cost
        acc["cost_known"] = True


def _task_dimensions(task: dict, project: str) -> dict[str, str]:
    """Resolve the grouping keys for a task's recorded usage.

    Usage is recorded at the terminal transition from the current stage's agent
    spec (``state.record_task_usage``), so that same stage names both the vendor
    and the stage role. Falls back to ``unknown`` / ``-`` when the stage or its
    agent spec is missing or malformed.
    """
    idx = status_data_mod.current_stage_index(task)
    stages = task.get("stages") or []
    vendor = "unknown"
    role = "-"
    if 0 <= idx < len(stages) and isinstance(stages[idx], dict):
        stage = stages[idx]
        role = str(stage.get("role") or "-")
        spec = stage.get("agent")
        if isinstance(spec, str) and ":" in spec:
            vendor = spec.partition(":")[0].strip() or "unknown"
    return {
        "session": state_mod.task_owner_session(task),
        "project": project,
        "vendor": vendor,
        "stage": role,
    }


def aggregate() -> dict:
    """Walk every project's live + archived tasks and fold recorded usage.

    Returns ``{"task_count", "total", "groups"}`` where ``groups`` maps each
    dimension name to ``{key: accumulator}``. Best-effort per project: an
    unreadable project contributes nothing rather than aborting the report.
    """
    reg = state_mod.load_registry()
    total = _new_acc()
    groups: dict[str, dict[str, dict]] = {dim: {} for dim, _label in _DIMENSIONS}
    task_count = 0
    for name in sorted(reg.get("projects", {})):
        state_dir = state_mod.project_state_dir(name)
        if not state_dir.is_dir():
            continue
        tasks: list[dict] = []
        try:
            tasks.extend(state_mod.list_tasks(state_dir))
        except Exception:  # noqa: BLE001
            pass
        try:
            tasks.extend(state_mod.list_archived_tasks(state_dir))
        except Exception:  # noqa: BLE001
            pass
        for task in tasks:
            usage = status_data_mod.task_usage(task)
            if usage is None:
                continue
            task_count += 1
            _fold(total, usage)
            dims = _task_dimensions(task, name)
            for dim, _label in _DIMENSIONS:
                bucket = groups[dim].setdefault(dims[dim], _new_acc())
                _fold(bucket, usage)
    return {"task_count": task_count, "total": total, "groups": groups}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _acc_cost(acc: dict) -> float | None:
    """Group cost: the summed cost when any task reported one, else ``None``."""
    return acc["cost"] if acc["cost_known"] else None


def _acc_json(acc: dict) -> dict:
    return {
        "tasks": acc["tasks"],
        "input_tokens": acc["input_tokens"],
        "output_tokens": acc["output_tokens"],
        "total_tokens": acc["total_tokens"],
        "cost": round(acc["cost"], 6) if acc["cost_known"] else None,
    }


def _json_obj(agg: dict) -> dict:
    return {
        "task_count": agg["task_count"],
        "total": _acc_json(agg["total"]),
        "by": {
            dim: {
                key: _acc_json(acc)
                for key, acc in sorted(agg["groups"][dim].items())
            }
            for dim, _label in _DIMENSIONS
        },
    }


def _print_report(agg: dict, use_color: bool) -> None:
    n = agg["task_count"]
    if n == 0:
        print(
            "(no recorded usage yet — usage is recorded when a task reaches a "
            "terminal state)"
        )
        return
    contributing = len(agg["groups"]["project"])
    total = agg["total"]
    print(
        _style(
            f"USAGE  {n} task(s) with recorded usage across {contributing} project(s)",
            _BOLD,
            use_color,
        )
    )
    print(
        "  input {inp}  ·  output {out}  ·  total {tot}  ·  cost {cost}".format(
            inp=status_data_mod.humanize_tokens(total["input_tokens"]),
            out=status_data_mod.humanize_tokens(total["output_tokens"]),
            tot=status_data_mod.humanize_tokens(total["total_tokens"]),
            cost=status_data_mod.format_cost(_acc_cost(total)),
        )
    )
    for dim, label in _DIMENSIONS:
        print()
        print(_style(label, _BOLD, use_color))
        buckets = agg["groups"][dim]
        if not buckets:
            print("  (none)")
            continue
        # Largest consumer first; ties broken by key for a stable order.
        rows = sorted(buckets.items(), key=lambda kv: (-kv[1]["total_tokens"], kv[0]))
        key_width = max(len(k) for k, _ in rows)
        for key, acc in rows:
            print(
                "  {key:<{kw}}  {tasks:>3} task(s)  total {tot:>8}  {cost}".format(
                    key=key,
                    kw=key_width,
                    tasks=acc["tasks"],
                    tot=status_data_mod.humanize_tokens(acc["total_tokens"]),
                    cost=status_data_mod.format_cost(_acc_cost(acc)),
                )
            )


def _style(text: str, code: str, enabled: bool) -> str:
    if not enabled or not code:
        return text
    return f"{code}{text}{_RESET}"
