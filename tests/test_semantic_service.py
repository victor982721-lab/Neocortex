from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Sequence, cast

import pytest

from _04_Nucleo_Operativo import semantic_service as service
from _04_Nucleo_Operativo import semantic_preparation
from _04_Nucleo_Operativo import semantic_search_service as search_implementation
from _04_Nucleo_Operativo import semantic_state as state
from _04_Nucleo_Operativo.semantic_chunking import TextChunkingConfig
from _04_Nucleo_Operativo.semantic_config import (
    compact_multilingual_text_model,
    fastembed_cache_contract,
)
from _04_Nucleo_Operativo.semantic_lexical import (
    LEXICAL_MODEL_SIGNATURE,
    MAX_QUERY_CHARS,
    LexicalAvailability,
    LexicalRanking,
)
from _04_Nucleo_Operativo.semantic_models import (
    BackendEmbedding,
    EmbeddingJobLease,
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRequest,
    EmbeddingRole,
    EvidenceDisposition,
    ResolvedSearchHit,
    SearchHit,
    SemanticEntityKind,
    SemanticItem,
    TextSection,
    fingerprint_bytes,
    fingerprint_text,
)
from _04_Nucleo_Operativo.semantic_ontology import CONCEPTS, ONTOLOGY_VERSION
from _04_Nucleo_Operativo.semantic_sources import ImageSourceRecord, TextSourceRecord
from _04_Nucleo_Operativo.semantic_state import (
    has_active_embeddings,
    list_semantic_evidence,
    semantic_database,
)


# region [01] Deterministic service fixture backend


class _FixtureBackend:
    def __init__(self, model: EmbeddingModelSpec) -> None:
        self._model = model

    @property
    def model(self) -> EmbeddingModelSpec:
        return self._model

    @property
    def max_batch_size(self) -> int:
        return 16

    def embed(
        self,
        requests: Sequence[EmbeddingRequest],
    ) -> Sequence[BackendEmbedding]:
        output: list[BackendEmbedding] = []
        for request in requests:
            position = (
                int(request.fingerprint.xxh3_128[:16], 16) % self.model.dimensions
            )
            vector = tuple(
                1.0 if index == position else 0.0
                for index in range(self.model.dimensions)
            )
            output.append(
                BackendEmbedding(
                    request_id=request.request_id,
                    vector=vector,
                    provenance={"backend": "semantic-service-fixture"},
                )
            )
        return tuple(output)


class _ConstantBackend(_FixtureBackend):
    def __init__(
        self,
        model: EmbeddingModelSpec,
        *,
        oppose_queries: bool = False,
    ) -> None:
        super().__init__(model)
        self.oppose_queries = oppose_queries
        self.requests: list[EmbeddingRequest] = []

    def embed(
        self,
        requests: Sequence[EmbeddingRequest],
    ) -> Sequence[BackendEmbedding]:
        self.requests.extend(requests)
        output: list[BackendEmbedding] = []
        for request in requests:
            leading = (
                -1.0
                if self.oppose_queries and request.role is EmbeddingRole.QUERY
                else 1.0
            )
            output.append(
                BackendEmbedding(
                    request_id=request.request_id,
                    vector=(leading,) + (0.0,) * (self.model.dimensions - 1),
                    provenance={"backend": "constant-fixture"},
                )
            )
        return tuple(output)


def _patch_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "_backend",
        lambda model, **_kwargs: _FixtureBackend(model),
    )


def _text_record() -> TextSourceRecord:
    text = "Mantenimiento y diagnóstico de un transformador de potencia."
    item = SemanticItem(
        item_id="item:pdf:fixture-pdf",
        source_kind="pdf",
        source_identity="fixture-pdf",
        identity_version="fixture-source-v1",
        fingerprint=fingerprint_text("fixture-pdf-source"),
        path="C:/fixtures/transformador.pdf",
        provenance={"fixture": True},
    )
    return TextSourceRecord(
        item,
        TextSection("pdf_page", "1", text, {"fixture": True}),
    )


def _text_records(count: int) -> tuple[TextSourceRecord, ...]:
    records: list[TextSourceRecord] = []
    for index in range(count):
        identity = f"fixture-pdf-{index}"
        item = SemanticItem(
            item_id=f"item:pdf:{identity}",
            source_kind="pdf",
            source_identity=identity,
            identity_version="fixture-source-v1",
            fingerprint=fingerprint_text(f"{identity}-source"),
            path=f"C:/fixtures/{identity}.pdf",
            provenance={"fixture": True},
        )
        records.append(
            TextSourceRecord(
                item,
                TextSection(
                    "pdf_page",
                    "1",
                    f"Mantenimiento del transformador número {index}.",
                    {"fixture": True},
                ),
            )
        )
    return tuple(records)


def _declare_source_state(state_directory: Path, source_kind: str) -> None:
    service.semantic_source_database(state_directory, source_kind).touch()


# endregion [01]


# region [02] Resumable text and multimodal indexing


def test_missing_source_state_cannot_deactivate_or_create_semantic_state(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="semantic source state is missing"):
        service.index_text_embeddings(tmp_path, source_kinds=("pdf",))
    with pytest.raises(FileNotFoundError, match="semantic source state is missing"):
        service.index_image_embeddings(tmp_path)

    assert not (tmp_path / service.SEMANTIC_DATABASE_NAME).exists()


