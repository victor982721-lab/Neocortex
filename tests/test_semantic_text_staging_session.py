# region [00] Contexto del módulo
# Módulo: tests/test_semantic_text_staging_session.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]
# region [01] Dependencias del módulo
from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from _04_Nucleo_Operativo import semantic_text_index
from _04_Nucleo_Operativo.semantic_chunking import (
    TextChunkingConfig,
    iter_text_chunks,
)
from _04_Nucleo_Operativo.semantic_generation_worker import batches, run_generation
from _04_Nucleo_Operativo.semantic_models import (
    BackendEmbedding,
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRequest,
    EmbeddingRole,
    SemanticItem,
    TextChunk,
    TextSection,
    fingerprint_text,
)
from _04_Nucleo_Operativo.semantic_sources import TextSourceRecord
from _04_Nucleo_Operativo.semantic_state import (
    claim_embedding_jobs,
    complete_embedding_job,
    enqueue_text_chunk_jobs,
    finalize_semantic_item_refresh,
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
    max_chars=128,
    max_terms=32,
    overlap_chars=0,
    overlap_terms=0,
    min_natural_break_chars=32,
)


class _FatalStage(BaseException):
    pass


class _FixtureBackend:
    def __init__(self, model: EmbeddingModelSpec) -> None:
        self._model = model

    @property
    def model(self) -> EmbeddingModelSpec:
        return self._model

    @property
    def max_batch_size(self) -> int:
        return 32

    def embed(
        self,
        requests: Sequence[EmbeddingRequest],
    ) -> Sequence[BackendEmbedding]:
        return tuple(
            BackendEmbedding(
                request.request_id,
                (1.0, 0.0, 0.0, 0.0),
                {"fixture": "staging-resume"},
            )
            for request in requests
        )


def _model() -> EmbeddingModelSpec:
    return EmbeddingModelSpec(
        "staging-model-v1",
        "staging-space-v1",
        EmbeddingModality.TEXT,
        "fixture/staging",
        "1",
        4,
        "test-deterministic",
        (EmbeddingRole.QUERY, EmbeddingRole.PASSAGE),
    )


def _generation(database: Path, processing_signature: str = "staging-run") -> int:
    model = _model()
    initialize_semantic_state(database)
    register_embedding_model(database, model, allow_test_provider=True)
    return start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature=processing_signature,
    )


def _records(
    count: int,
    *,
    sections_per_item: int = 1,
) -> tuple[TextSourceRecord, ...]:
    records: list[TextSourceRecord] = []
    for item_index in range(count):
        identity = f"fixture-{item_index:05d}"
        item = SemanticItem(
            item_id=f"item:pdf:{identity}",
            source_kind="pdf",
            source_identity=identity,
            identity_version="fixture-source-v1",
            fingerprint=fingerprint_text(f"source:{identity}"),
            path=f"C:/fixtures/{identity}.pdf",
            provenance={"fixture": True},
        )
        for section_index in range(sections_per_item):
            text = (
                f"Transformador {item_index} sección {section_index}; "
                "mantenimiento preventivo y pruebas del interruptor."
            )
            records.append(
                TextSourceRecord(
                    item,
                    TextSection(
                        "pdf_page",
                        str(section_index + 1),
                        text,
                        {"page": section_index + 1},
                    ),
                )
            )
    return tuple(records)


def _iterator(
    records: Sequence[TextSourceRecord],
):
    def selected(_state: Path, _source_kind: str) -> Iterator[TextSourceRecord]:
        return iter(records)

    return selected


def _stage(
    database: Path,
    generation_id: int,
    records: Sequence[TextSourceRecord],
    *,
    refresh_token: str = "refresh-1",
    cancellation_check=None,
) -> tuple[int, int, int]:
    return semantic_text_index._stage_source(
        database,
        database.parent,
        "pdf",
        generation_id=generation_id,
        refresh_token=refresh_token,
        chunking=CHUNKING,
        source_record_iterator=_iterator(records),
        cancellation_check=cancellation_check,
    )


