"""Deterministic, read-only maintenance shortlist over published Code evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from .code_schema import (
    CODE_SCHEMA_VERSION,
    readonly_code_database,
    validate_code_schema,
)
from .self_analysis_status import (
    CodeRunStatusEvidence,
    SelfAnalysisStatus,
    read_self_analysis_status,
    require_sqlite_sidecars_absent,
)
from .semantic_models import canonical_json, fingerprint_text


# region [01] Public review contract


CODE_REVIEW_SCHEMA = "neocortex.code-review/v1"
CODE_REVIEW_RANKING = "python-confirmed-hotspots-v2"
CODE_REVIEW_LIMIT = 10
CODE_REVIEW_MAX_LIMIT = 50
CODE_REVIEW_MAX_PER_FILE = 2
CODE_REVIEW_MAX_CANDIDATES = 10_000
CODE_REVIEW_CALLER_EXAMPLES = 3

ReviewStatus = Literal["ready", "abstained"]
ReviewFreshness = Literal["current", "publication_only"]
FindingCategory = Literal[
    "complex_and_long_hotspot",
    "high_complexity_hotspot",
    "long_function_hotspot",
]


@dataclass(frozen=True, slots=True)
class CodeReviewDiagnostic:
    """One exact analyzer diagnostic supporting a symbol hotspot."""

    code: Literal["high_complexity", "long_function"]
    value: int
    threshold: int | None
    source: str
    tool_name: str
    tool_version: str
    confirmed: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class CodeReviewCaller:
    """One bounded, resolved static call site used as impact evidence."""

    path: str
    symbol: str | None
    start_line: int
    end_line: int
    confidence: float
    provenance: str


@dataclass(frozen=True, slots=True)
class CodeReviewFinding:
    """One symbol-level review candidate; rank is advisory, not calibrated risk."""

    finding_id: str
    rank: int
    category: FindingCategory
    path: str
    symbol: str
    symbol_kind: str
    signature: str | None
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    start_byte: int
    end_byte: int
    complexity: int
    function_lines: int
    complexity_ratio_basis_points: int
    length_ratio_basis_points: int
    score_basis_points: int
    incoming_references: int
    incoming_calls: int
    resolved_static_callers: int
    analyzer_id: str
    analyzer_version: str
    file_xxh3_128: str | None
    file_xxh3_64_guard: str | None
    diagnostics: tuple[CodeReviewDiagnostic, ...]
    callers: tuple[CodeReviewCaller, ...]
    reasons: tuple[str, ...]
    confidence: Literal["confirmed_static_evidence"] = "confirmed_static_evidence"


@dataclass(frozen=True, slots=True)
class CodeReviewSnapshot:
    """Published self-analysis snapshot that authorized this review."""

    analysis_run_id: int
    framework_run_id: int
    scan_id: int
    processing_signature: str
    root: str
    freshness: ReviewFreshness
    current: bool
    journal_status: str


@dataclass(frozen=True, slots=True)
class CodeReviewCoverage:
    """Bounded coverage and deliberately suppressed evidence."""

    current_python_files: int
    complete_python_files: int
    incomplete_python_files: int
    candidate_hotspots: int
    enumerated_hotspots: int
    probable_dead_suppressed: int
    call_edges: int
    resolved_call_edges: int


@dataclass(frozen=True, slots=True)
class CodeReviewDigest:
    """Collision-guarded deterministic identity of review evidence."""

    xxh3_128: str
    xxh3_64_guard: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class CodeReviewResult:
    """One JSON-ready review result with a deterministic content digest."""

    database: str
    status: ReviewStatus
    reason: str | None
    ranking: str
    snapshot: CodeReviewSnapshot | None
    coverage: CodeReviewCoverage | None
    findings: tuple[CodeReviewFinding, ...]
    limitations: tuple[str, ...]
    digest: CodeReviewDigest | None

    def as_payload(self) -> dict[str, object]:
        return {
            "kind": "code-review",
            "schema": CODE_REVIEW_SCHEMA,
            **asdict(self),
        }


# endregion [01]


# region [02] Immutable Code snapshot and ranking inputs


@dataclass(frozen=True, slots=True)
class _Candidate:
    file_id: int
    volume_id: str
    physical_file_id: str
    version_id: int
    symbol_id: int
    path: str
    symbol: str
    symbol_kind: str
    signature: str | None
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    start_byte: int
    end_byte: int
    complexity: int
    function_lines: int
    complexity_threshold: int | None
    length_threshold: int | None
    complexity_ratio_basis_points: int
    length_ratio_basis_points: int
    score_basis_points: int
    high_complexity: bool
    long_function: bool
    incoming_references: int
    incoming_calls: int
    resolved_static_callers: int
    analyzer_id: str
    analyzer_version: str
    file_xxh3_128: str | None
    file_xxh3_64_guard: str | None


@dataclass(frozen=True, slots=True)
class _ReviewRead:
    latest_run: CodeRunStatusEvidence | None
    coverage: CodeReviewCoverage
    findings: tuple[CodeReviewFinding, ...]
    enumeration_truncated: bool


_CANDIDATE_SQL = """
WITH current_references AS (
    SELECT r.target_symbol_id,
           COUNT(*) AS incoming_references,
           SUM(CASE WHEN r.kind='call' THEN 1 ELSE 0 END) AS incoming_calls,
           COUNT(DISTINCT CASE WHEN r.kind='call' THEN
               printf('%d:%d',r.version_id,COALESCE(r.source_symbol_id,0)) END
           ) AS resolved_static_callers
    FROM code_references r
    JOIN file_versions source_version ON source_version.version_id=r.version_id
    JOIN files source_file
      ON source_file.current_version_id=source_version.version_id
     AND source_file.status='current'
    WHERE r.target_symbol_id IS NOT NULL
      AND r.confirmed=1
      AND source_version.invalidated_ns IS NULL
      AND source_version.language='python'
    GROUP BY r.target_symbol_id
), signals AS (
    SELECT d.version_id,d.start_byte,d.end_byte,
           MAX(CASE WHEN d.code='high_complexity' AND d.confirmed=1
               THEN 1 ELSE 0 END) AS high_complexity,
           MAX(CASE WHEN d.code='long_function' AND d.confirmed=1
               THEN 1 ELSE 0 END) AS long_function,
           MAX(CASE WHEN d.code='high_complexity' AND d.confirmed=1
               THEN d.metadata_json END) AS complexity_metadata_json,
           MAX(CASE WHEN d.code='long_function' AND d.confirmed=1
               THEN d.metadata_json END) AS length_metadata_json
    FROM diagnostics d
    JOIN file_versions diagnostic_version
      ON diagnostic_version.version_id=d.version_id
    WHERE diagnostic_version.invalidated_ns IS NULL
      AND d.code IN ('high_complexity','long_function')
    GROUP BY d.version_id,d.start_byte,d.end_byte
)
SELECT f.file_id,f.volume_id,f.physical_file_id,v.version_id,s.symbol_id,
       f.current_path,s.qualified_name,s.kind,s.signature,s.start_line,s.end_line,
       s.start_column,s.end_column,s.start_byte,s.end_byte,
       COALESCE(s.complexity,0) AS complexity,
       s.end_line-s.start_line+1 AS function_lines,
       signals.high_complexity,signals.long_function,
       COALESCE(current_references.incoming_references,0) AS incoming_references,
       COALESCE(current_references.incoming_calls,0) AS incoming_calls,
       COALESCE(current_references.resolved_static_callers,0)
           AS resolved_static_callers,
       signals.complexity_metadata_json,signals.length_metadata_json,
       v.analyzer_id,v.analyzer_version,v.raw_xxh3_128,v.raw_xxh3_64_guard,
       COUNT(*) OVER() AS total_candidates