def test_text_index_reuses_cached_vector_on_second_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, source: iter((_text_record(),)) if source == "pdf" else iter(()),
    )

    first = service.index_text_embeddings(tmp_path, source_kinds=("pdf",))
    second = service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    assert first.items_staged == 1
    assert first.chunks_staged == 1
    assert first.generations[0].embedded == 1
    assert first.generations[0].summary.status == "ready"
    assert second.generations[0].embedded == 0
    assert second.generations[0].reused == 1
    assert second.errors == 0


def test_image_and_ocr_use_separate_embedding_generations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "image")
    image_path = tmp_path / "subestacion.png"
    payload = b"fixture-image-payload"
    image_path.write_bytes(payload)
    item = SemanticItem(
        item_id="item:image:fixture-image",
        source_kind="image",
        source_identity="fixture-image",
        identity_version="fixture-image-v1",
        fingerprint=fingerprint_bytes(payload),
        path=str(image_path),
        provenance={"fixture": True},
        source_revision={
            "size": len(payload),
            "mtime_ns": image_path.stat().st_mtime_ns,
            "birthtime_ns": image_path.stat().st_ctime_ns,
            "raw_content_xxh3_128": fingerprint_bytes(payload).xxh3_128,
        },
    )
    record = ImageSourceRecord(
        item,
        TextSection(
            "image_ocr",
            "ocr",
            "Transformador de potencia en mantenimiento.",
            {"fixture": True},
        ),
    )
    monkeypatch.setattr(
        service,
        "iter_image_source_records",
        lambda _state: iter((record,)),
    )

    result = service.index_image_embeddings(tmp_path, embed_ocr_text=True)

    assert result.sources == ("image", "image-ocr")
    assert result.items_staged == 1
    assert result.chunks_staged == 1
    assert len(result.generations) == 2
    assert all(generation.embedded == 1 for generation in result.generations)
    assert {
        generation.summary.model_signature for generation in result.generations
    } == {
        service.clip_image_model().model_signature,
        service.multilingual_text_model().model_signature,
    }
    search = service.search_semantic_index(
        tmp_path,
        "subestación con transformador",
        include_text=False,
        include_images=True,
        include_lexical=False,
    )
    image_ranking = search.rankings[0]
    assert image_ranking.available
    assert len(image_ranking.hits) == 1
    assert image_ranking.hits[0].indexed_model_signature == (
        service.clip_image_model().model_signature
    )
    assert image_ranking.hits[0].query_model_signature == (
        service.clip_text_model().model_signature
    )
    assert image_ranking.resolved[0].hit.query_model_signature == (
        service.clip_text_model().model_signature
    )
    fusion_evidence = search.fused[0].fused.evidence[0]
    assert fusion_evidence.indexed_model_signature == (
        service.clip_image_model().model_signature
    )
    assert fusion_evidence.query_model_signature == (
        service.clip_text_model().model_signature
    )

    compact_model = compact_multilingual_text_model()
    compact = service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=compact_model,
    )
    assert compact.chunks_staged == 1
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    with semantic_database(database, readonly=True) as connection:
        active_signatures = connection.execute(
            """SELECT chunking_signature FROM text_chunks
            WHERE item_id=? AND active=1""",
            (item.item_id,),
        ).fetchall()
    assert {str(row[0]) for row in active_signatures} == {
        service.text_chunking_for_model(service.multilingual_text_model()).signature,
        service.text_chunking_for_model(compact_model).signature,
    }
    assert has_active_embeddings(
        database,
        service.multilingual_text_model().model_signature,
    )
    assert has_active_embeddings(database, compact_model.model_signature)

    without_ocr = service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=False,
        chunking=TextChunkingConfig(
            max_chars=96,
            max_terms=24,
            overlap_chars=0,
            overlap_terms=0,
            min_natural_break_chars=20,
        ),
    )
    assert without_ocr.sources == ("image",)
    assert without_ocr.chunks_staged == 0
    with semantic_database(
        tmp_path / service.SEMANTIC_DATABASE_NAME,
        readonly=True,
    ) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM text_chunks WHERE item_id=? AND active=1",
                (item.item_id,),
            ).fetchone()[0]
            == 0
        )
    # Model-specific published snapshots remain stable until each model publishes
    # a successor; disabling OCR does not silently rewrite unrelated heads.
    assert has_active_embeddings(
        database,
        service.multilingual_text_model().model_signature,
    )
    assert has_active_embeddings(database, compact_model.model_signature)

    service.index_image_embeddings(tmp_path, embed_ocr_text=True)
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=compact_model,
    )
    assert has_active_embeddings(
        database,
        service.multilingual_text_model().model_signature,
    )
    assert has_active_embeddings(database, compact_model.model_signature)
    monkeypatch.setattr(
        service,
        "iter_image_source_records",
        lambda _state: iter((ImageSourceRecord(item, None),)),
    )
    absent_ocr = service.index_image_embeddings(tmp_path, embed_ocr_text=True)
    assert absent_ocr.chunks_staged == 0
    with semantic_database(database, readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM text_chunks WHERE item_id=? AND active=1",
                (item.item_id,),
            ).fetchone()[0]
            == 0
        )
    assert not has_active_embeddings(
        database,
        service.multilingual_text_model().model_signature,
    )
    assert has_active_embeddings(database, compact_model.model_signature)
    compact_cleanup = state.start_embedding_generation(
        database,
        model_signature=compact_model.model_signature,
        processing_signature="fixture-compact-ocr-cleanup",
    )
    state.finalize_embedding_generation(database, compact_cleanup)
    assert not has_active_embeddings(database, compact_model.model_signature)


