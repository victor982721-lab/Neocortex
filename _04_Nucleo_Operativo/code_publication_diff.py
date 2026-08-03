"""Deterministic read-only comparison of two completed Code publications."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .code_external_evidence import (
    RUFF_CONFIGURATION_SIGNATURE,
    ExternalEvidenceStatus,
    external_status_digest_payload,
    read_external_evidence,
)
from .code_schema import (
    CODE_SCHEMA_VERSION,
    readonly_code_database,
    validate_code_schema,
)
from .external_evidence_models import (
    ExternalEvidenceSuiteStatus,
    ExternalProviderStatus,
)
from .external_evidence_store import (
    read_external_evidence_suite,
    read_external_provider_finding_ids,
)
from .self_analysis_status import require_sqlite_sidecars_absent
from .semantic_models import canonical_json, fingerprint_text

CODE_PUBLICATION_DIFF_SCHEMA = "neocortex.code-publication-diff/v3"
CODE_PUBLICATION_DIFF_COMPATIBLE_SCHEMAS = (
    "neocortex.code-publication-diff/v1",
    "neocortex.code-publication-diff/v2",
)
CODE_PUBLICATION_DIFF_EXAMPLE_LIMIT = 20
_LEGACY_RUFF_COMPARABILITY_REASON = "legacy_ruff_contract_compatibility_projection"
CODE_PUBLICATION_DIFF_MAX_CALLS = 250_000
CODE_PUBLICATION_DIFF_MAX_HOTSPOTS = 20_000

DiffStatus = Literal["ready", "abstained"]


@dataclass(frozen=True, slots=True)
class CodePublicationSnapshot:
    """Bounded facts read from one completed Code owner."""

    state_directory: str
    database: str
    root: str
    processing_signature: str
    python_files: int
    complete_python_files: int
    call_edges: int
    resolved_call_edges: int
    hotspots: int
    high_complexity: int
    long_function: int
    probable_dead: int


@dataclass(frozen=True, slots=True)
class CodeCallResolutionChange:
    """One bounded example from a common unchanged call site."""

    change: Literal["newly_resolved", "corrected", "lost"]
    path: str
    source_symbol: str | None
    name: str
    target_hint: str | None
    line: int
    baseline_target: str | None
    current_target: str | None


@dataclass(frozen=True, slots=True)
class CodeCallResolutionDelta:
    common_call_sites: int
    baseline_only_call_sites: int
    current_only_call_sites: int
    unchanged_resolved: int
    still_unresolved: int
    newly_resolved: int
    corrected: int
    lost: int
    examples: tuple[CodeCallResolutionChange, ...]


@dataclass(frozen=True, slots=True)
class CodeHotspotDelta:
    common: int
    added: int
    removed: int
    changed_evidence: int
    added_examples: tuple[str, ...]
    removed_examples: tuple[str, ...]
    changed_examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeExternalEvidenceDelta:
    """Comparable Ruff evidence and its non-mutating acceptance gate."""

    status: Literal["ready", "not_evaluated"]
    reason: str | None
    baseline: ExternalEvidenceStatus
    current: ExternalEvidenceStatus
    common: int
    added: int | None
    resolved: int | None
    added_examples: tuple[str, ...]
    resolved_examples: tuple[str, ...]
    gate: Literal["passed", "failed", "not_evaluated"]


@dataclass(frozen=True, slots=True)
class CodeProviderEvidenceDelta:
    provider_id: str
    status: Literal["ready", "not_evaluated"]
    reason: str | None
    baseline: ExternalProviderStatus | None
    current: ExternalProviderStatus | None
    common: int
    added: int | None
    resolved: int | None
    gate: Literal["passed", "failed", "not_evaluated"]


@dataclass(frozen=True, slots=True)
class CodePublicationDiffDigest:
    xxh3_128: str
    xxh3_64_guard: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class CodePublicationDiffResult:
    baseline_database: str
    current_database: str
    status: DiffStatus
    reason: str | None
    baseline: CodePublicationSnapshot | None
    current: CodePublicationSnapshot | None
    calls: CodeCallResolutionDelta | None
    hotspots: CodeHotspotDelta | None
    probable_dead_delta: int | None
    external_evidence: CodeExternalEvidenceDelta | None
    analysis_profile: str | None
    providers: tuple[CodeProviderEvidenceDelta, ...]
    verdict: (
        Literal[
            "improved",
            "regressed",
            "mixed",
            "equivalent_under_observed_metrics",
            "incomparable",
        ]
        | None
    )
    limitations: tuple[str, ...]
    digest: CodePublicationDiffDigest | None

    def as_payload(self) -> dict[str, object]:
        return {
            "kind": "code-publication-diff",
            "schema": CODE_PUBLICATION_DIFF_SCHEMA,
            "compatible_schemas": list(CODE_PUBLICATION_DIFF_COMPATIBLE_SCHEMAS),
            **asdict(self),
        }


@dataclass(frozen=True, slots=True)
class _CallSite:
    path: str
    source_symbol: str | None
    name: str
    target_hint: str | None
    line: int
    target: str | None


@dataclass(frozen=True, slots=True)
class _Publication:
    snapshot: CodePublicationSnapshot
    calls: dict[tuple[object, ...], _CallSite]
    hotspots: dict[tuple[str, str], tuple[str, ...]]
    external_evidence: ExternalEvidenceStatus
    external_diagnostic_ids: frozenset[str]
    external_evidence_suite: ExternalEvidenceSuiteStatus
    provider_finding_ids: dict[str, frozenset[str]]


def _root_hint(connection: sqlite3.Connection) -> Path:
    project_roots = [
        str(row[0])
        for row in connection.execute(
            "SELECT probable_root FROM projects WHERE status='current' "
            "ORDER BY probable_root COLLATE NOCASE"
        )
        if row[0]
    ]
    file_parents = [
        str(Path(str(row[0])).parent)
        for row in connection.execute(
            "SELECT current_path FROM files WHERE status='current' "
            "ORDER BY current_path COLLATE NOCASE"
        )
    ]
    candidates = project_roots or file_parents
    if not candidates:
        raise ValueError("code publication has no current project or file root")
    try:
        root = Path(os.path.commonpath(candidates))
    except ValueError as exc:
        raise ValueError("code publication spans incompatible filesystem roots") from exc
    if not root.is_absolute():
        raise ValueError("code publication root is not absolute")
    return root


def _relative_path(path: object, root: Path) -> str:
    absolute = Path(str(path))
    if not absolute.is_absolute():
        raise ValueError(f"code publication path is not absolute: {absolute}")
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"code publication path escapes root: {absolute}") from exc
    value = relative.as_posix()
    if not value or value == ".":
        raise ValueError("code publication path resolves to its root")
    return value


def _target_identity(row: sqlite3.Row, root: Path) -> str | None:
    if row["target_symbol_id"] is None:
        return None
    if row["target_path"] is None or row["target_qualified_name"] is None:
        raise ValueError("resolved call target lacks current file or symbol evidence")
    path = _relative_path(row["target_path"], root)
    return f"{path}::{row['target_qualified_name']}::{row['target_kind']}"


def _read_calls(
    connection: sqlite3.Connection,
    root: Path,
    total_calls: int,
) -> dict[tuple[object, ...], _CallSite]:
    if total_calls > CODE_PUBLICATION_DIFF_MAX_CALLS:
        raise ValueError(
            f"code publication has {total_calls} calls; maximum is "
            f"{CODE_PUBLICATION_DIFF_MAX_CALLS}"
        )
    rows = connection.execute(
        """SELECT f.current_path,
        source.qualified_name AS source_qualified_name,
        r.target_symbol_id,r.name,r.target_hint,
        r.start_line,r.start_column,r.end_line,r.end_column,
        r.start_byte,r.end_byte,
        target.qualified_name AS target_qualified_name,
        target.kind AS target_kind,target_file.current_path AS target_path
        FROM code_references r
        JOIN file_versions v ON v.version_id=r.version_id
        JOIN files f ON f.current_version_id=v.version_id AND f.status='current'
        LEFT JOIN symbols source ON source.symbol_id=r.source_symbol_id
        LEFT JOIN symbols target ON target.symbol_id=r.target_symbol_id
        LEFT JOIN file_versions target_version
          ON target_version.version_id=target.version_id
        LEFT JOIN files target_file
          ON target_file.file_id=target_version.file_id
         AND target_file.current_version_id=target_version.version_id
         AND target_file.status='current'
        WHERE v.invalidated_ns IS NULL AND v.language='python'
          AND r.kind='call' AND r.confirmed=1
        ORDER BY f.current_path COLLATE NOCASE,r.start_byte,r.end_byte,
                 r.name,r.reference_id"""
    ).fetchall()
    if len(rows) != total_calls:
        raise ValueError("code call count changed during immutable publication read")
    calls: dict[tuple[object, ...], _CallSite] = {}
    for row in rows:
        relative = _relative_path(row["current_path"], root)
        source_symbol = (
            None if row["source_qualified_name"] is None else str(row["source_qualified_name"])
        )
        target_hint = None if row["target_hint"] is None else str(row["target_hint"])
        key = (
            relative.casefold(),
            int(row["start_byte"]),
            int(row["end_byte"]),
            str(row["name"]),
        )
        if key in calls:
            raise ValueError(f"duplicate stable call identity in publication: {relative}")
        calls[key] = _CallSite(
            relative,
            source_symbol,
            str(row["name"]),
            target_hint,
            int(row["start_line"]),
            _target_identity(row, root),
        )
    return calls


def _diagnostic_signal(row: sqlite3.Row) -> str:
    metadata = json.loads(str(row["metadata_json"]))
    if not isinstance(metadata, dict):
        raise ValueError("hotspot diagnostic metadata is not an object")
    value = metadata.get("value")
    threshold = metadata.get("threshold")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("hotspot diagnostic value is not an integer")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise ValueError("hotspot diagnostic threshold is not positive")
    return f"{row['code']}:{value}/{threshold}"


def _read_hotspots(
    connection: sqlite3.Connection,
    root: Path,
    total_diagnostics: int,
) -> tuple[dict[tuple[str, str], tuple[str, ...]], int, int]:
    if total_diagnostics > CODE_PUBLICATION_DIFF_MAX_HOTSPOTS * 2:
        raise ValueError(
            f"code publication has {total_diagnostics} hotspot diagnostics; maximum is "
            f"{CODE_PUBLICATION_DIFF_MAX_HOTSPOTS * 2}"
        )
    rows = connection.execute(
        """SELECT f.current_path,s.qualified_name,d.code,d.metadata_json
        FROM diagnostics d
        JOIN file_versions v ON v.version_id=d.version_id
        JOIN files f ON f.current_version_id=v.version_id AND f.status='current'
        JOIN symbols s ON s.version_id=d.version_id
         AND s.start_byte=d.start_byte AND s.end_byte=d.end_byte
        WHERE v.invalidated_ns IS NULL AND v.language='python'
          AND d.confirmed=1 AND d.code IN ('high_complexity','long_function')
        ORDER BY f.current_path COLLATE NOCASE,s.qualified_name,d.code"""
    ).fetchall()
    if len(rows) != total_diagnostics:
        raise ValueError("hotspot count changed during immutable publication read")
    grouped: dict[tuple[str, str], set[str]] = {}
    high_complexity = 0
    long_function = 0
    for row in rows:
        relative = _relative_path(row["current_path"], root)
        key = (relative, str(row["qualified_name"]))
        signal = _diagnostic_signal(row)
        if signal in grouped.setdefault(key, set()):
            raise ValueError(f"duplicate hotspot signal in publication: {relative}")
        grouped[key].add(signal)
        high_complexity += row["code"] == "high_complexity"
        long_function += row["code"] == "long_function"
    if len(grouped) > CODE_PUBLICATION_DIFF_MAX_HOTSPOTS:
        raise ValueError(
            f"code publication has {len(grouped)} hotspots; maximum is "
            f"{CODE_PUBLICATION_DIFF_MAX_HOTSPOTS}"
        )
    return (
        {key: tuple(sorted(signals)) for key, signals in grouped.items()},
        high_complexity,
        long_function,
    )


def _read_publication(state_directory: Path) -> _Publication:
    state_directory = Path(state_directory)
    database = state_directory / "code.sqlite3"
    require_sqlite_sidecars_absent(database)
    if not database.is_file():
        raise FileNotFoundError(database)
    with readonly_code_database(database) as connection:
        validate_code_schema(connection)
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version != CODE_SCHEMA_VERSION:
            raise RuntimeError(f"code state schema {schema_version} is unsupported for diff")
        latest = connection.execute(
            """SELECT analysis_run_id,processing_signature,status FROM analysis_runs
            ORDER BY analysis_run_id DESC LIMIT 1"""
        ).fetchone()
        if latest is None:
            raise ValueError("code publication has no analysis run")
        if str(latest["status"]) != "completed":
            raise ValueError(f"latest code run is not completed: {latest['status']}")
        root = _root_hint(connection)
        python_files, complete_python_files = connection.execute(
            """SELECT COUNT(*),SUM(CASE WHEN v.analysis_status='complete' THEN 1
            ELSE 0 END) FROM file_versions v
            JOIN files f ON f.current_version_id=v.version_id
            WHERE f.status='current' AND v.invalidated_ns IS NULL
              AND v.language='python' AND v.generated=0 AND v.vendored=0"""
        ).fetchone()
        call_edges, resolved_call_edges = connection.execute(
            """SELECT COUNT(*),COUNT(r.target_symbol_id)
            FROM code_references r
            JOIN file_versions v ON v.version_id=r.version_id
            JOIN files f ON f.current_version_id=v.version_id
            WHERE f.status='current' AND v.invalidated_ns IS NULL
              AND v.language='python' AND r.kind='call' AND r.confirmed=1"""
        ).fetchone()
        total_hotspot_diagnostics = int(
            connection.execute(
                """SELECT COUNT(*) FROM diagnostics d
                JOIN file_versions v ON v.version_id=d.version_id
                JOIN files f ON f.current_version_id=v.version_id
                WHERE f.status='current' AND v.invalidated_ns IS NULL
                  AND v.language='python' AND d.confirmed=1
                  AND d.code IN ('high_complexity','long_function')"""
            ).fetchone()[0]
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
        calls = _read_calls(connection, root, int(call_edges))
        hotspots, high_complexity, long_function = _read_hotspots(
            connection,
            root,
            total_hotspot_diagnostics,
        )
        external_evidence, external_ids, _external_row = read_external_evidence(
            connection,
            int(latest["analysis_run_id"]),
            enforce_current_runtime=False,
        )
        external_suite = read_external_evidence_suite(
            connection,
            int(latest["analysis_run_id"]),
            enforce_current_runtime=False,
        )
        provider_ids = read_external_provider_finding_ids(
            connection,
            int(latest["analysis_run_id"]),
        )
    snapshot = CodePublicationSnapshot(
        state_directory=str(state_directory),
        database=str(database),
        root=str(root),
        processing_signature=str(latest["processing_signature"]),
        python_files=int(python_files),
        complete_python_files=int(complete_python_files or 0),
        call_edges=int(call_edges),
        resolved_call_edges=int(resolved_call_edges),
        hotspots=len(hotspots),
        high_complexity=high_complexity,
        long_function=long_function,
        probable_dead=probable_dead,
    )
    return _Publication(
        snapshot,
        calls,
        hotspots,
        external_evidence,
        external_ids,
        external_suite,
        provider_ids,
    )


def _call_delta(
    baseline: _Publication,
    current: _Publication,
) -> CodeCallResolutionDelta:
    baseline_keys = set(baseline.calls)
    current_keys = set(current.calls)
    common_keys = sorted(baseline_keys & current_keys)
    unchanged_resolved = 0
    still_unresolved = 0
    newly_resolved = 0
    corrected = 0
    lost = 0
    examples: list[CodeCallResolutionChange] = []
    for key in common_keys:
        before = baseline.calls[key]
        after = current.calls[key]
        change: Literal["newly_resolved", "corrected", "lost"] | None = None
        if before.target is None and after.target is None:
            still_unresolved += 1
        elif before.target is None:
            newly_resolved += 1
            change = "newly_resolved"
        elif after.target is None:
            lost += 1
            change = "lost"
        elif before.target == after.target:
            unchanged_resolved += 1
        else:
            corrected += 1
            change = "corrected"
        if change is not None and len(examples) < CODE_PUBLICATION_DIFF_EXAMPLE_LIMIT:
            examples.append(
                CodeCallResolutionChange(
                    change,
                    after.path,
                    after.source_symbol,
                    after.name,
                    after.target_hint,
                    after.line,
                    before.target,
                    after.target,
                )
            )
    return CodeCallResolutionDelta(
        common_call_sites=len(common_keys),
        baseline_only_call_sites=len(baseline_keys - current_keys),
        current_only_call_sites=len(current_keys - baseline_keys),
        unchanged_resolved=unchanged_resolved,
        still_unresolved=still_unresolved,
        newly_resolved=newly_resolved,
        corrected=corrected,
        lost=lost,
        examples=tuple(examples),
    )


def _hotspot_label(key: tuple[str, str]) -> str:
    return f"{key[0]}::{key[1]}"


def _hotspot_delta(baseline: _Publication, current: _Publication) -> CodeHotspotDelta:
    baseline_keys = set(baseline.hotspots)
    current_keys = set(current.hotspots)
    common_keys = baseline_keys & current_keys
    added = sorted(current_keys - baseline_keys)
    removed = sorted(baseline_keys - current_keys)
    changed = sorted(key for key in common_keys if baseline.hotspots[key] != current.hotspots[key])
    limit = CODE_PUBLICATION_DIFF_EXAMPLE_LIMIT
    return CodeHotspotDelta(
        common=len(common_keys),
        added=len(added),
        removed=len(removed),
        changed_evidence=len(changed),
        added_examples=tuple(_hotspot_label(key) for key in added[:limit]),
        removed_examples=tuple(_hotspot_label(key) for key in removed[:limit]),
        changed_examples=tuple(_hotspot_label(key) for key in changed[:limit]),
    )


def _external_delta(
    baseline: _Publication,
    current: _Publication,
) -> CodeExternalEvidenceDelta:
    before = baseline.external_evidence
    after = current.external_evidence
    comparable = (
        before.status == "ready"
        and after.status == "ready"
        and before.tool_version == after.tool_version
        and before.configuration_signature == after.configuration_signature
    )
    if not comparable:
        reasons = []
        if before.status != "ready":
            reasons.append(f"baseline_{before.status}:{before.reason}")
        if after.status != "ready":
            reasons.append(f"current_{after.status}:{after.reason}")
        if (
            before.status == "ready"
            and after.status == "ready"
            and before.tool_version != after.tool_version
        ):
            reasons.append("tool_version_changed")
        if (
            before.status == "ready"
            and after.status == "ready"
            and before.configuration_signature != after.configuration_signature
        ):
            reasons.append("configuration_changed")
        return CodeExternalEvidenceDelta(
            "not_evaluated",
            ";".join(reasons) or "external_evidence_not_comparable",
            before,
            after,
            0,
            None,
            None,
            (),
            (),
            "not_evaluated",
        )
    common = baseline.external_diagnostic_ids & current.external_diagnostic_ids
    added = sorted(current.external_diagnostic_ids - baseline.external_diagnostic_ids)
    resolved = sorted(baseline.external_diagnostic_ids - current.external_diagnostic_ids)
    return CodeExternalEvidenceDelta(
        "ready",
        None,
        before,
        after,
        len(common),
        len(added),
        len(resolved),
        tuple(added[:CODE_PUBLICATION_DIFF_EXAMPLE_LIMIT]),
        tuple(resolved[:CODE_PUBLICATION_DIFF_EXAMPLE_LIMIT]),
        "passed" if not added else "failed",
    )


def _legacy_ruff_contract_compatible(
    provider_id: str,
    left: ExternalProviderStatus | None,
    right: ExternalProviderStatus | None,
) -> bool:
    return (
        left is not None
        and right is not None
        and provider_id == "ruff-protected-basic"
        and left.profile == right.profile == "protected"
        and left.provider_schema == right.provider_schema == "neocortex.ruff-protected-basic/v1"
        and left.tool_name == right.tool_name == "ruff"
        and left.tool_version == right.tool_version
        and (
            (
                left.comparability_signature == RUFF_CONFIGURATION_SIGNATURE
                and right.comparability_signature.startswith("ruff-protected-basic-comparable-v1:")
            )
            or (
                right.comparability_signature == RUFF_CONFIGURATION_SIGNATURE
                and left.comparability_signature.startswith("ruff-protected-basic-comparable-v1:")
            )
        )
    )


def _provider_deltas(
    baseline: _Publication,
    current: _Publication,
) -> tuple[CodeProviderEvidenceDelta, ...]:
    before = {item.provider_id: item for item in baseline.external_evidence_suite.providers}
    after = {item.provider_id: item for item in current.external_evidence_suite.providers}
    deltas: list[CodeProviderEvidenceDelta] = []
    for provider_id in sorted(set(before) | set(after)):
        left = before.get(provider_id)
        right = after.get(provider_id)
        legacy_ruff_compatible = _legacy_ruff_contract_compatible(provider_id, left, right)
        comparable = (
            left is not None
            and right is not None
            and left.status == "ready"
            and right.status == "ready"
            and left.profile == right.profile
            and left.provider_schema == right.provider_schema
            and left.tool_version == right.tool_version
            and (
                left.comparability_signature == right.comparability_signature
                or legacy_ruff_compatible
            )
        )
        if not comparable:
            reasons: list[str] = []
            if left is None:
                reasons.append("baseline_provider_missing")
            elif left.status != "ready":
                reasons.append(f"baseline_{left.status}:{left.reason}")
            if right is None:
                reasons.append("current_provider_missing")
            elif right.status != "ready":
                reasons.append(f"current_{right.status}:{right.reason}")
            if left is not None and right is not None:
                if left.profile != right.profile:
                    reasons.append("profile_changed")
                if left.provider_schema != right.provider_schema:
                    reasons.append("provider_schema_changed")
                if left.tool_version != right.tool_version:
                    reasons.append("tool_version_changed")
                if (
                    left.comparability_signature != right.comparability_signature
                    and not legacy_ruff_compatible
                ):
                    reasons.append("comparability_signature_changed")
            deltas.append(
                CodeProviderEvidenceDelta(
                    provider_id,
                    "not_evaluated",
                    ";".join(reasons) or "provider_not_comparable",
                    left,
                    right,
                    0,
                    None,
                    None,
                    "not_evaluated",
                )
            )
            continue
        baseline_ids = baseline.provider_finding_ids.get(provider_id)
        if baseline_ids is None:
            baseline_ids = (
                baseline.external_diagnostic_ids
                if left.comparability_signature == RUFF_CONFIGURATION_SIGNATURE
                else frozenset()
            )
        current_ids = current.provider_finding_ids.get(provider_id)
        if current_ids is None:
            current_ids = (
                current.external_diagnostic_ids
                if right.comparability_signature == RUFF_CONFIGURATION_SIGNATURE
                else frozenset()
            )
        added = current_ids - baseline_ids
        resolved = baseline_ids - current_ids
        deltas.append(
            CodeProviderEvidenceDelta(
                provider_id,
                "ready",
                None,
                left,
                right,
                len(baseline_ids & current_ids),
                len(added),
                len(resolved),
                "passed" if not added else "failed",
            )
        )
    return tuple(deltas)


def _provider_verdict(
    deltas: tuple[CodeProviderEvidenceDelta, ...],
) -> Literal[
    "improved",
    "regressed",
    "mixed",
    "equivalent_under_observed_metrics",
    "incomparable",
]:
    comparable = tuple(item for item in deltas if item.status == "ready")
    if not comparable:
        return "incomparable"
    added = sum(item.added or 0 for item in comparable)
    resolved = sum(item.resolved or 0 for item in comparable)
    if added and resolved:
        return "mixed"
    if added:
        return "regressed"
    if resolved:
        return "improved"
    return "equivalent_under_observed_metrics"


def _digest_payload(
    baseline: CodePublicationSnapshot,
    current: CodePublicationSnapshot,
    calls: CodeCallResolutionDelta,
    hotspots: CodeHotspotDelta,
    probable_dead_delta: int,
    external_evidence: CodeExternalEvidenceDelta,
    providers: tuple[CodeProviderEvidenceDelta, ...],
    verdict: str,
    limitations: tuple[str, ...],
) -> CodePublicationDiffDigest:
    def snapshot(value: CodePublicationSnapshot) -> dict[str, object]:
        payload = asdict(value)
        for key in ("state_directory", "database", "root"):
            payload.pop(key)
        return payload

    serialized = canonical_json(
        {
            "schema": CODE_PUBLICATION_DIFF_SCHEMA,
            "baseline": snapshot(baseline),
            "current": snapshot(current),
            "calls": asdict(calls),
            "hotspots": asdict(hotspots),
            "probable_dead_delta": probable_dead_delta,
            "external_evidence": {
                "status": external_evidence.status,
                "reason": external_evidence.reason,
                "baseline": external_status_digest_payload(external_evidence.baseline),
                "current": external_status_digest_payload(external_evidence.current),
                "common": external_evidence.common,
                "added": external_evidence.added,
                "resolved": external_evidence.resolved,
                "added_examples": list(external_evidence.added_examples),
                "resolved_examples": list(external_evidence.resolved_examples),
                "gate": external_evidence.gate,
            },
            "providers": [asdict(item) for item in providers],
            "verdict": verdict,
            "limitations": list(limitations),
        }
    )
    fingerprint = fingerprint_text(serialized)
    return CodePublicationDiffDigest(
        fingerprint.xxh3_128,
        fingerprint.xxh3_64_guard,
        fingerprint.byte_count,
    )


def _abstained(
    baseline_state: Path,
    current_state: Path,
    reason: str,
) -> CodePublicationDiffResult:
    return CodePublicationDiffResult(
        baseline_database=str(Path(baseline_state) / "code.sqlite3"),
        current_database=str(Path(current_state) / "code.sqlite3"),
        status="abstained",
        reason=reason,
        baseline=None,
        current=None,
        calls=None,
        hotspots=None,
        probable_dead_delta=None,
        external_evidence=None,
        analysis_profile=None,
        providers=(),
        verdict=None,
        limitations=(),
        digest=None,
    )


def compare_code_publications(
    baseline_state: Path,
    current_state: Path,
) -> CodePublicationDiffResult:
    """Compare two immutable completed publications without writing either owner."""

    baseline_state = Path(baseline_state)
    current_state = Path(current_state)
    try:
        baseline = _read_publication(baseline_state)
    except (FileNotFoundError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _abstained(
            baseline_state,
            current_state,
            f"baseline_unavailable:{type(exc).__name__}:{exc}",
        )
    try:
        current = _read_publication(current_state)
    except (FileNotFoundError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return _abstained(
            baseline_state,
            current_state,
            f"current_unavailable:{type(exc).__name__}:{exc}",
        )
    calls = _call_delta(baseline, current)
    hotspots = _hotspot_delta(baseline, current)
    probable_dead_delta = current.snapshot.probable_dead - baseline.snapshot.probable_dead
    external_evidence = _external_delta(baseline, current)
    providers = _provider_deltas(baseline, current)
    verdict = _provider_verdict(providers)
    limitations = [
        "common_calls_require_matching_source_path_byte_range_and_name",
        "dynamic_dispatch_is_not_observed",
        "probable_dead_is_count_only_uncalibrated_evidence",
        "diff_is_observational_and_never_authorizes_code_or_corpus_mutation",
    ]
    if any(item.status != "ready" for item in providers):
        limitations.append("provider_verdict_uses_only_comparable_providers")
    if any(
        _legacy_ruff_contract_compatible(item.provider_id, item.baseline, item.current)
        for item in providers
    ):
        limitations.append(_LEGACY_RUFF_COMPARABILITY_REASON)
    frozen_limitations = tuple(limitations)
    digest = _digest_payload(
        baseline.snapshot,
        current.snapshot,
        calls,
        hotspots,
        probable_dead_delta,
        external_evidence,
        providers,
        verdict,
        frozen_limitations,
    )
    return CodePublicationDiffResult(
        baseline_database=baseline.snapshot.database,
        current_database=current.snapshot.database,
        status="ready",
        reason=None,
        baseline=baseline.snapshot,
        current=current.snapshot,
        calls=calls,
        hotspots=hotspots,
        probable_dead_delta=probable_dead_delta,
        external_evidence=external_evidence,
        analysis_profile=current.external_evidence_suite.profile,
        providers=providers,
        verdict=verdict,
        limitations=frozen_limitations,
        digest=digest,
    )


__all__ = [
    "CODE_PUBLICATION_DIFF_COMPATIBLE_SCHEMAS",
    "CODE_PUBLICATION_DIFF_SCHEMA",
    "CodeCallResolutionChange",
    "CodeCallResolutionDelta",
    "CodeExternalEvidenceDelta",
    "CodeHotspotDelta",
    "CodeProviderEvidenceDelta",
    "CodePublicationDiffDigest",
    "CodePublicationDiffResult",
    "CodePublicationSnapshot",
    "compare_code_publications",
]
