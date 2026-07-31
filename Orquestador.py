"""Stable command-line shim for the modular NeoCortex CLI."""


# region [01] Compatibility imports and reexports
# Keep historical import names available while implementation lives in focused
# CLI modules. Direct operations remain lazy so parser-only use starts no route.

from __future__ import annotations

import argparse

from neocortex.cli import entrypoint
from _04_Nucleo_Operativo.cli_app import main, run_framework as _run_framework
from _04_Nucleo_Operativo.cli_parser import (
    ExplicitArgumentParser as _ExplicitArgumentParser,
    build_parser as _parser,
    decimal_megabytes as _decimal_megabytes,
)
from _04_Nucleo_Operativo.cli_reporting import (
    has_strict_route_errors as _has_strict_route_errors,
    print_reports as _print_reports,
)

__all__ = [
    "_ExplicitArgumentParser",
    "_decimal_megabytes",
    "_has_strict_route_errors",
    "_parser",
    "_print_reports",
    "_run_docx_layout_groups",
    "_run_docx_missing_pdf",
    "_run_docx_search",
    "_run_audio_doctor",
    "_run_audio_search",
    "_run_framework",
    "_run_pdf_doctor",
    "_run_pdf_layout_groups",
    "_run_pdf_search",
    "_run_pdf_verify",
    "_run_review_candidates",
    "_run_operational_status",
    "_validate_arguments",
    "main",
]

# endregion [01]


# region [02] Lazy compatibility adapters


def _validate_arguments(args: argparse.Namespace) -> None:
    from _04_Nucleo_Operativo.cli_validation import validate_arguments

    validate_arguments(args)


def _run_pdf_search(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_pdf_search

    return run_pdf_search(args)


def _run_pdf_layout_groups(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_pdf_layout_groups

    return run_pdf_layout_groups(args)


def _run_pdf_doctor(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_pdf_doctor

    return run_pdf_doctor(args)


def _run_pdf_verify(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_pdf_verify

    return run_pdf_verify(args)


def _run_docx_search(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_docx_search

    return run_docx_search(args)


def _run_docx_layout_groups(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_docx_layout_groups

    return run_docx_layout_groups(args)


def _run_docx_missing_pdf(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_docx_missing_pdf

    return run_docx_missing_pdf(args)


def _run_review_candidates(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_review_candidates

    return run_review_candidates(args)


def _run_operational_status(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_operational_status

    return run_operational_status(args)


def _run_audio_search(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_audio_search

    return run_audio_search(args)


def _run_audio_doctor(args: argparse.Namespace) -> int:
    from _04_Nucleo_Operativo.cli_direct import run_audio_doctor

    return run_audio_doctor(args)


# endregion [02]


# region [03] Historical process shim

if __name__ == "__main__":
    raise SystemExit(entrypoint())

# endregion [03]
