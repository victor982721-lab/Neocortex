"""Unified read-only Knowledge retrieval over existing owner indexes.

The service keeps source rankings separate, converts only resolved evidence to
public contracts, fuses by concrete evidence identity with RRF, and applies
resource diversity afterwards.  It does not index, migrate or create state.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from . import semantic_service
from .code_contracts import CodeSearchHit, CodeSearchQuery, CodeSearchRelation
from .code_detection import LANGUAGE_EXTENSIONS
from .code_schema import connect_code_state
from .code_search import search_code
from .document_catalog import document_catalog_database
from .file_identity import FileIdentity, FileIdentityEncoding, FileIdentityError
from .knowledge_contracts import (
    MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS,
    EvidenceMethod,
    EvidenceRef,
    KnowledgeHit,
    KnowledgePhaseTiming,
    KnowledgeQueryTelemetry,
    KnowledgeSnapshot,
    KnowledgeTelemetryClock,
    KnowledgeTelemetryOperation,
    KnowledgeTimingPhase,
    OwnerAvailability,
    PhysicalIdentityRef,
    RankingSignal,
    ResourceRef,
    RevisionRef,
    RevisionState,
    SnapshotConsistency,
)
from .knowledge_exact import ExactOwnerTiming, lookup_plan_exact
from .knowledge_planner import KnowledgePlan, RetrievalMode, RetrievalStep
from .knowledge_search_contracts import (
    KnowledgeCandidate,
    KnowledgeSearchResult,
    RankingExecution,
    ResourceDiscoverySignal,
)
from .knowledge_search_code import (
    bounded_code_relation_value as _code_bounded_relation_value_impl,
    code_ranking as _code_ranking_impl,
    code_relation_candidate as _code_relation_candidate_impl,
    code_resource_revision as _code_resource_revision_impl,
    code_version_metadata as _code_version_metadata_impl,
)
from .knowledge_search_content import (
    candidate_from_resolved as _content_candidate_from_resolved,
    direct_resource_ref as _content_direct_resource_ref,
    exact_rankings as _content_exact_rankings,
    int_provenance as _content_int_provenance,
    lexical_rankings as _content_lexical_rankings,
    resolved_physical_identity as _content_resolved_physical_identity,
    resource_discovery_signal_from_resolved as _content_resource_discovery_signal_from_resolved,
    revision_identity as _content_revision_identity,
    semantic_rankings as _content_semantic_rankings,
)
from .knowledge_search_catalog import (
    catalog_identifiers as _catalog_identifiers_impl,
    catalog_ranking as _catalog_ranking_impl,
    escape_like as _catalog_escape_like_impl,
)
from .knowledge_search_inventory import (
    apply_inventory_dispositions as _inventory_apply_inventory_dispositions,
    inventory_identity_blob as _inventory_identity_blob_impl,
    inventory_plan_heads as _inventory_plan_heads_impl,
    inventory_relation_row as _inventory_relation_row_impl,
    open_direct_readonly_sqlite as _inventory_open_direct_readonly_sqlite,
    physical_identity_tuple as _inventory_physical_identity_tuple,
    valid_full_fingerprint as _inventory_valid_full_fingerprint,
    validated_inventory_blob as _inventory_validated_inventory_blob,
)
from .knowledge_search_fusion import (
    fuse_evidence_rankings as _fuse_evidence_rankings,
    overlaps_or_too_close as _fusion_overlaps_or_too_close,
)
from .knowledge_snapshot import KnowledgeStatePaths
from .semantic_lexical import (
    LexicalAvailability,
    LexicalStatePaths,
    search_lexical_sources,
)
from .semantic_models import ResolvedSearchHit, canonical_json, fingerprint_text
from .sqlite_cancellation import SQLiteCancellationBridge, sqlite_cancellation_scope
from .sqlite_paths import readonly_sqlite_uri

# region [01] Public search facade and runtime constants


RRF_K = 60.0
MAX_KNOWLEDGE_CANDIDATES = 1_000
SQLITE_BATCH_SIZE = 500
INVENTORY_IDENTITY_BATCH_SIZE = 200
INVENTORY_HEAD_BATCH_SIZE = 50
MAX_INVENTORY_RELATIONS = 4_000
MAX_CODE_RELATION_CANDIDATES = 4_000
_LEXICAL_OWNER_FORMATS: dict[str, frozenset[str]] = {
    "pdf": frozenset({"pdf"}),
    "docx": frozenset({"docx"}),
    "office": frozenset(
        {"doc", "odt", "ods", "odp", "xls", "xlsx", "xlsm", "ppt", "pptx"}
    ),
    "audio": frozenset(
        {"audio", "aac", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"}
    ),
}
_IMAGE_FORMATS = frozenset(
    {"avif", "bmp", "gif", "heic", "heif", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
)
_CODE_LANGUAGE_BY_EXTENSION = {
    extension.removeprefix(".").casefold(): language.casefold()
    for extension, language in LANGUAGE_EXTENSIONS.items()
}
_CODE_QUERY_CUES = frozenset(
    {
        "call",
        "calls",
        "class",
        "clase",
        "definition",
        "definición",
        "function",
        "función",
        "import",
        "importa",
        "reference",
        "referencia",
        "signature",
        "símbolo",
        "symbol",
    }
)


def _duration_ns(clock_ns: Callable[[], int], started_ns: int) -> int:
    finished_ns = clock_ns()
    if (
        isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
        or finished_ns < started_ns
    ):
        raise RuntimeError("Knowledge monotonic clock moved backwards or was invalid")
    return finished_ns - started_ns


def _cleanup_preserving_primary(
    cleanup: Callable[[], object],
    primary: BaseException,
    *,
    label: str,
) -> None:
    """Run cleanup without replacing an already-raised primary exception."""

    try:
        cleanup()
    except BaseException as cleanup_error:
        primary.add_note(
            f"{label} failed: {type(cleanup_error).__name__}: {cleanup_error}"
        )


def _reraise_captured_cancellation(
    bridge: SQLiteCancellationBridge,
    cause: BaseException,
) -> None:
    """Re-raise the exact callback exception captured before owner fallback."""

    captured = bridge.captured_exception
    if captured is None:
        return
    if captured is cause:
        raise captured
    raise captured from cause


# endregion [01]


# region [02] Evidence-key RRF and post-retrieval diversity


def _overlaps_or_too_close(
    selected: EvidenceRef,
    candidate: EvidenceRef,
    minimum_distance: int,
) -> bool:
    return _fusion_overlaps_or_too_close(
        selected,
        candidate,
        minimum_distance,
    )


def fuse_evidence_rankings(
    rankings: Mapping[str, Sequence[KnowledgeCandidate]],
    *,
    discovery_signals: Sequence[ResourceDiscoverySignal] = (),
    limit: int,
    max_per_resource: int,
    min_section_distance: int,
    include_history: bool = False,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[tuple[KnowledgeHit, ...], int]:
    """Fuse independent ranks by evidence, then apply bounded diversity."""

    return _fuse_evidence_rankings(
        rankings,
        discovery_signals=discovery_signals,
        limit=limit,
        max_per_resource=max_per_resource,
        min_section_distance=min_section_distance,
        include_history=include_history,
        cancellation_check=cancellation_check,
        rrf_k=RRF_K,
        overlap_check=_overlaps_or_too_close,
    )


# endregion [02]


# region [03] Owner result normalization


def _owner_available(snapshot: KnowledgeSnapshot, owner: str) -> bool:
    return any(
        item.owner == owner and item.state is OwnerAvailability.AVAILABLE
        for item in snapshot.owners
    )


def _planned_steps(
    plan: KnowledgePlan,
    channel: str,
) -> tuple[RetrievalStep, ...]:
    return tuple(step for step in plan.steps if step.channel == channel)


def _planned_step(plan: KnowledgePlan, channel: str) -> RetrievalStep:
    steps = _planned_steps(plan, channel)
    if len(steps) != 1:
        raise ValueError(
            f"Knowledge plan must contain exactly one {channel} retrieval step"
        )
    return steps[0]


def _planned_candidate_limit(plan: KnowledgePlan, channel: str) -> int:
    return _planned_step(plan, channel).candidate_limit


def _open_direct_readonly_sqlite(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite owner with read-only behavior verified live."""

    return _inventory_open_direct_readonly_sqlite(
        path,
        sqlite_connect=sqlite3.connect,
        readonly_sqlite_uri=readonly_sqlite_uri,
        sqlite_row_factory=sqlite3.Row,
        sqlite_operational_error=sqlite3.OperationalError,
        cleanup_preserving_primary=_cleanup_preserving_primary,
    )