FROM symbols s
JOIN file_versions v ON v.version_id=s.version_id
JOIN files f ON f.current_version_id=v.version_id AND f.status='current'
JOIN signals
  ON signals.version_id=s.version_id
 AND signals.start_byte=s.start_byte
 AND signals.end_byte=s.end_byte
LEFT JOIN current_references ON current_references.target_symbol_id=s.symbol_id
WHERE v.invalidated_ns IS NULL
  AND v.analysis_status='complete'
  AND v.language='python'
  AND v.generated=0 AND v.vendored=0
  AND s.confirmed=1
  AND s.kind IN ('function','method','nested_function')
  AND (signals.high_complexity=1 OR signals.long_function=1)
ORDER BY (signals.high_complexity+signals.long_function) DESC,
         COALESCE(current_references.resolved_static_callers,0) DESC,
         COALESCE(s.complexity,0) DESC,
         function_lines DESC,
         f.current_path COLLATE NOCASE,s.qualified_name,s.start_byte,s.symbol_id
LIMIT ?
"""


def _latest_run(connection: sqlite3.Connection) -> CodeRunStatusEvidence | None:
    row = connection.execute(
        """SELECT analysis_run_id,framework_run_id,scan_id,
        processing_signature,status
        FROM analysis_runs ORDER BY analysis_run_id DESC LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    return CodeRunStatusEvidence(
        analysis_run_id=int(row["analysis_run_id"]),
        framework_run_id=int(row["framework_run_id"]),
        scan_id=int(row["scan_id"]),
        processing_signature=str(row["processing_signature"]),
        status=str(row["status"]),
    )


