# region [00] Contexto del módulo
# Módulo: tests/test_semantic_generation_publication_v6.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]
# region [01] Dependencias del módulo
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from _04_Nucleo_Operativo import semantic_schema
from _04_Nucleo_Operativo.semantic_chunking import (
    TextChunkingConfig,
    chunk_text_sections,
)
from _04_Nucleo_Operativo.semantic_models import (
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRole,
    ExactSearchQuery,
    SemanticItem,
    TextChunk,
    TextSection,
    encode_vector,
    fingerprint_text,
)
from _04_Nucleo_Operativo.semantic_item_repository import _encode_chunk_text
from _04_Nucleo_Operativo.semantic_state import (
    SemanticStateError,
    claim_embedding_jobs,
    complete_embedding_job,
    enqueue_text_chunk_jobs,
    fail_embedding_job,
    finalize_embedding_generation,
    finalize_text_chunk_refresh,
    has_active_embeddings,
    initialize_semantic_state,
    register_embedding_model,
    resolve_search_hits,
    search_exact_page,
    semantic_database,
    stage_text_chunks,
    start_embedding_generation,
    upsert_semantic_item,
)
# endregion [01]

# region [02] Implementación


def _model() -> EmbeddingModelSpec:
    return EmbeddingModelSpec(
        "publication-model-v1",
        "publication-space-v1",
        EmbeddingModality.TEXT,
        "fixture/publication-model",
        "1",
        4,
        "test-deterministic",
        (EmbeddingRole.QUERY, EmbeddingRole.PASSAGE),
    )


def _initialize(path: Path) -> EmbeddingModelSpec:
    model = _model()
    initialize_semantic_state(path)
    register_embedding_model(path, model, allow_test_provider=True)
    return model


def _stage(path: Path, item_id: str, text: str, revision: int) -> TextChunk:
    item = SemanticItem(
        item_id,
        "pdf",
        f"identity:{item_id}",
        "fixture-v1",
        fingerprint_text(text),
        path=f"C:/fixtures/{item_id}.pdf",
        provenance={"revision": revision},
    )
    upsert_semantic_item(
        path,
        item,
        refresh_token=f"item-r{revision}",
        updated_ns=revision * 10,
    )
    config = TextChunkingConfig(
        max_chars=256,
        max_terms=64,
        overlap_chars=0,
        overlap_terms=0,
        min_natural_break_chars=32,
    )
    chunks = chunk_text_sections(
        item_id,
        (TextSection("pdf_page", "1", text, {"revision": revision}),),
        config,
    )
    assert len(chunks) == 1
    refresh = f"chunk-r{revision}:{item_id}"
    stage_text_chunks(path, chunks, refresh_token=refresh, updated_ns=revision * 10 + 1)
    finalize_text_chunk_refresh(
        path,
        item_id=item_id,
        chunking_signature=config.signature,
        refresh_token=refresh,
        updated_ns=revision * 10 + 2,
    )
    return chunks[0]


def _query(model: EmbeddingModelSpec) -> ExactSearchQuery:
    return ExactSearchQuery(
        model.model_signature,
        model.vector_space,
        model.dimensions,
        (1.0, 0.0, 0.0, 0.0),
        EmbeddingModality.TEXT,
        indexed_model_signatures=(model.model_signature,),
    )


def _complete_jobs(path: Path, generation_id: int, *, now_ns: int) -> None:
    leases = claim_embedding_jobs(
        path,
        generation_id,
        worker_id="publication-worker",
        limit=32,
        lease_seconds=60,
        now_ns=now_ns,
    )
    for offset, lease in enumerate(leases, 1):
        complete_embedding_job(
            path,
            lease.job_id,
            worker_id="publication-worker",
            vector=(1.0, 0.0, 0.0, 0.0),
            provenance={"fixture": "publication"},
            now_ns=now_ns + offset,
        )