def _revision_identity(
    resolved: ResolvedSearchHit,
    producer: str,
) -> tuple[str, str, RevisionState, tuple[str, ...]]:
    return _content_revision_identity(
        resolved,
        producer,
        canonical_json_fn=canonical_json,
        fingerprint_text_fn=fingerprint_text,
    )


def _int_provenance(
    provenance: Mapping[str, object],
    name: str,
) -> int | None:
    return _content_int_provenance(provenance, name)


def _resolved_physical_identity(resolved: ResolvedSearchHit) -> str | None:
    return _content_resolved_physical_identity(
        resolved,
        int_provenance_fn=_int_provenance,
        file_identity_type=FileIdentity,
        file_identity_encoding=FileIdentityEncoding.AUTO,
        identity_errors=(FileIdentityError, ValueError),
    )


def _direct_resource_ref(
    *,
    source_kind: str,
    owner: str,
    source_identity: str,
    identity: FileIdentity,
    birthtime_ns: object,
    path: str | None,
) -> tuple[ResourceRef, tuple[str, ...]]:
    return _content_direct_resource_ref(
        source_kind=source_kind,
        owner=owner,
        source_identity=source_identity,
        identity=identity,
        birthtime_ns=birthtime_ns,
        path=path,
        resource_ref_type=ResourceRef,
        physical_identity_ref_type=PhysicalIdentityRef,
    )


