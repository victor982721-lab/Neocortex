"""Bounded direct CLI operations for structured code intelligence."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from .code_architecture_analysis import CodeArchitectureAnalysis
    from .code_coverage_analysis import CodeCoverageAnalysis
    from .code_publication_diff import (
        CodeArchitectureDelta,
        CodeModuleArchitectureDelta,
        CodeUnusedAnalysisDelta,
    )
    from .code_review_models import CodeReviewResult


_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT = 20
_CODE_CLI_COVERAGE_EXAMPLE_LIMIT = 20
_CODE_CLI_UNUSED_EXAMPLE_LIMIT = 20
_CODE_ARCHITECTURE_PROVIDER_IDS = (
    "complexipy-cognitive",
    "grimp-architecture",
    "ruff-analyze-imports",
)
_CODE_ARCHITECTURE_ACCEPTANCE_GATES = frozenset(
    {
        "architecture_contracts_not_degraded",
        "module_complexity_not_displaced",
        "no_new_import_cycles",
    }
)


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
    external_evidence: dict[str, object]
    external_evidence_suite: dict[str, object]
    architecture: dict[str, object]
    test_coverage: dict[str, object]
    unused_analysis: dict[str, object] = field(default_factory=dict)


def _architecture_abstained_payload(
    database: str,
    reason: str,
    *,
    analysis_run_id: int | None = None,
) -> dict[str, object]:
    return {
        "schema": "neocortex.code-architecture-analysis/v1",
        "status": "abstained",
        "reason": reason,
        "gate": "abstained",
        "analysis_run_id": analysis_run_id,
        "providers": [
            {
                "provider_id": provider_id,
                "status": "abstained",
                "reason": reason,
                "tool_name": None,
                "tool_version": None,
                "execution": None,
                "provider_gate": None,
                "metrics": 0,
                "relations": 0,
            }
            for provider_id in _CODE_ARCHITECTURE_PROVIDER_IDS
        ],
        "summary": None,
        "counts": {
            "modules": 0,
            "symbols": 0,
            "imports": 0,
            "cycles": 0,
            "contracts": 0,
            "failed_contracts": 0,
        },
        "gates": [
            {"gate": gate, "status": "not_evaluated", "reason": reason}
            for gate in (
                "import_graph_consensus",
                "architecture_contracts",
                "module_complexity_displacement",
            )
        ],
        "database": database,
    }


def _architecture_status_payload(
    analysis: CodeArchitectureAnalysis,
) -> dict[str, object]:
    failed_contracts = sum(item.status == "failed" for item in analysis.contracts)
    return {
        "schema": "neocortex.code-architecture-analysis/v1",
        "status": analysis.status,
        "reason": analysis.reason,
        "gate": analysis.gate,
        "analysis_run_id": analysis.analysis_run_id,
        "providers": [
            {
                "provider_id": item.provider_id,
                "status": item.status,
                "reason": item.reason,
                "tool_name": item.tool_name,
                "tool_version": item.tool_version,
                "execution": item.execution,
                "provider_gate": item.provider_gate,
                "metrics": item.metrics,
                "relations": item.relations,
            }
            for item in analysis.providers
        ],
        "summary": None if analysis.summary is None else asdict(analysis.summary),
        "counts": {
            "modules": len(analysis.modules),
            "symbols": len(analysis.symbols),
            "imports": len(analysis.imports),
            "cycles": len(analysis.cycles),
            "contracts": len(analysis.contracts),
            "failed_contracts": failed_contracts,
        },
        "gates": [asdict(item) for item in analysis.gates],
        "database": analysis.database,
    }


def _coverage_status_payload(
    analysis: CodeCoverageAnalysis,
) -> dict[str, object]:
    """Project bounded status facts without dumping every measured symbol/test edge."""

    return {
        "kind": "code-coverage-analysis",
        "schema": "neocortex.code-coverage-analysis/v1",
        "database": analysis.database,
        "analysis_run_id": analysis.analysis_run_id,
        "provider_id": analysis.provider_id,
        "tool_run_id": analysis.tool_run_id,
        "effective_tool_run_id": analysis.effective_tool_run_id,
        "status": analysis.status,
        "reason": analysis.reason,
        "suite_selection": analysis.suite_selection,
        "measurement_complete": analysis.measurement_complete,
        "content_executed": analysis.content_executed,
        "tool_versions": [asdict(item) for item in analysis.tool_versions],
        "suite_signature": analysis.suite_signature,
        "configuration_signature": analysis.configuration_signature,
        "measurement_scope_signature": analysis.measurement_scope_signature,
        "outcomes": None if analysis.outcomes is None else asdict(analysis.outcomes),
        "totals": None if analysis.totals is None else asdict(analysis.totals),
        "counts": {
            "modules": len(analysis.modules),
            "symbols": len(analysis.symbols),
            "test_relations": len(analysis.test_relations),
            "failed_tests": len(analysis.failed_test_nodeids),
        },
        "failed_test_examples": list(
            analysis.failed_test_nodeids[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]
        ),
        "failed_test_examples_truncated": (
            len(analysis.failed_test_nodeids) > _CODE_CLI_COVERAGE_EXAMPLE_LIMIT
        ),
        "gates": [asdict(item) for item in analysis.gates],
        "limitations": list(analysis.limitations),
    }


def _coverage_abstained_payload(
    database: str,
    reason: str,
    *,
    analysis_run_id: int | None = None,
) -> dict[str, object]:
    return {
        "kind": "code-coverage-analysis",
        "schema": "neocortex.code-coverage-analysis/v1",
        "database": database,
        "analysis_run_id": analysis_run_id,
        "provider_id": "pytest-coverage-trusted-deep",
        "tool_run_id": None,
        "effective_tool_run_id": None,
        "status": "abstained",
        "reason": reason,
        "suite_selection": None,
        "measurement_complete": None,
        "content_executed": None,
        "tool_versions": [],
        "suite_signature": None,
        "configuration_signature": None,
        "measurement_scope_signature": None,
        "outcomes": None,
        "totals": None,
        "counts": {"modules": 0, "symbols": 0, "test_relations": 0, "failed_tests": 0},
        "failed_test_examples": [],
        "failed_test_examples_truncated": False,
        "gates": [
            {"gate": gate, "status": "not_evaluated", "reason": reason}
            for gate in ("tests_passed", "coverage_available")
        ],
        "limitations": ["trusted_deep_evidence_not_ready"],
    }


def _unused_abstained_payload(
    database: str,
    reason: str,
    *,
    analysis_run_id: int | None = None,
) -> dict[str, object]:
    return {
        "kind": "code-unused-analysis",
        "schema": "neocortex.code-unused-analysis/v1",
        "database": database,
        "analysis_run_id": analysis_run_id,
        "status": "abstained",
        "reason": reason,
        "counts": {
            "total": 0,
            "explained_usage": 0,
            "dynamic_usage_possible": 0,
            "insufficient_evidence": 0,
            "probable_unused_high_consensus": 0,
        },
        "providers": [],
        "candidates": [],
        "calibration": None,
        "holdout": None,
        "gates": [],
        "limitations": ["unused_evidence_not_ready"],
        "authority": "advisory",
        "mutation_authority": False,
    }


def _code_status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    active_embedding_links = int(
        connection.execute("SELECT COUNT(*) FROM embedding_links WHERE active=1").fetchone()[0]
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
            connection.execute("SELECT COUNT(*) FROM files WHERE status='current'").fetchone()[0]
        ),
        "versions": int(connection.execute("SELECT COUNT(*) FROM file_versions").fetchone()[0]),
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
        "current_external_diagnostics": int(
            connection.execute(
                """SELECT COUNT(*) FROM diagnostics d JOIN file_versions v
                ON v.version_id=d.version_id WHERE v.invalidated_ns IS NULL
                AND d.source='external:ruff'"""
            ).fetchone()[0]
        ),
        "current_provider_diagnostics": int(
            connection.execute(
                """SELECT COUNT(*) FROM diagnostics d JOIN file_versions v
                ON v.version_id=d.version_id WHERE v.invalidated_ns IS NULL
                AND d.source LIKE 'external:%' AND d.source<>'external:ruff'"""
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
    from .code_architecture_analysis import read_code_architecture_analysis
    from .code_coverage_analysis import read_code_coverage_analysis
    from .code_external_evidence import read_external_evidence
    from .code_review_models import bounded_code_unused_payload
    from .code_unused_analysis import read_code_unused_analysis
    from .code_schema import CODE_SCHEMA_VERSION, validate_code_schema
    from .external_evidence_store import read_external_evidence_suite
    from .self_analysis_status import quiescent_sqlite_database

    with quiescent_sqlite_database(path) as connection:
        validate_code_schema(connection)
        counts = _code_status_counts(connection)
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != CODE_SCHEMA_VERSION:
            raise RuntimeError(f"code state schema {version} is unsupported for status")
        latest = _latest_code_run(connection)
        external_evidence = (
            read_external_evidence(
                connection,
                int(latest["analysis_run_id"]),
                enforce_current_runtime=True,
            )[0].as_payload()
            if latest is not None
            else read_external_evidence(
                connection,
                -1,
                enforce_current_runtime=True,
            )[0].as_payload()
        )
        suite = read_external_evidence_suite(
            connection,
            -1 if latest is None else int(latest["analysis_run_id"]),
            enforce_current_runtime=True,
        ).as_payload()
        if latest is None:
            architecture = _architecture_abstained_payload(
                str(path),
                "code_run_missing",
            )
        elif latest["status"] != "completed":
            architecture = _architecture_abstained_payload(
                str(path),
                f"code_run_not_completed:{latest['status']}",
                analysis_run_id=int(latest["analysis_run_id"]),
            )
        else:
            architecture = _architecture_status_payload(
                read_code_architecture_analysis(
                    connection,
                    int(latest["analysis_run_id"]),
                    database=str(path),
                )
            )
        if latest is None:
            test_coverage = _coverage_abstained_payload(
                str(path),
                "code_run_missing",
            )
        elif latest["status"] != "completed":
            test_coverage = _coverage_abstained_payload(
                str(path),
                f"code_run_not_completed:{latest['status']}",
                analysis_run_id=int(latest["analysis_run_id"]),
            )
        else:
            test_coverage = _coverage_status_payload(
                read_code_coverage_analysis(
                    connection,
                    int(latest["analysis_run_id"]),
                    database=str(path),
                )
            )
        unused_analysis = bounded_code_unused_payload(
            read_code_unused_analysis(
                connection,
                -1 if latest is None else int(latest["analysis_run_id"]),
                database=str(path),
            )
        )
    return _CodeStatusSnapshot(
        version,
        counts,
        latest,
        external_evidence,
        suite,
        architecture,
        test_coverage,
        unused_analysis,
    )


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
    architecture = _architecture_abstained_payload(
        str(path),
        "code_state_missing",
    )
    payload = {
        "kind": "code-status",
        "database": str(path),
        "exists": False,
        "analyzers": analyzers,
        "self_analysis": None,
        "external_evidence": {
            "status": "not_recorded",
            "reason": "code_state_missing",
            "provider": "ruff",
        },
        "external_evidence_suite": {
            "schema": "neocortex.external-evidence-suite/v1",
            "profile": "protected",
            "status": "not_recorded",
            "providers": [],
            "type_consensus": {"status": "not_comparable"},
            "gates": [],
        },
        "architecture": architecture,
        "test_coverage": _coverage_abstained_payload(str(path), "code_state_missing"),
        "unused_analysis": _unused_abstained_payload(str(path), "code_state_missing"),
    }
    if json_output:
        _emit(payload, json_output=True)
        return
    _emit(f"CODE_STATUS database={path} exists=false", json_output=False)
    _emit_code_status_architecture(architecture)
    _emit_code_coverage(
        "CODE_COVERAGE",
        _coverage_abstained_payload(str(path), "code_state_missing"),
    )
    _emit_code_unused(
        "CODE_UNUSED",
        _unused_abstained_payload(str(path), "code_state_missing"),
    )


def _emit_code_status_architecture(architecture: dict[str, object]) -> None:
    counts = architecture.get("counts")
    bounded_counts = counts if isinstance(counts, dict) else {}
    _print_console_line(
        f"CODE_ARCHITECTURE status={architecture.get('status')} "
        f"gate={architecture.get('gate')} "
        f"modules={bounded_counts.get('modules', 0)} "
        f"imports={bounded_counts.get('imports', 0)} "
        f"cycles={bounded_counts.get('cycles', 0)} "
        f"contracts={bounded_counts.get('contracts', 0)} "
        f"failed_contracts={bounded_counts.get('failed_contracts', 0)} "
        f"reason={json.dumps(architecture.get('reason'), ensure_ascii=True)}"
    )
    architecture_summary = architecture.get("summary")
    if isinstance(architecture_summary, dict):
        _print_console_line(
            "CODE_ARCHITECTURE_SUMMARY "
            f"modules={architecture_summary.get('modules', 0)} "
            f"import_edges={architecture_summary.get('import_edges', 0)} "
            f"consensus_edges={architecture_summary.get('consensus_edges', 0)} "
            f"graph_disagreements={architecture_summary.get('graph_disagreements', 0)} "
            f"cyclic_sccs={architecture_summary.get('cyclic_sccs', 0)}"
        )
    else:
        _print_console_line("CODE_ARCHITECTURE_SUMMARY status=not_evaluated")
    architecture_providers = architecture.get("providers")
    if isinstance(architecture_providers, list):
        for provider in architecture_providers:
            if not isinstance(provider, dict):
                continue
            _print_console_line(
                f"CODE_ARCHITECTURE_PROVIDER id={provider.get('provider_id')} "
                f"status={provider.get('status')} execution={provider.get('execution')} "
                f"metrics={provider.get('metrics', 0)} "
                f"relations={provider.get('relations', 0)} "
                f"gate={provider.get('provider_gate')}"
            )
    architecture_gates = architecture.get("gates")
    if isinstance(architecture_gates, list):
        for gate in architecture_gates:
            if not isinstance(gate, dict):
                continue
            _print_console_line(
                f"CODE_ARCHITECTURE_GATE id={gate.get('gate')} "
                f"status={gate.get('status')} "
                f"reason={json.dumps(gate.get('reason'), ensure_ascii=True)}"
            )


def _emit_code_coverage(prefix: str, coverage: dict[str, object]) -> None:
    """Render bounded trusted-deep evidence without assuming it is available."""

    outcomes_value = coverage.get("outcomes")
    totals_value = coverage.get("totals")
    outcomes = outcomes_value if isinstance(outcomes_value, dict) else {}
    totals = totals_value if isinstance(totals_value, dict) else {}
    _print_console_line(
        f"{prefix} status={coverage.get('status')} "
        f"suite={coverage.get('suite_selection')} "
        f"measurement_complete={int(bool(coverage.get('measurement_complete')))} "
        f"content_executed={int(bool(coverage.get('content_executed')))} "
        f"tests={outcomes.get('passed', 0)}/{outcomes.get('selected', 0)} "
        f"collected={outcomes.get('collected', 0)} "
        f"failed={outcomes.get('failed', 0)} skipped={outcomes.get('skipped', 0)} "
        f"lines={totals.get('covered_lines', 0)}/{totals.get('executable_lines', 0)} "
        f"branches={totals.get('covered_branch_exits', 0)}/"
        f"{totals.get('branch_exits', 0)} "
        f"reason={json.dumps(coverage.get('reason'), ensure_ascii=True)}"
    )
    gates_value = coverage.get("gates")
    if not isinstance(gates_value, (list, tuple)):
        return
    for gate in gates_value[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]:
        if not isinstance(gate, dict):
            continue
        _print_console_line(
            f"{prefix}_GATE id={gate.get('gate')} status={gate.get('status')} "
            f"reason={json.dumps(gate.get('reason'), ensure_ascii=True)}"
        )
    failed_tests = coverage.get("failed_test_nodeids")
    if failed_tests is None:
        failed_tests = coverage.get("failed_test_examples")
    if isinstance(failed_tests, (list, tuple)):
        for nodeid in failed_tests[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]:
            _print_console_line(
                f"{prefix}_FAILED_TEST nodeid={json.dumps(nodeid, ensure_ascii=True)}"
            )


def _emit_code_unused(prefix: str, analysis: dict[str, object]) -> None:
    """Render bounded advisory unused-code evidence across all four states."""

    counts_value = analysis.get("counts")
    counts = counts_value if isinstance(counts_value, dict) else {}
    state_total = sum(
        int(counts.get(state, 0))
        for state in (
            "explained_usage",
            "dynamic_usage_possible",
            "insufficient_evidence",
            "probable_unused_high_consensus",
        )
    )
    _print_console_line(
        f"{prefix} status={analysis.get('status')} "
        f"total={counts.get('total', state_total)} "
        f"explained_usage={counts.get('explained_usage', 0)} "
        f"dynamic_usage_possible={counts.get('dynamic_usage_possible', 0)} "
        f"insufficient_evidence={counts.get('insufficient_evidence', 0)} "
        f"probable_unused_high_consensus="
        f"{counts.get('probable_unused_high_consensus', 0)} "
        f"authority={analysis.get('authority')} "
        f"mutation_authority={int(bool(analysis.get('mutation_authority')))} "
        f"reason={json.dumps(analysis.get('reason'), ensure_ascii=True)}"
    )
    providers = analysis.get("providers")
    if isinstance(providers, (list, tuple)):
        for provider in providers[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
            if not isinstance(provider, dict):
                continue
            _print_console_line(
                f"{prefix}_PROVIDER id={provider.get('provider_id')} "
                f"status={provider.get('status')} "
                f"comparability={provider.get('comparability')} "
                f"findings={provider.get('findings', 0)} "
                f"eligible={provider.get('eligible_candidates', 0)} "
                f"covered={provider.get('covered_candidates', 0)} "
                f"reason={json.dumps(provider.get('reason'), ensure_ascii=True)}"
            )
    for split in ("calibration", "holdout"):
        report = analysis.get(split)
        if not isinstance(report, dict):
            _print_console_line(f"{prefix}_{split.upper()} status=not_evaluated")
            continue
        _print_console_line(
            f"{prefix}_{split.upper()} signature={report.get('signature')} "
            f"samples={report.get('total', 0)} "
            f"precision={report.get('precision')} recall={report.get('recall')} "
            f"abstention={report.get('abstention_rate', report.get('abstention'))} "
            f"unsupported={report.get('unsupported', 0)}"
        )
    candidates = analysis.get("candidates")
    if isinstance(candidates, (list, tuple)):
        for candidate in candidates[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
            if not isinstance(candidate, dict):
                continue
            reasons = candidate.get("reasons")
            bounded_reasons = (
                reasons[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]
                if isinstance(reasons, (list, tuple))
                else []
            )
            _print_console_line(
                f"{prefix}_CANDIDATE id={candidate.get('candidate_id')} "
                f"state={candidate.get('state')} "
                f"path={json.dumps(candidate.get('relative_path'), ensure_ascii=True)} "
                f"symbol={json.dumps(candidate.get('symbol'), ensure_ascii=True)} "
                f"line={candidate.get('start_line')} "
                f"providers={json.dumps(candidate.get('provider_ids'), ensure_ascii=True)} "
                f"reasons={json.dumps(bounded_reasons, ensure_ascii=True)}"
            )
        if len(candidates) > _CODE_CLI_UNUSED_EXAMPLE_LIMIT:
            _print_console_line(
                f"{prefix}_CANDIDATES shown={_CODE_CLI_UNUSED_EXAMPLE_LIMIT} "
                f"omitted={len(candidates) - _CODE_CLI_UNUSED_EXAMPLE_LIMIT}"
            )
    limitations = analysis.get("limitations")
    if isinstance(limitations, (list, tuple)):
        for limitation in limitations[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
            _print_console_line(f"{prefix}_LIMITATION {limitation}")
    gates = analysis.get("gates")
    if isinstance(gates, (list, tuple)):
        for gate in gates[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
            if not isinstance(gate, dict):
                continue
            _print_console_line(
                f"{prefix}_GATE id={gate.get('gate')} status={gate.get('status')} "
                f"reason={json.dumps(gate.get('reason'), ensure_ascii=True)}"
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
        "external_evidence": snapshot.external_evidence,
        "analysis_profile": snapshot.external_evidence_suite.get("profile"),
        "external_evidence_suite": snapshot.external_evidence_suite,
        "architecture": snapshot.architecture,
        "test_coverage": snapshot.test_coverage,
        "unused_analysis": snapshot.unused_analysis,
    }
    if json_output:
        _emit(payload, json_output=True)
        return
    _print_console_line(
        f"CODE_STATUS database={path} schema={snapshot.schema_version} "
        + " ".join(f"{name}={value}" for name, value in snapshot.counts.items())
    )
    suite = snapshot.external_evidence_suite
    _print_console_line(
        f"CODE_PROVIDER_SUITE profile={suite.get('profile')} status={suite.get('status')}"
    )
    providers = suite.get("providers")
    if isinstance(providers, list):
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            _print_console_line(
                f"CODE_PROVIDER id={provider.get('provider_id')} "
                f"status={provider.get('status')} execution={provider.get('execution')} "
                f"findings={provider.get('findings', 0)} "
                f"metrics={provider.get('metrics', 0)} "
                f"relations={provider.get('relations', 0)} "
                f"content_executed={int(bool(provider.get('content_executed')))} "
                f"gate={provider.get('gate')}"
            )
    if latest is not None:
        _print_console_line(
            f"CODE_RUN id={latest['analysis_run_id']} "
            f"framework_run={latest['framework_run_id']} status={latest['status']} "
            f"candidates={latest['candidates']} processed={latest['processed']} "
            f"cache_hits={latest['cache_hits']} errors={latest['errors']}"
        )
    external = snapshot.external_evidence
    _print_console_line(
        f"CODE_EXTERNAL provider=ruff status={external['status']} "
        f"execution={external.get('execution')} diagnostics="
        f"{external.get('diagnostics', 0)} gate={external.get('gate')}"
    )
    _emit_code_status_architecture(snapshot.architecture)
    _emit_code_coverage("CODE_COVERAGE", snapshot.test_coverage)
    _emit_code_unused("CODE_UNUSED", snapshot.unused_analysis)


def _emit_code_review_architecture(analysis: CodeArchitectureAnalysis) -> None:
    failed_contracts = tuple(item for item in analysis.contracts if item.status == "failed")
    _print_console_line(
        f"CODE_REVIEW_ARCHITECTURE status={analysis.status} gate={analysis.gate} "
        f"failed_contracts={len(failed_contracts)} "
        f"reason={json.dumps(analysis.reason, ensure_ascii=True)}"
    )
    if analysis.summary is None:
        _print_console_line("CODE_REVIEW_ARCHITECTURE_SUMMARY status=not_evaluated")
    else:
        summary = analysis.summary
        _print_console_line(
            f"CODE_REVIEW_ARCHITECTURE_SUMMARY modules={summary.modules} "
            f"import_edges={summary.import_edges} consensus_edges={summary.consensus_edges} "
            f"graph_disagreements={summary.graph_disagreements} "
            f"cyclic_sccs={summary.cyclic_sccs}"
        )
    for gate in analysis.gates:
        _print_console_line(
            f"CODE_REVIEW_ARCHITECTURE_GATE id={gate.gate} status={gate.status} "
            f"reason={json.dumps(gate.reason, ensure_ascii=True)}"
        )
    for contract in failed_contracts[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT]:
        _print_console_line(
            "CODE_REVIEW_ARCHITECTURE_CONTRACT status=failed "
            f"id={json.dumps(contract.contract_id, ensure_ascii=True)} "
            f"violations={contract.violations} "
            f"importers={json.dumps(contract.importer_modules, ensure_ascii=True)} "
            f"imported={json.dumps(contract.imported_modules, ensure_ascii=True)}"
        )
    if len(failed_contracts) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT:
        _print_console_line(
            "CODE_REVIEW_ARCHITECTURE_CONTRACTS "
            f"shown={_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT} "
            f"omitted={len(failed_contracts) - _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT}"
        )


def _module_architecture_changed(module: CodeModuleArchitectureDelta) -> bool:
    return bool(
        module.cognitive_complexity_delta
        or module.fan_in_delta
        or module.fan_out_delta
        or module.baseline_cycle_ids != module.current_cycle_ids
        or module.baseline_contract_ids != module.current_contract_ids
    )


def _emit_code_publication_architecture(architecture: CodeArchitectureDelta) -> None:
    modules = architecture.modules
    changed_modules = tuple(item for item in modules if _module_architecture_changed(item))
    added_contracts = architecture.added_failed_contracts
    resolved_contracts = architecture.resolved_failed_contracts
    added_cycles = architecture.added_cycles
    resolved_cycles = architecture.resolved_cycles
    displacements = architecture.displaced_complexity
    _print_console_line(
        f"CODE_PUBLICATION_DIFF_ARCHITECTURE status={architecture.status} "
        f"module_deltas={len(modules)} changed_modules={len(changed_modules)} "
        f"added_failed_contracts={len(added_contracts)} "
        f"resolved_failed_contracts={len(resolved_contracts)} "
        f"added_cycles={len(added_cycles)} resolved_cycles={len(resolved_cycles)} "
        f"displacements={len(displacements)} "
        f"contracts_gate={architecture.architecture_contracts_not_degraded} "
        f"cycles_gate={architecture.no_new_import_cycles} "
        f"displacement_gate={architecture.module_complexity_not_displaced} "
        f"reason={json.dumps(architecture.reason, ensure_ascii=True)}"
    )
    _print_console_line(
        "CODE_PUBLICATION_DIFF_ARCHITECTURE_CONTRACTS "
        f"added={json.dumps(added_contracts[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT], ensure_ascii=True)} "
        f"resolved={json.dumps(resolved_contracts[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT], ensure_ascii=True)} "
        f"added_truncated={int(len(added_contracts) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)} "
        f"resolved_truncated={int(len(resolved_contracts) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)}"
    )
    _print_console_line(
        "CODE_PUBLICATION_DIFF_ARCHITECTURE_CYCLES "
        f"added={json.dumps(added_cycles[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT], ensure_ascii=True)} "
        f"resolved={json.dumps(resolved_cycles[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT], ensure_ascii=True)} "
        f"added_truncated={int(len(added_cycles) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)} "
        f"resolved_truncated={int(len(resolved_cycles) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)}"
    )
    for module in changed_modules[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT]:
        _print_console_line(
            "CODE_PUBLICATION_DIFF_ARCHITECTURE_MODULE "
            f"module={json.dumps(module.module_id, ensure_ascii=True)} "
            f"cognitive_delta={module.cognitive_complexity_delta} "
            f"fan_in_delta={module.fan_in_delta:+d} fan_out_delta={module.fan_out_delta:+d} "
            f"baseline_cycles={json.dumps(module.baseline_cycle_ids, ensure_ascii=True)} "
            f"current_cycles={json.dumps(module.current_cycle_ids, ensure_ascii=True)} "
            f"baseline_contracts={json.dumps(module.baseline_contract_ids, ensure_ascii=True)} "
            f"current_contracts={json.dumps(module.current_contract_ids, ensure_ascii=True)}"
        )
    for displacement in displacements[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT]:
        _print_console_line(
            "CODE_PUBLICATION_DIFF_ARCHITECTURE_DISPLACEMENT "
            f"target={json.dumps(displacement.target_module, ensure_ascii=True)} "
            f"target_decrease={displacement.target_decrease} "
            f"recipients={json.dumps(displacement.recipient_modules, ensure_ascii=True)} "
            f"recipient_increase={displacement.recipient_increase} "
            f"imports={json.dumps(displacement.import_relationships, ensure_ascii=True)}"
        )
    if (
        len(changed_modules) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT
        or len(displacements) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT
    ):
        _print_console_line(
            "CODE_PUBLICATION_DIFF_ARCHITECTURE_EXAMPLES "
            f"module_examples_omitted="
            f"{max(0, len(changed_modules) - _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)} "
            f"displacement_examples_omitted="
            f"{max(0, len(displacements) - _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)}"
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


def _emit_code_review_unused_result(result: CodeReviewResult) -> None:
    from .code_review_models import bounded_code_unused_payload

    unused_analysis = getattr(result, "unused_analysis", None)
    if unused_analysis is None:
        _emit_code_unused(
            "CODE_REVIEW_UNUSED",
            _unused_abstained_payload(result.database, "unused_result_missing"),
        )
        return
    _emit_code_unused(
        "CODE_REVIEW_UNUSED",
        bounded_code_unused_payload(unused_analysis),
    )


def _emit_code_review_ranked_evidence(result: CodeReviewResult) -> None:
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


def run_code_review(args: argparse.Namespace) -> int:
    """Rank confirmed Python hotspots in the published self-analysis snapshot."""

    from .code_review import review_code_state
    from .code_review_models import (
        bounded_code_coverage_payload,
    )

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
        f"recommendations={len(result.recommendations)} "
        f"work_packages={len(result.work_packages)} ranking={result.ranking} "
        f"actionability={result.actionability_version} planner={result.planning_version} "
        f"digest={result.digest.xxh3_128}"
    )
    _print_console_line(
        f"CODE_REVIEW_COVERAGE python_files={result.coverage.current_python_files} "
        f"complete={result.coverage.complete_python_files} "
        f"hotspots={result.coverage.candidate_hotspots} "
        f"probable_dead_suppressed={result.coverage.probable_dead_suppressed} "
        f"resolved_calls={result.coverage.resolved_call_edges}/"
        f"{result.coverage.call_edges}"
    )
    if result.external_evidence is not None:
        external = result.external_evidence
        _print_console_line(
            f"CODE_REVIEW_EXTERNAL provider={external.provider} "
            f"status={external.status} execution={external.execution} "
            f"diagnostics={external.diagnostics} added={external.added} "
            f"resolved={external.resolved} gate={external.gate}"
        )
    if result.external_evidence_suite is not None:
        _print_console_line(
            f"CODE_REVIEW_PROVIDER_SUITE profile="
            f"{result.external_evidence_suite.profile} "
            f"status={result.external_evidence_suite.status}"
        )
        for provider in result.external_evidence_suite.providers:
            _print_console_line(
                f"CODE_REVIEW_PROVIDER id={provider.provider_id} "
                f"status={provider.status} findings={provider.findings} "
                f"metrics={provider.metrics} relations={provider.relations} "
                f"content_executed={int(provider.content_executed)} "
                f"gate={provider.gate}"
            )
    if result.architecture is None:
        _print_console_line(
            'CODE_REVIEW_ARCHITECTURE status=not_evaluated reason="architecture_result_missing"'
        )
    else:
        _emit_code_review_architecture(result.architecture)
    if result.test_coverage is None:
        _emit_code_coverage(
            "CODE_REVIEW_TEST_COVERAGE",
            {
                "status": "abstained",
                "reason": "coverage_result_missing",
                "suite_selection": None,
                "measurement_complete": False,
                "content_executed": False,
                "outcomes": None,
                "totals": None,
                "gates": [],
            },
        )
    else:
        _emit_code_coverage(
            "CODE_REVIEW_TEST_COVERAGE",
            bounded_code_coverage_payload(result.test_coverage),
        )
    _emit_code_review_unused_result(result)
    if result.recommendation_status == "abstained":
        _print_console_line(
            f"CODE_REVIEW_RECOMMENDATION status=abstained reason={result.recommendation_reason}"
        )
    if result.work_package_status == "abstained":
        _print_console_line(
            f"CODE_REVIEW_WORK_PACKAGE status=abstained reason={result.work_package_reason}"
        )
    for package in result.work_packages:
        import_chains = package.import_chains[:_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT]
        affected_contracts = package.affected_architecture_contracts[
            :_CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT
        ]
        architecture_gates = tuple(
            gate for gate in package.acceptance_gates if gate in _CODE_ARCHITECTURE_ACCEPTANCE_GATES
        )
        _print_console_line(
            "CODE_REVIEW_WORK_PACKAGE status=ready "
            f"package_rank={package.package_rank} risk={package.change_risk} "
            f"kind={getattr(package, 'package_kind', 'hotspot_maintenance')} "
            f"members={len(package.members)} "
            f"members_truncated={int(package.members_truncated)} "
            f"confidence={package.confidence} "
            f"primary={json.dumps(package.primary_symbol, ensure_ascii=True)} "
            f"human_confirmation="
            f"{int(bool(getattr(package, 'requires_human_confirmation', False)))} "
            f"mutation_authority="
            f"{int(bool(getattr(package, 'mutation_authority', False)))} "
            f"package_id={package.package_id}"
        )
        unused_candidates = getattr(package, "unused_candidates", ())
        for candidate in unused_candidates[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
            _print_console_line(
                "CODE_REVIEW_WORK_PACKAGE_UNUSED "
                f"package_rank={package.package_rank} package_id={package.package_id} "
                f"candidate_id={candidate.candidate_id} state={candidate.state} "
                f"path={json.dumps(candidate.relative_path, ensure_ascii=True)} "
                f"symbol={json.dumps(candidate.symbol, ensure_ascii=True)} "
                f"reasons={json.dumps(candidate.reasons, ensure_ascii=True)}"
            )
        _print_console_line(
            "CODE_REVIEW_WORK_PACKAGE_ARCHITECTURE status=ready "
            f"package_rank={package.package_rank} package_id={package.package_id} "
            f"primary_module={json.dumps(package.primary_module, ensure_ascii=True)} "
            f"import_chains={json.dumps(import_chains, ensure_ascii=True)} "
            f"import_chains_truncated="
            f"{int(len(package.import_chains) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)} "
            f"affected_architecture_contracts="
            f"{json.dumps(affected_contracts, ensure_ascii=True)} "
            f"affected_contracts_truncated="
            f"{int(len(package.affected_architecture_contracts) > _CODE_CLI_ARCHITECTURE_EXAMPLE_LIMIT)} "
            f"architecture_acceptance_gates={json.dumps(architecture_gates, ensure_ascii=True)}"
        )
        coverage_projection = package.test_coverage
        if coverage_projection is None:
            _print_console_line(
                "CODE_REVIEW_WORK_PACKAGE_COVERAGE status=not_evaluated "
                f"package_rank={package.package_rank} package_id={package.package_id} "
                'reason="coverage_projection_missing"'
            )
        else:
            coverage_payload = asdict(coverage_projection)
            protecting_tests = coverage_payload.get("protecting_tests")
            bounded_tests = (
                protecting_tests[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]
                if isinstance(protecting_tests, (list, tuple))
                else []
            )
            coverage_scope = (
                {} if package.test_coverage_scope is None else asdict(package.test_coverage_scope)
            )
            missing_lines = coverage_scope.get("missing_line_ranges")
            bounded_lines = (
                missing_lines[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]
                if isinstance(missing_lines, (list, tuple))
                else []
            )
            missing_branches = coverage_scope.get("missing_branch_arcs")
            bounded_branches = (
                missing_branches[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]
                if isinstance(missing_branches, (list, tuple))
                else []
            )
            coverage_gate = coverage_payload.get("gate")
            bounded_gate = coverage_gate if isinstance(coverage_gate, dict) else {}
            relation_ids = coverage_payload.get("relation_ids")
            bounded_relations = (
                relation_ids[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]
                if isinstance(relation_ids, (list, tuple))
                else []
            )
            _print_console_line(
                "CODE_REVIEW_WORK_PACKAGE_COVERAGE "
                f"status={coverage_payload.get('status')} "
                f"package_rank={package.package_rank} package_id={package.package_id} "
                f"subject={json.dumps(coverage_payload.get('primary_symbol'), ensure_ascii=True)} "
                f"tests={json.dumps(bounded_tests, ensure_ascii=True)} "
                f"relations={json.dumps(bounded_relations, ensure_ascii=True)} "
                f"missing_lines={json.dumps(bounded_lines, ensure_ascii=True)} "
                f"missing_branches={json.dumps(bounded_branches, ensure_ascii=True)} "
                f"details_truncated={int(bool(coverage_scope.get('missing_line_ranges_truncated')) or bool(coverage_scope.get('missing_branch_arcs_truncated')))} "
                f"gate={bounded_gate.get('status')} "
                f"reason={json.dumps(bounded_gate.get('reason'), ensure_ascii=True)}"
            )
    _emit_code_review_ranked_evidence(result)
    return 0


def _emit_code_publication_unused(analysis: CodeUnusedAnalysisDelta) -> None:
    _print_console_line(
        f"CODE_PUBLICATION_DIFF_UNUSED status={analysis.status} "
        f"common={analysis.common} added={analysis.added} removed={analysis.removed} "
        f"state_changes={analysis.state_changes} "
        f"high_consensus_added={analysis.high_consensus_added} "
        f"high_consensus_resolved={analysis.high_consensus_resolved} "
        f"gate={analysis.gate} "
        f"gate_reason={json.dumps(analysis.gate_reason, ensure_ascii=True)} "
        f"reason={json.dumps(analysis.reason, ensure_ascii=True)}"
    )
    for change in analysis.state_change_examples[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
        _print_console_line(
            "CODE_PUBLICATION_DIFF_UNUSED_STATE "
            f"id={change.candidate_id} "
            f"baseline={change.baseline_state} current={change.current_state} "
            f"path={json.dumps(change.relative_path, ensure_ascii=True)} "
            f"symbol={json.dumps(change.symbol, ensure_ascii=True)}"
        )
    for candidate in analysis.added_examples[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
        _print_console_line(
            "CODE_PUBLICATION_DIFF_UNUSED_ADDED "
            f"id={candidate.candidate_id} state={candidate.state} "
            f"path={json.dumps(candidate.relative_path, ensure_ascii=True)} "
            f"symbol={json.dumps(candidate.symbol, ensure_ascii=True)}"
        )
    for candidate in analysis.removed_examples[:_CODE_CLI_UNUSED_EXAMPLE_LIMIT]:
        _print_console_line(
            "CODE_PUBLICATION_DIFF_UNUSED_REMOVED "
            f"id={candidate.candidate_id} state={candidate.state} "
            f"path={json.dumps(candidate.relative_path, ensure_ascii=True)} "
            f"symbol={json.dumps(candidate.symbol, ensure_ascii=True)}"
        )


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
        or result.external_evidence is None
        or result.architecture is None
        or result.test_coverage is None
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
    _print_console_line(
        "CODE_PUBLICATION_DIFF_EXTERNAL provider=ruff "
        f"status={result.external_evidence.status} "
        f"common={result.external_evidence.common} "
        f"added={result.external_evidence.added} "
        f"resolved={result.external_evidence.resolved} "
        f"gate={result.external_evidence.gate}"
    )
    _print_console_line(
        f"CODE_PUBLICATION_DIFF_PROVIDERS profile={result.analysis_profile} "
        f"verdict={result.verdict}"
    )
    for provider in result.providers:
        _print_console_line(
            f"CODE_PUBLICATION_DIFF_PROVIDER id={provider.provider_id} "
            f"status={provider.status} common={provider.common} "
            f"added={provider.added} resolved={provider.resolved} gate={provider.gate}"
        )
    _emit_code_publication_architecture(result.architecture)
    coverage_delta = asdict(result.test_coverage)
    _print_console_line(
        "CODE_PUBLICATION_DIFF_COVERAGE "
        f"status={coverage_delta.get('status')} "
        f"line_delta={coverage_delta.get('line_coverage_percent_delta')} "
        f"branch_delta={coverage_delta.get('branch_coverage_percent_delta')} "
        f"covered_lines_delta={coverage_delta.get('covered_lines_delta')} "
        f"missing_lines_delta={coverage_delta.get('missing_lines_delta')} "
        f"covered_branches_delta={coverage_delta.get('covered_branch_exits_delta')} "
        f"missing_branches_delta={coverage_delta.get('missing_branch_exits_delta')} "
        f"reason={json.dumps(coverage_delta.get('reason'), ensure_ascii=True)}"
    )
    coverage_gates = coverage_delta.get("gates")
    if isinstance(coverage_gates, (list, tuple)):
        for gate in coverage_gates[:_CODE_CLI_COVERAGE_EXAMPLE_LIMIT]:
            if not isinstance(gate, dict):
                continue
            _print_console_line(
                f"CODE_PUBLICATION_DIFF_COVERAGE_GATE id={gate.get('gate')} "
                f"status={gate.get('status')} "
                f"reason={json.dumps(gate.get('reason'), ensure_ascii=True)}"
            )
    unused_delta = getattr(result, "unused_analysis", None)
    if unused_delta is None:
        _print_console_line(
            "CODE_PUBLICATION_DIFF_UNUSED status=not_evaluated "
            'gate=not_evaluated reason="unused_delta_missing"'
        )
    else:
        _emit_code_publication_unused(unused_delta)
    for limitation in result.limitations:
        _print_console_line(f"CODE_PUBLICATION_DIFF_LIMITATION {limitation}")
    return 0


def run_code_doctor(args: argparse.Namespace) -> int:
    """Validate schema, FTS and optional tools without loading heavy analyzers."""

    from .code_analyzers import builtin_analyzer_registry
    from .code_external_evidence import RuffEvidenceProvider
    from .code_schema import code_database, validate_code_schema
    from .external_evidence_providers import provider_tool_versions

    path = _state_path(args)
    ruff_version = RuffEvidenceProvider.tool_version()
    provider_versions = provider_tool_versions()
    report: dict[str, object] = {
        "kind": "code-doctor",
        "database": str(path),
        "exists": path.is_file(),
        "analyzers": builtin_analyzer_registry().status(),
        "tools": {
            name: shutil.which(name)
            for name in ("cargo", "rustc", "rust-analyzer", "mypy", "node", "pyright")
        },
        "external_evidence": {
            "provider": "ruff",
            "available": ruff_version is not None,
            "version": ruff_version,
            "runtime": sys.executable,
            "resolution": "runtime-distribution",
        },
        "external_evidence_providers": {
            provider_id: {
                "available": version is not None,
                "version": version,
                "authority": "advisory",
                "mutation_authority": False,
            }
            for provider_id, version in provider_versions.items()
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
    from .code_semantic_links import code_semantic_search_availability

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
    "run_code_projects",
    "run_code_publication_diff",
    "run_code_reconstruct",
    "run_code_review",
    "run_code_search",
    "run_code_status",
]
