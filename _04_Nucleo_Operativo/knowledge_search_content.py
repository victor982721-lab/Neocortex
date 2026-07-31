"""Content retrieval and evidence materialization for Knowledge Search.

Providers and facade seams are injected on every call. This module contains no
dependency back to knowledge_search and captures no mutable facade defaults.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from .file_identity import FileIdentity, FileIdentityEncoding
from .knowledge_contracts import (
    EvidenceMethod,
    EvidenceRef,
    KnowledgeSnapshot,
    PhysicalIdentityRef,
    RankingSignal,
    ResourceRef,
    RevisionRef,
    RevisionState,
)
from .knowledge_exact import ExactLookupResult, ExactOwnerTiming
from .knowledge_planner import KnowledgePlan, RetrievalMode, RetrievalStep
from .knowledge_search_contracts import KnowledgeCandidate, RankingExecution
from .knowledge_snapshot import KnowledgeStatePaths
from .semantic_lexical import (
    LexicalAvailability,
    LexicalRanking,
    LexicalStatePaths,
)
from .semantic_models import ContentFingerprint, ResolvedSearchHit
from .sqlite_cancellation import SQLiteCancellationBridge

if TYPE_CHECKING:
    from .semantic_service_contracts import (
        SemanticRanking,
        SemanticSearchResult,
    )


OwnerAvailable = Callable[[KnowledgeSnapshot, str], bool]
PlannedSteps = Callable[[KnowledgePlan, str], tuple[RetrievalStep, ...]]
PlannedCandidateLimit = Callable[[KnowledgePlan, str], int]
MaterializeCandidate = Callable[..., KnowledgeCandidate]
DurationNanoseconds = Callable[[Callable[[], int], int], int]
ReraiseCapturedCancellation = Callable[
    [SQLiteCancellationBridge, BaseException],
    None,
]
CanonicalJson = Callable[[Mapping[str, object] | None], str]
FingerprintText = Callable[[str], ContentFingerprint]
IntProvenance = Callable[[Mapping[str, object], str], int | None]
ResolvedPhysicalIdentity = Callable[[ResolvedSearchHit], str | None]
RevisionIdentity = Callable[
    [ResolvedSearchHit, str],
    tuple[str, str, RevisionState, tuple[str, ...]],
]
LexicalSearch = Callable[..., tuple[LexicalRanking, ...]]
SemanticSearch = Callable[..., "SemanticSearchResult"]
ExactLookup = Callable[..., ExactLookupResult | None]


def revision_identity(
    resolved: ResolvedSearchHit,
    producer: str,
    *,
    canonical_json_fn: CanonicalJson,
    fingerprint_text_fn: FingerprintText,
) -> tuple[str, str, RevisionState, tuple[str, ...]]:
    revision_payload: dict[str, object] = {
        "source_kind": resolved.source_kind,
        "source_identity": resolved.source_identity,
        "source_revision": dict(resolved.source_revision),
    }
    if not resolved.source_revision and resolved.published_revision_id is not None:
        revision_payload["published_revision_id"] = resolved.published_revision_id
    fingerprint = fingerprint_text_fn(canonical_json_fn(revision_payload))
    revision_id = f"revision:{resolved.source_kind}:{fingerprint.xxh3_128}"
    signature = resolved.source_revision.get("processing_signature")
    if not isinstance(signature, str) or not signature.strip():
        signature = producer
    if resolved.source_revision_is_current is False:
        warnings = ["stale_revision"]
        if resolved.current_revision_id is None:
            warnings.append("current_revision_unavailable")
        return revision_id, signature, RevisionState.HISTORICAL, tuple(warnings)
    status = (resolved.source_status or "").strip().casefold()
    if status in {"historical", "history"}:
        return revision_id, signature, RevisionState.HISTORICAL, ()
    if status in {"stale", "superseded"}:
        return revision_id, signature, RevisionState.SUPERSEDED, ("stale_revision",)
    if resolved.source_revision:
        if status == "partial" or resolved.source_revision.get("is_partial") is True:
            return (
                revision_id,
                signature,
                RevisionState.PARTIAL,
                ("owner_partial_revision",),
            )
        return revision_id, signature, RevisionState.CURRENT, ()
    return revision_id, signature, RevisionState.AMBIGUOUS, ("revision_unavailable",)


def int_provenance(
    provenance: Mapping[str, object],
    name: str,
) -> int | None:
    value = provenance.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def resolved_physical_identity(
    resolved: ResolvedSearchHit,
    *,
    int_provenance_fn: IntProvenance,
    file_identity_type: type[FileIdentity],
    file_identity_encoding: FileIdentityEncoding,
    identity_errors: tuple[type[Exception], ...],
) -> str | None:
    birthtime_ns = int_provenance_fn(resolved.source_revision, "birthtime_ns")
    if birthtime_ns is None or birthtime_ns < 0:
        return None
    try:
        if resolved.source_kind == "code":
            volume, file_id = resolved.source_identity.split(":", 1)
            identity = file_identity_type(int(volume, 16), int(file_id, 16))
        else:
            identity = file_identity_type.decode(
                resolved.source_identity,
                encoding=file_identity_encoding,
            )
    except identity_errors:
        return None
    return f"{identity.volume_id}:{identity.file_id}:{birthtime_ns}"


def direct_resource_ref(
    *,
    source_kind: str,
    owner: str,
    source_identity: str,
    identity: FileIdentity,
    birthtime_ns: object,
    path: str | None,
    resource_ref_type: type[ResourceRef],
    physical_identity_ref_type: type[PhysicalIdentityRef],
) -> tuple[ResourceRef, tuple[str, ...]]:
    birthtime = (
        birthtime_ns
        if isinstance(birthtime_ns, int) and not isinstance(birthtime_ns, bool)
        else None
    )
    if birthtime is not None and birthtime >= 0:
        physical = f"{identity.volume_id}:{identity.file_id}:{birthtime}"
        return (
            resource_ref_type(
                f"resource:file:{physical}",
                source_kind,
                owner,
                physical_identity_ref_type(
                    "windows_file_id_birthtime",
                    physical,
                    1,
                ),
                path,
                None,
            ),
            (),
        )
    return (
        resource_ref_type(
            f"resource:{owner}:{source_identity}",
            source_kind,
            owner,
            physical_identity_ref_type("owner_file_key", source_identity, 1),
            path,
            None,
        ),
        ("physical_identity_unresolved",),
    )


def _candidate_owner(
    source_kind: str,
    lexical_owner_formats: Mapping[str, frozenset[str]],
) -> str:
    if source_kind in lexical_owner_formats["office"]:
        return "office"
    if source_kind in {"image", "image_ocr"}:
        return "image"
    return source_kind


def _resource_from_resolved(
    resolved: ResolvedSearchHit,
    *,
    resolved_physical_identity_fn: ResolvedPhysicalIdentity,
    int_provenance_fn: IntProvenance,
    lexical_owner_formats: Mapping[str, frozenset[str]],
    resource_ref_type: type[ResourceRef],
    physical_identity_ref_type: type[PhysicalIdentityRef],
) -> tuple[ResourceRef, tuple[str, ...]]:
    canonical_physical = resolved_physical_identity_fn(resolved)
    birthtime_ns = int_provenance_fn(resolved.source_revision, "birthtime_ns")
    if canonical_physical is not None:
        physical_value = canonical_physical
        physical_scheme = "windows_file_id_birthtime"
        resource_id = f"resource:file:{canonical_physical}"
        identity_warnings: tuple[str, ...] = ()
    elif birthtime_ns is not None and birthtime_ns >= 0:
        physical_value = f"{resolved.source_identity}:birthtime:{birthtime_ns}"
        physical_scheme = "owner_file_key_birthtime"
        resource_id = (
            f"resource:{resolved.source_kind}:{resolved.source_identity}:"
            f"birthtime:{birthtime_ns}"
        )
        identity_warnings = ("physical_identity_unresolved",)
    else:
        physical_value = resolved.source_identity
        physical_scheme = "owner_file_key"
        resource_id = f"resource:{resolved.source_kind}:{resolved.source_identity}"
        identity_warnings = ("physical_identity_unresolved",)
    resource = resource_ref_type(
        resource_id=resource_id,
        source_kind=resolved.source_kind,
        owner=_candidate_owner(resolved.source_kind, lexical_owner_formats),
        physical_identity=physical_identity_ref_type(
            physical_scheme,
            physical_value,
            1,
        ),
        current_path=resolved.path,
        disposition=None,
    )
    return resource, identity_warnings


def _generation_from_resolved(
    resolved: ResolvedSearchHit,
    *,
    int_provenance_fn: IntProvenance,
) -> int | None:
    generation = resolved.hit.generation_id if resolved.hit.generation_id > 0 else None
    if generation is not None:
        return generation
    observed_generation = int_provenance_fn(
        resolved.source_revision,
        "last_seen_run_id",
    )
    if observed_generation is not None and observed_generation >= 0:
        return observed_generation
    return None


def _evidence_from_resolved(
    resolved: ResolvedSearchHit,
    *,
    resource_id: str,
    revision_id: str,
    generation: int | None,
    producer: str,
    int_provenance_fn: IntProvenance,
    evidence_ref_type: type[EvidenceRef],
    extracted_method: EvidenceMethod,
) -> EvidenceRef:
    provenance = resolved.section_provenance
    page: int | None = None
    if (
        resolved.source_kind == "pdf"
        and resolved.section_id is not None
        and resolved.section_id.isascii()
        and resolved.section_id.isdecimal()
    ):
        page = int(resolved.section_id)
    section_kind = resolved.section_kind
    section_id = resolved.section_id
    if resolved.source_kind == "pdf" and page is not None:
        section_kind = "pdf_page"
        section_id = str(page)
    start_line = int_provenance_fn(provenance, "start_line")
    end_line = int_provenance_fn(provenance, "end_line")
    start_ms = int_provenance_fn(provenance, "start_ms")
    end_ms = int_provenance_fn(provenance, "end_ms")
    symbol = provenance.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        symbol = None
    return evidence_ref_type(
        evidence_id=f"evidence:{resolved.source_kind}:{resolved.hit.entity_id}",
        resource_id=resource_id,
        revision_id=revision_id,
        method=extracted_method,
        page=page,
        start_line=start_line,
        end_line=end_line,
        start_ms=start_ms,
        end_ms=end_ms,
        start_char=resolved.start_char,
        end_char=resolved.end_char,
        symbol=symbol,
        section_kind=section_kind,
        section_id=section_id,
        snippet=resolved.snippet,
        extractor=producer,
        extractor_version=str(provenance.get("adapter", "v1")),
        generation=generation,
        identifiers=(
            ("source_identity", resolved.source_identity),
            ("retrieval_entity_id", resolved.hit.entity_id),
        ),
    )


def candidate_from_resolved(
    resolved: ResolvedSearchHit,
    *,
    ranking_name: str,
    source_rank: int,
    producer: str,
    resolved_physical_identity_fn: ResolvedPhysicalIdentity,
    int_provenance_fn: IntProvenance,
    revision_identity_fn: RevisionIdentity,
    lexical_owner_formats: Mapping[str, frozenset[str]],
    resource_ref_type: type[ResourceRef],
    physical_identity_ref_type: type[PhysicalIdentityRef],
    revision_ref_type: type[RevisionRef],
    evidence_ref_type: type[EvidenceRef],
    extracted_method: EvidenceMethod,
    ranking_signal_type: type[RankingSignal],
    candidate_type: type[KnowledgeCandidate],
) -> KnowledgeCandidate:
    resource, identity_warnings = _resource_from_resolved(
        resolved,
        resolved_physical_identity_fn=resolved_physical_identity_fn,
        int_provenance_fn=int_provenance_fn,
        lexical_owner_formats=lexical_owner_formats,
        resource_ref_type=resource_ref_type,
        physical_identity_ref_type=physical_identity_ref_type,
    )
    revision_id, processing_signature, state, revision_warnings = revision_identity_fn(
        resolved, producer
    )
    generation = _generation_from_resolved(
        resolved,
        int_provenance_fn=int_provenance_fn,
    )
    revision = revision_ref_type(
        resource_id=resource.resource_id,
        revision_id=revision_id,
        producer=producer,
        processing_signature=processing_signature,
        generation=generation,
        state=state,
    )
    evidence = _evidence_from_resolved(
        resolved,
        resource_id=resource.resource_id,
        revision_id=revision_id,
        generation=generation,
        producer=producer,
        int_provenance_fn=int_provenance_fn,
        evidence_ref_type=evidence_ref_type,
        extracted_method=extracted_method,
    )
    score_kind = "bm25" if ranking_name.startswith("fts_") else "cosine"
    return candidate_type(
        resource=resource,
        revision=revision,
        evidence=evidence,
        signal=ranking_signal_type(
            source=ranking_name,
            score_kind=score_kind,
            raw_score=resolved.hit.score,
            source_rank=source_rank,
            model_signature=resolved.hit.indexed_model_signature,
            generation=generation,
            query_model_signature=resolved.hit.query_model_signature,
        ),
        reason=f"{ranking_name} returned this concrete evidence",
        warnings=tuple(sorted({*revision_warnings, *identity_warnings})),
    )


def _lexical_state_paths(
    paths: KnowledgeStatePaths,
    snapshot: KnowledgeSnapshot,
    *,
    owner_available: OwnerAvailable,
    state_paths_type: type[LexicalStatePaths],
) -> LexicalStatePaths:
    return state_paths_type(
        pdf=paths.pdf if owner_available(snapshot, "pdf") else None,
        docx=paths.docx if owner_available(snapshot, "docx") else None,
        office=paths.office if owner_available(snapshot, "office") else None,
        audio=paths.audio if owner_available(snapshot, "audio") else None,
    )


def _lexical_candidate_window_reached(
    *,
    hit_count: int,
    target_limit: int,
    max_candidates: int,
) -> bool:
    return hit_count > target_limit or (
        target_limit == max_candidates and hit_count >= target_limit
    )


def _lexical_reason(
    candidates: tuple[KnowledgeCandidate, ...],
    *,
    candidate_window_reached: bool,
    unavailable_reason: str | None,
    revision_partial: RevisionState,
) -> str | None:
    if any(candidate.revision.state is revision_partial for candidate in candidates):
        return "owner_partial_documents"
    if candidate_window_reached:
        return "lexical_candidate_limit_reached"
    return unavailable_reason


def lexical_rankings(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None,
    clock_ns: Callable[[], int] | None,
    owner_available: OwnerAvailable,
    planned_candidate_limit: PlannedCandidateLimit,
    materialize_candidate: MaterializeCandidate,
    lexical_search: LexicalSearch,
    state_paths_type: type[LexicalStatePaths],
    lexical_available: LexicalAvailability,
    revision_current: RevisionState,
    revision_partial: RevisionState,
    max_candidates: int,
) -> tuple[dict[str, tuple[KnowledgeCandidate, ...]], list[RankingExecution]]:
    state_paths = _lexical_state_paths(
        paths,
        snapshot,
        owner_available=owner_available,
        state_paths_type=state_paths_type,
    )
    target_limit = planned_candidate_limit(plan, "lexical")
    requested_limit = min(max_candidates, target_limit + 1)
    results = lexical_search(
        state_paths,
        plan.normalized_query,
        limit=requested_limit,
        cancellation_check=cancellation_check,
        clock_ns=clock_ns,
    )
    rankings: dict[str, tuple[KnowledgeCandidate, ...]] = {}
    reports: list[RankingExecution] = []
    for result in results:
        name = result.ranking_name
        available = result.availability is lexical_available
        candidate_window_reached = _lexical_candidate_window_reached(
            hit_count=len(result.hits),
            target_limit=target_limit,
            max_candidates=max_candidates,
        )
        candidates = tuple(
            materialize_candidate(
                resolved,
                ranking_name=name,
                source_rank=index,
                producer=f"{result.source_kind}-fts-v1",
            )
            for index, resolved in enumerate(result.hits[:target_limit], 1)
        )
        if candidates:
            rankings[name] = candidates
        reports.append(
            RankingExecution(
                name=name,
                channel="lexical",
                executed=available,
                available=available,
                complete=not candidate_window_reached
                and all(
                    candidate.revision.state is revision_current
                    for candidate in candidates
                ),
                returned=len(candidates),
                rows_scanned=len(result.hits),
                reason=_lexical_reason(
                    candidates,
                    candidate_window_reached=candidate_window_reached,
                    unavailable_reason=result.unavailable_reason,
                    revision_partial=revision_partial,
                ),
                owner=result.source_kind,
                elapsed_ns=result.elapsed_ns,
            )
        )
    return rankings, reports


def _validated_semantic_steps(
    plan: KnowledgePlan,
    *,
    planned_steps: PlannedSteps,
) -> tuple[RetrievalStep, ...]:
    semantic_steps = planned_steps(plan, "semantic")
    if not semantic_steps:
        return ()
    supported_names = {"semantic_text", "semantic_image"}
    if any(step.ranking_name not in supported_names for step in semantic_steps):
        raise ValueError("Knowledge plan contains an unsupported semantic ranking")
    if len({step.ranking_name for step in semantic_steps}) != len(semantic_steps):
        raise ValueError("Knowledge plan contains duplicate semantic rankings")
    return semantic_steps


def _semantic_unavailable_reports(
    semantic_steps: tuple[RetrievalStep, ...],
) -> list[RankingExecution]:
    return [
        RankingExecution(
            step.ranking_name,
            "semantic",
            False,
            False,
            False,
            0,
            reason="semantic_owner_unavailable",
            owner="semantic",
            elapsed_ns=0,
        )
        for step in semantic_steps
    ]


def _semantic_vector_budgets(
    max_vectors: int,
    step_count: int,
) -> tuple[int, ...]:
    base_budget, extra_budgets = divmod(max_vectors, step_count)
    return tuple(
        base_budget + (1 if index < extra_budgets else 0) for index in range(step_count)
    )


def _semantic_no_budget_report(expected_name: str) -> RankingExecution:
    return RankingExecution(
        expected_name,
        "semantic",
        False,
        True,
        False,
        0,
        reason="semantic_vector_budget_unavailable",
        owner="semantic",
        elapsed_ns=0,
    )


def _semantic_failed_report(
    expected_name: str,
    exc: BaseException,
    *,
    clock: Callable[[], int],
    started_ns: int,
    duration_ns: DurationNanoseconds,
) -> RankingExecution:
    return RankingExecution(
        expected_name,
        "semantic",
        True,
        False,
        False,
        0,
        reason=f"owner_read_failed:{type(exc).__name__}",
        owner="semantic",
        elapsed_ns=duration_ns(clock, started_ns),
    )


def _semantic_missing_report(
    expected_name: str,
    *,
    ambiguous: bool,
    clock: Callable[[], int],
    started_ns: int,
    duration_ns: DurationNanoseconds,
) -> RankingExecution:
    return RankingExecution(
        expected_name,
        "semantic",
        True,
        False,
        False,
        0,
        reason=(
            "semantic_ranking_ambiguous" if ambiguous else "semantic_ranking_missing"
        ),
        owner="semantic",
        elapsed_ns=duration_ns(clock, started_ns),
    )


def _semantic_result_report(
    expected_name: str,
    ranking: SemanticRanking,
    candidates: tuple[KnowledgeCandidate, ...],
    *,
    candidate_limit: int,
    clock: Callable[[], int],
    started_ns: int,
    duration_ns: DurationNanoseconds,
) -> RankingExecution:
    vector_cutoff = ranking.cutoff_reason in {
        "max_vectors",
        "max_vectors_reached",
    }
    candidate_cutoff = (
        ranking.cutoff_reason
        in {
            "candidate_limit",
            "candidate_limit_reached",
            "top_k",
            "top_k_reached",
        }
        or len(ranking.resolved) > candidate_limit
    )
    return RankingExecution(
        name=expected_name,
        channel="semantic",
        executed=True,
        available=ranking.available,
        complete=(
            ranking.available
            and ranking.complete
            and not vector_cutoff
            and not candidate_cutoff
        ),
        returned=len(candidates),
        vectors_scanned=ranking.scanned,
        reason=(
            ranking.unavailable_reason
            or (
                "semantic_vector_limit_reached"
                if vector_cutoff
                else ("semantic_candidate_limit_reached" if candidate_cutoff else None)
            )
        ),
        owner="semantic",
        elapsed_ns=duration_ns(clock, started_ns),
    )


def semantic_rankings(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    cancellation_check: Callable[[], None] | None,
    clock_ns: Callable[[], int] | None,
    *,
    planned_steps: PlannedSteps,
    owner_available: OwnerAvailable,
    duration_ns: DurationNanoseconds,
    materialize_candidate: MaterializeCandidate,
    default_clock: Callable[[], int],
    semantic_search: SemanticSearch,
    cancellation_bridge_type: type[SQLiteCancellationBridge],
    reraise_captured_cancellation: ReraiseCapturedCancellation,
    sqlite_error_type: type[Exception],
    evidence_mode: RetrievalMode,
) -> tuple[dict[str, tuple[KnowledgeCandidate, ...]], list[RankingExecution]]:
    clock = clock_ns or default_clock
    semantic_steps = _validated_semantic_steps(plan, planned_steps=planned_steps)
    if not semantic_steps:
        return {}, []
    if not owner_available(snapshot, "semantic"):
        return {}, _semantic_unavailable_reports(semantic_steps)

    vector_budgets = _semantic_vector_budgets(plan.max_vectors, len(semantic_steps))
    rankings: dict[str, tuple[KnowledgeCandidate, ...]] = {}
    reports: list[RankingExecution] = []
    cancellation = cancellation_bridge_type(cancellation_check)
    for step, vector_budget in zip(semantic_steps, vector_budgets, strict=True):
        expected_name = step.ranking_name
        if vector_budget < 1:
            reports.append(_semantic_no_budget_report(expected_name))
            continue
        started_ns = clock()
        try:
            result = semantic_search(
                paths.semantic.parent,
                plan.normalized_query,
                semantic_database=paths.semantic,
                candidate_limit=step.candidate_limit,
                limit=step.candidate_limit,
                max_vectors=vector_budget,
                include_text=expected_name == "semantic_text",
                include_images=expected_name == "semantic_image",
                include_lexical=False,
                local_files_only=True,
                evidence_mode=plan.retrieval_mode is evidence_mode,
                cancellation_check=(
                    cancellation.checkpoint if cancellation.enabled else None
                ),
            )
        except (OSError, RuntimeError, sqlite_error_type, ValueError) as exc:
            reraise_captured_cancellation(cancellation, exc)
            reports.append(
                _semantic_failed_report(
                    expected_name,
                    exc,
                    clock=clock,
                    started_ns=started_ns,
                    duration_ns=duration_ns,
                )
            )
            continue
        matching_rankings = tuple(
            ranking for ranking in result.rankings if ranking.name == expected_name
        )
        if len(matching_rankings) != 1:
            reports.append(
                _semantic_missing_report(
                    expected_name,
                    ambiguous=bool(matching_rankings),
                    clock=clock,
                    started_ns=started_ns,
                    duration_ns=duration_ns,
                )
            )
            continue
        ranking = matching_rankings[0]
        resolved = ranking.resolved[: step.candidate_limit]
        candidates = tuple(
            materialize_candidate(
                value,
                ranking_name=expected_name,
                source_rank=index,
                producer="semantic-v6",
            )
            for index, value in enumerate(resolved, 1)
        )
        if candidates:
            rankings[expected_name] = candidates
        reports.append(
            _semantic_result_report(
                expected_name,
                ranking,
                candidates,
                candidate_limit=step.candidate_limit,
                clock=clock,
                started_ns=started_ns,
                duration_ns=duration_ns,
            )
        )
    return rankings, reports


def exact_rankings(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    cancellation_check: Callable[[], None] | None,
    clock_ns: Callable[[], int] | None,
    lookup_exact: ExactLookup,
    planned_candidate_limit: PlannedCandidateLimit,
) -> tuple[
    dict[str, tuple[KnowledgeCandidate, ...]],
    list[RankingExecution],
    int,
    bool,
    tuple[ExactOwnerTiming, ...],
]:
    result = lookup_exact(
        paths,
        plan,
        snapshot,
        candidate_limit=planned_candidate_limit(plan, "exact"),
        cancellation_check=cancellation_check,
        clock_ns=clock_ns,
    )
    if result is None:
        return {}, [], 0, False, ()

    grouped: dict[str, list[KnowledgeCandidate]] = {}
    for match in result.matches:
        grouped.setdefault(match.ranking_name, []).append(
            KnowledgeCandidate(
                match.resource,
                match.revision,
                match.evidence,
                RankingSignal(
                    match.ranking_name,
                    "exact",
                    1.0,
                    match.source_rank,
                    model_signature=match.model_signature,
                    generation=match.generation,
                ),
                match.reason,
                confidence=match.confidence,
                warnings=tuple(sorted({*match.warnings, *result.warnings})),
            )
        )
    visible_counts: dict[str, int] = {}
    for match in result.matches:
        visible_counts[match.ranking_name] = (
            visible_counts.get(match.ranking_name, 0) + 1
        )
    reports = [
        RankingExecution(
            report.name,
            "exact",
            report.executed,
            report.available,
            report.complete,
            visible_counts.get(report.name, 0),
            rows_scanned=report.rows_observed,
            reason=report.reason,
            owner=report.owner,
        )
        for report in result.reports
    ]
    report_rows = sum(report.rows_observed for report in result.reports)
    reports.append(
        RankingExecution(
            "exact_coverage",
            "exact",
            any(report.executed for report in result.reports),
            any(report.available for report in result.reports),
            result.complete,
            len(result.matches),
            rows_scanned=max(0, result.rows_observed - report_rows),
            reason=(
                None
                if result.complete
                else (
                    "exact_global_result_limit_reached"
                    if result.omitted_matches
                    else "exact_lookup_incomplete"
                )
            ),
        )
    )
    return (
        {name: tuple(matches) for name, matches in grouped.items()},
        reports,
        result.omitted_matches,
        result.truncated,
        result.owner_timings,
    )


__all__: tuple[str, ...] = ()