def _candidate_from_resolved(
    resolved: ResolvedSearchHit,
    *,
    ranking_name: str,
    source_rank: int,
    producer: str,
) -> KnowledgeCandidate:
    return _content_candidate_from_resolved(
        resolved,
        ranking_name=ranking_name,
        source_rank=source_rank,
        producer=producer,
        resolved_physical_identity_fn=_resolved_physical_identity,
        int_provenance_fn=_int_provenance,
        revision_identity_fn=_revision_identity,
        lexical_owner_formats=_LEXICAL_OWNER_FORMATS,
        resource_ref_type=ResourceRef,
        physical_identity_ref_type=PhysicalIdentityRef,
        revision_ref_type=RevisionRef,
        evidence_ref_type=EvidenceRef,
        extracted_method=EvidenceMethod.EXTRACTED,
        ranking_signal_type=RankingSignal,
        candidate_type=KnowledgeCandidate,
    )


def _resource_discovery_signal_from_resolved(
    resolved: ResolvedSearchHit,
    *,
    ranking_name: str,
    source_rank: int,
    producer: str,
    fusion_weight: float,
) -> ResourceDiscoverySignal:
    return _content_resource_discovery_signal_from_resolved(
        resolved,
        ranking_name=ranking_name,
        source_rank=source_rank,
        producer=producer,
        fusion_weight=fusion_weight,
        resolved_physical_identity_fn=_resolved_physical_identity,
        int_provenance_fn=_int_provenance,
        revision_identity_fn=_revision_identity,
        lexical_owner_formats=_LEXICAL_OWNER_FORMATS,
        resource_ref_type=ResourceRef,
        physical_identity_ref_type=PhysicalIdentityRef,
        revision_ref_type=RevisionRef,
        ranking_signal_type=RankingSignal,
        discovery_signal_type=ResourceDiscoverySignal,
    )


def _lexical_rankings(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] | None = None,
) -> tuple[dict[str, tuple[KnowledgeCandidate, ...]], list[RankingExecution]]:
    return _content_lexical_rankings(
        paths,
        plan,
        snapshot,
        cancellation_check=cancellation_check,
        clock_ns=clock_ns,
        owner_available=_owner_available,
        planned_candidate_limit=_planned_candidate_limit,
        materialize_candidate=_candidate_from_resolved,
        lexical_search=search_lexical_sources,
        state_paths_type=LexicalStatePaths,
        lexical_available=LexicalAvailability.AVAILABLE,
        revision_current=RevisionState.CURRENT,
        revision_partial=RevisionState.PARTIAL,
        max_candidates=MAX_KNOWLEDGE_CANDIDATES,
    )


def _semantic_rankings(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    cancellation_check: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] | None = None,
) -> tuple[
    dict[str, tuple[KnowledgeCandidate, ...]],
    tuple[ResourceDiscoverySignal, ...],
    list[RankingExecution],
]:
    return _content_semantic_rankings(
        paths,
        plan,
        snapshot,
        cancellation_check,
        clock_ns,
        planned_steps=_planned_steps,
        owner_available=_owner_available,
        duration_ns=_duration_ns,
        materialize_candidate=_candidate_from_resolved,
        materialize_discovery_signal=_resource_discovery_signal_from_resolved,
        default_clock=time.perf_counter_ns,
        semantic_search=semantic_service.search_semantic_index,
        cancellation_bridge_type=SQLiteCancellationBridge,
        reraise_captured_cancellation=_reraise_captured_cancellation,
        sqlite_error_type=sqlite3.Error,
        evidence_mode=RetrievalMode.EVIDENCE,
    )