def _legacy_stage(
    database: Path,
    generation_id: int,
    records: Sequence[TextSourceRecord],
    *,
    refresh_token: str,
) -> tuple[int, int, int]:
    source_items = chunks_staged = queued = 0
    groups = semantic_text_index.grouped_text_records(
        database.parent,
        "pdf",
        source_record_iterator=_iterator(records),
    )
    for item_id, grouped in groups:
        iterator = iter(grouped)
        first = next(iterator)
        upsert_semantic_item(database, first.item, refresh_token=refresh_token)
        source_items += 1
        sections = itertools.chain(
            (first.section,),
            (record.section for record in iterator),
        )
        chunks = iter_text_chunks(item_id, sections, CHUNKING)
        for batch in batches(
            chunks,
            semantic_text_index.STAGING_BATCH_SIZE,
        ):
            chunks_staged += stage_text_chunks(
                database,
                batch,
                refresh_token=refresh_token,
                batch_size=semantic_text_index.STAGING_BATCH_SIZE,
            )
            queued += enqueue_text_chunk_jobs(
                database,
                generation_id,
                (chunk.chunk_id for chunk in batch),
                batch_size=semantic_text_index.STAGING_BATCH_SIZE,
            )
        finalize_text_chunk_refresh(
            database,
            item_id=item_id,
            chunking_signature=CHUNKING.signature,
            refresh_token=refresh_token,
        )
    finalize_semantic_item_refresh(
        database,
        source_kind="pdf",
        refresh_token=refresh_token,
    )
    return source_items, chunks_staged, queued


def _logical_projection(database: Path) -> tuple[tuple[tuple[object, ...], ...], ...]:
    queries = (
        """SELECT item_id,source_kind,source_identity,identity_version,path,
            content_xxh3_128,content_bytes,content_xxh3_64_guard,
            provenance_json,source_revision_json,refresh_token,active
        FROM semantic_items ORDER BY item_id""",
        """SELECT chunk_id,item_id,ordinal,section_kind,section_id,start_char,
            end_char,text_zlib,text_chars,content_xxh3_128,content_bytes,
            content_xxh3_64_guard,chunking_signature,provenance_json,
            refresh_token,active
        FROM text_chunks ORDER BY chunk_id""",
        """SELECT generation_id,model_signature,role,entity_kind,entity_id,
            item_id,content_xxh3_128,content_bytes,content_xxh3_64_guard,status,
            attempts,max_attempts,lease_owner,lease_until_ns,error_type,error_message
        FROM embedding_jobs ORDER BY entity_kind,entity_id""",
    )
    with semantic_database(database, readonly=True) as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(query).fetchall())
            for query in queries
        )


def _counts(database: Path) -> tuple[int, int, int]:
    with semantic_database(database, readonly=True) as connection:
        return (
            int(connection.execute("SELECT COUNT(*) FROM semantic_items").fetchone()[0]),
            int(connection.execute("SELECT COUNT(*) FROM text_chunks").fetchone()[0]),
            int(connection.execute("SELECT COUNT(*) FROM embedding_jobs").fetchone()[0]),
        )


def _assert_building_unpublished(database: Path, generation_id: int) -> None:
    with semantic_database(database, readonly=True) as connection:
        status = connection.execute(
            "SELECT status FROM embedding_generations WHERE generation_id=?",
            (generation_id,),
        ).fetchone()[0]
        heads = connection.execute(
            "SELECT COUNT(*) FROM published_embedding_heads"
        ).fetchone()[0]
    assert status == "building"
    assert heads == 0


def test_persistent_staging_is_logically_equivalent_to_legacy_flow(
    tmp_path: Path,
) -> None:
    records = _records(7, sections_per_item=3)
    legacy = tmp_path / "legacy.sqlite3"
    candidate = tmp_path / "candidate.sqlite3"
    legacy_generation = _generation(legacy, "legacy")
    candidate_generation = _generation(candidate, "candidate")

    expected = _legacy_stage(
        legacy,
        legacy_generation,
        records,
        refresh_token="equivalent-refresh",
    )
    actual = _stage(
        candidate,
        candidate_generation,
        records,
        refresh_token="equivalent-refresh",
    )

    assert actual == expected
    assert _logical_projection(candidate) == _logical_projection(legacy)