def test_changed_ocr_revision_keeps_other_head_until_its_model_republishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "image")
    image_path = tmp_path / "breaker.png"
    payload = b"stable-visual-payload"
    image_path.write_bytes(payload)
    fingerprint = fingerprint_bytes(payload)
    item = SemanticItem(
        item_id="item:image:ocr-revision",
        source_kind="image",
        source_identity="ocr-revision",
        identity_version="fixture-image-v1",
        fingerprint=fingerprint,
        path=str(image_path),
        source_revision={
            "size": len(payload),
            "mtime_ns": image_path.stat().st_mtime_ns,
            "birthtime_ns": image_path.stat().st_ctime_ns,
            "raw_content_xxh3_128": fingerprint.xxh3_128,
        },
    )
    current_record = [
        ImageSourceRecord(
            item,
            TextSection("image_ocr", "ocr", "Old breaker OCR text."),
        )
    ]
    monkeypatch.setattr(
        service,
        "iter_image_source_records",
        lambda _state: iter(current_record),
    )
    quality_model = service.multilingual_text_model()
    compact_model = compact_multilingual_text_model()
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=quality_model,
    )
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=compact_model,
    )
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    assert has_active_embeddings(database, quality_model.model_signature)
    assert has_active_embeddings(database, compact_model.model_signature)
    assert has_active_embeddings(
        database,
        service.clip_image_model().model_signature,
    )

    current_record[0] = ImageSourceRecord(
        item,
        TextSection("image_ocr", "ocr", "New transformer OCR text."),
    )
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=quality_model,
    )

    with semantic_database(database, readonly=True) as connection:
        active_signatures = connection.execute(
            """SELECT chunking_signature FROM text_chunks
            WHERE item_id=? AND active=1""",
            (item.item_id,),
        ).fetchall()
        revisions = connection.execute(
            """SELECT channel,revision_token FROM text_channel_revisions
            WHERE item_id=?""",
            (item.item_id,),
        ).fetchall()
    assert {str(row[0]) for row in active_signatures} == {
        service.text_chunking_for_model(quality_model).signature
    }
    assert len(revisions) == 1
    assert str(revisions[0][0]) == service.IMAGE_OCR_TEXT_CHANNEL
    assert "xxh3-128=" in str(revisions[0][1])
    assert has_active_embeddings(database, quality_model.model_signature)
    assert has_active_embeddings(database, compact_model.model_signature)
    assert has_active_embeddings(
        database,
        service.clip_image_model().model_signature,
    )
    with semantic_database(database, readonly=True) as connection:
        prior_compact_head = int(
            connection.execute(
                "SELECT generation_id FROM published_embedding_heads "
                "WHERE model_signature=?",
                (compact_model.model_signature,),
            ).fetchone()[0]
        )
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=compact_model,
    )
    with semantic_database(database, readonly=True) as connection:
        current_compact_head = int(
            connection.execute(
                "SELECT generation_id FROM published_embedding_heads "
                "WHERE model_signature=?",
                (compact_model.model_signature,),
            ).fetchone()[0]
        )
    assert current_compact_head != prior_compact_head


def test_visual_fingerprint_change_preserves_same_revision_ocr_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "image")
    image_path = tmp_path / "same-ocr.png"

    def image_item(payload: bytes) -> SemanticItem:
        image_path.write_bytes(payload)
        fingerprint = fingerprint_bytes(payload)
        stat_result = image_path.stat()
        return SemanticItem(
            item_id="item:image:same-ocr",
            source_kind="image",
            source_identity="same-ocr",
            identity_version="fixture-image-v1",
            fingerprint=fingerprint,
            path=str(image_path),
            source_revision={
                "size": len(payload),
                "mtime_ns": stat_result.st_mtime_ns,
                "birthtime_ns": stat_result.st_ctime_ns,
                "raw_content_xxh3_128": fingerprint.xxh3_128,
            },
        )

    ocr_section = TextSection(
        "image_ocr",
        "ocr",
        "Unchanged OCR text for transformer maintenance.",
    )
    current_record = [ImageSourceRecord(image_item(b"visual-v1"), ocr_section)]
    monkeypatch.setattr(
        service,
        "iter_image_source_records",
        lambda _state: iter(current_record),
    )
    quality_model = service.multilingual_text_model()
    compact_model = compact_multilingual_text_model()
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=quality_model,
    )
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=compact_model,
    )
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    assert has_active_embeddings(database, quality_model.model_signature)
    assert has_active_embeddings(database, compact_model.model_signature)

    current_record[0] = ImageSourceRecord(image_item(b"visual-v2"), ocr_section)
    service.index_image_embeddings(
        tmp_path,
        embed_ocr_text=True,
        ocr_model=quality_model,
    )

    with semantic_database(database, readonly=True) as connection:
        active_chunks = connection.execute(
            """SELECT chunking_signature FROM text_chunks
            WHERE item_id=? AND active=1""",
            (current_record[0].item.item_id,),
        ).fetchall()
    assert {str(row[0]) for row in active_chunks} == {
        service.text_chunking_for_model(quality_model).signature,
        service.text_chunking_for_model(compact_model).signature,
    }
    assert has_active_embeddings(database, quality_model.model_signature)
    assert has_active_embeddings(database, compact_model.model_signature)
    assert has_active_embeddings(
        database,
        service.clip_image_model().model_signature,
    )


# endregion [02]


# region [03] Retrieval and advisory ontology evidence


def test_resolved_hit_preserves_legacy_positional_mapping_arguments() -> None:
    hit = SearchHit(
        ref_id=1,
        entity_id="entity",
        item_id="item",
        indexed_model_signature="model",
        vector_space="space",
        modality=EmbeddingModality.TEXT,
        score=1.0,
        generation_id=1,
    )
    resolved = ResolvedSearchHit(
        hit,
        None,
        "pdf",
        "identity",
        None,
        None,
        None,
        None,
        None,
        {"revision": 1},
        {"section": 1},
    )

    assert resolved.source_revision == {"revision": 1}
    assert resolved.section_provenance == {"section": 1}
    assert resolved.source_status is None