def _create_populated_v5(path: Path) -> tuple[EmbeddingModelSpec, TextChunk]:
    """Build a genuine populated v5 fixture from its sequential migrations."""

    model = _model()
    text = "legacy published transformer record"
    fingerprint = fingerprint_text(text)
    config = TextChunkingConfig(
        max_chars=256,
        max_terms=64,
        overlap_chars=0,
        overlap_terms=0,
        min_natural_break_chars=32,
    )
    chunk = chunk_text_sections(
        "legacy-document",
        (TextSection("pdf_page", "1", text),),
        config,
    )[0]
    vector_blob, norm = encode_vector(
        (1.0, 0.0, 0.0, 0.0),
        model.dimensions,
        model.vector_dtype,
    )
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for version in range(1, 6):
            getattr(semantic_schema, f"_migrate_to_v{version}")(
                connection,
                version,
            )
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version','5')"
        )
        connection.execute("PRAGMA user_version=5")
        connection.execute(
            """INSERT INTO vector_spaces(
                vector_space,dimensions,distance,normalization,created_ns)
            VALUES(?,?,'cosine','l2',1)""",
            (model.vector_space, model.dimensions),
        )
        connection.execute(
            """INSERT INTO embedding_models(
                model_signature,vector_space,modality,model_id,model_version,
                dimensions,provider,supported_roles_json,vector_dtype,
                normalization,distance,provenance_json,active,created_ns)
            VALUES(?,?,?,?,?,?,?,?,?,'l2','cosine','{}',1,1)""",
            (
                model.model_signature,
                model.vector_space,
                model.modality.value,
                model.model_id,
                model.model_version,
                model.dimensions,
                model.provider,
                json.dumps(
                    [role.value for role in model.supported_roles],
                    separators=(",", ":"),
                ),
                model.vector_dtype.value,
            ),
        )
        connection.execute(
            """INSERT INTO semantic_items(
                item_id,source_kind,source_identity,identity_version,path,
                content_xxh3_128,content_bytes,content_xxh3_64_guard,
                provenance_json,refresh_token,active,updated_ns,
                source_revision_json)
            VALUES('legacy-document','pdf','legacy-identity','fixture-v1',?,
                ?,?,?,'{}','legacy-refresh',1,2,'{}')""",
            (
                "C:/fixtures/legacy-document.pdf",
                fingerprint.xxh3_128,
                fingerprint.byte_count,
                fingerprint.xxh3_64_guard,
            ),
        )
        connection.execute(
            """INSERT INTO text_chunks(
                chunk_id,item_id,ordinal,section_kind,section_id,start_char,
                end_char,text_zlib,text_chars,content_xxh3_128,content_bytes,
                content_xxh3_64_guard,chunking_signature,provenance_json,
                refresh_token,active,updated_ns)
            VALUES(?,'legacy-document',?,?,?,?,?,?,?,?,?,?,?,'{}',
                'legacy-refresh',1,3)""",
            (
                chunk.chunk_id,
                chunk.ordinal,
                chunk.section_kind,
                chunk.section_id,
                chunk.start_char,
                chunk.end_char,
                _encode_chunk_text(chunk),
                len(chunk.text),
                chunk.fingerprint.xxh3_128,
                chunk.fingerprint.byte_count,
                chunk.fingerprint.xxh3_64_guard,
                chunk.chunking_signature,
            ),
        )
        generation_cursor = connection.execute(
            """INSERT INTO embedding_generations(
                model_signature,processing_signature,status,provenance_json,
                cursor_json,started_ns,completed_ns,done_count)
            VALUES(?,'legacy-run','ready','{}','{}',4,5,1)""",
            (model.model_signature,),
        )
        payload_cursor = connection.execute(
            """INSERT INTO vector_payloads(
                model_signature,content_xxh3_128,content_bytes,
                content_xxh3_64_guard,dimensions,vector_dtype,vector_blob,
                original_norm,provenance_json,created_ns)
            VALUES(?,?,?,?,?,?,?,?,?,6)""",
            (
                model.model_signature,
                chunk.fingerprint.xxh3_128,
                chunk.fingerprint.byte_count,
                chunk.fingerprint.xxh3_64_guard,
                model.dimensions,
                model.vector_dtype.value,
                vector_blob,
                norm,
                '{"fixture":"legacy-v5"}',
            ),
        )
        generation_id = generation_cursor.lastrowid
        payload_id = payload_cursor.lastrowid
        assert generation_id is not None
        assert payload_id is not None
        connection.execute(
            """INSERT INTO text_embeddings(
                chunk_id,model_signature,payload_id,generation_id,
                content_xxh3_128,content_bytes,content_xxh3_64_guard,
                provenance_json,updated_ns)
            VALUES(?,?,?,?,?,?,?,'{"fixture":"legacy-v5"}',7)""",
            (
                chunk.chunk_id,
                model.model_signature,
                payload_id,
                generation_id,
                chunk.fingerprint.xxh3_128,
                chunk.fingerprint.byte_count,
                chunk.fingerprint.xxh3_64_guard,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return model, chunk


def test_building_rows_are_invisible_until_atomic_head_publication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _initialize(database)
    chunks = (
        _stage(database, "document-a", "old transformer record", 1),
        _stage(database, "document-b", "old breaker record", 1),
    )
    generation = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="initial-build",
        started_ns=100,
    )
    enqueue_text_chunk_jobs(
        database,
        generation,
        (chunk.chunk_id for chunk in chunks),
        now_ns=101,
    )
    leases = claim_embedding_jobs(
        database,
        generation,
        worker_id="publication-worker",
        limit=2,
        lease_seconds=60,
        now_ns=102,
    )
    complete_embedding_job(
        database,
        leases[0].job_id,
        worker_id="publication-worker",
        vector=(1.0, 0.0, 0.0, 0.0),
        now_ns=103,
    )
    assert not has_active_embeddings(database, model.model_signature)
    assert search_exact_page(database, _query(model)).hits == ()

    complete_embedding_job(
        database,
        leases[1].job_id,
        worker_id="publication-worker",
        vector=(1.0, 0.0, 0.0, 0.0),
        now_ns=104,
    )
    assert search_exact_page(database, _query(model)).hits == ()

    finalized = finalize_embedding_generation(database, generation, completed_ns=105)
    assert finalized.status == "ready"
    page = search_exact_page(database, _query(model), limit=10)
    assert {hit.item_id for hit in page.hits} == {"document-a", "document-b"}
    assert {hit.generation_id for hit in page.hits} == {generation}


def test_source_change_preserves_old_snapshot_until_successor_is_published(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _initialize(database)
    old_chunk = _stage(database, "document", "old transformer record", 1)
    first = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="first",
        started_ns=100,
    )
    enqueue_text_chunk_jobs(database, first, (old_chunk.chunk_id,), now_ns=101)
    _complete_jobs(database, first, now_ns=102)
    finalize_embedding_generation(database, first, completed_ns=110)
    old_hit = search_exact_page(database, _query(model)).hits[0]

    second = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="second",
        started_ns=120,
    )
    new_chunk = _stage(database, "document", "new breaker record", 2)
    enqueue_text_chunk_jobs(database, second, (new_chunk.chunk_id,), now_ns=121)
    _complete_jobs(database, second, now_ns=122)
    assert search_exact_page(database, _query(model)).hits == (old_hit,)
    old_resolved = resolve_search_hits(database, (old_hit,))[0]
    assert old_resolved.snippet == "old transformer record"
    assert old_resolved.published_revision_id is not None
    assert old_resolved.current_revision_id is not None
    assert old_resolved.published_revision_id != old_resolved.current_revision_id
    assert old_resolved.source_revision_is_current is False
    finalize_embedding_generation(database, second, completed_ns=130)

    current = search_exact_page(database, _query(model)).hits
    assert len(current) == 1
    assert current[0].generation_id == second
    assert current[0].entity_id == new_chunk.chunk_id
    current_resolved = resolve_search_hits(database, current)[0]
    assert current_resolved.snippet == "new breaker record"
    assert current_resolved.published_revision_id is not None
    assert current_resolved.published_revision_id == current_resolved.current_revision_id
    assert current_resolved.source_revision_is_current is True


