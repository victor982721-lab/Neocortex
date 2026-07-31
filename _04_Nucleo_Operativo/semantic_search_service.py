"""Separate-space semantic retrieval and deterministic rank-only fusion."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .semantic_backends import reciprocal_rank_fusion
from .semantic_config import clip_image_model, clip_text_model, multilingual_text_model
from .semantic_generation_worker import batches
from .semantic_lexical import MAX_QUERY_CHARS, LexicalRanking, LexicalStatePaths
from .semantic_models import (
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRequest,
    EmbeddingRole,
    ExactSearchQuery,
    ResolvedSearchHit,
    fingerprint_text,
)
from .semantic_ontology import expand_domain_query
from .semantic_preparation import (
    BackendFactory,
    SemanticModelUnavailableError,
    model_cache,
)
from .semantic_service_contracts import (
    MAX_LEXICAL_CANDIDATE_HITS,
    MAX_SEMANTIC_CANDIDATE_HITS,
    SEARCH_RESOLUTION_BATCH_SIZE,
    SEMANTIC_DATABASE_NAME,
    FusedResolvedHit,
    SemanticRanking,
    SemanticSearchResult,
)
from .semantic_state import (
    has_active_embeddings,
    load_embedding_model,
    resolve_search_hits,
    search_exact_evidence_page,
    search_exact_page,
)


class LexicalSearch(Protocol):
    def __call__(
        self,
        paths: LexicalStatePaths,
        query: str,
        *,
        limit: int,
        cancellation_check: Callable[[], None] | None = None,
    ) -> tuple[LexicalRanking, ...]: ...


# region [01] Query vectors and exact rankings


def query_vector(
    model: EmbeddingModelSpec,
    query: str,
    *,
    cache_dir: Path,
    local_files_only: bool,
    threads: int | None,
    backend_factory: BackendFactory,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[float, ...]:
    if cancellation_check is not None:
        cancellation_check()
    embedding_backend = backend_factory(
        model,
        cache_dir=cache_dir,
        local_files_only=local_files_only,
        threads=threads,
    )
    if cancellation_check is not None:
        cancellation_check()
    request = EmbeddingRequest(
        request_id="semantic-query",
        role=EmbeddingRole.QUERY,
        fingerprint=fingerprint_text(query),
        text=query,
    )
    vector = tuple(embedding_backend.embed((request,))[0].vector)
    if cancellation_check is not None:
        cancellation_check()
    return vector


def semantic_ranking(
    database: Path,
    *,
    name: str,
    query_model: EmbeddingModelSpec,
    target_modality: EmbeddingModality,
    vector: Sequence[float],
    indexed_model_signatures: tuple[str, ...],
    limit: int,
    max_vectors: int,
    evidence_mode: bool = False,
    cancellation_check: Callable[[], None] | None = None,
) -> SemanticRanking:
    search_page = search_exact_evidence_page if evidence_mode else search_exact_page
    page = search_page(
        database,
        ExactSearchQuery(
            query_model_signature=query_model.model_signature,
            vector_space=query_model.vector_space,
            dimensions=query_model.dimensions,
            vector=vector,
            target_modality=target_modality,
            indexed_model_signatures=indexed_model_signatures,
        ),
        limit=limit,
        max_vectors=max_vectors,
        cancellation_check=cancellation_check,
    )
    resolved_values: list[ResolvedSearchHit] = []
    for hit_batch in batches(page.hits, SEARCH_RESOLUTION_BATCH_SIZE):
        if cancellation_check is not None:
            cancellation_check()
        resolved_values.extend(resolve_search_hits(database, hit_batch))
    resolved = tuple(resolved_values)
    cutoff_reason = (
        "max_vectors_reached"
        if not page.complete
        else (
            "top_k"
            if len(page.hits) == limit and page.scanned > len(page.hits)
            else None
        )
    )
    cutoff_score = page.hits[-1].score if len(page.hits) == limit else None
    return SemanticRanking(
        name=name,
        hits=page.hits,
        resolved=resolved,
        scanned=page.scanned,
        complete=page.complete,
        cutoff_reason=cutoff_reason,
        next_cursor=page.next_cursor,
        cutoff_score=cutoff_score,
    )


def registered_model_available(
    database: Path,
    expected: EmbeddingModelSpec,
) -> bool:
    try:
        registered = load_embedding_model(database, expected.model_signature)
    except KeyError:
        return False
    if registered != expected:
        raise RuntimeError(
            f"registered model differs from current contract: {expected.model_id}"
        )
    return True


def indexed_model_available(
    database: Path,
    expected: EmbeddingModelSpec,
) -> bool:
    return registered_model_available(database, expected) and has_active_embeddings(
        database,
        expected.model_signature,
    )


def unavailable_semantic_ranking(name: str, reason: str) -> SemanticRanking:
    return SemanticRanking(
        name=name,
        hits=(),
        resolved=(),
        scanned=0,
        complete=False,
        available=False,
        unavailable_reason=reason,
    )


# endregion [01]


# region [02] Modality-isolated rankings


def default_lexical_paths(state_directory: Path) -> LexicalStatePaths:
    return LexicalStatePaths(
        pdf=state_directory / "pdf.sqlite3",
        docx=state_directory / "docx.sqlite3",
        office=state_directory / "office.sqlite3",
        audio=state_directory / "audio.sqlite3",
    )


def text_search_ranking(
    database: Path,
    *,
    database_exists: bool,
    selected_model: EmbeddingModelSpec,
    query: str,
    cache: Path,
    local_files_only: bool,
    threads: int | None,
    limit: int,
    max_vectors: int,
    backend_factory: BackendFactory,
    evidence_mode: bool = False,
    cancellation_check: Callable[[], None] | None = None,
) -> SemanticRanking:
    if selected_model.modality is not EmbeddingModality.TEXT:
        raise ValueError("semantic text search requires a text model")
    if not database_exists:
        return unavailable_semantic_ranking(
            "semantic_text",
            "semantic_index_missing",
        )
    if not indexed_model_available(database, selected_model):
        return unavailable_semantic_ranking(
            "semantic_text",
            "text_model_not_indexed",
        )
    try:
        vector = query_vector(
            selected_model,
            query,
            cache_dir=cache,
            local_files_only=local_files_only,
            threads=threads,
            backend_factory=backend_factory,
            cancellation_check=cancellation_check,
        )
    except SemanticModelUnavailableError as exc:
        return unavailable_semantic_ranking("semantic_text", exc.reason)
    return semantic_ranking(
        database,
        name="semantic_text",
        query_model=selected_model,
        target_modality=EmbeddingModality.TEXT,
        vector=vector,
        indexed_model_signatures=(selected_model.model_signature,),
        limit=limit,
        max_vectors=max_vectors,
        evidence_mode=evidence_mode,
        cancellation_check=cancellation_check,
    )


def image_search_ranking(
    database: Path,
    *,
    database_exists: bool,
    query: str,
    cache: Path,
    local_files_only: bool,
    threads: int | None,
    limit: int,
    max_vectors: int,
    backend_factory: BackendFactory,
    evidence_mode: bool = False,
    cancellation_check: Callable[[], None] | None = None,
) -> SemanticRanking:
    query_model = clip_text_model()
    indexed_model = clip_image_model()
    if not database_exists:
        return unavailable_semantic_ranking(
            "semantic_image",
            "semantic_index_missing",
        )
    if not (
        registered_model_available(database, query_model)
        and indexed_model_available(database, indexed_model)
    ):
        return unavailable_semantic_ranking(
            "semantic_image",
            "clip_models_not_indexed",
        )
    try:
        vector = query_vector(
            query_model,
            expand_domain_query(query),
            cache_dir=cache,
            local_files_only=local_files_only,
            threads=threads,
            backend_factory=backend_factory,
            cancellation_check=cancellation_check,
        )
    except SemanticModelUnavailableError as exc:
        return unavailable_semantic_ranking("semantic_image", exc.reason)
    return semantic_ranking(
        database,
        name="semantic_image",
        query_model=query_model,
        target_modality=EmbeddingModality.IMAGE,
        vector=vector,
        indexed_model_signatures=(indexed_model.model_signature,),
        limit=limit,
        max_vectors=max_vectors,
        evidence_mode=evidence_mode,
        cancellation_check=cancellation_check,
    )


# endregion [02]


# region [03] Rank fusion


def _resolve_fused_hits(
    rankings: Sequence[SemanticRanking],
    lexical_rankings: Sequence[LexicalRanking],
    *,
    limit: int,
) -> tuple[FusedResolvedHit, ...]:
    raw_rankings = {
        semantic_ranking.name: semantic_ranking.hits for semantic_ranking in rankings
    }
    raw_rankings.update(
        {
            lexical_ranking.ranking_name: lexical_ranking.search_hits
            for lexical_ranking in lexical_rankings
        }
    )
    fused = reciprocal_rank_fusion(raw_rankings, limit=limit)
    resolved_by_item: dict[str, ResolvedSearchHit] = {}
    for semantic_ranking_value in rankings:
        for semantic_resolved in semantic_ranking_value.resolved:
            resolved_by_item.setdefault(
                semantic_resolved.hit.item_id,
                semantic_resolved,
            )
    for lexical_ranking in lexical_rankings:
        for lexical_resolved in lexical_ranking.hits:
            resolved_by_item.setdefault(
                lexical_resolved.hit.item_id,
                lexical_resolved,
            )
    return tuple(
        FusedResolvedHit(
            value,
            resolved_by_item[value.item_id].path,
            resolved_by_item[value.item_id].source_kind,
            resolved_by_item[value.item_id].source_identity,
            resolved_by_item[value.item_id].snippet,
        )
        for value in fused
        if value.item_id in resolved_by_item
    )


def search_semantic_index(
    state_directory: Path,
    query: str,
    *,
    limit: int,
    candidate_limit: int | None = None,
    max_vectors: int,
    include_text: bool,
    include_images: bool,
    include_lexical: bool,
    lexical_paths: LexicalStatePaths | None,
    semantic_database: Path | None = None,
    text_model: EmbeddingModelSpec | None,
    model_cache_override: Path | None,
    local_files_only: bool,
    threads: int | None,
    backend_factory: BackendFactory,
    lexical_search: LexicalSearch,
    evidence_mode: bool = False,
    cancellation_check: Callable[[], None] | None = None,
) -> SemanticSearchResult:
    """Search incompatible spaces independently, then fuse only their ranks."""

    if not isinstance(query, str):
        raise ValueError("semantic query must be a string")
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("semantic query cannot be blank")
    if any(unicodedata.category(character) == "Cc" for character in query):
        raise ValueError("semantic query cannot contain control characters")
    if len(normalized_query) > MAX_QUERY_CHARS:
        raise ValueError(f"semantic query cannot exceed {MAX_QUERY_CHARS} characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
        raise ValueError("semantic search limit must be between 1 and 1000")
    if candidate_limit is not None and (
        isinstance(candidate_limit, bool)
        or not isinstance(candidate_limit, int)
        or not 1 <= candidate_limit <= MAX_SEMANTIC_CANDIDATE_HITS
    ):
        raise ValueError(
            "semantic candidate_limit must be between 1 and "
            f"{MAX_SEMANTIC_CANDIDATE_HITS}"
        )
    if (
        isinstance(max_vectors, bool)
        or not isinstance(max_vectors, int)
        or not 1 <= max_vectors <= 10_000_000
    ):
        raise ValueError("semantic max_vectors must be between 1 and 10000000")
    if not (include_text or include_images or include_lexical):
        raise ValueError("at least one semantic or lexical ranking must be selected")
    if semantic_database is not None and not isinstance(semantic_database, Path):
        raise ValueError("semantic_database must be a Path when provided")
    if cancellation_check is not None:
        cancellation_check()

    database = (
        semantic_database
        if semantic_database is not None
        else state_directory / SEMANTIC_DATABASE_NAME
    )
    database_exists = database.is_file()
    cache = model_cache(state_directory, model_cache_override)
    rankings: list[SemanticRanking] = []
    semantic_candidate_limit = (
        candidate_limit
        if candidate_limit is not None
        else min(MAX_SEMANTIC_CANDIDATE_HITS, max(limit * 3, limit))
    )
    if include_text:
        rankings.append(
            text_search_ranking(
                database,
                database_exists=database_exists,
                selected_model=text_model or multilingual_text_model(),
                query=normalized_query,
                cache=cache,
                local_files_only=local_files_only,
                threads=threads,
                limit=semantic_candidate_limit,
                max_vectors=max_vectors,
                backend_factory=backend_factory,
                evidence_mode=evidence_mode,
                cancellation_check=cancellation_check,
            )
        )
    if include_images:
        rankings.append(
            image_search_ranking(
                database,
                database_exists=database_exists,
                query=normalized_query,
                cache=cache,
                local_files_only=local_files_only,
                threads=threads,
                limit=semantic_candidate_limit,
                max_vectors=max_vectors,
                backend_factory=backend_factory,
                evidence_mode=evidence_mode,
                cancellation_check=cancellation_check,
            )
        )

    if cancellation_check is not None:
        cancellation_check()
    if include_lexical and cancellation_check is not None:
        lexical_rankings = lexical_search(
            lexical_paths or default_lexical_paths(state_directory),
            normalized_query,
            limit=min(MAX_LEXICAL_CANDIDATE_HITS, max(limit * 3, limit)),
            cancellation_check=cancellation_check,
        )
    elif include_lexical:
        lexical_rankings = lexical_search(
            lexical_paths or default_lexical_paths(state_directory),
            normalized_query,
            limit=min(MAX_LEXICAL_CANDIDATE_HITS, max(limit * 3, limit)),
        )
    else:
        lexical_rankings = ()
    if cancellation_check is not None:
        cancellation_check()
    return SemanticSearchResult(
        normalized_query,
        tuple(rankings),
        tuple(lexical_rankings),
        _resolve_fused_hits(rankings, lexical_rankings, limit=limit),
    )


# endregion [03]