def _candidate(row: sqlite3.Row) -> _Candidate:
    complexity = int(row["complexity"])
    function_lines = int(row["function_lines"])
    high_complexity = bool(row["high_complexity"])
    long_function = bool(row["long_function"])
    complexity_threshold = _diagnostic_threshold(
        row["complexity_metadata_json"],
        expected_value=complexity,
        required=high_complexity,
        label="high_complexity",
    )
    length_threshold = _diagnostic_threshold(
        row["length_metadata_json"],
        expected_value=function_lines,
        required=long_function,
        label="long_function",
    )
    complexity_ratio = _ratio_basis_points(complexity, complexity_threshold)
    length_ratio = _ratio_basis_points(function_lines, length_threshold)
    callers = int(row["resolved_static_callers"])
    score = _score_basis_points(complexity_ratio, length_ratio, callers)
    return _Candidate(
        file_id=int(row["file_id"]),
        volume_id=str(row["volume_id"]),
        physical_file_id=str(row["physical_file_id"]),
        version_id=int(row["version_id"]),
        symbol_id=int(row["symbol_id"]),
        path=str(row["current_path"]),
        symbol=str(row["qualified_name"]),
        symbol_kind=str(row["kind"]),
        signature=None if row["signature"] is None else str(row["signature"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        start_column=int(row["start_column"]),
        end_column=int(row["end_column"]),
        start_byte=int(row["start_byte"]),
        end_byte=int(row["end_byte"]),
        complexity=complexity,
        function_lines=function_lines,
        complexity_threshold=complexity_threshold,
        length_threshold=length_threshold,
        complexity_ratio_basis_points=complexity_ratio,
        length_ratio_basis_points=length_ratio,
        score_basis_points=score,
        high_complexity=high_complexity,
        long_function=long_function,
        incoming_references=int(row["incoming_references"]),
        incoming_calls=int(row["incoming_calls"]),
        resolved_static_callers=callers,
        analyzer_id=str(row["analyzer_id"]),
        analyzer_version=str(row["analyzer_version"]),
        file_xxh3_128=(
            None if row["raw_xxh3_128"] is None else str(row["raw_xxh3_128"])
        ),
        file_xxh3_64_guard=(
            None if row["raw_xxh3_64_guard"] is None else str(row["raw_xxh3_64_guard"])
        ),
    )


def _diagnostic_threshold(
    raw_metadata: object,
    *,
    expected_value: int,
    required: bool,
    label: str,
) -> int | None:
    if raw_metadata is None:
        if required:
            raise ValueError(f"{label} diagnostic metadata is missing")
        return None
    metadata = json.loads(str(raw_metadata))
    if not isinstance(metadata, dict):
        raise ValueError(f"{label} diagnostic metadata must be an object")
    value = metadata.get("value")
    threshold = metadata.get("threshold")
    if isinstance(value, bool) or not isinstance(value, int) or value != expected_value:
        raise ValueError(f"{label} diagnostic value disagrees with symbol evidence")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise ValueError(f"{label} diagnostic threshold must be positive")
    return threshold


def _ratio_basis_points(value: int, threshold: int | None) -> int:
    return 0 if threshold is None else (10_000 * value) // threshold


def _score_basis_points(
    complexity_ratio: int,
    length_ratio: int,
    resolved_static_callers: int,
) -> int:
    """Prioritize branching evidence while retaining length and impact support."""

    return complexity_ratio + length_ratio // 4 + 250 * min(resolved_static_callers, 20)


def _rank_key(candidate: _Candidate) -> tuple[object, ...]:
    return (
        -candidate.score_basis_points,
        -max(
            candidate.complexity_ratio_basis_points,
            candidate.length_ratio_basis_points,
        ),
        -candidate.complexity_ratio_basis_points,
        -candidate.length_ratio_basis_points,
        -candidate.resolved_static_callers,
        candidate.path.casefold(),
        candidate.path,
        candidate.symbol,
        candidate.start_line,
        candidate.symbol_id,
    )


def _select_candidates(
    candidates: tuple[_Candidate, ...],
    *,
    limit: int,
) -> tuple[_Candidate, ...]:
    ordered = tuple(sorted(candidates, key=_rank_key))
    selected: list[_Candidate] = []
    selected_ids: set[int] = set()
    per_file: dict[str, int] = {}
    for allowed_per_file in range(1, CODE_REVIEW_MAX_PER_FILE + 1):
        for candidate in ordered:
            if candidate.symbol_id in selected_ids:
                continue
            path_key = candidate.path.casefold()
            if per_file.get(path_key, 0) >= allowed_per_file:
                continue
            selected.append(candidate)
            selected_ids.add(candidate.symbol_id)
            per_file[path_key] = per_file.get(path_key, 0) + 1
            if len(selected) == limit:
                return tuple(selected)
    return tuple(selected)


def _diagnostics(
    connection: sqlite3.Connection,
    candidate: _Candidate,
) -> tuple[CodeReviewDiagnostic, ...]:
    rows = connection.execute(
        """SELECT d.code,d.source,d.tool_name,d.tool_version,d.confirmed,
        d.confidence,d.metadata_json FROM diagnostics d
        JOIN symbols s ON s.version_id=d.version_id
         AND s.start_byte=d.start_byte AND s.end_byte=d.end_byte
        WHERE s.symbol_id=? AND d.code IN ('high_complexity','long_function')
        ORDER BY CASE d.code WHEN 'high_complexity' THEN 0 ELSE 1 END,
        d.diagnostic_id""",
        (candidate.symbol_id,),
    ).fetchall()
    diagnostics: list[CodeReviewDiagnostic] = []
    for row in rows:
        metadata = json.loads(str(row["metadata_json"]))
        if not isinstance(metadata, dict):
            raise ValueError("code diagnostic metadata must be a JSON object")
        raw_code = str(row["code"])
        if raw_code not in {"high_complexity", "long_function"}:
            raise ValueError("code review diagnostic is unsupported")
        code = cast(Literal["high_complexity", "long_function"], raw_code)
        value = (
            candidate.complexity
            if code == "high_complexity"
            else candidate.function_lines
        )
        threshold_value = metadata.get("threshold")
        threshold = (
            None
            if isinstance(threshold_value, bool) or not isinstance(threshold_value, int)
            else threshold_value
        )
        diagnostics.append(
            CodeReviewDiagnostic(
                code=code,
                value=value,
                threshold=threshold,
                source=str(row["source"]),
                tool_name=str(row["tool_name"]),
                tool_version=str(row["tool_version"]),
                confirmed=bool(row["confirmed"]),
                confidence=float(row["confidence"]),
            )
        )
    return tuple(diagnostics)


def _callers(
    connection: sqlite3.Connection,
    symbol_id: int,
) -> tuple[CodeReviewCaller, ...]:
    rows = connection.execute(
        """SELECT source_file.current_path,source_symbol.qualified_name,
        r.start_line,r.end_line,r.confidence,r.evidence
        FROM code_references r
        JOIN file_versions source_version ON source_version.version_id=r.version_id
        JOIN files source_file
          ON source_file.current_version_id=source_version.version_id
         AND source_file.status='current'
        LEFT JOIN symbols source_symbol
          ON source_symbol.symbol_id=r.source_symbol_id
         AND source_symbol.version_id=source_version.version_id
        WHERE r.target_symbol_id=? AND r.kind='call' AND r.confirmed=1
          AND source_version.invalidated_ns IS NULL
        ORDER BY source_file.current_path COLLATE NOCASE,
        COALESCE(source_symbol.qualified_name,''),r.start_line,r.reference_id
        LIMIT ?""",
        (symbol_id, CODE_REVIEW_CALLER_EXAMPLES),
    ).fetchall()
    return tuple(
        CodeReviewCaller(
            path=str(row["current_path"]),
            symbol=(
                None if row["qualified_name"] is None else str(row["qualified_name"])
            ),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            confidence=float(row["confidence"]),
            provenance=str(row["evidence"]),
        )
        for row in rows
    )


def _category(candidate: _Candidate) -> FindingCategory:
    if candidate.high_complexity and candidate.long_function:
        return "complex_and_long_hotspot"
    if candidate.high_complexity:
        return "high_complexity_hotspot"
    return "long_function_hotspot"


def _finding_id(candidate: _Candidate) -> str:
    payload = canonical_json(
        {
            "detector": CODE_REVIEW_RANKING,
            "volume_id": candidate.volume_id,
            "physical_file_id": candidate.physical_file_id,
            "symbol": candidate.symbol,
            "symbol_kind": candidate.symbol_kind,
        }
    )
    return "code-review-finding-v1:xxh3_128:" + fingerprint_text(payload).xxh3_128


def _finding(
    connection: sqlite3.Connection,
    candidate: _Candidate,
    rank: int,
) -> CodeReviewFinding:
    reasons: list[str] = []
    if candidate.high_complexity:
        reasons.append(f"confirmed_cyclomatic_complexity:{candidate.complexity}")
    if candidate.long_function:
        reasons.append(f"confirmed_function_lines:{candidate.function_lines}")
    reasons.append(f"resolved_static_callers:{candidate.resolved_static_callers}")
    return CodeReviewFinding(
        finding_id=_finding_id(candidate),
        rank=rank,
        category=_category(candidate),
        path=candidate.path,
        symbol=candidate.symbol,
        symbol_kind=candidate.symbol_kind,
        signature=candidate.signature,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        start_column=candidate.start_column,
        end_column=candidate.end_column,
        start_byte=candidate.start_byte,
        end_byte=candidate.end_byte,
        complexity=candidate.complexity,
        function_lines=candidate.function_lines,
        complexity_ratio_basis_points=candidate.complexity_ratio_basis_points,
        length_ratio_basis_points=candidate.length_ratio_basis_points,
        score_basis_points=candidate.score_basis_points,
        incoming_references=candidate.incoming_references,
        incoming_calls=candidate.incoming_calls,
        resolved_static_callers=candidate.resolved_static_callers,
        analyzer_id=candidate.analyzer_id,
        analyzer_version=candidate.analyzer_version,
        file_xxh3_128=candidate.file_xxh3_128,
        file_xxh3_64_guard=candidate.file_xxh3_64_guard,
        diagnostics=_diagnostics(connection, candidate),
        callers=_callers(connection, candidate.symbol_id),
        reasons=tuple(reasons),
    )


def _read_review(path: Path, *, limit: int) -> _ReviewRead:
    with readonly_code_database(path) as connection:
        validate_code_schema(connection)
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version != CODE_SCHEMA_VERSION:
            raise RuntimeError(
                f"code state schema {schema_version} is unsupported for review"
            )
        latest_run = _latest_run(connection)
        rows = connection.execute(
            _CANDIDATE_SQL,
            (CODE_REVIEW_MAX_CANDIDATES,),
        ).fetchall()
        candidates = tuple(_candidate(row) for row in rows)
        total_candidates = int(rows[0]["total_candidates"]) if rows else 0
        selected = _select_candidates(candidates, limit=limit)
        findings = tuple(
            _finding(connection, candidate, rank)
            for rank, candidate in enumerate(selected, start=1)
        )
        probable_dead = int(
            connection.execute(
                """SELECT COUNT(*) FROM diagnostics d
                JOIN file_versions v ON v.version_id=d.version_id
                JOIN files f ON f.current_version_id=v.version_id
                WHERE f.status='current' AND v.invalidated_ns IS NULL
                AND v.language='python' AND d.code='probable_dead_symbol'"""
            ).fetchone()[0]
        )
        call_edges, resolved_call_edges = connection.execute(
            """SELECT COUNT(*),COUNT(r.target_symbol_id) FROM code_references r
            JOIN file_versions v ON v.version_id=r.version_id
            JOIN files f ON f.current_version_id=v.version_id
            WHERE f.status='current' AND v.invalidated_ns IS NULL
            AND v.language='python' AND r.kind='call' AND r.confirmed=1"""
        ).fetchone()
        current_python_files, complete_python_files = connection.execute(
            """SELECT COUNT(*),SUM(CASE WHEN v.analysis_status='complete'
            THEN 1 ELSE 0 END) FROM file_versions v
            JOIN files f ON f.current_version_id=v.version_id
            WHERE f.status='current' AND v.invalidated_ns IS NULL
            AND v.language='python' AND v.generated=0 AND v.vendored=0"""
        ).fetchone()
        current_python = int(current_python_files)
        complete_python = int(complete_python_files or 0)
    return _ReviewRead(
        latest_run=latest_run,
        coverage=CodeReviewCoverage(
            current_python_files=current_python,
            complete_python_files=complete_python,
            incomplete_python_files=current_python - complete_python,
            candidate_hotspots=total_candidates,
            enumerated_hotspots=len(candidates),
            probable_dead_suppressed=probable_dead,
            call_edges=int(call_edges),
            resolved_call_edges=int(resolved_call_edges),
        ),
        findings=findings,
        enumeration_truncated=total_candidates > len(candidates),
    )


# endregion [02]


# region [03] Self-analysis eligibility and public service


def _abstained(path: Path, reason: str) -> CodeReviewResult:
    return CodeReviewResult(
        database=str(path),
        status="abstained",
        reason=reason,
        ranking=CODE_REVIEW_RANKING,
        snapshot=None,
        coverage=None,
        findings=(),
        limitations=(),
        digest=None,
    )


def _manifest_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"self-analysis manifest {label} must be an object")
    return value


def _eligibility(
    status: SelfAnalysisStatus | None,
) -> tuple[str | None, ReviewFreshness | None, str | None]:
    if status is None:
        return "self_analysis_manifest_missing", None, None
    if status.manifest_status != "valid" or status.manifest is None:
        return f"self_analysis_manifest_{status.manifest_status}", None, None
    freshness = status.freshness
    if not freshness.root_identity_current:
        return "self_analysis_root_identity_not_current", None, None
    if not freshness.framework_link_current:
        return "self_analysis_framework_link_not_current", None, None
    manifest = status.manifest
    inventory = _manifest_mapping(manifest.get("inventory"), "inventory")
    journal = _manifest_mapping(inventory.get("journal"), "inventory journal")
    if freshness.current:
        return None, "current", None
    if freshness.journal_status == "unavailable":
        if inventory.get("mode") == "full" and journal.get("status") == "unavailable":
            return (
                None,
                "publication_only",
                ("live_tree_freshness_not_proven_without_journal"),
            )
        return "self_analysis_journal_probe_unavailable", None, None
    if freshness.journal_status == "advanced":
        return "self_analysis_journal_advanced", None, None
    if freshness.journal_status == "discontinuous":
        return "self_analysis_journal_discontinuous", None, None
    if not freshness.inventory_checkpoint_current:
        return "self_analysis_inventory_checkpoint_not_current", None, None
    return "self_analysis_not_current", None, None


def _manifest_root(status: SelfAnalysisStatus) -> str:
    if status.manifest is None:
        raise ValueError("self-analysis manifest is unavailable")
    run = _manifest_mapping(status.manifest.get("run"), "run")
    root = run.get("root")
    if not isinstance(root, str) or not root:
        raise ValueError("self-analysis manifest root must be a non-empty string")
    return root


def _digest(
    snapshot: CodeReviewSnapshot,
    coverage: CodeReviewCoverage,
    findings: tuple[CodeReviewFinding, ...],
    limitations: tuple[str, ...],
) -> CodeReviewDigest:
    payload = canonical_json(
        {
            "schema": CODE_REVIEW_SCHEMA,
            "ranking": CODE_REVIEW_RANKING,
            "snapshot": {
                "processing_signature": snapshot.processing_signature,
                "freshness": snapshot.freshness,
                "current": snapshot.current,
                "journal_status": snapshot.journal_status,
            },
            "coverage": asdict(coverage),
            "findings": [asdict(finding) for finding in findings],
            "limitations": list(limitations),
        }
    )
    fingerprint = fingerprint_text(payload)
    return CodeReviewDigest(
        fingerprint.xxh3_128,
        fingerprint.xxh3_64_guard,
        fingerprint.byte_count,
    )


def review_code_state(
    state_directory: Path,
    *,
    limit: int = CODE_REVIEW_LIMIT,
) -> CodeReviewResult:
    """Return a bounded maintenance shortlist without writing any owner."""

    if isinstance(limit, bool) or not 1 <= limit <= CODE_REVIEW_MAX_LIMIT:
        raise ValueError(
            f"code review limit must be between 1 and {CODE_REVIEW_MAX_LIMIT}"
        )
    state_directory = Path(state_directory)
    path = state_directory / "code.sqlite3"
    require_sqlite_sidecars_absent(path)
    if not path.is_file():
        return _abstained(path, "code_state_missing")
    read = _read_review(path, limit=limit)
    if read.latest_run is None:
        return _abstained(path, "code_run_missing")
    if read.latest_run.status != "completed":
        return _abstained(path, f"code_run_not_completed:{read.latest_run.status}")
    status = read_self_analysis_status(state_directory, read.latest_run)
    reason, freshness, freshness_limitation = _eligibility(status)
    if reason is not None:
        return _abstained(path, reason)
    if status is None or freshness is None:
        raise AssertionError("eligible code review requires self-analysis status")
    limitations = [
        "ranking_score_is_not_calibrated_risk",
        "static_call_resolution_is_partial",
        "dynamic_dispatch_is_not_observed",
        "probable_dead_symbol_is_suppressed_uncalibrated_evidence",
    ]
    if freshness_limitation is not None:
        limitations.insert(0, freshness_limitation)
    if read.enumeration_truncated:
        limitations.append("candidate_enumeration_truncated")
    snapshot = CodeReviewSnapshot(
        analysis_run_id=read.latest_run.analysis_run_id,
        framework_run_id=read.latest_run.framework_run_id,
        scan_id=read.latest_run.scan_id,
        processing_signature=read.latest_run.processing_signature,
        root=_manifest_root(status),
        freshness=freshness,
        current=status.freshness.current,
        journal_status=status.freshness.journal_status,
    )
    limitation_tuple = tuple(limitations)
    return CodeReviewResult(
        database=str(path),
        status="ready",
        reason=None,
        ranking=CODE_REVIEW_RANKING,
        snapshot=snapshot,
        coverage=read.coverage,
        findings=read.findings,
        limitations=limitation_tuple,
        digest=_digest(snapshot, read.coverage, read.findings, limitation_tuple),
    )


__all__ = [
    "CODE_REVIEW_LIMIT",
    "CODE_REVIEW_MAX_LIMIT",
    "CODE_REVIEW_MAX_PER_FILE",
    "CODE_REVIEW_RANKING",
    "CODE_REVIEW_SCHEMA",
    "CodeReviewCaller",
    "CodeReviewCoverage",
    "CodeReviewDigest",
    "CodeReviewDiagnostic",
    "CodeReviewFinding",
    "CodeReviewResult",
    "CodeReviewSnapshot",
    "review_code_state",
]


# endregion [03]