def test_resolve_preserves_evidence_without_replacement_locator(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _initialize(database)
    text = "published transformer record"
    chunk = _stage(database, "document", text, 1)
    generation = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="published",
        started_ns=100,
    )
    enqueue_text_chunk_jobs(database, generation, (chunk.chunk_id,), now_ns=101)
    _complete_jobs(database, generation, now_ns=102)
    finalize_embedding_generation(database, generation, completed_ns=110)
    hit = search_exact_page(database, _query(model)).hits[0]

    upsert_semantic_item(
        database,
        SemanticItem(
            "document",
            "pdf",
            "identity:replacement",
            "fixture-v1",
            fingerprint_text(text),
            path="C:/fixtures/replacement.pdf",
        ),
        refresh_token="replacement",
        updated_ns=120,
    )

    assert search_exact_page(database, _query(model)).hits == (hit,)
    resolved = resolve_search_hits(database, (hit,))[0]
    assert resolved.source_identity == "identity:document"
    assert resolved.path is None
    assert resolved.snippet == text
    assert resolved.published_revision_id is not None
    assert resolved.current_revision_id is None
    assert resolved.source_revision_is_current is False


def test_resolve_uses_current_path_without_marking_safe_move_stale(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _initialize(database)
    text = "published transformer record"
    chunk = _stage(database, "document", text, 1)
    generation = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="published",
        started_ns=100,
    )
    enqueue_text_chunk_jobs(database, generation, (chunk.chunk_id,), now_ns=101)
    _complete_jobs(database, generation, now_ns=102)
    finalize_embedding_generation(database, generation, completed_ns=110)
    hit = search_exact_page(database, _query(model)).hits[0]

    upsert_semantic_item(
        database,
        SemanticItem(
            "document",
            "pdf",
            "identity:document",
            "fixture-v1",
            fingerprint_text(text),
            path="C:/fixtures/moved-document.pdf",
            provenance={
                "revision": 1,
                "source_status": "  ",
                "analysis_status": "complete",
            },
        ),
        refresh_token="safe-move",
        updated_ns=120,
    )
    moved_generation = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="safe-move",
        started_ns=121,
    )
    enqueue_text_chunk_jobs(
        database,
        moved_generation,
        (chunk.chunk_id,),
        now_ns=122,
    )
    _complete_jobs(database, moved_generation, now_ns=123)
    with semantic_database(database, readonly=True) as connection:
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM semantic_item_revisions WHERE item_id=?",
                ("document",),
            ).fetchone()[0]
        ) == 2

    resolved = resolve_search_hits(database, (hit,))[0]
    assert resolved.path == "C:/fixtures/moved-document.pdf"
    assert resolved.source_status == "complete"
    assert resolved.published_revision_id == resolved.current_revision_id
    assert resolved.source_revision_is_current is True