def test_staging_reuses_one_connection_and_commits_bounded_slices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    records = _records(257)
    real_database = semantic_text_index.semantic_database
    observed = {"connections": 0, "commits": 0}

    class ObservedConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def commit(self) -> None:
            observed["commits"] += 1
            self._connection.commit()

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    @contextmanager
    def observed_database(path: Path, *, readonly: bool = False):
        observed["connections"] += 1
        with real_database(path, readonly=readonly) as connection:
            yield ObservedConnection(connection)

    monkeypatch.setattr(semantic_text_index, "semantic_database", observed_database)

    assert _stage(database, generation_id, records) == (257, 257, 257)
    assert observed == {"connections": 1, "commits": 3}
    assert _counts(database) == (257, 257, 257)


def test_restage_preserves_completed_job_and_does_not_duplicate_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    records = _records(1)
    assert _stage(database, generation_id, records) == (1, 1, 1)
    lease = claim_embedding_jobs(
        database,
        generation_id,
        worker_id="fixture-worker",
    )[0]
    complete_embedding_job(
        database,
        lease.job_id,
        worker_id="fixture-worker",
        vector=(1.0, 0.0, 0.0, 0.0),
        provenance={"fixture": True},
    )

    assert _stage(database, generation_id, records) == (1, 1, 1)
    with semantic_database(database, readonly=True) as connection:
        statuses = tuple(
            row[0]
            for row in connection.execute(
                "SELECT status FROM embedding_jobs ORDER BY job_id"
            )
        )
    assert _counts(database) == (1, 1, 1)
    assert statuses == ("done",)


@pytest.mark.parametrize("error_type", [RuntimeError, _FatalStage])
def test_fault_rolls_back_current_slice_and_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)

    def fail_enqueue(*_args: object, **_kwargs: object) -> int:
        raise error_type("injected staging fault")

    monkeypatch.setattr(
        semantic_text_index,
        "_enqueue_text_chunk_batch",
        fail_enqueue,
    )
    with pytest.raises(error_type, match="injected staging fault"):
        _stage(database, generation_id, _records(1))

    assert _counts(database) == (0, 0, 0)
    _assert_building_unpublished(database, generation_id)


def test_partial_crash_keeps_committed_prefix_resumable_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    records = _records(130)
    original_finalize = semantic_text_index._finalize_text_chunk_refresh
    finalized = 0

    def fail_after_committed_prefix(
        connection: sqlite3.Connection,
        *,
        item_id: str,
        chunking_signature: str,
        refresh_token: str,
        updated_ns: int,
    ) -> int:
        nonlocal finalized
        finalized += 1
        if finalized == 129:
            raise RuntimeError("injected partial crash")
        return original_finalize(
            connection,
            item_id=item_id,
            chunking_signature=chunking_signature,
            refresh_token=refresh_token,
            updated_ns=updated_ns,
        )

    monkeypatch.setattr(
        semantic_text_index,
        "_finalize_text_chunk_refresh",
        fail_after_committed_prefix,
    )
    with pytest.raises(RuntimeError, match="injected partial crash"):
        _stage(database, generation_id, records)

    assert _counts(database) == (128, 128, 128)
    _assert_building_unpublished(database, generation_id)

    monkeypatch.setattr(
        semantic_text_index,
        "_finalize_text_chunk_refresh",
        original_finalize,
    )
    assert _stage(database, generation_id, records) == (130, 130, 130)
    assert _counts(database) == (130, 130, 130)
    _assert_building_unpublished(database, generation_id)


