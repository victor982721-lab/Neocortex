"""Flat argument and validation contract for structured-code CLI operations."""


# region [01] Lightweight imports and public contract

from __future__ import annotations

import argparse
from collections.abc import Callable

from .cli_operations import DirectOperationFamily, selected_direct_operations
from .route_selection import BUILTIN_ROUTE_ORDER, normalize_route_selection

__all__ = ["register_code_arguments", "validate_code_arguments"]

# endregion [01]


# region [02] Stable flat argument registration


def register_code_arguments(
    parser: argparse.ArgumentParser,
    *,
    megabyte_type: Callable[[str], int],
) -> None:
    """Append the existing flat Code group using its legacy size converter."""

    code = parser.add_argument_group("Structured source-code intelligence route")
    code.add_argument(
        "--code-max-mb",
        dest="code_max_file_bytes",
        type=megabyte_type,
        default=8 * 1024 * 1024,
        metavar="MB",
        help="analyze only textual artifacts at or below this bounded size",
    )
    code.add_argument(
        "--code-max-count",
        dest="code_max_documents",
        type=int,
        metavar="N",
    )
    code.add_argument("--code-max-text-chars", type=int, default=4_000_000)
    code.add_argument("--code-chunk-chars", type=int, default=12_000)
    code.add_argument("--code-complexity-warning", type=int, default=15)
    code.add_argument("--code-function-lines-warning", type=int, default=200)
    code.add_argument(
        "--code-cache-validation",
        choices=("metadata", "full"),
        default="metadata",
        help="metadata is incremental-fast; full rechecks exact bytes before reuse",
    )
    code.add_argument(
        "--code-generated",
        dest="code_include_generated",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="retain generated artifacts in structural analysis",
    )
    code.add_argument(
        "--code-vendored",
        dest="code_include_vendored",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="retain vendored artifacts in structural analysis",
    )
    code.add_argument(
        "--retry-code-errors",
        action="store_true",
        help="retry unchanged partial or failed code observations",
    )
    code.add_argument(
        "--code-status",
        action="store_true",
        help="show bounded code database, analyzer and index status",
    )
    code.add_argument(
        "--code-review",
        action="store_true",
        help=(
            "show a deterministic read-only top-10 maintenance shortlist from "
            "published self-analysis"
        ),
    )
    code.add_argument("--code-search", metavar="QUERY")
    code.add_argument(
        "--code-search-mode",
        action="append",
        choices=(
            "literal",
            "fts",
            "path",
            "language",
            "symbol",
            "definition",
            "reference",
            "import",
            "dependency",
            "call",
            "signature",
            "diagnostic",
            "complexity",
            "semantic",
            "hybrid",
        ),
        help="repeat to fuse complementary exact and structural search modes",
    )
    code.add_argument("--code-search-limit", type=int, default=20, metavar="N")
    code.add_argument("--code-path", metavar="FRAGMENT")
    code.add_argument("--code-language")
    code.add_argument("--code-project")
    code.add_argument("--code-symbol")
    code.add_argument("--code-diagnostic")
    code.add_argument("--code-min-complexity", type=float)
    code.add_argument(
        "--code-projects",
        action="store_true",
        help="list inferred project instances without touching source files",
    )
    code.add_argument("--code-reconstruct", metavar="PROJECT_OR_ID")
    code.add_argument(
        "--code-reconstruct-strategy",
        choices=("latest", "coherent", "branches"),
        default="coherent",
    )
    code.add_argument(
        "--code-doctor",
        action="store_true",
        help="verify code schema, FTS and analyzer availability without analysis",
    )
    code.add_argument("--code-json", action="store_true")


# endregion [02]


# region [03] Stable validation and error precedence


def validate_code_arguments(args: argparse.Namespace) -> None:
    """Validate Code route and direct selections in the established order."""

    if args.code_max_file_bytes < 4096:
        raise SystemExit("--code-max-mb must be at least 0.004096")
    if args.code_max_documents is not None and args.code_max_documents < 1:
        raise SystemExit("--code-max-count must be positive")
    if args.code_max_text_chars < 1024:
        raise SystemExit("--code-max-text-chars must be at least 1024")
    if not 1024 <= args.code_chunk_chars <= 1_000_000:
        raise SystemExit("--code-chunk-chars must be between 1024 and 1000000")
    if args.code_complexity_warning < 1 or args.code_function_lines_warning < 1:
        raise SystemExit("code diagnostic thresholds must be positive")
    if args.code_search is not None:
        if not args.code_search.strip():
            raise SystemExit("--code-search must be non-empty")
        if len(args.code_search) > 4096:
            raise SystemExit("--code-search cannot exceed 4096 characters")
    if not 1 <= args.code_search_limit <= 1000:
        raise SystemExit("--code-search-limit must be between 1 and 1000")
    if args.code_min_complexity is not None and args.code_min_complexity < 0:
        raise SystemExit("--code-min-complexity cannot be negative")
    if args.code_search_mode and len(set(args.code_search_mode)) != len(
        args.code_search_mode
    ):
        raise SystemExit("--code-search-mode values cannot be duplicated")

    explicit = set(getattr(args, "_explicit_options", ()))
    search_options = {
        "code_search_mode",
        "code_search_limit",
        "code_path",
        "code_language",
        "code_project",
        "code_symbol",
        "code_diagnostic",
        "code_min_complexity",
    }
    if search_options.intersection(explicit) and args.code_search is None:
        raise SystemExit("code search filters require --code-search")
    if "code_reconstruct_strategy" in explicit and args.code_reconstruct is None:
        raise SystemExit("--code-reconstruct-strategy requires --code-reconstruct")
    code_direct = selected_direct_operations(args, family=DirectOperationFamily.CODE)
    if "code_json" in explicit and not code_direct:
        raise SystemExit("--code-json requires a direct code operation")
    if code_direct and args.apply:
        raise SystemExit("direct code operations are read-only and reject --apply")
    if code_direct and normalize_route_selection(args.route, BUILTIN_ROUTE_ORDER):
        raise SystemExit("direct code operations cannot be combined with --route")


# endregion [03]