def test_exact_search_checks_cancellation_at_scan_batches(tmp_path: Path) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _initialize(database)
    chunk = _stage(database, "document", "transformer record", 1)
    generation = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="published",
        started_ns=100,
    )
    enqueue_text_chunk_jobs(database, generation, (chunk.chunk_id,), now_ns=101)
    _complete_jobs(database, generation, now_ns=102)
    finalize_embedding_generation(database, generation, completed_ns=110)
    checkpoints = 0

    class SearchCancelled(Exception):
        pass

    def cancellation_check() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            raise SearchCancelled

    with pytest.raises(SearchCancelled):
        search_exact_page(
            database,
            _query(model),
            cancellation_check=cancellation_check,
        )
    assert checkpoints == 2


def test_partial_and_cas_loser_generations_never_replace_the_published_head(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic.sqlite3"
    model = _initialize(database)
    chunk = _stage(database, "document", "published transformer record", 1)
    initial = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="initial",
        started_ns=100,
    )
    enqueue_text_chunk_jobs(database, initial, (chunk.chunk_id,), now_ns=101)
    _complete_jobs(database, initial, now_ns=102)
    finalize_embedding_generation(database, initial, completed_ns=110)

    partial = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="partial",
        started_ns=120,
    )
    enqueue_text_chunk_jobs(database, partial, (chunk.chunk_id,), now_ns=121)
    lease = claim_embedding_jobs(
        database,
        partial,
        worker_id="publication-worker",
        limit=1,
        lease_seconds=60,
        now_ns=122,
    )[0]
    fail_embedding_job(
        database,
        lease.job_id,
        worker_id="publication-worker",
        error_type="fixture_failure",
        error_message="injected",
        retryable=False,
        now_ns=123,
    )
    summary = finalize_embedding_generation(
        database,
        partial,
        allow_partial=True,
        completed_ns=124,
    )
    assert summary.status == "ready_partial"
    assert {hit.generation_id for hit in search_exact_page(database, _query(model)).hits} == {
        initial
    }

    winner = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="winner",
        started_ns=130,
    )
    loser = start_embedding_generation(
        database,
        model_signature=model.model_signature,
        processing_signature="loser",
        started_ns=131,
    )
    finalize_embedding_generation(database, winner, completed_ns=132)
    with pytest.raises(SemanticStateError, match="must be rebased"):
        finalize_embedding_generation(database, loser, completed_ns=133)
    assert {hit.generation_id for hit in search_exact_page(database, _query(model)).hits} == {
        winner
    }
    with semantic_database(database, readonly=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None


def test_populated_v5_migration_preserves_legacy_rows_and_publishes_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "semantic-v5.sqlite3"
    model, chunk = _create_populated_v5(database)

    initialize_semantic_state(database)

    page = search_exact_page(database, _query(model), limit=10)
    assert tuple(hit.entity_id for hit in page.hits) == (chunk.chunk_id,)
    resolved = resolve_search_hits(database, page.hits)
    assert resolved[0].path == "C:/fixtures/legacy-document.pdf"
    assert resolved[0].snippet == "legacy published transformer record"
    with semantic_database(database, readonly=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM text_embeddings").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM embedding_generation_members"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM published_embedding_heads"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    before = database.read_bytes()
    initialize_semantic_state(database)
    assert database.read_bytes() == before


def test_v6_migration_rolls_back_on_base_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "semantic-v5-rollback.sqlite3"
    _create_populated_v5(database)
    real_migration = semantic_schema._MIGRATIONS_BY_TARGET[6]

    def interrupt_after_migration(
        connection: sqlite3.Connection,
        applied_ns: int,
    ) -> None:
        real_migration(connection, applied_ns)
        raise KeyboardInterrupt("injected migration interruption")

    monkeypatch.setitem(
        semantic_schema._MIGRATIONS_BY_TARGET,
        6,
        interrupt_after_migration,
    )
    with pytest.raises(KeyboardInterrupt, match="injected migration interruption"):
        initialize_semantic_state(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone() == ("5",)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='published_embedding_heads'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM text_embeddings").fetchone()[0] == 1


@pytest.mark.parametrize("unknown_kind", ("table", "column", "index", "trigger"))
def test_v5_migration_abstains_from_unknown_objects_without_mutation(
    tmp_path: Path,
    unknown_kind: str,
) -> None:
    database = tmp_path / f"semantic-v5-{unknown_kind}.sqlite3"
    _create_populated_v5(database)
    with sqlite3.connect(database) as connection:
        if unknown_kind == "table":
            connection.execute("CREATE TABLE vendor_extension(value TEXT)")
            connection.execute(
                "INSERT INTO vendor_extension(value) VALUES('preserve-me')"
            )
        elif unknown_kind == "column":
            connection.execute(
                "ALTER TABLE semantic_items ADD COLUMN vendor_payload TEXT"
            )
        elif unknown_kind == "index":
            connection.execute(
                "CREATE INDEX vendor_semantic_items_idx ON semantic_items(updated_ns)"
            )
        else:
            connection.execute(
                """CREATE TRIGGER vendor_semantic_items_trigger
                AFTER INSERT ON semantic_items BEGIN SELECT 1; END"""
            )
    with sqlite3.connect(database) as connection:
        before_objects = tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )

    with pytest.raises(SemanticStateError, match="unexpected|incompatible"):
        initialize_semantic_state(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM text_embeddings").fetchone()[0] == 1
        if unknown_kind == "table":
            assert connection.execute(
                "SELECT value FROM vendor_extension"
            ).fetchone() == ("preserve-me",)
        assert tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ) == before_objects
# endregion [02]