def test_search_reports_unindexed_space_without_losing_available_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    result = service.search_semantic_index(
        tmp_path,
        "mantenimiento del transformador",
        include_text=True,
        include_images=True,
        include_lexical=False,
    )

    assert result.rankings[0].name == "semantic_text"
    assert result.rankings[0].available is True
    assert result.rankings[0].hits
    assert result.rankings[1].name == "semantic_image"
    assert result.rankings[1].available is False
    assert result.rankings[1].complete is False
    assert result.rankings[1].unavailable_reason == "clip_models_not_indexed"
    assert result.complete is False
    assert result.fused[0].source_kind == "pdf"


def test_empty_requested_semantic_and_lexical_rankings_are_incomplete(
    tmp_path: Path,
) -> None:
    all_rankings = service.search_semantic_index(tmp_path, "transformador")

    assert all_rankings.complete is False
    assert all(not ranking.available for ranking in all_rankings.rankings)
    assert all(not ranking.complete for ranking in all_rankings.rankings)
    assert all(
        ranking.availability is not LexicalAvailability.AVAILABLE
        for ranking in all_rankings.lexical_rankings
    )
    assert not (tmp_path / service.SEMANTIC_DATABASE_NAME).exists()

    lexical_only = service.search_semantic_index(
        tmp_path,
        "transformador",
        include_text=False,
        include_images=False,
        include_lexical=True,
    )
    assert lexical_only.rankings == ()
    assert lexical_only.complete is False


def test_semantic_vector_cap_exposes_incomplete_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    records = _text_records(4)
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter(records),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    result = service.search_semantic_index(
        tmp_path,
        "transformador",
        limit=1,
        max_vectors=1,
        include_text=True,
        include_images=False,
        include_lexical=False,
    )

    ranking = result.rankings[0]
    assert ranking.available is True
    assert ranking.complete is False
    assert ranking.scanned == 1
    assert ranking.cutoff_reason == "max_vectors_reached"
    assert ranking.next_cursor is not None
    assert result.complete is False


def test_semantic_top_k_cutoff_is_observable_without_marking_scan_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    records = _text_records(4)
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter(records),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    result = service.search_semantic_index(
        tmp_path,
        "transformador",
        limit=1,
        max_vectors=100,
        include_text=True,
        include_images=False,
        include_lexical=False,
    )

    ranking = result.rankings[0]
    assert ranking.complete is True
    assert ranking.scanned == 4
    assert len(ranking.hits) == 3
    assert ranking.cutoff_reason == "top_k"
    assert ranking.next_cursor is None
    assert ranking.cutoff_score == ranking.hits[-1].score
    assert result.complete is True


def test_explicit_candidate_limit_is_honored_without_multiplication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    records = _text_records(4)
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter(records),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    result = service.search_semantic_index(
        tmp_path,
        "transformador",
        limit=1,
        candidate_limit=2,
        max_vectors=100,
        include_text=True,
        include_images=False,
        include_lexical=False,
    )

    ranking = result.rankings[0]
    assert ranking.complete is True
    assert ranking.scanned == 4
    assert len(ranking.hits) == 2
    assert ranking.cutoff_reason == "top_k"
    assert ranking.cutoff_score == ranking.hits[-1].score
    assert len(result.fused) == 1


