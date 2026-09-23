"""``fleet config`` — show / get / set the global config (``global/config.yaml``).

The only supported way to change it: the leader protocol forbids hand-editing
state files. Settings here are cross-project (not per-project like
``fleet notify`` / ``fleet workspace``), so there is no ``--project``.

``get`` and the plain listing report the *config layer* (the file, else the
built-in default). ``FLEET_MUX`` in the environment overrides ``mux`` at run
time and is only noted; ``fleet preflight`` shows the backend actually selected.
``leader_agent`` has no such env override; see ``fleet leader --help``.
"""
from __future__ import annotations

import argparse
import os
import sys

from .. import config as config_mod


def _values_help(key: str) -> str:
    """Human-readable accepted-values description for ``key``, for help text."""
    if key in config_mod.KEYS:
        return "|".join(config_mod.KEYS[key])
    return config_mod.FREEFORM[key][1]


def add_parser(sub: "argparse._SubParsersAction") -> None:
    keys = ", ".join(f"{k}={_values_help(k)}" for k in config_mod.ALL_KEYS)
    p = sub.add_parser(
        "config",
        help="Show or set the global config (fleet-state/global/config.yaml)",
        description=(
            "Show or set the global config. With no subcommand, print every "
            f"key. Known keys: {keys}. Precedence for mux: FLEET_MUX env > "
            "this config > built-in default (zellij). leader_agent has no env "
            "override: fleet leader --agent > this config > built-in default."
        ),
    )
    p.set_defaults(func=run_show)
    sp = p.add_subparsers(dest="config_cmd", metavar="<sub>")

    sp_get = sp.add_parser("get", help="Print one key's value")
    sp_get.add_argument("key", help=f"one of: {', '.join(config_mod.ALL_KEYS)}")
    sp_get.set_defaults(func=run_get)

    sp_set = sp.add_parser("set", help="Set a key")
    sp_set.add_argument("key", help=f"one of: {', '.join(config_mod.ALL_KEYS)}")
    sp_set.add_argument(
        "value",
        help="the new value (mux: tmux | zellij; leader_agent: vendor:model, e.g. claude:opus)",
    )
    sp_set.set_defaults(func=run_set)


def run_show(args: argparse.Namespace) -> int:
    print(f"config file: {config_mod.config_path()}")
    for key in config_mod.ALL_KEYS:
        value, source = config_mod.get(key)
        print(f"{key}: {value} ({source})")
    env_mux = (os.environ.get("FLEET_MUX") or "").strip()
    if env_mux:
        print(f"note: FLEET_MUX={env_mux} in the environment overrides mux")
    return 0


def run_get(args: argparse.Namespace) -> int:
    try:
        value, _source = config_mod.get(args.key)
    except config_mod.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(value)
    return 0


def run_set(args: argparse.Namespace) -> int:
    try:
        value = config_mod.set_value(args.key, args.value)
    except config_mod.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{args.key.strip()}: {value}")
    return 0
