"""Bounded direct CLI operations for structured code intelligence."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


def _state_path(args: argparse.Namespace) -> Path:
    return args.state_directory / "code.sqlite3"


def _emit(value: object, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(value)


def _error(operation: str, exc: BaseException) -> int:
    print(f"ERROR {operation} {type(exc).__name__}: {exc}", file=sys.stderr)
    return 2


# region [01] Status and diagnostics


@dataclass(frozen=True, slots=True)
class _CodeStatusSnapshot:
    schema_version: int
    counts: dict[str, int]
    latest_run: sqlite3.Row | None


def _code_status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "current_files": int(
            connection.execute(
                "SELECT COUNT(*) FROM files WHERE status='current'"
            ).fetchone()[0]
        ),
        "versions": int(
            connection.execute("SELECT COUNT(*) FROM file_versions").fetchone()[0]
        ),
        "current_symbols": int(
            connection.execute(
                """SELECT COUNT(*) FROM symbols s JOIN file_versions v
                ON v.version_id=s.version_id WHERE v.invalidated_ns IS NULL"""
            ).fetchone()[0]
        ),
        "current_references": int(
            connection.execute(
                """SELECT COUNT(*) FROM code_references r JOIN file_versions v
                ON v.version_id=r.version_id WHERE v.invalidated_ns IS NULL"""
            ).fetchone()[0]
        ),
        "current_diagnostics": int(
            connection.execute(
                """SELECT COUNT(*) FROM diagnostics d JOIN file_versions v
                ON v.version_id=d.version_id WHERE v.invalidated_ns IS NULL"""
            ).fetchone()[0]
        ),
        "projects": int(
            connection.execute(
                "SELECT COUNT(*) FROM projects WHERE status<>'historical'"
            ).fetchone()[0]
        ),
        "active_embedding_links": int(
            connection.execute(
                "SELECT COUNT(*) FROM embedding_links WHERE active=1"
            ).fetchone()[0]
        ),
    }


def _latest_code_run(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT analysis_run_id,framework_run_id,scan_id,
        CASE WHEN length(CAST(processing_signature AS BLOB))
            BETWEEN 1 AND 4096 THEN processing_signature END
            AS processing_signature,
        CASE WHEN length(CAST(status AS BLOB)) BETWEEN 1 AND 32
            THEN status END AS status,
        started_ns,completed_ns,candidates,
        processed,cache_hits,errors
        FROM analysis_runs ORDER BY analysis_run_id DESC LIMIT 1"""
    ).fetchone()


def _read_code_status_snapshot(path: Path) -> _CodeStatusSnapshot:
    from .code_schema import CODE_SCHEMA_VERSION, validate_code_schema
    from .self_analysis_status import quiescent_sqlite_database

    with quiescent_sqlite_database(path) as connection:
        validate_code_schema(connection)
        counts = _code_status_counts(connection)
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != CODE_SCHEMA_VERSION:
            raise RuntimeError(f"code state schema {version} is unsupported for status")
        latest = _latest_code_run(connection)
    return _CodeStatusSnapshot(version, counts, latest)


def _read_self_analysis_payload(
    args: argparse.Namespace,
    latest: sqlite3.Row | None,
) -> dict[str, object] | None:
    if not args.code_json or latest is None:
        return None
    from .self_analysis_status import (
        CodeRunStatusEvidence,
        read_self_analysis_status,
    )

    processing_signature = latest["processing_signature"]
    run_status = latest["status"]
    if not isinstance(processing_signature, str) or not isinstance(run_status, str):
        raise ValueError("latest code run has unbounded status evidence")
    status = read_self_analysis_status(
        args.state_directory,
        CodeRunStatusEvidence(
            analysis_run_id=int(latest["analysis_run_id"]),
            framework_run_id=int(latest["framework_run_id"]),
            scan_id=int(latest["scan_id"]),
            processing_signature=processing_signature,
            status=run_status,
        ),
    )
    return None if status is None else status.as_payload()


def _emit_missing_code_status(
    path: Path,
    analyzers: object,
    *,
    json_output: bool,
) -> None:
    payload = {
        "kind": "code-status",
        "database": str(path),
        "exists": False,
        "analyzers": analyzers,
        "self_analysis": None,
    }
    _emit(
        payload if json_output else f"CODE_STATUS database={path} exists=false",
        json_output=json_output,
    )


def _emit_code_status(
    path: Path,
    analyzers: object,
    snapshot: _CodeStatusSnapshot,
    self_analysis: dict[str, object] | None,
    *,
    json_output: bool,
) -> None:
    latest = snapshot.latest_run
    payload = {
        "kind": "code-status",
        "database": str(path),
        "exists": True,
        "schema_version": snapshot.schema_version,
        "counts": snapshot.counts,
        "latest_run": None if latest is None else dict(latest),
        "analyzers": analyzers,
        "self_analysis": self_analysis,
    }
    if json_output:
        _emit(payload, json_output=True)
        return
    print(
        f"CODE_STATUS database={path} schema={snapshot.schema_version} "
        + " ".join(f"{name}={value}" for name, value in snapshot.counts.items())
    )
    if latest is not None:
        print(
            f"CODE_RUN id={latest['analysis_run_id']} "
            f"framework_run={latest['framework_run_id']} status={latest['status']} "
            f"candidates={latest['candidates']} processed={latest['processed']} "
            f"cache_hits={latest['cache_hits']} errors={latest['errors']}"
        )


