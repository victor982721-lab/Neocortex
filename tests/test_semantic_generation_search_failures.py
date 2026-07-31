"""Critical failure contracts for semantic generations and exact search."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from _04_Nucleo_Operativo.semantic_models import (
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRole,
    ExactSearchQuery,
    SearchHit,
    SemanticItem,
    TextChunk,
    fingerprint_text,
)
from _04_Nucleo_Operativo.semantic_state import (
    SemanticStateError,
    StaleEmbeddingJobError,
    claim_embedding_jobs,
    complete_embedding_job,
    enqueue_image_item_jobs,
    enqueue_text_chunk_jobs,
    finalize_embedding_generation,
    finalize_semantic_item_refresh,
    finalize_text_chunk_refresh,
    generation_summary,
    heartbeat_embedding_job,
    initialize_semantic_state,
    load_active_embedding_page,
    register_embedding_model,
    resolve_search_hits,
    search_exact_page,
    stage_text_chunks,
    start_embedding_generation,
    update_embedding_generation_cursor,
    upsert_semantic_item,
)


# region [01] Published-generation fixture


def _model(
    signature: str = "failure-model-v1",
    space: str = "failure-space-v1",
) -> EmbeddingModelSpec:
    return EmbeddingModelSpec(
        signature,
        space,
        EmbeddingModality.TEXT,
        f"fixture/{signature}",
        "1",
        4,
        "test-deterministic",
        (EmbeddingRole.QUERY, EmbeddingRole.PASSAGE),
    )


def _query(
    model: EmbeddingModelSpec,
    *,
    vector_space: str | None = None,
    indexed_models: tuple[str, ...] | None = None,
) -> ExactSearchQuery:
    return ExactSearchQuery(
        model.model_signature,
        vector_space or model.vector_space,
        model.dimensions,
        (1.0, 0.0, 0.0, 0.0),
        EmbeddingModality.TEXT,
        indexed_model_signatures=(
            (model.model_signature,) if indexed_models is None else indexed_models
        ),
    )


def _initialize(database: Path, *models: EmbeddingModelSpec) -> None:
    initialize_semantic_state(database)
    for model in models:
        register_embedding_model(database, model, allow_test_provider=True)


def _stage(database: Path, item_id: str, text: str) -> TextChunk:
    item = SemanticItem(
        item_id,
        "pdf",
        f"identity:{item_id}",
        "failure-fixture-v1",
        fingerprint_text(text),
        path=f"C:/fixtures/{item_id}.pdf",
    )
    chunk = TextChunk(
        f"chunk:{item_id}:0",
        item_id,
        0,
        "pdf_page",
        "1",
        0,
        len(text),
        text,
        fingerprint_text(text),
        "failure-chunks-v1",
    )
    upsert_semantic_item(
        database,
        item,
        refresh_token="items-current",
        updated_ns=10,
    )
    stage_text_chunks(
        database,
        (chunk,),
        refresh_token="chunks-current",
        updated_ns=11,
    )
    finalize_text_chunk_refresh(
        database,
        item_id=item_id,
        chunking_signature=chunk.chunking_signature,
        refresh_token="chunks-current",
        updated_ns=12,
    )
    return chunk


def _complete_generation(
    database: Path,
    model: EmbeddingModelSpec,
    chunk: TextChunk,
    *,
    processing_signature: str,
    started_ns: int,
) -> tuple[int, SearchHit]:
    generation_id = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature=processing_signature,
        started_ns=started_ns,
    )
    enqueue_text_chunk_jobs(
        database,
        generation_id,
        (chunk.chunk_id,),
        now_ns=started_ns + 1,
    )
    lease = claim_embedding_jobs(
        database,
        generation_id,
        worker_id="fixture-worker",
        limit=1,
        lease_seconds=60,
        now_ns=started_ns + 2,
    )[0]
    complete_embedding_job(
        database,
        lease.job_id,
        worker_id="fixture-worker",
        vector=(1.0, 0.0, 0.0, 0.0),
        provenance={"fixture": "failure-contract"},
        now_ns=started_ns + 3,
    )
    finalize_embedding_generation(
        database,
        generation_id,
        completed_ns=started_ns + 4,
    )
    hit = search_exact_page(database, _query(model), limit=1).hits[0]
    return generation_id, hit


# endregion [01]


# region [02] Search integrity and compatibility boundaries


def test_search_rejects_incompatible_or_unavailable_model_spaces(tmp_path: Path) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    _initialize(database, model)

    with pytest.raises(ValueError, match="incompatible with its registered model"):
        search_exact_page(
            database,
            _query(model, vector_space="different-space"),
        )
    with pytest.raises(ValueError, match="indexed models are absent or incompatible"):
        search_exact_page(
            database,
            _query(model, indexed_models=("missing-model",)),
        )

    with pytest.raises(ValueError, match="no compatible indexed models"):
        search_exact_page(
            database,
            ExactSearchQuery(
                model.model_signature,
                model.vector_space,
                model.dimensions,
                (1.0, 0.0, 0.0, 0.0),
                EmbeddingModality.IMAGE,
            ),
        )


def test_search_rejects_published_head_bound_to_another_model(tmp_path: Path) -> None:
    database = tmp_path / "semantic.sqlite3"
    first = _model("first-model-v1", "first-space-v1")
    second = _model("second-model-v1", "second-space-v1")
    _initialize(database, first, second)
    chunk = _stage(database, "document", "published transformer record")
    generation_id, _ = _complete_generation(
        database,
        first,
        chunk,
        processing_signature="first-publication",
        started_ns=100,
    )

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM published_embedding_heads")
        connection.execute(
            "INSERT INTO published_embedding_heads VALUES(?,?,?)",
            (second.model_signature, generation_id, 200),
        )

    with pytest.raises(SemanticStateError, match="published embedding head"):
        search_exact_page(database, _query(second))


def test_resolver_rejects_every_identity_or_space_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    _initialize(database, model)
    chunk = _stage(database, "document", "published breaker record")
    _, hit = _complete_generation(
        database,
        model,
        chunk,
        processing_signature="published",
        started_ns=100,
    )

    inconsistent = (
        replace(hit, ref_id=hit.ref_id + 10_000),
        replace(hit, generation_id=hit.generation_id + 1),
        replace(hit, indexed_model_signature="forged-model"),
        replace(hit, vector_space="forged-space"),
        replace(hit, modality=EmbeddingModality.IMAGE),
        replace(hit, entity_id="forged-entity"),
        replace(hit, item_id="forged-item"),
    )
    for forged in inconsistent:
        with pytest.raises(
            SemanticStateError,
            match="published hit snapshot is unavailable or inconsistent",
        ):
            resolve_search_hits(database, (forged,))


def test_search_rejects_corrupt_vector_and_member_provenance(tmp_path: Path) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    _initialize(database, model)
    chunk = _stage(database, "document", "published switchgear record")
    generation_id, _ = _complete_generation(
        database,
        model,
        chunk,
        processing_signature="published",
        started_ns=100,
    )

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE vector_payloads SET dimensions=3")
    with pytest.raises(SemanticStateError, match="dimension violates its space"):
        search_exact_page(database, _query(model))

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE vector_payloads SET dimensions=4")
        connection.execute(
            "UPDATE embedding_generation_members SET provenance_json='[]'"
        )
    with pytest.raises(SemanticStateError, match="provenance is not a JSON object"):
        load_active_embedding_page(
            database,
            model.model_signature,
            _generation_id=generation_id,
        )


# endregion [02]


# region [03] Generation recovery and state boundaries


def test_deleted_source_stales_lease_and_cannot_replace_published_head(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    _initialize(database, model)
    chunk = _stage(database, "document", "published relay record")
    published_generation, published_hit = _complete_generation(
        database,
        model,
        chunk,
        processing_signature="published",
        started_ns=100,
    )
    successor = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="successor",
        started_ns=200,
    )
    enqueue_text_chunk_jobs(database, successor, (chunk.chunk_id,), now_ns=201)
    lease = claim_embedding_jobs(
        database,
        successor,
        worker_id="successor-worker",
        limit=1,
        lease_seconds=60,
        now_ns=202,
    )[0]

    assert finalize_semantic_item_refresh(
        database,
        source_kind="pdf",
        refresh_token="source-deleted",
        updated_ns=203,
    ) == 1
    with pytest.raises(StaleEmbeddingJobError, match="source changed"):
        complete_embedding_job(
            database,
            lease.job_id,
            worker_id="successor-worker",
            vector=(1.0, 0.0, 0.0, 0.0),
            now_ns=204,
        )

    summary = generation_summary(database, successor)
    assert summary.stale == 1
    with pytest.raises(SemanticStateError, match="stale jobs"):
        finalize_embedding_generation(database, successor, completed_ns=205)
    partial = finalize_embedding_generation(
        database,
        successor,
        allow_partial=True,
        completed_ns=206,
    )
    assert partial.status == "ready_partial"
    assert search_exact_page(database, _query(model)).hits == (published_hit,)
    assert published_hit.generation_id == published_generation


def test_generation_resume_and_post_publication_mutations_are_guarded(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    _initialize(database, model)
    generation_id = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="resumable",
        provenance={"run": 1},
        cursor={"after": 10},
        started_ns=100,
    )
    assert start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="resumable",
        provenance={"run": 1},
        cursor={"after": 99},
        started_ns=101,
    ) == generation_id
    with pytest.raises(ValueError, match="provenance does not match"):
        start_embedding_generation(
            database,
            model_signature=model.model_signature,
            processing_signature="resumable",
            provenance={"run": 2},
            started_ns=102,
        )

    finalize_embedding_generation(database, generation_id, completed_ns=103)
    with pytest.raises(SemanticStateError, match="no longer building"):
        update_embedding_generation_cursor(database, generation_id, {"after": 20})
    with pytest.raises(SemanticStateError, match="is not building"):
        enqueue_text_chunk_jobs(database, generation_id, ("missing-chunk",))


def test_wrong_modality_and_lease_owner_cannot_advance_generation(tmp_path: Path) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    _initialize(database, model)
    chunk = _stage(database, "document", "pending transformer record")
    generation_id = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="pending",
        started_ns=100,
    )
    with pytest.raises(ValueError, match="incompatible with requested entities"):
        enqueue_image_item_jobs(database, generation_id, ("document",), now_ns=101)

    enqueue_text_chunk_jobs(database, generation_id, (chunk.chunk_id,), now_ns=102)
    lease = claim_embedding_jobs(
        database,
        generation_id,
        worker_id="owner-worker",
        limit=1,
        lease_seconds=60,
        now_ns=103,
    )[0]
    with pytest.raises(SemanticStateError, match="owned elsewhere"):
        heartbeat_embedding_job(
            database,
            lease.job_id,
            worker_id="other-worker",
            now_ns=104,
        )
    extended_until = heartbeat_embedding_job(
        database,
        lease.job_id,
        worker_id="owner-worker",
        lease_seconds=120,
        now_ns=105,
    )
    with pytest.raises(SemanticStateError, match="expired"):
        heartbeat_embedding_job(
            database,
            lease.job_id,
            worker_id="owner-worker",
            now_ns=extended_until + 1,
        )


# endregion [03]
