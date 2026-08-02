"""Bounded direct CLI operations for structured code intelligence."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO


def _state_path(args: argparse.Namespace) -> Path:
    return args.state_directory / "code.sqlite3"


def _console_text(value: str, stream: object) -> str:
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return value
    try:
        value.encode(encoding)
    except UnicodeEncodeError:
        return value.encode(encoding, errors="backslashreplace").decode(encoding)
    except LookupError:
        return value
    return value


def _print_console_line(value: str, *, file: TextIO | None = None) -> None:
    stream = sys.stdout if file is None else file
    print(_console_text(value, stream), file=stream)


def _emit(value: object, *, json_output: bool) -> None:
    if json_output:
        _print_console_line(json.dumps(value, ensure_ascii=True, sort_keys=True))
    else:
        _print_console_line(str(value))


def _error(operation: str, exc: BaseException) -> int:
    _print_console_line(
        f"ERROR {operation} {type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    return 2


# region [01] Status and diagnostics


@dataclass(frozen=True, slots=True)
class _CodeStatusSnapshot:
    schema_version: int
    counts: dict[str, int]
    latest_run: sqlite3.Row | None


def _code_status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    active_embedding_links = int(
        connection.execute(
            "SELECT COUNT(*) FROM embedding_links WHERE active=1"
        ).fetchone()[0]
    )
    current_embedding_links = int(
        connection.execute(
            """SELECT COUNT(*) FROM embedding_links e
            JOIN code_chunks c ON c.chunk_id=e.chunk_id
            JOIN file_versions v ON v.version_id=c.version_id
            JOIN files f ON f.current_version_id=v.version_id
            WHERE e.active=1 AND f.status='current'
            AND v.invalidated_ns IS NULL"""
        ).fetchone()[0]
    )
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
        "active_embedding_links": active_embedding_links,
        "current_embedding_links": current_embedding_links,
        "stale_embedding_links": active_embedding_links - current_embedding_links,
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
    _print_console_line(
        f"CODE_STATUS database={path} schema={snapshot.schema_version} "
        + " ".join(f"{name}={value}" for name, value in snapshot.counts.items())
    )
    if latest is not None:
        _print_console_line(
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


def run_code_review(args: argparse.Namespace) -> int:
    """Rank confirmed Python hotspots in the published self-analysis snapshot."""

    from .code_review import review_code_state

    try:
        result = review_code_state(
            args.state_directory,
            limit=args.code_review_limit,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _error("code-review", exc)
    if args.code_json:
        _emit(result.as_payload(), json_output=True)
        return 0 if result.status == "ready" else 2
    if result.status != "ready":
        _print_console_line(
            f"CODE_REVIEW status=abstained reason={result.reason} "
            f"database={json.dumps(result.database, ensure_ascii=True)}"
        )
        return 2
    if result.snapshot is None or result.coverage is None or result.digest is None:
        return _error("code-review", RuntimeError("ready result is incomplete"))
    _print_console_line(
        f"CODE_REVIEW status=ready freshness={result.snapshot.freshness} "
        f"current={int(result.snapshot.current)} findings={len(result.findings)} "
        f"recommendations={len(result.recommendations)} ranking={result.ranking} "
        f"actionability={result.actionability_version} digest={result.digest.xxh3_128}"
    )
    _print_console_line(
        f"CODE_REVIEW_COVERAGE python_files={result.coverage.current_python_files} "
        f"complete={result.coverage.complete_python_files} "
        f"hotspots={result.coverage.candidate_hotspots} "
        f"probable_dead_suppressed={result.coverage.probable_dead_suppressed} "
        f"resolved_calls={result.coverage.resolved_call_edges}/"
        f"{result.coverage.call_edges}"
    )
    if result.recommendation_status == "abstained":
        _print_console_line(
            "CODE_REVIEW_RECOMMENDATION status=abstained "
            f"reason={result.recommendation_reason}"
        )
    for recommendation in result.recommendations:
        _print_console_line(
            "CODE_REVIEW_RECOMMENDATION status=ready "
            f"recommendation_rank={recommendation.recommendation_rank} "
            f"hotspot_rank={recommendation.hotspot_rank} "
            f"construction={recommendation.construction} "
            f"risk={recommendation.change_risk} "
            f"production_callers={recommendation.production_callers} "
            f"test_callers={recommendation.test_callers} "
            f"path={json.dumps(recommendation.path, ensure_ascii=True)} "
            f"symbol={json.dumps(recommendation.symbol, ensure_ascii=True)}"
        )
    for finding in result.findings:
        _print_console_line(
            f"CODE_REVIEW_FINDING rank={finding.rank} "
            f"score_bp={finding.score_basis_points} category={finding.category} "
            f"construction={finding.construction} "
            f"actionability={finding.actionability} risk={finding.change_risk} "
            f"complexity={finding.complexity} lines={finding.function_lines} "
            f"production_callers={finding.impact.production_callers} "
            f"test_callers={finding.impact.test_callers + finding.impact.fixture_callers} "
            f"path={json.dumps(finding.path, ensure_ascii=True)} "
            f"symbol={json.dumps(finding.symbol, ensure_ascii=True)} "
            f"line={finding.start_line}"
        )
    for limitation in result.limitations:
        _print_console_line(f"CODE_REVIEW_LIMITATION {limitation}")
    return 0


def run_code_publication_diff(args: argparse.Namespace) -> int:
    """Compare two completed Code publications without mutating either state."""

    from .code_publication_diff import compare_code_publications

    try:
        result = compare_code_publications(
            Path(args.code_publication_diff),
            args.state_directory,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _error("code-publication-diff", exc)
    if args.code_json:
        _emit(result.as_payload(), json_output=True)
        return 0 if result.status == "ready" else 2
    if result.status != "ready":
        _print_console_line(
            f"CODE_PUBLICATION_DIFF status=abstained reason={result.reason} "
            f"baseline={json.dumps(result.baseline_database, ensure_ascii=True)} "
            f"current={json.dumps(result.current_database, ensure_ascii=True)}"
        )
        return 2
    if (
        result.baseline is None
        or result.current is None
        or result.calls is None
        or result.hotspots is None
        or result.probable_dead_delta is None
        or result.digest is None
    ):
        return _error(
            "code-publication-diff",
            RuntimeError("ready result is incomplete"),
        )
    _print_console_line(
        f"CODE_PUBLICATION_DIFF status=ready digest={result.digest.xxh3_128} "
        f"baseline_calls={result.baseline.resolved_call_edges}/"
        f"{result.baseline.call_edges} current_calls="
        f"{result.current.resolved_call_edges}/{result.current.call_edges}"
    )
    _print_console_line(
        f"CODE_PUBLICATION_DIFF_CALLS common={result.calls.common_call_sites} "
        f"baseline_only={result.calls.baseline_only_call_sites} "
        f"current_only={result.calls.current_only_call_sites} "
        f"newly_resolved={result.calls.newly_resolved} "
        f"corrected={result.calls.corrected} lost={result.calls.lost}"
    )
    _print_console_line(
        f"CODE_PUBLICATION_DIFF_HOTSPOTS common={result.hotspots.common} "
        f"added={result.hotspots.added} removed={result.hotspots.removed} "
        f"changed_evidence={result.hotspots.changed_evidence} "
        f"probable_dead_delta={result.probable_dead_delta:+d}"
    )
    for limitation in result.limitations:
        _print_console_line(f"CODE_PUBLICATION_DIFF_LIMITATION {limitation}")
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
    from .code_semantic_links import code_semantic_search_availability
    from .code_search import search_code

    try:
        query = CodeSearchQuery(
            text=args.code_search,
            modes=tuple(args.code_search_mode or ("hybrid",)),
            path=args.code_path,
            language=args.code_language,
            project=args.code_project,
            symbol=args.code_symbol,
            diagnostic=args.code_diagnostic,
            minimum_complexity=args.code_min_complexity,
            limit=args.code_search_limit,
        )
        semantic_requested = any(mode in {"semantic", "hybrid"} for mode in query.modes)
        semantic_availability = (
            code_semantic_search_availability(
                args.state_directory,
                model_cache_override=args.semantic_model_cache,
            )
            if semantic_requested
            else None
        )
        hits = search_code(
            _state_path(args),
            query,
            semantic_model_cache=args.semantic_model_cache,
            semantic_threads=args.semantic_threads,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _error("code-search", exc)
    if semantic_availability is not None:
        semantic_payload = {
            "kind": "code-search-channel",
            "channel": "semantic",
            **asdict(semantic_availability),
        }
        if args.code_json:
            _emit(semantic_payload, json_output=True)
        else:
            _print_console_line(
                "CODE_SEARCH_CHANNEL name=semantic "
                f"available={int(semantic_availability.available)} "
                f"reason={semantic_availability.reason} "
                f"generation={semantic_availability.generation_id or '-'} "
                f"current_links={semantic_availability.current_links} "
                f"calibration={semantic_availability.calibration}"
            )
    for hit in hits:
        if args.code_json:
            _emit({"kind": "code-search-hit", **asdict(hit)}, json_output=True)
        else:
            _print_console_line(
                f"CODE_HIT score={hit.score:.6f} matches={','.join(hit.match_types)} "
                f"language={hit.language or '-'} project={hit.project or '-'} "
                f"path={json.dumps(hit.path, ensure_ascii=False)} "
                f"lines={hit.start_line}-{hit.end_line} "
                f"symbol={json.dumps(hit.symbol, ensure_ascii=False)} "
                f"snippet={json.dumps(hit.snippet, ensure_ascii=False)}"
            )
    if (
        semantic_availability is not None
        and query.modes == ("semantic",)
        and not semantic_availability.available
    ):
        return 2
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
            _print_console_line(
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
    _print_console_line(
        f"CODE_RECONSTRUCTION project_id={manifest.project_id} "
        f"name={json.dumps(manifest.project_name)} ecosystem={manifest.ecosystem} "
        f"strategy={manifest.strategy} conflicts={len(manifest.conflicts)}"
    )
    for entry in manifest.entries:
        _print_console_line(
            f"CODE_RECONSTRUCTION_ENTRY selected={str(entry.selected).lower()} "
            f"confidence={entry.confidence:.3f} relation={entry.relation} "
            f"proposed={json.dumps(entry.proposed_path)} "
            f"source={json.dumps(entry.source_path, ensure_ascii=False)} "
            f"version={entry.version_id} xxh3_128={entry.xxh3_128} "
            f"conflict={entry.conflict_group or '-'}"
        )
    for conflict in manifest.conflicts:
        _print_console_line(f"CODE_RECONSTRUCTION_CONFLICT {conflict}")
    return 0


# endregion [02]


__all__ = [
    "run_code_doctor",
    "run_code_publication_diff",
    "run_code_projects",
    "run_code_reconstruct",
    "run_code_review",
    "run_code_search",
    "run_code_status",
]