def _decimal_identity_value(value: object) -> int:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return int.from_bytes(bytes(value), "little")
    if isinstance(value, bool):
        raise ValueError("physical identity cannot be boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 10)
    raise ValueError("physical identity must be bytes, decimal text, or integer")


def _physical_identity_tuple(
    resource: ResourceRef,
) -> tuple[int, int, int] | None:
    return _inventory_physical_identity_tuple(
        resource,
        file_identity_type=FileIdentity,
        file_identity_errors=(FileIdentityError, ValueError),
    )


def _inventory_plan_heads(
    snapshot: KnowledgeSnapshot,
) -> tuple[tuple[tuple[int, int, int, int, int], ...], bool]:
    return _inventory_plan_heads_impl(
        snapshot,
        available_state=OwnerAvailability.AVAILABLE,
    )


def _inventory_identity_blob(value: int) -> bytes:
    return _inventory_identity_blob_impl(value)


def _validated_inventory_blob(value: object) -> int:
    return _inventory_validated_inventory_blob(
        value,
        file_identity_type=FileIdentity,
    )


def _valid_full_fingerprint(value: object) -> bool:
    return _inventory_valid_full_fingerprint(value)


def _inventory_relation_row(
    row: sqlite3.Row,
) -> tuple[tuple[int, int, int], str, tuple[int, int, int]] | None:
    return _inventory_relation_row_impl(
        row,
        validated_inventory_blob=_validated_inventory_blob,
        file_identity_type=FileIdentity,
        valid_full_fingerprint=_valid_full_fingerprint,
    )


def _apply_inventory_dispositions(
    paths: KnowledgeStatePaths,
    snapshot: KnowledgeSnapshot,
    rankings: Mapping[str, Sequence[KnowledgeCandidate]],
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[dict[str, tuple[KnowledgeCandidate, ...]], RankingExecution]:
    """Read planned duplicate relations, but abstain without exact provenance."""

    return _inventory_apply_inventory_dispositions(
        paths,
        snapshot,
        rankings,
        cancellation_check=cancellation_check,
        owner_available=_owner_available,
        inventory_plan_heads=_inventory_plan_heads,
        physical_identity_tuple=_physical_identity_tuple,
        open_direct_readonly_sqlite=_open_direct_readonly_sqlite,
        inventory_identity_blob=_inventory_identity_blob,
        validated_inventory_blob=_validated_inventory_blob,
        inventory_relation_row=_inventory_relation_row,
        cleanup_preserving_primary=_cleanup_preserving_primary,
        identity_batch_size=INVENTORY_IDENTITY_BATCH_SIZE,
        head_batch_size=INVENTORY_HEAD_BATCH_SIZE,
        max_inventory_relations=MAX_INVENTORY_RELATIONS,
        sqlite_error_type=sqlite3.Error,
        ranking_execution_type=RankingExecution,
        replace_fn=replace,
    )


def _exact_rankings(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] | None = None,
) -> tuple[
    dict[str, tuple[KnowledgeCandidate, ...]],
    list[RankingExecution],
    int,
    bool,
    tuple[ExactOwnerTiming, ...],
]:
    return _content_exact_rankings(
        paths,
        plan,
        snapshot,
        cancellation_check=cancellation_check,
        clock_ns=clock_ns,
        lookup_exact=lookup_plan_exact,
        planned_candidate_limit=_planned_candidate_limit,
    )


def _code_version_metadata(
    path: Path,
    version_ids: Sequence[int],
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> dict[int, sqlite3.Row]:
    """Resolve physical identity and producer provenance in bounded batches."""

    return _code_version_metadata_impl(
        path,
        version_ids,
        cancellation_check=cancellation_check,
        connect_code_state_fn=connect_code_state,
        cancellation_bridge_type=SQLiteCancellationBridge,
        sqlite_cancellation_scope_fn=sqlite_cancellation_scope,
        cleanup_preserving_primary_fn=_cleanup_preserving_primary,
        sqlite_batch_size=SQLITE_BATCH_SIZE,
    )


def _code_resource_revision(
    row: sqlite3.Row,
    *,
    path: str,
) -> tuple[ResourceRef, RevisionRef, tuple[str, ...]]:
    """Build the neutral resource/revision identity for one current code version."""

    return _code_resource_revision_impl(
        row,
        path=path,
        file_identity_type=FileIdentity,
        direct_resource_ref_fn=_direct_resource_ref,
        canonical_json_fn=canonical_json,
        fingerprint_text_fn=fingerprint_text,
        revision_ref_type=RevisionRef,
        revision_state_type=RevisionState,
    )


def _bounded_code_relation_value(
    namespace: str,
    value: str,
    warnings: set[str],
) -> str:
    return _code_bounded_relation_value_impl(
        namespace,
        value,
        warnings,
        max_identifier_chars=MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS,
        fingerprint_text_fn=fingerprint_text,
    )


def _code_relation_candidate(
    metadata: Mapping[int, sqlite3.Row],
    *,
    source_rank: int,
    hit: CodeSearchHit,
    relation: CodeSearchRelation,
) -> tuple[KnowledgeCandidate | None, bool]:
    """Materialize one owner relation without fabricating a target endpoint."""

    return _code_relation_candidate_impl(
        metadata,
        source_rank=source_rank,
        hit=hit,
        relation=relation,
        code_resource_revision_fn=_code_resource_revision,
        bounded_relation_value_fn=_bounded_code_relation_value,
        file_identity_error_type=FileIdentityError,
        canonical_json_fn=canonical_json,
        fingerprint_text_fn=fingerprint_text,
        evidence_method_type=EvidenceMethod,
        evidence_ref_type=EvidenceRef,
        knowledge_candidate_type=KnowledgeCandidate,
        ranking_signal_type=RankingSignal,
        revision_state_type=RevisionState,
    )


def _code_ranking(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[tuple[KnowledgeCandidate, ...], RankingExecution]:
    return _code_ranking_impl(
        paths,
        plan,
        snapshot,
        cancellation_check=cancellation_check,
        owner_available_fn=_owner_available,
        planned_candidate_limit_fn=_planned_candidate_limit,
        max_candidates=MAX_KNOWLEDGE_CANDIDATES,
        max_relation_candidates=MAX_CODE_RELATION_CANDIDATES,
        code_query_cues=_CODE_QUERY_CUES,
        cancellation_bridge_type=SQLiteCancellationBridge,
        search_code_fn=search_code,
        code_search_query_type=CodeSearchQuery,
        code_version_metadata_fn=_code_version_metadata,
        code_resource_revision_fn=_code_resource_revision,
        code_relation_candidate_fn=_code_relation_candidate,
        sqlite_error_type=sqlite3.Error,
        reraise_captured_cancellation_fn=_reraise_captured_cancellation,
        file_identity_error_type=FileIdentityError,
        evidence_method_type=EvidenceMethod,
        evidence_ref_type=EvidenceRef,
        fingerprint_text_fn=fingerprint_text,
        knowledge_candidate_type=KnowledgeCandidate,
        ranking_signal_type=RankingSignal,
        ranking_execution_type=RankingExecution,
    )


def _escape_like(value: str) -> str:
    return _catalog_escape_like_impl(value)


def _catalog_identifiers(value: object) -> tuple[tuple[str, str], ...]:
    return _catalog_identifiers_impl(value, json_loads_fn=json.loads)


def _catalog_ranking(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[tuple[KnowledgeCandidate, ...], RankingExecution]:
    return _catalog_ranking_impl(
        paths,
        plan,
        snapshot,
        cancellation_check=cancellation_check,
        owner_available_fn=_owner_available,
        ranking_execution_type=RankingExecution,
        lexical_owner_formats=_LEXICAL_OWNER_FORMATS,
        escape_like_fn=_escape_like,
        planned_candidate_limit_fn=_planned_candidate_limit,
        max_candidates=MAX_KNOWLEDGE_CANDIDATES,
        cancellation_bridge_type=SQLiteCancellationBridge,
        document_catalog_database_fn=document_catalog_database,
        sqlite_cancellation_scope_fn=sqlite_cancellation_scope,
        sqlite_error_type=sqlite3.Error,
        reraise_captured_cancellation_fn=_reraise_captured_cancellation,
        cleanup_preserving_primary_fn=_cleanup_preserving_primary,
        decimal_identity_value_fn=_decimal_identity_value,
        file_identity_type=FileIdentity,
        file_identity_encoding=FileIdentityEncoding.AUTO,
        file_identity_error_type=FileIdentityError,
        value_error_type=ValueError,
        direct_resource_ref_fn=_direct_resource_ref,
        canonical_json_fn=canonical_json,
        fingerprint_text_fn=fingerprint_text,
        revision_ref_type=RevisionRef,
        revision_state_type=RevisionState,
        catalog_identifiers_fn=_catalog_identifiers,
        json_loads_fn=json.loads,
        evidence_ref_type=EvidenceRef,
        evidence_method_type=EvidenceMethod,
        knowledge_candidate_type=KnowledgeCandidate,
        ranking_signal_type=RankingSignal,
    )


def _matches_explicit_source_filters(
    candidate: KnowledgeCandidate,
    plan: KnowledgePlan,
) -> bool:
    path = (candidate.resource.current_path or "").casefold()
    extension = Path(path).suffix.casefold().lstrip(".")
    aliases = {
        candidate.resource.source_kind.casefold(),
        candidate.resource.owner.casefold(),
    }
    if extension:
        aliases.add(extension)
    if extension in _LEXICAL_OWNER_FORMATS["office"]:
        aliases.add("office")
    if extension in _LEXICAL_OWNER_FORMATS["audio"]:
        aliases.add("audio")
    if extension in _IMAGE_FORMATS or "image" in aliases or "image_ocr" in aliases:
        aliases.add("image")
    code_language = _CODE_LANGUAGE_BY_EXTENSION.get(extension)
    if code_language is not None:
        aliases.update(("code", code_language))
    if candidate.evidence.section_kind == "image_ocr":
        aliases.add("image_ocr")
    if plan.source_kinds and aliases.isdisjoint(plan.source_kinds):
        return False
    if plan.formats:
        if not any(
            value.lstrip(".") in aliases or path.endswith(f".{value.lstrip('.')}")
            for value in plan.formats
        ):
            return False
    return True


def _apply_plan_filters(
    rankings: Mapping[str, Sequence[KnowledgeCandidate]],
    plan: KnowledgePlan,
) -> dict[str, tuple[KnowledgeCandidate, ...]]:
    """Apply restrictions to every ranking; filters never become rank signals."""

    if plan.date_from is not None or plan.date_to is not None:
        return {name: () for name in rankings}
    catalog_resources = {
        candidate.resource.resource_id
        for candidate in rankings.get("catalog_metadata", ())
    }
    filtered: dict[str, tuple[KnowledgeCandidate, ...]] = {}
    for name, candidates in rankings.items():
        accepted: list[KnowledgeCandidate] = []
        for candidate in candidates:
            if not _matches_explicit_source_filters(candidate, plan):
                continue
            if (
                plan.project is not None
                and name
                not in {
                    "catalog_metadata",
                    "code_structural",
                }
                and candidate.resource.resource_id not in catalog_resources
            ):
                continue
            accepted.append(candidate)
        filtered[name] = tuple(accepted)
    return filtered


# endregion [03]


# region [04] Unified execution boundary


def _planned(plan: KnowledgePlan, channel: str) -> bool:
    return any(step.channel == channel for step in plan.steps)


def _required_lexical_ranking_names(plan: KnowledgePlan) -> frozenset[str]:
    if not any(step.channel == "lexical" and step.required for step in plan.steps):
        return frozenset()

    owners = set(_LEXICAL_OWNER_FORMATS)
    if plan.source_kinds:
        source_owners = {
            owner
            for value in plan.source_kinds
            for owner, formats in _LEXICAL_OWNER_FORMATS.items()
            if value == owner or value in formats
        }
        owners.intersection_update(source_owners)
    if plan.formats:
        format_owners = {
            owner
            for value in plan.formats
            for owner, formats in _LEXICAL_OWNER_FORMATS.items()
            if value.lstrip(".") in formats
        }
        owners.intersection_update(format_owners)
    if not owners and (plan.source_kinds or plan.formats):
        return frozenset({"lexical_scope_unsupported"})
    return frozenset(f"fts_{owner}" for owner in owners)


def _required_semantic_ranking_names(plan: KnowledgePlan) -> frozenset[str]:
    return frozenset(
        step.ranking_name
        for step in plan.steps
        if step.channel == "semantic" and step.required
    )


def _required_direct_ranking_names(plan: KnowledgePlan) -> frozenset[str]:
    return frozenset(
        step.ranking_name
        for step in plan.steps
        if step.required and step.channel not in {"exact", "lexical", "semantic"}
    )


def execute_knowledge_search(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] | None = None,
    telemetry_clock: KnowledgeTelemetryClock | None = None,
) -> KnowledgeSearchResult:
    """Execute one already-snapshotted, read-only, bounded retrieval plan."""

    if telemetry_clock is not None and not isinstance(
        telemetry_clock,
        KnowledgeTelemetryClock,
    ):
        raise ValueError("telemetry_clock must be a KnowledgeTelemetryClock")
    if telemetry_clock is not None and clock_ns is not None:
        raise ValueError("telemetry_clock and legacy clock_ns cannot both be provided")
    clock_contract = (
        telemetry_clock
        if telemetry_clock is not None
        else KnowledgeTelemetryClock.from_legacy(clock_ns)
    )
    clock = clock_contract.now_ns
    started_ns = clock()
    rankings: dict[str, tuple[KnowledgeCandidate, ...]] = {}
    discovery_signals: tuple[ResourceDiscoverySignal, ...] = ()
    reports: list[RankingExecution] = []
    phase_timings: list[KnowledgePhaseTiming] = []

    def check_cancelled() -> None:
        if cancellation_check is not None:
            cancellation_check()

    def record_report_timings(values: Sequence[RankingExecution]) -> None:
        for report in values:
            if report.owner is None or report.elapsed_ns is None:
                continue
            phase_timings.append(
                KnowledgePhaseTiming(
                    KnowledgeTimingPhase.OWNER_RANKING,
                    report.elapsed_ns,
                    service_attempt=1,
                    owner=report.owner,
                    ranking_names=(report.name,),
                    executed=report.executed,
                )
            )

    check_cancelled()
    lexical_cancellation = SQLiteCancellationBridge(cancellation_check)
    try:
        lexical, lexical_reports = _lexical_rankings(
            paths,
            plan,
            snapshot,
            cancellation_check=(
                lexical_cancellation.checkpoint
                if lexical_cancellation.enabled
                else None
            ),
            clock_ns=clock,
        )
    except ValueError as exc:
        _reraise_captured_cancellation(lexical_cancellation, exc)
        lexical = {}
        lexical_reports = [
            RankingExecution(
                f"fts_{owner}",
                "lexical",
                False,
                _owner_available(snapshot, owner),
                False,
                0,
                reason=f"query_unsupported_by_fts:{type(exc).__name__}",
                owner=owner,
            )
            for owner in _LEXICAL_OWNER_FORMATS
        ]
    rankings.update(lexical)
    reports.extend(lexical_reports)
    record_report_timings(lexical_reports)

    check_cancelled()
    if _planned(plan, "semantic"):
        semantic_cancellation = SQLiteCancellationBridge(cancellation_check)
        try:
            semantic_result = _semantic_rankings(
                paths,
                plan,
                snapshot,
                (
                    semantic_cancellation.checkpoint
                    if semantic_cancellation.enabled
                    else None
                ),
                clock_ns=clock,
            )
            if len(semantic_result) == 2:  # compatibility for injected v2 seams
                semantic, semantic_reports = semantic_result
            else:
                semantic, discovery_signals, semantic_reports = semantic_result
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            _reraise_captured_cancellation(semantic_cancellation, exc)
            semantic = {}
            discovery_signals = ()
            failed_steps = (
                *_planned_steps(plan, "semantic"),
                *_planned_steps(plan, "semantic_discovery"),
            )
            semantic_reports = [
                RankingExecution(
                    step.ranking_name,
                    step.channel,
                    True,
                    False,
                    False,
                    0,
                    reason=f"owner_read_failed:{type(exc).__name__}",
                    owner="semantic",
                )
                for step in failed_steps
            ]
        rankings.update(semantic)
        reports.extend(semantic_reports)
        record_report_timings(semantic_reports)

    exact_omitted = 0
    exact_truncated = False
    check_cancelled()
    if _planned(plan, "exact"):
        exact_result = _exact_rankings(
            paths,
            plan,
            snapshot,
            cancellation_check=cancellation_check,
            clock_ns=clock,
        )
        if len(exact_result) == 4:
            (
                exact_rankings,
                exact_reports,
                exact_omitted,
                exact_truncated,
            ) = exact_result
            exact_owner_timings: tuple[ExactOwnerTiming, ...] = ()
        else:
            (
                exact_rankings,
                exact_reports,
                exact_omitted,
                exact_truncated,
                exact_owner_timings,
            ) = exact_result
        rankings.update(exact_rankings)
        reports.extend(exact_reports)
        phase_timings.extend(
            KnowledgePhaseTiming(
                KnowledgeTimingPhase.OWNER_RANKING,
                timing.duration_ns,
                service_attempt=1,
                owner=timing.owner,
                ranking_names=timing.ranking_names,
                executed=timing.executed,
            )
            for timing in exact_owner_timings
        )

    check_cancelled()
    if _planned(plan, "structural_code"):
        ranking_started_ns = clock()
        code, code_report = _code_ranking(
            paths,
            plan,
            snapshot,
            cancellation_check=cancellation_check,
        )
        code_report = replace(
            code_report,
            owner="code",
            elapsed_ns=_duration_ns(clock, ranking_started_ns),
        )
        if code:
            rankings[code_report.name] = code
        reports.append(code_report)
        record_report_timings((code_report,))
    check_cancelled()
    if _planned(plan, "catalog"):
        ranking_started_ns = clock()
        catalog, catalog_report = _catalog_ranking(
            paths,
            plan,
            snapshot,
            cancellation_check=cancellation_check,
        )
        catalog_report = replace(
            catalog_report,
            owner="catalog",
            elapsed_ns=_duration_ns(clock, ranking_started_ns),
        )
        if catalog:
            rankings[catalog_report.name] = catalog
        reports.append(catalog_report)
        record_report_timings((catalog_report,))
    check_cancelled()
    if _planned(plan, "relational"):
        reports.append(
            RankingExecution(
                "verified_relations",
                "relational",
                False,
                False,
                True,
                0,
                reason="cross-owner graph is not available in phase 1",
            )
        )
    check_cancelled()
    if _planned(plan, "temporal"):
        reports.append(
            RankingExecution(
                "published_history",
                "temporal",
                False,
                False,
                True,
                0,
                reason="owner history is not uniformly available",
            )
        )

    check_cancelled()
    counts_before_filters = {name: len(values) for name, values in rankings.items()}
    rankings = _apply_plan_filters(rankings, plan)
    postfiltered_names = {
        name
        for name, count in counts_before_filters.items()
        if len(rankings.get(name, ())) < count
    }
    if postfiltered_names:
        reports = [
            replace(
                report,
                complete=False,
                reason=report.reason or "postfilter_candidate_window",
            )
            if report.name in postfiltered_names
            else report
            for report in reports
        ]
    # Catalog candidates are a membership/filter surface. Exact relevance is
    # supplied by the typed exact adapter and metadata must never become an
    # independent query-relevance signal.
    rankings.pop("catalog_metadata", None)
    check_cancelled()
    ranking_started_ns = clock()
    rankings, duplicate_report = _apply_inventory_dispositions(
        paths,
        snapshot,
        rankings,
        cancellation_check=cancellation_check,
    )
    duplicate_report = replace(
        duplicate_report,
        owner="inventory",
        elapsed_ns=_duration_ns(clock, ranking_started_ns),
    )
    reports.append(duplicate_report)
    record_report_timings((duplicate_report,))
    check_cancelled()
    fusion_started_ns = clock()
    if discovery_signals:
        hits, omitted = fuse_evidence_rankings(
            rankings,
            discovery_signals=discovery_signals,
            limit=plan.limit,
            max_per_resource=plan.max_per_resource,
            min_section_distance=plan.min_section_distance,
            include_history=plan.include_history,
            cancellation_check=cancellation_check,
        )
    else:
        hits, omitted = fuse_evidence_rankings(
            rankings,
            limit=plan.limit,
            max_per_resource=plan.max_per_resource,
            min_section_distance=plan.min_section_distance,
            include_history=plan.include_history,
            cancellation_check=cancellation_check,
        )
    phase_timings.append(
        KnowledgePhaseTiming(
            KnowledgeTimingPhase.FUSION,
            _duration_ns(clock, fusion_started_ns),
            service_attempt=1,
        )
    )
    omitted += exact_omitted
    check_cancelled()
    required_channels = {step.channel for step in plan.steps if step.required}
    required_lexical = _required_lexical_ranking_names(plan)
    required_semantic = _required_semantic_ranking_names(plan)
    required_direct = _required_direct_ranking_names(plan)
    unavailable_required: set[str] = set()
    incomplete_required: set[str] = set()
    reports_by_name = {report.name: report for report in reports}
    for name in required_lexical:
        report = reports_by_name.get(name)
        if report is None or not report.executed or not report.available:
            unavailable_required.add(name)
        elif not report.complete:
            incomplete_required.add(name)
    for name in required_semantic:
        report = reports_by_name.get(name)
        if report is None or not report.executed or not report.available:
            unavailable_required.add(name)
        elif not report.complete:
            incomplete_required.add(name)
    for name in required_direct:
        report = reports_by_name.get(name)
        if report is None or not report.executed or not report.available:
            unavailable_required.add(name)
        elif not report.complete:
            incomplete_required.add(name)
    if not duplicate_report.complete:
        incomplete_required.add(duplicate_report.name)
    if "exact" in required_channels:
        required_exact_reports = tuple(
            report for report in reports if report.channel == "exact"
        )
        if required_exact_reports:
            for report in required_exact_reports:
                if not report.executed or not report.available:
                    unavailable_required.add(report.name)
                elif not report.complete:
                    incomplete_required.add(report.name)
        else:
            unavailable_required.add("exact")
    blocking_ranking_names = (unavailable_required | incomplete_required).intersection(
        required_lexical | required_semantic | required_direct
    )
    blocking_owners = tuple(
        sorted(
            {
                report.owner
                for report in reports
                if report.name in blocking_ranking_names and report.owner is not None
            }
        )
    )
    upstream_cutoffs = tuple(
        report
        for report in reports
        if report.reason is not None
        and "limit_reached" in report.reason
        and report.channel != "exact"
        and (report.name in rankings or report.channel in required_channels)
    )
    omitted += len(upstream_cutoffs)
    complete = (
        snapshot.consistency is SnapshotConsistency.STABLE
        and not unavailable_required
        and not incomplete_required
        and omitted == 0
        and not exact_truncated
    )
    partial_warnings = {
        f"ranking_partial:{report.name}:{(report.reason or 'incomplete').replace(' ', '_')}"
        for report in reports
        if report.executed and not report.complete
    }
    warnings = tuple(
        sorted(
            {
                *(f"ranking_unavailable:{name}" for name in unavailable_required),
                *partial_warnings,
                *(
                    ("no_lexical_owner_available",)
                    if required_lexical
                    and required_lexical.issubset(unavailable_required)
                    else ()
                ),
            }
        )
    )
    broker_duration_ns = _duration_ns(clock, started_ns)
    phase_timings.append(
        KnowledgePhaseTiming(
            KnowledgeTimingPhase.BROKER,
            broker_duration_ns,
            service_attempt=1,
        )
    )
    elapsed = broker_duration_ns // 1_000_000
    rows_scanned = sum(report.rows_scanned for report in reports)
    vectors_scanned = sum(report.vectors_scanned for report in reports)
    return KnowledgeSearchResult(
        plan=plan,
        snapshot=snapshot,
        hits=hits,
        rankings=tuple(reports),
        complete=complete,
        truncated=omitted > 0 or exact_truncated,
        omitted_candidates=omitted,
        rows_scanned=rows_scanned,
        vectors_scanned=vectors_scanned,
        elapsed_milliseconds=elapsed,
        warnings=warnings,
        telemetry=KnowledgeQueryTelemetry(
            KnowledgeTelemetryOperation.SEARCH,
            broker_duration_ns,
            tuple(phase_timings),
            clock_signature=clock_contract.signature,
        ),
        blocking_owners=blocking_owners,
    )


# endregion [04]


__all__ = (
    "KnowledgeCandidate",
    "KnowledgeSearchResult",
    "RankingExecution",
    "execute_knowledge_search",
    "fuse_evidence_rankings",
)
