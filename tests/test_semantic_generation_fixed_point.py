# region [00] Contexto del módulo
# Módulo: tests/test_semantic_generation_fixed_point.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]
# region [01] Dependencias del módulo
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from _04_Nucleo_Operativo import semantic_generation_worker
from _04_Nucleo_Operativo.semantic_chunking import (
    TextChunkingConfig,
    iter_text_chunks,
)
from _04_Nucleo_Operativo.semantic_generation_worker import run_generation
from _04_Nucleo_Operativo.semantic_models import (
    BackendEmbedding,
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRequest,
    EmbeddingRole,
    SemanticItem,
    TextSection,
    fingerprint_text,
)
from _04_Nucleo_Operativo.semantic_state import (
    enqueue_text_chunk_jobs,
    finalize_text_chunk_refresh,
    initialize_semantic_state,
    register_embedding_model,
    semantic_database,
    stage_text_chunks,
    start_embedding_generation,
    upsert_semantic_item,
)
# endregion [01]

# region [02] Implementación


CHUNKING = TextChunkingConfig(
    max_chars=256,
    max_terms=64,
    overlap_chars=0,
    overlap_terms=0,
    min_natural_break_chars=32,
)


class _FatalRequestConstruction(BaseException):
    pass


def _model() -> EmbeddingModelSpec:
    return EmbeddingModelSpec(
        "fixed-point-model-v1",
        "fixed-point-space-v1",
        EmbeddingModality.TEXT,
        "fixture/fixed-point",
        "1",
        4,
        "test-deterministic",
        (EmbeddingRole.QUERY, EmbeddingRole.PASSAGE),
    )


class _RecordingBackend:
    def __init__(
        self,
        model: EmbeddingModelSpec,
        *,
        max_batch_size: int,
        failure: BaseException | None = None,
    ) -> None:
        self._model = model
        self._max_batch_size = max_batch_size
        self._failure = failure
        self.calls: list[tuple[str, ...]] = []

    @property
    def model(self) -> EmbeddingModelSpec:
        return self._model

    @property
    def max_batch_size(self) -> int:
        return self._max_batch_size

    def embed(
        self,
        requests: Sequence[EmbeddingRequest],
    ) -> Sequence[BackendEmbedding]:
        self.calls.append(tuple(request.request_id for request in requests))
        if self._failure is not None:
            raise self._failure
        return tuple(
            BackendEmbedding(
                request.request_id,
                (1.0, 0.0, 0.0, 0.0),
                {"fixture": "fixed-point"},
            )
            for request in requests
        )


def _duplicate_generation(tmp_path: Path) -> tuple[Path, int, EmbeddingModelSpec]:
    database = tmp_path / "semantic.sqlite3"
    model = _model()
    initialize_semantic_state(database)
    register_embedding_model(database, model, allow_test_provider=True)

    chunk_ids: list[str] = []
    duplicate_text = "Protección diferencial de transformador y prueba de interruptor."
    for index in range(2):
        identity = f"duplicate-{index}"
        item = SemanticItem(
            item_id=f"item:pdf:{identity}",
            source_kind="pdf",
            source_identity=identity,
            identity_version="fixed-point-source-v1",
            fingerprint=fingerprint_text(f"source:{identity}"),
            path=f"C:/fixtures/{identity}.pdf",
            provenance={"fixture": True},
        )
        upsert_semantic_item(
            database,
            item,
            refresh_token="fixed-point-item-refresh",
        )
        chunk = tuple(
            iter_text_chunks(
                item.item_id,
                (TextSection("pdf_page", "1", duplicate_text),),
                CHUNKING,
            )
        )[0]
        refresh_token = f"fixed-point-chunk-refresh:{index}"
        stage_text_chunks(
            database,
            (chunk,),
            refresh_token=refresh_token,
        )
        finalize_text_chunk_refresh(
            database,
            item_id=item.item_id,
            chunking_signature=CHUNKING.signature,
            refresh_token=refresh_token,
        )
        chunk_ids.append(chunk.chunk_id)

    generation_id = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="fixed-point-generation-v1",
    )
    assert enqueue_text_chunk_jobs(database, generation_id, chunk_ids) == 2
    return database, generation_id, model


