"""Pure contracts for protected source-tree self-analysis runs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from _02_Deduplicacion import InventoryExclusionPolicy

from .models import FrameworkConfig


# region [01] Stable profile and manifest contracts


SELF_ANALYSIS_PROFILE_VERSION = "neocortex.self-analysis-profile/v1"
SELF_ANALYSIS_MANIFEST_SCHEMA = "neocortex.self-analysis-manifest/v2"
LEGACY_SELF_ANALYSIS_MANIFEST_SCHEMAS = frozenset(
    {"neocortex.self-analysis-manifest/v1"}
)
SELF_ANALYSIS_MANIFEST_PHASE = "self-analysis-manifest"
SELF_ANALYSIS_MANIFEST_MESSAGE = "Manifest de autoanálisis publicado"
MAX_SELF_ANALYSIS_MANIFEST_BYTES = 256 * 1024

_EXCLUDED_DIRECTORY_NAMES = (
    ".cache",
    ".codex-lab",
    ".complexipy_cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "backup",
    "backups",
    "build",
    "coverage",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "out",
    "site-packages",
    "target",
    "temp",
    "third-party",
    "third_party",
    "tmp",
    "vendor",
    "vendors",
    "venv",
)
_EXCLUDED_FILE_NAMES = (
    ".coverage",
    "coverage.json",
    "coverage.xml",
)
_EXCLUDED_FILE_SUFFIXES = (
    ".backup",
    ".bak",
    ".coverage",
    ".db",
    ".db-shm",
    ".db-wal",
    ".db3",
    ".log",
    ".old",
    ".orig",
    ".prof",
    ".pstats",
    ".pyc",
    ".pyo",
    ".shm",
    ".sqlite",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".temp",
    ".tmp",
    ".wal",
)
_TRANSIENT_DIRECTORY_PREFIXES = (".pytest-", ".test-tmp")
_MAX_TRANSIENT_DIRECTORY_ROOTS = 256


# endregion [01]


# region [02] Inventory profile


def _transient_project_directories(root: Path) -> tuple[Path, ...]:
    """Discover bounded top-level test artifacts absent from stable name rules."""

    matches: list[Path] = []
    with os.scandir(root) as entries:
        for entry in entries:
            name_key = entry.name.casefold()
            if not name_key.startswith(_TRANSIENT_DIRECTORY_PREFIXES):
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not is_directory:
                continue
            matches.append(Path(entry.path))
            if len(matches) > _MAX_TRANSIENT_DIRECTORY_ROOTS:
                raise ValueError("too many transient self-analysis directories")
    return tuple(sorted(matches, key=lambda path: os.path.normcase(str(path))))


def build_self_analysis_inventory_policy(
    root: Path,
    state_directory: Path,
) -> InventoryExclusionPolicy:
    """Compile the exact project-local exclusions for protected analysis."""

    explicit_roots = (
        state_directory,
        root / ".codex-lab",
        root / "docs" / "audit_evidence",
        root / "Laboratory",
        root / "neocortex_framework.egg-info",
        *_transient_project_directories(root),
    )
    return InventoryExclusionPolicy.compile(
        explicit_roots,
        directory_names=_EXCLUDED_DIRECTORY_NAMES,
        file_names=_EXCLUDED_FILE_NAMES,
        file_suffixes=_EXCLUDED_FILE_SUFFIXES,
    )


def inventory_policy_manifest(
    policy: InventoryExclusionPolicy,
) -> dict[str, object]:
    """Return the bounded canonical rules that produced one policy signature."""

    explicit_roots = list(policy.explicit_roots)
    directory_names = sorted(policy.directory_names)
    file_names = sorted(policy.file_names)
    file_suffixes = list(policy.file_suffixes)
    manifest: dict[str, object] = {
        "profile": SELF_ANALYSIS_PROFILE_VERSION,
        "signature": policy.signature,
        "signature_version": policy.signature_version,
        "explicit_roots": explicit_roots,
        "directory_names": directory_names,
        "file_names": file_names,
        "file_suffixes": file_suffixes,
    }
    rebuilt = InventoryExclusionPolicy.compile(
        explicit_roots,
        directory_names=directory_names,
        file_names=file_names,
        file_suffixes=file_suffixes,
    )
    if rebuilt.signature != policy.signature:
        raise ValueError("self-analysis inventory policy manifest is inconsistent")
    return manifest


# endregion [02]


# region [03] Reproducible command and completion manifest helpers


def _decimal_megabytes(byte_count: int) -> str:
    whole, remainder = divmod(byte_count, 1_000_000)
    if remainder == 0:
        return str(whole)
    return f"{whole}.{remainder:06d}".rstrip("0")


def self_analysis_commands(
    config: FrameworkConfig,
    root: Path,
    state_directory: Path,
) -> dict[str, list[str]]:
    """Build exact argv arrays equivalent to the effective protected run."""

    analyze = [
        "Neocortex",
        "--self-analysis",
        "--root",
        str(root),
        "--state-directory",
        str(state_directory),
        "--code-max-mb",
        _decimal_megabytes(config.code_max_file_bytes),
        "--code-max-text-chars",
        str(config.code_max_text_chars),
        "--code-chunk-chars",
        str(config.code_chunk_chars),
        "--code-complexity-warning",
        str(config.code_complexity_warning),
        "--code-function-lines-warning",
        str(config.code_function_lines_warning),
        "--code-cache-validation",
        config.code_cache_validation,
        "--no-code-generated",
        "--no-code-vendored",
    ]
    if config.code_max_documents is not None:
        analyze.extend(("--code-max-count", str(config.code_max_documents)))
    if config.code_retry_errors:
        analyze.append("--retry-code-errors")
    return {
        "analyze": analyze,
        "status": [
            "Neocortex",
            "--state-directory",
            str(state_directory),
            "--code-status",
            "--code-json",
        ],
    }


def _bounded_argv(values: Sequence[str], *, label: str) -> list[str]:
    result = list(values)
    if (
        not result
        or len(result) > 128
        or any(
            not isinstance(value, str)
            or not value
            or len(value.encode("utf-8")) > 32_768
            for value in result
        )
    ):
        raise ValueError(f"invalid self-analysis {label} argv")
    return result


def build_self_analysis_completion_manifest(
    *,
    run: Mapping[str, object],
    inventory: Mapping[str, object],
    inventory_policy: InventoryExclusionPolicy,
    code_processing_signature: str,
    code_summary: Mapping[str, object],
    safety_counts: Mapping[str, int],
    commands: Mapping[str, Sequence[str]],
) -> tuple[dict[str, object], str]:
    """Build and serialize one fixed, bounded completion manifest."""

    if (
        not code_processing_signature
        or code_processing_signature.strip() != code_processing_signature
        or len(code_processing_signature.encode("utf-8")) > 4096
    ):
        raise ValueError("invalid code processing signature")
    expected_zeroes = {
        "route_candidates",
        "file_actions",
        "run_actions",
        "organization_events",
    }
    if set(safety_counts) != expected_zeroes or any(
        type(value) is not int or value != 0 for value in safety_counts.values()
    ):
        raise ValueError("self-analysis safety counts must be exact zeroes")
    if set(commands) != {"analyze", "status"}:
        raise ValueError("self-analysis commands are incomplete")
    manifest: dict[str, object] = {
        "schema": SELF_ANALYSIS_MANIFEST_SCHEMA,
        "run": dict(run),
        "inventory": {
            **dict(inventory),
            "policy": inventory_policy_manifest(inventory_policy),
        },
        "code": {
            "route_name": "code",
            "input_source": "inventory_snapshot",
            "processing_signature": code_processing_signature,
            "summary": dict(code_summary),
        },
        "safety": dict(safety_counts),
        "commands": {
            "analyze": _bounded_argv(commands["analyze"], label="analyze"),
            "status": _bounded_argv(commands["status"], label="status"),
        },
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(payload.encode("utf-8")) > MAX_SELF_ANALYSIS_MANIFEST_BYTES:
        raise ValueError("self-analysis completion manifest exceeds its bound")
    return manifest, payload


# endregion [03]


__all__ = [
    "MAX_SELF_ANALYSIS_MANIFEST_BYTES",
    "LEGACY_SELF_ANALYSIS_MANIFEST_SCHEMAS",
    "SELF_ANALYSIS_MANIFEST_MESSAGE",
    "SELF_ANALYSIS_MANIFEST_PHASE",
    "SELF_ANALYSIS_MANIFEST_SCHEMA",
    "SELF_ANALYSIS_PROFILE_VERSION",
    "build_self_analysis_completion_manifest",
    "build_self_analysis_inventory_policy",
    "inventory_policy_manifest",
    "self_analysis_commands",
]
