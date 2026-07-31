"""Application control flow for the NeoCortex command-line interface."""


# region [01] Lightweight imports and public contract
# Route engines are imported only after parsing and direct-command dispatch.

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .cli_operations import dispatch_direct_operation
from .cli_parser import build_parser

__all__ = ["dispatch_direct", "main", "run_framework"]

# endregion [01]


# region [02] Direct operation dispatch


def dispatch_direct(args: argparse.Namespace) -> int | None:
    """Run a selected direct operation, or return ``None`` for a full run."""

    return dispatch_direct_operation(args)


# endregion [02]


# region [03] Framework configuration and execution


def run_framework(args: argparse.Namespace):
    """Build the validated configuration and run the integrated framework."""

    from _03_Progreso import RichProgress

    from .cli_config import framework_config_from_args
    from .console_cancellation import ConsoleCancellationBridge
    from .orchestrator import FrameworkOrchestrator

    config = framework_config_from_args(args)
    with RichProgress() as progress:
        orchestrator = FrameworkOrchestrator(config, progress=progress)
        with ConsoleCancellationBridge(orchestrator.request_cancellation):
            return orchestrator.run()


# endregion [03]


# region [04] Application control flow


def main(arguments: Sequence[str] | None = None) -> int:
    """Parse, validate and dispatch one NeoCortex command invocation."""

    parser = build_parser()
    args = parser.parse_args(arguments)

    from .cli_validation import validate_arguments

    try:
        validate_arguments(args)
    except SystemExit as exc:
        # Domain validators retain a lightweight direct-call contract, but the
        # public CLI reports every argument error through argparse with code 2.
        if isinstance(exc.code, str):
            parser.error(exc.code)
        raise
    direct_exit_code = dispatch_direct(args)
    if direct_exit_code is not None:
        return direct_exit_code

    result = run_framework(args)

    from .cli_reporting import (
        has_organization_errors,
        has_strict_route_errors,
        print_reports,
    )

    print_reports(result, args)
    actions = getattr(result, "actions", None)
    if (actions is not None and actions.errors) or has_organization_errors(result):
        return 2
    if args.strict_exit_codes and has_strict_route_errors(result):
        return 2
    return 0


# endregion [04]