def test_new_payload_satisfies_duplicate_pending_after_prior_batch(
    tmp_path: Path,
) -> None:
    database, generation_id, model = _duplicate_generation(tmp_path)
    backend = _RecordingBackend(model, max_batch_size=1)

    work = run_generation(database, generation_id, backend, queued=2)

    assert backend.calls and tuple(map(len, backend.calls)) == (1,)
    assert work.queued == 2
    assert work.reused == 1
    assert work.embedded == 1
    assert work.failed == 0
    assert work.summary.status == "ready"
    assert work.summary.done == 2
    assert work.summary.unfinished == 0
    with semantic_database(database, readonly=True) as connection:
        statuses = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT status FROM embedding_jobs ORDER BY job_id"
            )
        )
        payloads = int(
            connection.execute("SELECT COUNT(*) FROM vector_payloads").fetchone()[0]
        )
        members = int(
            connection.execute(
                "SELECT COUNT(*) FROM embedding_generation_members "
                "WHERE generation_id=?",
                (generation_id,),
            ).fetchone()[0]
        )
        head = connection.execute(
            "SELECT generation_id FROM published_embedding_heads "
            "WHERE model_signature=?",
            (model.model_signature,),
        ).fetchone()
    assert statuses == ("done", "done")
    assert payloads == 1
    assert members == 2
    assert head is not None and int(head[0]) == generation_id


def test_systemic_embedding_error_leaves_no_job_leased(tmp_path: Path) -> None:
    database, generation_id, model = _duplicate_generation(tmp_path)
    backend = _RecordingBackend(
        model,
        max_batch_size=2,
        failure=RuntimeError("injected systemic failure"),
    )

    work = run_generation(database, generation_id, backend, queued=2)

    assert tuple(map(len, backend.calls)) == (2,)
    assert work.failed == 2
    assert work.summary.status == "ready_partial"
    with semantic_database(database, readonly=True) as connection:
        statuses = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT status FROM embedding_jobs ORDER BY job_id"
            )
        )
        leased = int(
            connection.execute(
                "SELECT COUNT(*) FROM embedding_jobs WHERE status='leased'"
            ).fetchone()[0]
        )
        heads = int(
            connection.execute("SELECT COUNT(*) FROM published_embedding_heads").fetchone()[
                0
            ]
        )
    assert statuses == ("error", "error")
    assert leased == 0
    assert heads == 0


def test_keyboard_interrupt_releases_exact_leases_without_publication(
    tmp_path: Path,
) -> None:
    database, generation_id, model = _duplicate_generation(tmp_path)
    backend = _RecordingBackend(
        model,
        max_batch_size=2,
        failure=KeyboardInterrupt("cancel embedding"),
    )

    with pytest.raises(KeyboardInterrupt, match="cancel embedding"):
        run_generation(database, generation_id, backend, queued=2)

    assert tuple(map(len, backend.calls)) == (2,)
    with semantic_database(database, readonly=True) as connection:
        jobs = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT status,lease_owner,lease_until_ns FROM embedding_jobs "
                "ORDER BY job_id"
            )
        )
        generation_status = str(
            connection.execute(
                "SELECT status FROM embedding_generations WHERE generation_id=?",
                (generation_id,),
            ).fetchone()[0]
        )
        heads = int(
            connection.execute("SELECT COUNT(*) FROM published_embedding_heads").fetchone()[
                0
            ]
        )
    assert jobs == (("pending", None, None), ("pending", None, None))
    assert generation_status == "building"
    assert heads == 0


@pytest.mark.parametrize(
    "error_type",
    [RuntimeError, KeyboardInterrupt, _FatalRequestConstruction],
)
def test_request_construction_failure_releases_exact_leases_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    database, generation_id, model = _duplicate_generation(tmp_path)
    backend = _RecordingBackend(model, max_batch_size=2)

    def fail_request(_lease: object) -> EmbeddingRequest:
        raise error_type("injected request construction failure")

    monkeypatch.setattr(
        semantic_generation_worker,
        "embedding_request_from_lease",
        fail_request,
    )
    with pytest.raises(error_type, match="injected request construction failure"):
        run_generation(database, generation_id, backend, queued=2)

    assert backend.calls == []
    with semantic_database(database, readonly=True) as connection:
        jobs = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT status,attempts,lease_owner,lease_until_ns "
                "FROM embedding_jobs ORDER BY job_id"
            )
        )
        generation_status = str(
            connection.execute(
                "SELECT status FROM embedding_generations WHERE generation_id=?",
                (generation_id,),
            ).fetchone()[0]
        )
        heads = int(
            connection.execute("SELECT COUNT(*) FROM published_embedding_heads").fetchone()[
                0
            ]
        )
    assert jobs == (("pending", 1, None, None), ("pending", 1, None, None))
    assert generation_status == "building"
    assert heads == 0
# endregion [02]