def run_code_status(args: argparse.Namespace) -> int:
    """Show bounded read-only state and lazy analyzer registration status."""

    from .code_analyzers import builtin_analyzer_registry
    from .self_analysis_status import require_sqlite_sidecars_absent

    path = _state_path(args)
    analyzers = builtin_analyzer_registry().status()
    try:
        require_sqlite_sidecars_absent(path)
        if not path.is_file():
            _emit_missing_code_status(path, analyzers, json_output=args.code_json)
            return 0
        snapshot = _read_code_status_snapshot(path)
        self_analysis = _read_self_analysis_payload(args, snapshot.latest_run)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _error("code-status", exc)
    _emit_code_status(
        path,
        analyzers,
        snapshot,
        self_analysis,
        json_output=args.code_json,
    )
    return 0


def run_code_doctor(args: argparse.Namespace) -> int:
    """Validate schema, FTS and optional tools without loading heavy analyzers."""

    from .code_analyzers import builtin_analyzer_registry
    from .code_schema import code_database, validate_code_schema

    path = _state_path(args)
    report: dict[str, object] = {
        "kind": "code-doctor",
        "database": str(path),
        "exists": path.is_file(),
        "analyzers": builtin_analyzer_registry().status(),
        "tools": {
            name: shutil.which(name)
            for name in ("cargo", "rustc", "rust-analyzer", "ruff", "mypy")
        },
    }
    if path.is_file():
        try:
            with code_database(path, readonly=True) as connection:
                validate_code_schema(connection)
                connection.execute(
                    "SELECT rowid FROM code_fts WHERE code_fts MATCH 'neocortex' LIMIT 1"
                ).fetchall()
                report["schema"] = "ok"
                report["foreign_key_violations"] = len(
                    connection.execute("PRAGMA foreign_key_check").fetchall()
                )
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            report["schema"] = "error"
            report["error"] = f"{type(exc).__name__}: {exc}"
            _emit(report, json_output=True)
            return 2
    else:
        report["schema"] = "not-initialized"
    _emit(report, json_output=True)
    return 0


# endregion [01]


# region [02] Search and reconstruction


def run_code_search(args: argparse.Namespace) -> int:
    from .code_contracts import CodeSearchQuery
    from .code_search import search_code

    try:
        hits = search_code(
            _state_path(args),
            CodeSearchQuery(
                text=args.code_search,
                modes=tuple(args.code_search_mode or ("hybrid",)),
                path=args.code_path,
                language=args.code_language,
                project=args.code_project,
                symbol=args.code_symbol,
                diagnostic=args.code_diagnostic,
                minimum_complexity=args.code_min_complexity,
                limit=args.code_search_limit,
            ),
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _error("code-search", exc)
    for hit in hits:
        if args.code_json:
            _emit({"kind": "code-search-hit", **asdict(hit)}, json_output=True)
        else:
            print(
                f"CODE_HIT score={hit.score:.6f} matches={','.join(hit.match_types)} "
                f"language={hit.language or '-'} project={hit.project or '-'} "
                f"path={json.dumps(hit.path, ensure_ascii=False)} "
                f"lines={hit.start_line}-{hit.end_line} "
                f"symbol={json.dumps(hit.symbol, ensure_ascii=False)} "
                f"snippet={json.dumps(hit.snippet, ensure_ascii=False)}"
            )
    return 0


def run_code_projects(args: argparse.Namespace) -> int:
    from .code_projects import list_projects

    try:
        projects = list_projects(_state_path(args))
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _error("code-projects", exc)
    for project in projects:
        if args.code_json:
            _emit({"kind": "code-project", **asdict(project)}, json_output=True)
        else:
            print(
                f"CODE_PROJECT id={project.project_id} name={json.dumps(project.name)} "
                f"ecosystem={project.ecosystem} status={project.status} "
                f"confidence={project.confidence:.3f} current={project.current_files} "
                f"historical={project.historical_files} "
                f"root={json.dumps(project.probable_root, ensure_ascii=False)}"
            )
    return 0


def run_code_reconstruct(args: argparse.Namespace) -> int:
    from .code_projects import reconstruct_project

    project: str | int = args.code_reconstruct
    if str(project).isdigit():
        project = int(project)
    try:
        manifest = reconstruct_project(
            _state_path(args),
            project,
            strategy=args.code_reconstruct_strategy,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError, LookupError) as exc:
        return _error("code-reconstruct", exc)
    if args.code_json:
        _emit({"kind": "code-reconstruction", **asdict(manifest)}, json_output=True)
        return 0
    print(
        f"CODE_RECONSTRUCTION project_id={manifest.project_id} "
        f"name={json.dumps(manifest.project_name)} ecosystem={manifest.ecosystem} "
        f"strategy={manifest.strategy} conflicts={len(manifest.conflicts)}"
    )
    for entry in manifest.entries:
        print(
            f"CODE_RECONSTRUCTION_ENTRY selected={str(entry.selected).lower()} "
            f"confidence={entry.confidence:.3f} relation={entry.relation} "
            f"proposed={json.dumps(entry.proposed_path)} "
            f"source={json.dumps(entry.source_path, ensure_ascii=False)} "
            f"version={entry.version_id} xxh3_128={entry.xxh3_128} "
            f"conflict={entry.conflict_group or '-'}"
        )
    for conflict in manifest.conflicts:
        print(f"CODE_RECONSTRUCTION_CONFLICT {conflict}")
    return 0


# endregion [02]


__all__ = [
    "run_code_doctor",
    "run_code_projects",
    "run_code_reconstruct",
    "run_code_search",
    "run_code_status",
]