def test_semantic_database_override_uses_the_exact_generation_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    indexed_state = tmp_path / "indexed-state"
    indexed_state.mkdir()
    _declare_source_state(indexed_state, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    service.index_text_embeddings(indexed_state, source_kinds=("pdf",))
    generated_database = tmp_path / "semantic-generation-000042.sqlite3"
    (indexed_state / service.SEMANTIC_DATABASE_NAME).replace(generated_database)
    query_state = tmp_path / "query-state"

    result = service.search_semantic_index(
        query_state,
        "transformador",
        include_text=True,
        include_images=False,
        include_lexical=False,
        semantic_database=generated_database,
    )

    assert not (query_state / service.SEMANTIC_DATABASE_NAME).exists()
    assert result.rankings[0].available is True
    assert result.rankings[0].hits
    assert result.fused[0].source_identity == "fixture-pdf"


@pytest.mark.parametrize("candidate_limit", (True, 0, 1_001, "2"))
def test_semantic_candidate_limit_rejects_invalid_values(
    tmp_path: Path,
    candidate_limit,
) -> None:
    with pytest.raises(ValueError, match="semantic candidate_limit"):
        service.search_semantic_index(
            tmp_path,
            "transformador",
            candidate_limit=candidate_limit,
            include_text=True,
            include_images=False,
            include_lexical=False,
        )


@pytest.mark.parametrize(
    ("query", "message"),
    (
        (None, "semantic query must be a string"),
        (7, "semantic query must be a string"),
        ("   ", "semantic query cannot be blank"),
        ("transformador\x00", "semantic query cannot contain control"),
        ("transformador\nmantenimiento", "semantic query cannot contain control"),
        ("x" * (MAX_QUERY_CHARS + 1), "cannot exceed 4096 characters"),
    ),
)
def test_public_semantic_search_rejects_invalid_query_with_controlled_error(
    tmp_path: Path,
    query: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        service.search_semantic_index(
            tmp_path,
            cast(str, query),
            include_text=True,
            include_images=False,
            include_lexical=False,
        )

    assert not (tmp_path / service.SEMANTIC_DATABASE_NAME).exists()


@pytest.mark.parametrize("limit", (True, False, 0, 1_001, 1.5, "2", None))
def test_public_semantic_search_rejects_invalid_limit_with_controlled_error(
    tmp_path: Path,
    limit: object,
) -> None:
    with pytest.raises(ValueError, match="semantic search limit"):
        service.search_semantic_index(
            tmp_path,
            "transformador",
            limit=cast(int, limit),
            include_text=True,
            include_images=False,
            include_lexical=False,
        )


@pytest.mark.parametrize(
    "max_vectors",
    (True, False, 0, 10_000_001, 1.5, "2", None),
)
def test_public_semantic_search_rejects_invalid_vector_limit_with_controlled_error(
    tmp_path: Path,
    max_vectors: object,
) -> None:
    with pytest.raises(ValueError, match="semantic max_vectors"):
        service.search_semantic_index(
            tmp_path,
            "transformador",
            max_vectors=cast(int, max_vectors),
            include_text=True,
            include_images=False,
            include_lexical=False,
        )


@pytest.mark.parametrize(
    ("limit", "max_vectors"),
    ((1, 1), (1_000, 10_000_000)),
)
def test_public_semantic_search_accepts_inclusive_query_and_integer_bounds(
    tmp_path: Path,
    limit: int,
    max_vectors: int,
) -> None:
    result = service.search_semantic_index(
        tmp_path,
        "x" * MAX_QUERY_CHARS,
        limit=limit,
        max_vectors=max_vectors,
        include_text=True,
        include_images=False,
        include_lexical=False,
    )

    assert result.rankings[0].unavailable_reason == "semantic_index_missing"


def _fixture_lexical_ranking(tmp_path: Path) -> LexicalRanking:
    lexical_hit = SearchHit(
        ref_id=1,
        entity_id="fts:pdf:fixture-pdf:1",
        item_id="item:pdf:fixture-pdf",
        indexed_model_signature=LEXICAL_MODEL_SIGNATURE,
        vector_space="lexical:fts5:pdf:v1",
        modality=EmbeddingModality.TEXT,
        score=1.0,
        generation_id=0,
    )
    lexical_resolved = ResolvedSearchHit(
        hit=lexical_hit,
        path="C:/fixtures/transformador.pdf",
        source_kind="pdf",
        source_identity="fixture-pdf",
        section_kind="page",
        section_id="1",
        start_char=None,
        end_char=None,
        snippet="mantenimiento de transformador",
    )
    lexical = LexicalRanking(
        source_kind="pdf",
        state_path=tmp_path / "pdf.sqlite3",
        availability=LexicalAvailability.AVAILABLE,
        normalized_query="transformador",
        hits=(lexical_resolved,),
    )
    return lexical


def test_missing_local_query_model_preserves_lexical_without_cache_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))
    lexical = _fixture_lexical_ranking(tmp_path)
    monkeypatch.setattr(
        service,
        "search_lexical_sources",
        lambda _paths, _query, *, limit: (lexical,),
    )
    missing_cache = tmp_path / "missing-fastembed-cache"

    def unexpected_backend(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("FastEmbedBackend must not run when its cache is absent")

    monkeypatch.setattr(semantic_preparation, "FastEmbedBackend", unexpected_backend)
    monkeypatch.setattr(service, "_backend", semantic_preparation.backend)
    result = service.search_semantic_index(
        tmp_path,
        "transformador",
        include_text=True,
        include_images=False,
        include_lexical=True,
        model_cache=missing_cache,
    )

    assert result.rankings[0].available is False
    assert result.rankings[0].unavailable_reason == "semantic_model_cache_missing"
    assert result.lexical_rankings == (lexical,)
    assert result.fused[0].source_identity == "fixture-pdf"
    assert not missing_cache.exists()


def test_unloadable_cached_model_is_optional_and_preserves_lexical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))
    lexical = _fixture_lexical_ranking(tmp_path)
    monkeypatch.setattr(
        service,
        "search_lexical_sources",
        lambda _paths, _query, *, limit: (lexical,),
    )
    model = service.multilingual_text_model()
    contract = fastembed_cache_contract(model.model_signature)
    cache = tmp_path / "corrupt-fastembed-cache"
    repository = cache / ("models--" + contract.repository_id.replace("/", "--"))
    commit = "a" * 40
    reference = repository / "refs" / "main"
    reference.parent.mkdir(parents=True)
    reference.write_text(commit, encoding="ascii")
    snapshot = repository / "snapshots" / commit
    for relative_path in contract.required_files:
        cached_file = snapshot.joinpath(*relative_path.split("/"))
        cached_file.parent.mkdir(parents=True, exist_ok=True)
        cached_file.write_bytes(b"corrupt-cached-model-fixture")

    class CorruptCachedBackend(_FixtureBackend):
        def embed(
            self,
            requests: Sequence[EmbeddingRequest],
        ) -> Sequence[BackendEmbedding]:
            del requests
            raise OSError("cached model cannot be loaded")

    monkeypatch.setattr(
        semantic_preparation,
        "FastEmbedBackend",
        lambda selected_model, **_kwargs: CorruptCachedBackend(selected_model),
    )
    monkeypatch.setattr(service, "_backend", semantic_preparation.backend)
    result = service.search_semantic_index(
        tmp_path,
        "transformador",
        include_text=True,
        include_images=False,
        include_lexical=True,
        model_cache=cache,
    )

    assert result.rankings[0].available is False
    assert result.rankings[0].unavailable_reason == ("semantic_query_model_unloadable")
    assert result.lexical_rankings == (lexical,)
    assert result.fused[0].source_identity == "fixture-pdf"


