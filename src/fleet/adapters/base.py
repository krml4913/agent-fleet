"""The vendor adapter interface.

A ``VendorAdapter`` captures everything vendor-specific about launching
and driving an agent CLI inside a tmux pane. Subclass it once per vendor
in ``<vendor>.py`` and register the class in ``fleet.adapters.REGISTRY``.
Adding a vendor must mean adding one file plus one registry line — nothing
scattered across ``agents.py`` / ``prompt_deliverer.py`` / the launchers.
"""
from __future__ import annotations

import re


class VendorAdapter:
    """Base class for a single vendor's adapter.

    Subclasses set the class attributes below and override
    :meth:`cli_command`. Adapters are used as classes (not instances):
    ``REGISTRY[vendor].cli_command(model)`` / ``REGISTRY[vendor].ready``.
    """

    #: Vendor key used in ``vendor:model`` specs (e.g. ``"claude"``).
    name: str

    #: Matches a captured pane when the CLI is at its input prompt and
    #: ready to receive the driver prompt.
    ready: re.Pattern[str]

    #: Matches a captured pane when the CLI is blocked on a boot gate
    #: (login / update prompt / directory-trust menu) a human must clear.
    gate: re.Pattern[str]

    #: Whether the CLI shows an update-check prompt on startup that the
    #: launch command suppresses (folded into :meth:`cli_command`).
    suppress_update_check: bool = False

    @classmethod
    def cli_command(cls, model: str) -> list[str]:
        """Return the argv used to launch this vendor's CLI for ``model``.

        Higher layers may append further flags (mode toggles, prompt
        paths) on top of the returned list.
        """
        raise NotImplementedError
