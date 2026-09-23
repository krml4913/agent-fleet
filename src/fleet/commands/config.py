"""``fleet config`` — show / get / set / unset the global config (``global/config.yaml``).

The only supported way to change it: the leader protocol forbids hand-editing
state files. Settings here are cross-project (not per-project like
``fleet notify`` / ``fleet workspace``), so there is no ``--project``.

``get`` and the plain listing report the *config layer* (the file, else the
built-in default). ``FLEET_MUX`` in the environment overrides ``mux`` at run
time and is only noted; ``fleet preflight`` shows the backend actually selected.
``leader_agent`` has no such env override; see ``fleet leader --help``.

Agent aliases (the ``agent_aliases`` map) are addressed as dotted keys:
``fleet config set agent_aliases.<name> <vendor:model>``,
``fleet config get agent_aliases.<name>``, ``fleet config unset agent_aliases.<name>``.
"""
from __future__ import annotations

import argparse
import os
import sys

from .. import agents as agents_mod
from .. import config as config_mod

_ALIAS_PREFIX = f"{config_mod.ALIASES_KEY}."


def _values_help(key: str) -> str:
    """Human-readable accepted-values description for ``key``, for help text."""
    if key in config_mod.KEYS:
        return "|".join(config_mod.KEYS[key])
    return config_mod.FREEFORM[key][1]


def _alias_name(key: str) -> str | None:
    """The alias name when ``key`` is ``agent_aliases.<name>``, else ``None``."""
    key = str(key).strip()
    if key.startswith(_ALIAS_PREFIX):
        return key[len(_ALIAS_PREFIX):]
    return None


def add_parser(sub: "argparse._SubParsersAction") -> None:
    keys = ", ".join(f"{k}={_values_help(k)}" for k in config_mod.ALL_KEYS)
    key_help = (
        f"one of: {', '.join(config_mod.ALL_KEYS)}, or {_ALIAS_PREFIX}<name> for an agent alias"
    )
    p = sub.add_parser(
        "config",
        help="Show or set the global config (fleet-state/global/config.yaml)",
        description=(
            "Show or set the global config. With no subcommand, print every "
            f"key. Known keys: {keys}. Agent aliases: "
            f"{_ALIAS_PREFIX}<name>=vendor:model — the alias name can then be "
            "used anywhere an agent spec is accepted (formation agent / "
            "peer_review.agent, fleet-agent start --agent, fleet leader --agent, "
            "leader_agent). Precedence for mux: FLEET_MUX env > "
            "this config > built-in default (zellij). leader_agent has no env "
            "override: fleet leader --agent > this config > built-in default."
        ),
    )
    p.set_defaults(func=run_show)
    sp = p.add_subparsers(dest="config_cmd", metavar="<sub>")

    sp_get = sp.add_parser("get", help="Print one key's value")
    sp_get.add_argument("key", help=key_help)
    sp_get.set_defaults(func=run_get)

    sp_set = sp.add_parser("set", help="Set a key or an agent alias")
    sp_set.add_argument("key", help=key_help)
    sp_set.add_argument(
        "value",
        help=(
            "the new value (mux: tmux | zellij; leader_agent: vendor:model or an "
            f"alias, e.g. claude:opus; {_ALIAS_PREFIX}<name>: vendor:model)"
        ),
    )
    sp_set.set_defaults(func=run_set)

    sp_unset = sp.add_parser(
        "unset", help="Remove a key (back to its default) or an agent alias"
    )
    sp_unset.add_argument("key", help=key_help)
    sp_unset.set_defaults(func=run_unset)


def run_show(args: argparse.Namespace) -> int:
    print(f"config file: {config_mod.config_path()}")
    aliases = config_mod.load_aliases()
    for key in config_mod.ALL_KEYS:
        value, source = config_mod.get(key)
        if key == "leader_agent" and agents_mod.is_alias(value):
            resolved = aliases.get(value.strip())
            print(f"{key}: {value} -> {resolved} ({source})")
            continue
        print(f"{key}: {value} ({source})")
    if aliases:
        print(f"{config_mod.ALIASES_KEY}:")
        for name, target in aliases.items():
            print(f"  {name}: {target}")
    else:
        print(f"{config_mod.ALIASES_KEY}: (none)")
    env_mux = (os.environ.get("FLEET_MUX") or "").strip()
    if env_mux:
        print(f"note: FLEET_MUX={env_mux} in the environment overrides mux")
    return 0


def run_get(args: argparse.Namespace) -> int:
    try:
        alias = _alias_name(args.key)
        if alias is not None:
            value = config_mod.get_alias(alias)
        else:
            value, _source = config_mod.get(args.key)
    except config_mod.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(value)
    return 0


def run_set(args: argparse.Namespace) -> int:
    try:
        alias = _alias_name(args.key)
        if alias is not None:
            name, target = config_mod.set_alias(alias, args.value)
            print(f"{_ALIAS_PREFIX}{name}: {target}")
            return 0
        value = config_mod.set_value(args.key, args.value)
    except config_mod.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{args.key.strip()}: {value}")
    return 0


def run_unset(args: argparse.Namespace) -> int:
    try:
        alias = _alias_name(args.key)
        if alias is not None:
            config_mod.unset_alias(alias)
        else:
            config_mod.unset_value(args.key)
    except config_mod.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"unset {args.key.strip()}")
    return 0
