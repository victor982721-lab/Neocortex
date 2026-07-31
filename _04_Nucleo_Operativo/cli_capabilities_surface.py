"""Hidden flat compatibility contract for the canonical capabilities doctor."""


# region [01] Lightweight imports and public contract

from __future__ import annotations

import argparse

from .cli_operations import DirectOperationFamily, selected_direct_operations
from .route_selection import BUILTIN_ROUTE_ORDER, normalize_route_selection

__all__ = [
    "register_capabilities_arguments",
    "validate_capabilities_arguments",
]

# endregion [01]


# region [02] Hidden flat compatibility flags


def register_capabilities_arguments(parser: argparse.ArgumentParser) -> None:
    """Register internal flags used after canonical argv translation."""

    parser.add_argument(
        "--doctor-capabilities",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--doctor-capabilities-json",
        action="store_true",
        help=argparse.SUPPRESS,
    )


# endregion [02]


# region [03] Stable validation and safety boundary


def validate_capabilities_arguments(args: argparse.Namespace) -> None:
    """Validate the internal capabilities operation without touching runtime state."""

    explicit = set(getattr(args, "_explicit_options", ()))
    operations = selected_direct_operations(
        args,
        family=DirectOperationFamily.CAPABILITIES,
    )
    if "doctor_capabilities_json" in explicit and not operations:
        raise SystemExit("--doctor-capabilities-json requires --doctor-capabilities")
    if operations and args.apply:
        raise SystemExit("doctor capabilities is read-only and rejects --apply")
    if operations and normalize_route_selection(args.route, BUILTIN_ROUTE_ORDER):
        raise SystemExit("doctor capabilities cannot be combined with --route")


# endregion [03]