def test_public_search_propagates_cancellation_into_exact_vector_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backend(monkeypatch)
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))
    original_search = search_implementation.search_exact_page
    entered_repository = False

    def observing_search(*args, **kwargs):
        nonlocal entered_repository
        entered_repository = True
        return original_search(*args, **kwargs)

    monkeypatch.setattr(
        search_implementation,
        "search_exact_page",
        observing_search,
    )

    class SearchCancelled(Exception):
        pass

    def cancellation_check() -> None:
        if entered_repository:
            raise SearchCancelled

    with pytest.raises(SearchCancelled):
        service.search_semantic_index(
            tmp_path,
            "transformador",
            include_text=True,
            include_images=False,
            include_lexical=False,
            cancellation_check=cancellation_check,
        )
    assert entered_repository


def test_public_search_passes_cancellation_into_lexical_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered_lexical = False

    class SearchCancelled(RuntimeError):
        pass

    def cancellation_check() -> None:
        if entered_lexical:
            raise SearchCancelled

    def lexical_search(
        _paths,
        _query,
        *,
        limit,
        cancellation_check=None,
    ):
        nonlocal entered_lexical
        del limit
        entered_lexical = True
        assert cancellation_check is not None
        cancellation_check()
        return ()

    monkeypatch.setattr(service, "search_lexical_sources", lexical_search)
    with pytest.raises(SearchCancelled):
        service.search_semantic_index(
            tmp_path,
            "transformador",
            include_text=False,
            include_images=False,
            include_lexical=True,
            cancellation_check=cancellation_check,
        )
    assert entered_lexical


def test_registered_model_without_active_vectors_is_reported_unindexed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = service.multilingual_text_model()
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    service._initialize_models(database, (model,))
    monkeypatch.setattr(
        service,
        "_backend",
        lambda *_args, **_kwargs: pytest.fail(
            "an empty registered model must not initialize an inference backend"
        ),
    )

    search = service.search_semantic_index(
        tmp_path,
        "transformador",
        include_text=True,
        include_images=False,
        include_lexical=False,
        text_model=model,
    )
    classification = service.classify_semantic_index(
        tmp_path,
        include_text=True,
        include_images=False,
        text_model=model,
    )

    assert search.rankings[0].available is False
    assert search.rankings[0].unavailable_reason == "text_model_not_indexed"
    assert classification.passes == ()
    assert classification.skipped == {"text": "text_model_not_indexed"}


def test_missing_semantic_index_preserves_lexical_ranking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hit = SearchHit(
        ref_id=1,
        entity_id="fts:pdf:fixture-pdf:1",
        item_id="item:pdf:fixture-pdf",
        indexed_model_signature=LEXICAL_MODEL_SIGNATURE,
        vector_space="lexical:fts5:pdf:v1",
        modality=EmbeddingModality.TEXT,
        score=1.0,
        generation_id=0,
    )
    resolved = ResolvedSearchHit(
        hit=hit,
        path="C:/fixtures/transformador.pdf",
        source_kind="pdf",
        source_identity="fixture-pdf",
        section_kind="page",
        section_id="1",
        start_char=None,
        end_char=None,
        snippet="mantenimiento de transformador",
    )
    lexical = LexicalRanking(
        source_kind="pdf",
        state_path=tmp_path / "pdf.sqlite3",
        availability=LexicalAvailability.AVAILABLE,
        normalized_query="transformador",
        hits=(resolved,),
    )
    monkeypatch.setattr(
        service,
        "search_lexical_sources",
        lambda _paths, _query, *, limit: (lexical,),
    )

    result = service.search_semantic_index(tmp_path, "transformador")

    assert not (tmp_path / service.SEMANTIC_DATABASE_NAME).exists()
    assert tuple(ranking.unavailable_reason for ranking in result.rankings) == (
        "semantic_index_missing",
        "semantic_index_missing",
    )
    assert result.lexical_rankings == (lexical,)
    assert result.fused[0].source_identity == "fixture-pdf"