def test_crash_resume_then_worker_atomically_publishes_complete_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    records = _records(130)
    original_finalize = semantic_text_index._finalize_text_chunk_refresh
    finalized = 0

    def fail_after_committed_prefix(
        connection: sqlite3.Connection,
        *,
        item_id: str,
        chunking_signature: str,
        refresh_token: str,
        updated_ns: int,
    ) -> int:
        nonlocal finalized
        finalized += 1
        if finalized == 129:
            raise RuntimeError("injected integrated crash")
        return original_finalize(
            connection,
            item_id=item_id,
            chunking_signature=chunking_signature,
            refresh_token=refresh_token,
            updated_ns=updated_ns,
        )

    monkeypatch.setattr(
        semantic_text_index,
        "_finalize_text_chunk_refresh",
        fail_after_committed_prefix,
    )
    with pytest.raises(RuntimeError, match="injected integrated crash"):
        _stage(database, generation_id, records)
    assert _counts(database) == (128, 128, 128)
    _assert_building_unpublished(database, generation_id)

    monkeypatch.setattr(
        semantic_text_index,
        "_finalize_text_chunk_refresh",
        original_finalize,
    )
    assert _stage(database, generation_id, records) == (130, 130, 130)
    work = run_generation(
        database,
        generation_id,
        _FixtureBackend(_model()),
        queued=130,
    )

    assert work.summary.status == "ready"
    assert work.embedded == 130
    assert work.failed == 0
    with semantic_database(database, readonly=True) as connection:
        head = connection.execute(
            "SELECT generation_id FROM published_embedding_heads "
            "WHERE model_signature=?",
            (_model().model_signature,),
        ).fetchone()
        members = connection.execute(
            "SELECT COUNT(*) FROM embedding_generation_members "
            "WHERE generation_id=?",
            (generation_id,),
        ).fetchone()[0]
    assert head is not None
    assert int(head[0]) == generation_id
    assert int(members) == 130


def test_cancellation_rolls_back_current_slice_and_preserves_original_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    armed = False
    original_enqueue = semantic_text_index._enqueue_text_chunk_batch

    def enqueue_and_arm(
        connection: sqlite3.Connection,
        generation_id: int,
        chunk_ids: tuple[str, ...],
        *,
        max_attempts: int = 3,
        now_ns: int,
    ) -> int:
        nonlocal armed
        result = original_enqueue(
            connection,
            generation_id,
            chunk_ids,
            max_attempts=max_attempts,
            now_ns=now_ns,
        )
        armed = True
        return result

    def cancellation_check() -> None:
        if armed:
            raise KeyboardInterrupt("cancel staging")

    monkeypatch.setattr(
        semantic_text_index,
        "_enqueue_text_chunk_batch",
        enqueue_and_arm,
    )
    with pytest.raises(KeyboardInterrupt, match="cancel staging"):
        _stage(
            database,
            generation_id,
            _records(1),
            cancellation_check=cancellation_check,
        )

    assert _counts(database) == (0, 0, 0)
    _assert_building_unpublished(database, generation_id)


def test_single_large_item_is_sliced_without_unbounded_chunk_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    records = _records(1, sections_per_item=260)
    original_stage_batch = semantic_text_index._stage_text_chunk_batch
    observed_batch_sizes: list[int] = []

    def observe_batch(
        connection: sqlite3.Connection,
        chunks: Sequence[TextChunk],
        *,
        refresh_token: str,
        updated_ns: int,
    ) -> int:
        observed_batch_sizes.append(len(chunks))
        return original_stage_batch(
            connection,
            chunks,
            refresh_token=refresh_token,
            updated_ns=updated_ns,
        )

    monkeypatch.setattr(
        semantic_text_index,
        "_stage_text_chunk_batch",
        observe_batch,
    )

    assert _stage(database, generation_id, records) == (1, 260, 260)
    assert observed_batch_sizes == [128, 128, 4]
    assert _counts(database) == (1, 260, 260)


def test_write_lock_is_released_after_every_bounded_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    generation_id = _generation(database)
    records = _records(1, sections_per_item=260)
    real_database = semantic_text_index.semantic_database
    successful_probe_writers = 0

    class ReleasingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def commit(self) -> None:
            nonlocal successful_probe_writers
            self._connection.commit()
            probe = sqlite3.connect(database, timeout=0.1)
            try:
                probe.execute("PRAGMA busy_timeout=100")
                probe.execute("BEGIN IMMEDIATE")
                probe.rollback()
            finally:
                probe.close()
            successful_probe_writers += 1

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    @contextmanager
    def observed_database(
        path: Path,
        *,
        readonly: bool = False,
    ) -> Iterator[ReleasingConnection]:
        with real_database(path, readonly=readonly) as connection:
            yield ReleasingConnection(connection)

    monkeypatch.setattr(semantic_text_index, "semantic_database", observed_database)

    assert _stage(database, generation_id, records) == (1, 260, 260)
    assert successful_probe_writers == 3
    assert _counts(database) == (1, 260, 260)
# endregion [02]