def test_semantic_status_handles_uri_fragment_character_in_path(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "semantic#state"
    database = state_directory / service.SEMANTIC_DATABASE_NAME
    service._initialize_models(database, ())

    status = service.semantic_status(state_directory)

    assert status.exists is True
    assert status.schema_version == 6
    assert status.counts["semantic_items"] == 0
    assert status.counts["text_channel_revisions"] == 0


def test_semantic_status_reads_v4_state_without_migrating_or_missing_table_error(
    tmp_path: Path,
) -> None:
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    with semantic_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        state._migrate_to_v1(connection, 1)
        state._migrate_to_v2(connection, 2)
        state._migrate_to_v3(connection, 3)
        state._migrate_to_v4(connection, 4)
        connection.execute("PRAGMA user_version=4")
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version','4')"
        )

    status = service.semantic_status(tmp_path)

    assert status.schema_version == 4
    assert status.counts["text_channel_revisions"] == 0
    with semantic_database(database, readonly=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name='text_channel_revisions'"""
            ).fetchone()[0]
            == 0
        )


def test_classification_persists_only_uncalibrated_advisory_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "_backend",
        lambda model, **_kwargs: _ConstantBackend(model),
    )
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    selected_concepts = (
        CONCEPTS["industrial.equipment.transformer"],
        CONCEPTS["industrial.activity.maintenance"],
    )
    monkeypatch.setattr(
        service,
        "_classification_concepts",
        lambda _modality: selected_concepts,
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    result = service.classify_semantic_index(
        tmp_path,
        include_text=True,
        include_images=False,
        max_evidence_per_entity=2,
        page_size=1,
    )
    evidence = list_semantic_evidence(
        tmp_path / service.SEMANTIC_DATABASE_NAME,
        item_id="item:pdf:fixture-pdf",
        ontology_id=service.SEMANTIC_ONTOLOGY_ID,
        ontology_version=ONTOLOGY_VERSION,
    )

    assert result.skipped == {}
    assert len(result.passes) == 1
    assert result.passes[0].prototypes == 2
    assert result.passes[0].entities_scored == 1
    assert result.passes[0].entities_abstained == 0
    assert result.passes[0].evidence_staged == 2
    assert len(evidence) == 2
    assert {value.concept_id for value in evidence} == {
        concept.concept_id for concept in selected_concepts
    }
    assert all(value.disposition is EvidenceDisposition.ADVISORY for value in evidence)
    assert all(value.calibration_status.value == "uncalibrated" for value in evidence)


def test_prototype_preparation_reuses_complete_active_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected_concepts = (
        CONCEPTS["industrial.equipment.transformer"],
        CONCEPTS["industrial.activity.maintenance"],
    )
    monkeypatch.setattr(
        service,
        "_classification_concepts",
        lambda _modality: selected_concepts,
    )
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    model = service.multilingual_text_model()
    service._initialize_models(database, (model,))
    backend = _ConstantBackend(model)

    first = service._prepare_label_prototypes(
        database,
        backend,
        target_modality=model.modality,
    )
    first_request_count = len(backend.requests)
    second = service._prepare_label_prototypes(
        database,
        backend,
        target_modality=model.modality,
    )

    assert first_request_count == len(selected_concepts)
    assert len(backend.requests) == first_request_count
    assert second == first


def test_classification_abstains_from_unsupported_negative_scores(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "_backend",
        lambda model, **_kwargs: _ConstantBackend(model, oppose_queries=True),
    )
    _declare_source_state(tmp_path, "pdf")
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((_text_record(),)),
    )
    selected_concepts = (
        CONCEPTS["industrial.equipment.transformer"],
        CONCEPTS["industrial.activity.maintenance"],
    )
    monkeypatch.setattr(
        service,
        "_classification_concepts",
        lambda _modality: selected_concepts,
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))

    result = service.classify_semantic_index(
        tmp_path,
        include_text=True,
        include_images=False,
        max_evidence_per_entity=2,
        page_size=1,
    )
    evidence = list_semantic_evidence(
        tmp_path / service.SEMANTIC_DATABASE_NAME,
        item_id="item:pdf:fixture-pdf",
        ontology_id=service.SEMANTIC_ONTOLOGY_ID,
        ontology_version=ONTOLOGY_VERSION,
    )

    assert result.passes[0].entities_scored == 1
    assert result.passes[0].entities_abstained == 1
    assert result.passes[0].evidence_staged == 0
    assert evidence == ()


def test_interrupted_evidence_refresh_keeps_published_entity_coherent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "_backend",
        lambda model, **_kwargs: _ConstantBackend(model),
    )
    _declare_source_state(tmp_path, "pdf")
    first = _text_record()
    second_text = "Inspección termográfica de interruptores de potencia."
    second_item = SemanticItem(
        item_id="item:pdf:fixture-pdf-2",
        source_kind="pdf",
        source_identity="fixture-pdf-2",
        identity_version="fixture-source-v1",
        fingerprint=fingerprint_text("fixture-pdf-source-2"),
        path="C:/fixtures/interruptor.pdf",
        provenance={"fixture": True},
    )
    second = TextSourceRecord(
        second_item,
        TextSection("pdf_page", "1", second_text, {"fixture": True}),
    )
    monkeypatch.setattr(
        service,
        "iter_text_source_records",
        lambda _state, _source: iter((first, second)),
    )
    selected_concepts = (
        CONCEPTS["industrial.equipment.transformer"],
        CONCEPTS["industrial.activity.maintenance"],
    )
    monkeypatch.setattr(
        service,
        "_classification_concepts",
        lambda _modality: selected_concepts,
    )
    service.index_text_embeddings(tmp_path, source_kinds=("pdf",))
    service.classify_semantic_index(
        tmp_path,
        include_text=True,
        include_images=False,
        max_evidence_per_entity=2,
        page_size=1,
    )
    database = tmp_path / service.SEMANTIC_DATABASE_NAME
    assert (
        len(
            list_semantic_evidence(
                database,
                item_id=first.item.item_id,
                ontology_id=service.SEMANTIC_ONTOLOGY_ID,
                ontology_version=ONTOLOGY_VERSION,
            )
        )
        == 2
    )
    assert (
        len(
            list_semantic_evidence(
                database,
                item_id=second.item.item_id,
                ontology_id=service.SEMANTIC_ONTOLOGY_ID,
                ontology_version=ONTOLOGY_VERSION,
            )
        )
        == 2
    )

    monkeypatch.setattr(
        service,
        "_selected_prototype_indices",
        lambda _scores, _prototypes, _families, _maximum: (0,),
    )
    real_publish = service.publish_semantic_evidence_entities
    publication_calls = 0

    def fail_after_first_publication(*args, **kwargs):
        nonlocal publication_calls
        publication_calls += 1
        if publication_calls == 2:
            raise RuntimeError("deliberate interruption after first evidence page")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(
        service,
        "publish_semantic_evidence_entities",
        fail_after_first_publication,
    )
    with pytest.raises(RuntimeError, match="deliberate interruption"):
        service.classify_semantic_index(
            tmp_path,
            include_text=True,
            include_images=False,
            max_evidence_per_entity=2,
            page_size=1,
        )

    first_evidence = list_semantic_evidence(
        database,
        item_id=first.item.item_id,
        ontology_id=service.SEMANTIC_ONTOLOGY_ID,
        ontology_version=ONTOLOGY_VERSION,
    )
    second_evidence = list_semantic_evidence(
        database,
        item_id=second.item.item_id,
        ontology_id=service.SEMANTIC_ONTOLOGY_ID,
        ontology_version=ONTOLOGY_VERSION,
    )
    assert publication_calls == 2
    assert len(first_evidence) == 1
    assert len(second_evidence) == 2
    assert {value.concept_id for value in first_evidence}.issubset(
        {value.concept_id for value in second_evidence}
    )


def test_payload_local_embedding_failure_isolated_without_losing_peers() -> None:
    model = service.multilingual_text_model()

    class PayloadLocalBackend(_ConstantBackend):
        def __init__(self, selected_model: EmbeddingModelSpec) -> None:
            super().__init__(selected_model)
            self.calls = 0

        def embed(
            self,
            requests: Sequence[EmbeddingRequest],
        ) -> Sequence[BackendEmbedding]:
            self.calls += 1
            if any(request.request_id == "mutable" for request in requests):
                raise service.SourceRevisionMismatchError("source changed")
            return super().embed(requests)

    backend = PayloadLocalBackend(model)
    requests = tuple(
        EmbeddingRequest(
            request_id=request_id,
            role=EmbeddingRole.QUERY,
            fingerprint=fingerprint_text(request_id),
            text=request_id,
        )
        for request_id in ("stable-1", "mutable", "stable-2")
    )

    successes, failures = service._embed_requests_isolated(backend, requests)

    assert tuple(index for index, _output in successes) == (0, 2)
    assert tuple(index for index, _error in failures) == (1,)
    assert backend.calls > 1


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_systemic_embedding_failure_is_not_bisected(
    error_type: type[Exception],
) -> None:
    model = service.multilingual_text_model()

    class SystemicBackend(_ConstantBackend):
        def __init__(self, selected_model: EmbeddingModelSpec) -> None:
            super().__init__(selected_model)
            self.calls = 0

        def embed(
            self,
            _requests: Sequence[EmbeddingRequest],
        ) -> Sequence[BackendEmbedding]:
            self.calls += 1
            raise error_type("systemic failure")

    backend = SystemicBackend(model)
    requests = tuple(
        EmbeddingRequest(
            request_id=f"request-{index}",
            role=EmbeddingRole.QUERY,
            fingerprint=fingerprint_text(f"request-{index}"),
            text=f"request-{index}",
        )
        for index in range(3)
    )

    successes, failures = service._embed_requests_isolated(backend, requests)

    assert successes == ()
    assert tuple(index for index, _error in failures) == (0, 1, 2)
    assert backend.calls == 1


def test_embedding_heartbeat_is_joined_and_surfaces_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = service.multilingual_text_model()
    text = "relay protection heartbeat"
    request = EmbeddingRequest(
        request_id="1",
        role=EmbeddingRole.PASSAGE,
        fingerprint=fingerprint_text(text),
        text=text,
    )
    lease = EmbeddingJobLease(
        job_id=1,
        generation_id=7,
        model_signature=model.model_signature,
        vector_space=model.vector_space,
        modality=EmbeddingModality.TEXT,
        role=EmbeddingRole.PASSAGE,
        entity_kind=SemanticEntityKind.TEXT_CHUNK,
        entity_id="chunk:1",
        item_id="item:1",
        fingerprint=request.fingerprint,
        attempt=1,
        lease_until_ns=time.time_ns() + 1_000_000_000,
        text=text,
    )

    class SlowBackend(_FixtureBackend):
        def embed(
            self,
            requests: Sequence[EmbeddingRequest],
        ) -> Sequence[BackendEmbedding]:
            time.sleep(0.05)
            return super().embed(requests)

    calls: list[tuple[int, ...]] = []

    def successful_heartbeat(
        _database: Path,
        job_ids: Sequence[int],
        **_kwargs: object,
    ) -> int:
        calls.append(tuple(job_ids))
        return time.time_ns() + 1_000_000_000

    monkeypatch.setattr(service, "heartbeat_embedding_jobs", successful_heartbeat)
    successes, failures = service._embed_requests_with_heartbeat(
        tmp_path / "semantic.sqlite3",
        (lease,),
        worker_id="worker",
        backend=SlowBackend(model),
        requests=(request,),
        lease_seconds=1.0,
        heartbeat_interval_seconds=0.01,
    )
    assert len(successes) == 1
    assert failures == ()
    assert calls
    assert all(value == (lease.job_id,) for value in calls)
    assert not any(
        thread.name.startswith("neocortex-semantic-lease:")
        for thread in threading.enumerate()
    )

    def failed_heartbeat(
        _database: Path,
        _job_ids: Sequence[int],
        **_kwargs: object,
    ) -> int:
        raise state.SemanticStateError("lease disappeared")

    monkeypatch.setattr(service, "heartbeat_embedding_jobs", failed_heartbeat)
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        service._embed_requests_with_heartbeat(
            tmp_path / "semantic.sqlite3",
            (lease,),
            worker_id="worker",
            backend=SlowBackend(model),
            requests=(request,),
            lease_seconds=1.0,
            heartbeat_interval_seconds=0.01,
        )
    assert not any(
        thread.name.startswith("neocortex-semantic-lease:")
        for thread in threading.enumerate()
    )


# endregion [03]
