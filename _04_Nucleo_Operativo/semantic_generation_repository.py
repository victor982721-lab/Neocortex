"""Resumable semantic embedding generations and bounded job queue."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .semantic_item_repository import _decode_chunk_text
from .semantic_models import (
    EmbeddingJobLease,
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRequest,
    EmbeddingRole,
    GenerationSummary,
    SemanticEntityKind,
    canonical_json,
    encode_vector,
)
from .semantic_repository_common import (
    MAX_ERROR_CHARS,
    MAX_WRITE_BATCH,
    StaleEmbeddingJobError,
    _batches,
    _check_batch_size,
    _fingerprint_from_row,
    _load_model,
    _now,
    _same_fingerprint,
)
from .semantic_schema import SemanticStateError, semantic_database

# region [05] Resumable generations and job queue


def _clone_published_members(path: Path, generation_id: int) -> None:
    """Resume a bounded copy of the base head's immutable member references."""

    while True:
        with semantic_database(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = connection.execute(
                """SELECT status,base_generation_id,base_clone_complete
                FROM embedding_generations WHERE generation_id=?""",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise KeyError(f"unknown embedding generation {generation_id}")
            if str(generation["status"]) != "building":
                raise SemanticStateError(
                    f"generation {generation_id} stopped building during base clone"
                )
            if bool(generation["base_clone_complete"]):
                return
            base_generation_id = generation["base_generation_id"]
            if base_generation_id is None:
                connection.execute(
                    "UPDATE embedding_generations SET base_clone_complete=1 "
                    "WHERE generation_id=? AND status='building'",
                    (generation_id,),
                )
                return
            cursor_row = connection.execute(
                """SELECT COALESCE(MAX(base_member_id),0)
                FROM embedding_generation_members WHERE generation_id=?""",
                (generation_id,),
            ).fetchone()
            after_member_id = int(cursor_row[0])
            rows = connection.execute(
                """SELECT member_id,model_signature,entity_kind,entity_id,item_id,
                    item_revision_id,chunk_revision_id,payload_id,
                    content_xxh3_128,content_bytes,content_xxh3_64_guard,
                    provenance_json,updated_ns
                FROM embedding_generation_members
                WHERE generation_id=? AND member_id>?
                ORDER BY member_id LIMIT ?""",
                (int(base_generation_id), after_member_id, MAX_WRITE_BATCH),
            ).fetchall()
            if not rows:
                connection.execute(
                    "UPDATE embedding_generations SET base_clone_complete=1 "
                    "WHERE generation_id=? AND status='building'",
                    (generation_id,),
                )
                return
            connection.executemany(
                """INSERT INTO embedding_generation_members(
                    generation_id,model_signature,entity_kind,entity_id,item_id,
                    item_revision_id,chunk_revision_id,payload_id,
                    content_xxh3_128,content_bytes,content_xxh3_64_guard,
                    provenance_json,updated_ns,base_member_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(generation_id,entity_kind,entity_id) DO NOTHING""",
                (
                    (
                        generation_id,
                        str(row["model_signature"]),
                        str(row["entity_kind"]),
                        str(row["entity_id"]),
                        str(row["item_id"]),
                        int(row["item_revision_id"]),
                        (
                            None
                            if row["chunk_revision_id"] is None
                            else int(row["chunk_revision_id"])
                        ),
                        int(row["payload_id"]),
                        str(row["content_xxh3_128"]),
                        int(row["content_bytes"]),
                        str(row["content_xxh3_64_guard"]),
                        str(row["provenance_json"]),
                        int(row["updated_ns"]),
                        int(row["member_id"]),
                    )
                    for row in rows
                ),
            )


def start_embedding_generation(
    path: Path,
    *,
    model_signature: str,
    processing_signature: str,
    provenance: Mapping[str, object] | None = None,
    cursor: Mapping[str, object] | None = None,
    started_ns: int | None = None,
) -> int:
    """Return an existing compatible building generation or start a new one."""

    if not processing_signature.strip():
        raise ValueError("processing_signature cannot be blank")
    selected_ns = _now(started_ns)
    provenance_json = canonical_json(provenance)
    cursor_json = canonical_json(cursor)
    generation_id: int
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _load_model(connection, model_signature)
        head = connection.execute(
            "SELECT generation_id FROM published_embedding_heads "
            "WHERE model_signature=?",
            (model_signature,),
        ).fetchone()
        base_generation_id = None if head is None else int(head[0])
        existing = connection.execute(
            """SELECT generation_id,provenance_json,base_generation_id,
                base_clone_complete FROM embedding_generations
            WHERE model_signature=? AND processing_signature=? AND status='building'
            ORDER BY generation_id DESC LIMIT 1""",
            (model_signature, processing_signature),
        ).fetchone()
        if existing is not None:
            if str(existing["provenance_json"]) != provenance_json:
                raise ValueError("resumed generation provenance does not match")
            generation_id = int(existing["generation_id"])
            if (
                existing["base_generation_id"] is None
                and not bool(existing["base_clone_complete"])
            ):
                connection.execute(
                    """UPDATE embedding_generations
                    SET base_generation_id=?,base_clone_complete=?
                    WHERE generation_id=? AND status='building'""",
                    (
                        base_generation_id,
                        int(base_generation_id is None),
                        generation_id,
                    ),
                )
        else:
            cursor_row = connection.execute(
                """INSERT INTO embedding_generations(
                    model_signature,processing_signature,status,provenance_json,
                    cursor_json,started_ns,base_generation_id,base_clone_complete)
                VALUES(?,?,'building',?,?,?,?,?)""",
                (
                    model_signature,
                    processing_signature,
                    provenance_json,
                    cursor_json,
                    selected_ns,
                    base_generation_id,
                    int(base_generation_id is None),
                ),
            )
            if cursor_row.lastrowid is None:
                raise SemanticStateError(
                    "generation insert did not return an identifier"
                )
            generation_id = int(cursor_row.lastrowid)
    _clone_published_members(path, generation_id)
    return generation_id


def _generation_model(
    connection: sqlite3.Connection,
    generation_id: int,
    *,
    require_building: bool,
) -> EmbeddingModelSpec:
    row = connection.execute(
        "SELECT model_signature,status FROM embedding_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown embedding generation {generation_id}")
    if require_building and str(row["status"]) != "building":
        raise SemanticStateError(f"generation {generation_id} is not building")
    return _load_model(connection, str(row["model_signature"]))


def _queue_job_rows(
    connection: sqlite3.Connection,
    *,
    generation_id: int,
    model: EmbeddingModelSpec,
    entity_kind: SemanticEntityKind,
    role: EmbeddingRole,
    identifiers: tuple[str, ...],
    max_attempts: int,
    now_ns: int,
) -> int:
    placeholders = ",".join("?" for _ in identifiers)
    if entity_kind is SemanticEntityKind.TEXT_CHUNK:
        rows = connection.execute(
            f"""SELECT c.chunk_id AS entity_id,c.item_id,
                c.content_xxh3_128,c.content_bytes,c.content_xxh3_64_guard
            FROM text_chunks c JOIN semantic_items i ON i.item_id=c.item_id
            WHERE c.active=1 AND i.active=1 AND c.chunk_id IN ({placeholders})""",
            identifiers,
        ).fetchall()
    else:
        rows = connection.execute(
            f"""SELECT item_id AS entity_id,item_id,content_xxh3_128,
                content_bytes,content_xxh3_64_guard
            FROM semantic_items WHERE active=1 AND path IS NOT NULL
                AND item_id IN ({placeholders})""",
            identifiers,
        ).fetchall()
    found = {str(row["entity_id"]) for row in rows}
    missing = set(identifiers).difference(found)
    if missing:
        raise KeyError(f"unknown, inactive or payload-less entities: {sorted(missing)}")
    connection.executemany(
        """INSERT INTO embedding_jobs(
                generation_id,model_signature,role,entity_kind,entity_id,item_id,
                content_xxh3_128,content_bytes,content_xxh3_64_guard,status,
                max_attempts,available_ns,created_ns,updated_ns)
            VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)
            ON CONFLICT(generation_id,entity_kind,entity_id) DO UPDATE SET
                item_id=excluded.item_id,
                content_xxh3_128=excluded.content_xxh3_128,
                content_bytes=excluded.content_bytes,
                content_xxh3_64_guard=excluded.content_xxh3_64_guard,
                status=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN 'pending' ELSE embedding_jobs.status END,
                attempts=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN 0 ELSE embedding_jobs.attempts END,
                max_attempts=excluded.max_attempts,
                available_ns=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN excluded.available_ns ELSE embedding_jobs.available_ns END,
                lease_owner=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN NULL ELSE embedding_jobs.lease_owner END,
                lease_until_ns=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN NULL ELSE embedding_jobs.lease_until_ns END,
                error_type=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN NULL ELSE embedding_jobs.error_type END,
                error_message=CASE WHEN
                    embedding_jobs.content_xxh3_128<>excluded.content_xxh3_128 OR
                    embedding_jobs.content_bytes<>excluded.content_bytes OR
                    embedding_jobs.content_xxh3_64_guard<>excluded.content_xxh3_64_guard
                    THEN NULL ELSE embedding_jobs.error_message END,
                updated_ns=excluded.updated_ns""",
        (
            (
                generation_id,
                model.model_signature,
                role.value,
                entity_kind.value,
                str(row["entity_id"]),
                str(row["item_id"]),
                str(row["content_xxh3_128"]),
                int(row["content_bytes"]),
                str(row["content_xxh3_64_guard"]),
                max_attempts,
                now_ns,
                now_ns,
                now_ns,
            )
            for row in rows
        ),
    )
    return len(rows)


def _enqueue_job_batch(
    connection: sqlite3.Connection,
    generation_id: int,
    identifiers: tuple[str, ...],
    *,
    entity_kind: SemanticEntityKind,
    expected_modality: EmbeddingModality,
    role: EmbeddingRole,
    max_attempts: int,
    now_ns: int,
) -> int:
    """Queue one already-bounded entity batch in the caller's transaction."""

    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    if len(identifiers) > MAX_WRITE_BATCH:
        raise ValueError(
            f"embedding job batch cannot exceed {MAX_WRITE_BATCH} identifiers"
        )
    batch = tuple(dict.fromkeys(identifiers))
    if any(not identifier.strip() for identifier in batch):
        raise ValueError("entity identifiers cannot be blank")
    if not batch:
        return 0
    model = _generation_model(
        connection,
        generation_id,
        require_building=True,
    )
    if model.modality is not expected_modality or role not in model.supported_roles:
        raise ValueError("generation model is incompatible with requested entities")
    return _queue_job_rows(
        connection,
        generation_id=generation_id,
        model=model,
        entity_kind=entity_kind,
        role=role,
        identifiers=batch,
        max_attempts=max_attempts,
        now_ns=now_ns,
    )


def _enqueue_text_chunk_batch(
    connection: sqlite3.Connection,
    generation_id: int,
    chunk_ids: tuple[str, ...],
    *,
    max_attempts: int = 3,
    now_ns: int,
) -> int:
    """Queue one bounded text-chunk batch without opening another connection."""

    return _enqueue_job_batch(
        connection,
        generation_id,
        chunk_ids,
        entity_kind=SemanticEntityKind.TEXT_CHUNK,
        expected_modality=EmbeddingModality.TEXT,
        role=EmbeddingRole.PASSAGE,
        max_attempts=max_attempts,
        now_ns=now_ns,
    )


def _enqueue_jobs(
    path: Path,
    generation_id: int,
    identifiers: Iterable[str],
    *,
    entity_kind: SemanticEntityKind,
    expected_modality: EmbeddingModality,
    role: EmbeddingRole,
    max_attempts: int,
    batch_size: int,
    now_ns: int | None,
) -> int:
    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    _check_batch_size(batch_size)
    selected_ns = _now(now_ns)
    changed = 0
    for raw_batch in _batches(identifiers, batch_size):
        with semantic_database(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed += _enqueue_job_batch(
                connection,
                generation_id,
                raw_batch,
                entity_kind=entity_kind,
                expected_modality=expected_modality,
                role=role,
                max_attempts=max_attempts,
                now_ns=selected_ns,
            )
    return changed


def enqueue_text_chunk_jobs(
    path: Path,
    generation_id: int,
    chunk_ids: Iterable[str],
    *,
    max_attempts: int = 3,
    batch_size: int = MAX_WRITE_BATCH,
    now_ns: int | None = None,
) -> int:
    return _enqueue_jobs(
        path,
        generation_id,
        chunk_ids,
        entity_kind=SemanticEntityKind.TEXT_CHUNK,
        expected_modality=EmbeddingModality.TEXT,
        role=EmbeddingRole.PASSAGE,
        max_attempts=max_attempts,
        batch_size=batch_size,
        now_ns=now_ns,
    )


def enqueue_image_item_jobs(
    path: Path,
    generation_id: int,
    item_ids: Iterable[str],
    *,
    max_attempts: int = 3,
    batch_size: int = MAX_WRITE_BATCH,
    now_ns: int | None = None,
) -> int:
    return _enqueue_jobs(
        path,
        generation_id,
        item_ids,
        entity_kind=SemanticEntityKind.IMAGE_ITEM,
        expected_modality=EmbeddingModality.IMAGE,
        role=EmbeddingRole.IMAGE,
        max_attempts=max_attempts,
        batch_size=batch_size,
        now_ns=now_ns,
    )


def _mark_stale_jobs(
    connection: sqlite3.Connection,
    generation_id: int,
    modality: EmbeddingModality,
    now_ns: int,
) -> None:
    if modality is EmbeddingModality.TEXT:
        current_match = """EXISTS(
            SELECT 1 FROM text_chunks c
            JOIN semantic_items i ON i.item_id=c.item_id
            WHERE c.chunk_id=embedding_jobs.entity_id AND c.active=1 AND i.active=1
              AND c.content_xxh3_128=embedding_jobs.content_xxh3_128
              AND c.content_bytes=embedding_jobs.content_bytes
              AND c.content_xxh3_64_guard=embedding_jobs.content_xxh3_64_guard)"""
    else:
        current_match = """EXISTS(
            SELECT 1 FROM semantic_items i
            WHERE i.item_id=embedding_jobs.entity_id AND i.active=1
              AND i.path IS NOT NULL
              AND i.content_xxh3_128=embedding_jobs.content_xxh3_128
              AND i.content_bytes=embedding_jobs.content_bytes
              AND i.content_xxh3_64_guard=embedding_jobs.content_xxh3_64_guard)"""
    connection.execute(
        f"""UPDATE embedding_jobs SET status='stale',lease_owner=NULL,
            lease_until_ns=NULL,error_type='source_changed',
            error_message='source changed or became inactive before embedding',updated_ns=?
        WHERE generation_id=? AND status IN ('pending','leased') AND NOT {current_match}""",
        (now_ns, generation_id),
    )


def _snapshot_item_revision(
    connection: sqlite3.Connection,
    item_id: str,
    now_ns: int,
) -> int:
    item = connection.execute(
        """SELECT item_id,source_kind,source_identity,identity_version,path,
            content_xxh3_128,content_bytes,content_xxh3_64_guard,
            provenance_json,source_revision_json
        FROM semantic_items WHERE item_id=? AND active=1""",
        (item_id,),
    ).fetchone()
    if item is None:
        raise StaleEmbeddingJobError("semantic item became inactive before snapshot")
    existing = connection.execute(
        """SELECT item_revision_id FROM semantic_item_revisions
        WHERE item_id=? AND source_kind=? AND source_identity=?
          AND identity_version=? AND path IS ? AND content_xxh3_128=?
          AND content_bytes=? AND content_xxh3_64_guard=?
          AND provenance_json=? AND source_revision_json=?
        ORDER BY item_revision_id DESC LIMIT 1""",
        (
            str(item["item_id"]),
            str(item["source_kind"]),
            str(item["source_identity"]),
            str(item["identity_version"]),
            None if item["path"] is None else str(item["path"]),
            str(item["content_xxh3_128"]),
            int(item["content_bytes"]),
            str(item["content_xxh3_64_guard"]),
            str(item["provenance_json"]),
            str(item["source_revision_json"]),
        ),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    cursor = connection.execute(
        """INSERT INTO semantic_item_revisions(
            item_id,source_kind,source_identity,identity_version,path,
            content_xxh3_128,content_bytes,content_xxh3_64_guard,
            provenance_json,source_revision_json,captured_ns)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(item["item_id"]),
            str(item["source_kind"]),
            str(item["source_identity"]),
            str(item["identity_version"]),
            None if item["path"] is None else str(item["path"]),
            str(item["content_xxh3_128"]),
            int(item["content_bytes"]),
            str(item["content_xxh3_64_guard"]),
            str(item["provenance_json"]),
            str(item["source_revision_json"]),
            now_ns,
        ),
    )
    if cursor.lastrowid is None:
        raise SemanticStateError("item revision insert returned no identifier")
    return int(cursor.lastrowid)


def _snapshot_chunk_revision(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    now_ns: int,
) -> int:
    chunk = connection.execute(
        """SELECT chunk_id,item_id,ordinal,section_kind,section_id,start_char,
            end_char,text_zlib,text_chars,content_xxh3_128,content_bytes,
            content_xxh3_64_guard,chunking_signature,provenance_json
        FROM text_chunks WHERE chunk_id=? AND active=1""",
        (str(row["entity_id"]),),
    ).fetchone()
    if chunk is None or not _same_fingerprint(chunk, _fingerprint_from_row(row)):
        raise StaleEmbeddingJobError("text chunk changed before snapshot")
    existing = connection.execute(
        "SELECT * FROM semantic_chunk_revisions WHERE chunk_id=?",
        (str(chunk["chunk_id"]),),
    ).fetchone()
    values = (
        str(chunk["item_id"]),
        int(chunk["ordinal"]),
        str(chunk["section_kind"]),
        str(chunk["section_id"]),
        int(chunk["start_char"]),
        int(chunk["end_char"]),
        bytes(chunk["text_zlib"]),
        int(chunk["text_chars"]),
        str(chunk["content_xxh3_128"]),
        int(chunk["content_bytes"]),
        str(chunk["content_xxh3_64_guard"]),
        str(chunk["chunking_signature"]),
        str(chunk["provenance_json"]),
    )
    if existing is not None:
        persisted = (
            str(existing["item_id"]),
            int(existing["ordinal"]),
            str(existing["section_kind"]),
            str(existing["section_id"]),
            int(existing["start_char"]),
            int(existing["end_char"]),
            bytes(existing["text_zlib"]),
            int(existing["text_chars"]),
            str(existing["content_xxh3_128"]),
            int(existing["content_bytes"]),
            str(existing["content_xxh3_64_guard"]),
            str(existing["chunking_signature"]),
            str(existing["provenance_json"]),
        )
        if persisted != values:
            raise SemanticStateError(
                "content-addressed chunk id is bound to different snapshot data"
            )
        return int(existing["chunk_revision_id"])
    cursor = connection.execute(
        """INSERT INTO semantic_chunk_revisions(
            chunk_id,item_id,ordinal,section_kind,section_id,start_char,end_char,
            text_zlib,text_chars,content_xxh3_128,content_bytes,
            content_xxh3_64_guard,chunking_signature,provenance_json,captured_ns)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(chunk["chunk_id"]), *values, now_ns),
    )
    if cursor.lastrowid is None:
        raise SemanticStateError("chunk revision insert returned no identifier")
    return int(cursor.lastrowid)


def _attach_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    payload_id: int,
    provenance_json: str,
    now_ns: int,
) -> None:
    generation = connection.execute(
        """SELECT status,model_signature,base_clone_complete
        FROM embedding_generations WHERE generation_id=?""",
        (int(row["generation_id"]),),
    ).fetchone()
    if (
        generation is None
        or str(generation["status"]) != "building"
        or str(generation["model_signature"]) != str(row["model_signature"])
        or not bool(generation["base_clone_complete"])
    ):
        raise SemanticStateError(
            "embedding result cannot attach outside a cloned building generation"
        )
    item_revision_id = _snapshot_item_revision(
        connection,
        str(row["item_id"]),
        now_ns,
    )
    chunk_revision_id = (
        _snapshot_chunk_revision(connection, row, now_ns)
        if str(row["entity_kind"]) == SemanticEntityKind.TEXT_CHUNK.value
        else None
    )
    values = (
        str(row["entity_id"]),
        str(row["model_signature"]),
        payload_id,
        int(row["generation_id"]),
        str(row["content_xxh3_128"]),
        int(row["content_bytes"]),
        str(row["content_xxh3_64_guard"]),
        provenance_json,
        now_ns,
    )
    if str(row["entity_kind"]) == SemanticEntityKind.TEXT_CHUNK.value:
        connection.execute(
            """INSERT INTO text_embeddings(
                chunk_id,model_signature,payload_id,generation_id,
                content_xxh3_128,content_bytes,content_xxh3_64_guard,
                provenance_json,updated_ns)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(chunk_id,model_signature) DO UPDATE SET
                payload_id=excluded.payload_id,
                generation_id=excluded.generation_id,
                content_xxh3_128=excluded.content_xxh3_128,
                content_bytes=excluded.content_bytes,
                content_xxh3_64_guard=excluded.content_xxh3_64_guard,
                provenance_json=excluded.provenance_json,
                updated_ns=excluded.updated_ns""",
            values,
        )

    else:
        connection.execute(
            """INSERT INTO image_embeddings(
                item_id,model_signature,payload_id,generation_id,
                content_xxh3_128,content_bytes,content_xxh3_64_guard,
                provenance_json,updated_ns)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_id,model_signature) DO UPDATE SET
                payload_id=excluded.payload_id,
                generation_id=excluded.generation_id,
                content_xxh3_128=excluded.content_xxh3_128,
                content_bytes=excluded.content_bytes,
                content_xxh3_64_guard=excluded.content_xxh3_64_guard,
                provenance_json=excluded.provenance_json,
                updated_ns=excluded.updated_ns""",
            values,
        )

    connection.execute(
        """INSERT INTO embedding_generation_members(
            generation_id,model_signature,entity_kind,entity_id,item_id,
            item_revision_id,chunk_revision_id,payload_id,content_xxh3_128,
            content_bytes,content_xxh3_64_guard,provenance_json,updated_ns,
            base_member_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
        ON CONFLICT(generation_id,entity_kind,entity_id) DO UPDATE SET
            item_id=excluded.item_id,
            item_revision_id=excluded.item_revision_id,
            chunk_revision_id=excluded.chunk_revision_id,
            payload_id=excluded.payload_id,
            content_xxh3_128=excluded.content_xxh3_128,
            content_bytes=excluded.content_bytes,
            content_xxh3_64_guard=excluded.content_xxh3_64_guard,
            provenance_json=excluded.provenance_json,
            updated_ns=excluded.updated_ns,
            base_member_id=NULL""",
        (
            int(row["generation_id"]),
            str(row["model_signature"]),
            str(row["entity_kind"]),
            str(row["entity_id"]),
            str(row["item_id"]),
            item_revision_id,
            chunk_revision_id,
            payload_id,
            str(row["content_xxh3_128"]),
            int(row["content_bytes"]),
            str(row["content_xxh3_64_guard"]),
            provenance_json,
            now_ns,
        ),
    )


def reuse_cached_jobs(
    path: Path,
    generation_id: int,
    *,
    limit: int = MAX_WRITE_BATCH,
    now_ns: int | None = None,
) -> int:
    """Attach identical model/content payloads without invoking a backend."""

    if not 1 <= limit <= MAX_WRITE_BATCH:
        raise ValueError(f"limit must be between 1 and {MAX_WRITE_BATCH}")
    selected_ns = _now(now_ns)
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        model = _generation_model(connection, generation_id, require_building=True)
        _mark_stale_jobs(connection, generation_id, model.modality, selected_ns)
        rows = connection.execute(
            """SELECT j.*,p.payload_id,p.provenance_json AS payload_provenance_json
            FROM embedding_jobs j JOIN vector_payloads p
              ON p.model_signature=j.model_signature
             AND p.content_xxh3_128=j.content_xxh3_128
             AND p.content_bytes=j.content_bytes
             AND p.content_xxh3_64_guard=j.content_xxh3_64_guard
            WHERE j.generation_id=? AND j.status='pending'
            ORDER BY j.job_id LIMIT ?""",
            (generation_id, limit),
        ).fetchall()
        for row in rows:
            _attach_payload(
                connection,
                row,
                int(row["payload_id"]),
                canonical_json(
                    {
                        "reuse": "exact-xxh3-content",
                        "payload_provenance": json.loads(
                            str(row["payload_provenance_json"])
                        ),
                    }
                ),
                selected_ns,
            )
            connection.execute(
                "UPDATE embedding_jobs SET status='done',lease_owner=NULL,"
                "lease_until_ns=NULL,error_type=NULL,error_message=NULL,updated_ns=? "
                "WHERE job_id=?",
                (selected_ns, int(row["job_id"])),
            )
        return len(rows)


def _lease_rows(
    connection: sqlite3.Connection,
    generation_id: int,
    modality: EmbeddingModality,
    now_ns: int,
    limit: int,
) -> list[sqlite3.Row]:
    common = """j.generation_id=? AND j.attempts<j.max_attempts AND
        ((j.status='pending' AND j.available_ns<=?) OR
         (j.status='leased' AND j.lease_until_ns<=?))"""
    if modality is EmbeddingModality.TEXT:
        return connection.execute(
            f"""SELECT j.*,m.vector_space,c.text_zlib
            FROM embedding_jobs j
            JOIN embedding_models m ON m.model_signature=j.model_signature
            JOIN text_chunks c ON c.chunk_id=j.entity_id
            JOIN semantic_items i ON i.item_id=c.item_id
            WHERE {common} AND c.active=1 AND i.active=1
              AND c.content_xxh3_128=j.content_xxh3_128
              AND c.content_bytes=j.content_bytes
              AND c.content_xxh3_64_guard=j.content_xxh3_64_guard
            ORDER BY j.job_id LIMIT ?""",
            (generation_id, now_ns, now_ns, limit),
        ).fetchall()
    return connection.execute(
        f"""SELECT j.*,m.vector_space,i.path,i.source_revision_json
        FROM embedding_jobs j
        JOIN embedding_models m ON m.model_signature=j.model_signature
        JOIN semantic_items i ON i.item_id=j.entity_id
        WHERE {common} AND i.active=1 AND i.path IS NOT NULL
          AND i.content_xxh3_128=j.content_xxh3_128
          AND i.content_bytes=j.content_bytes
          AND i.content_xxh3_64_guard=j.content_xxh3_64_guard
        ORDER BY j.job_id LIMIT ?""",
        (generation_id, now_ns, now_ns, limit),
    ).fetchall()


def claim_embedding_jobs(
    path: Path,
    generation_id: int,
    *,
    worker_id: str,
    limit: int = 32,
    lease_seconds: float = 300.0,
    now_ns: int | None = None,
) -> tuple[EmbeddingJobLease, ...]:
    """Atomically lease bounded jobs, including their text/path payloads."""

    if not worker_id.strip():
        raise ValueError("worker_id cannot be blank")
    if not 1 <= limit <= MAX_WRITE_BATCH:
        raise ValueError(f"limit must be between 1 and {MAX_WRITE_BATCH}")
    if not math.isfinite(lease_seconds) or not 1.0 <= lease_seconds <= 86_400.0:
        raise ValueError("lease_seconds must be between 1 and 86400")
    selected_ns = _now(now_ns)
    lease_until_ns = selected_ns + int(lease_seconds * 1_000_000_000)
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        model = _generation_model(connection, generation_id, require_building=True)
        _mark_stale_jobs(connection, generation_id, model.modality, selected_ns)
        connection.execute(
            """UPDATE embedding_jobs SET status='error',lease_owner=NULL,
                lease_until_ns=NULL,error_type='lease_expired',
                error_message='maximum attempts reached after lease expiration',updated_ns=?
            WHERE generation_id=? AND status='leased' AND lease_until_ns<=?
              AND attempts>=max_attempts""",
            (selected_ns, generation_id, selected_ns),
        )
        rows = _lease_rows(
            connection,
            generation_id,
            model.modality,
            selected_ns,
            limit,
        )
        if not rows:
            return ()
        job_ids = tuple(int(row["job_id"]) for row in rows)
        placeholders = ",".join("?" for _ in job_ids)
        connection.execute(
            f"""UPDATE embedding_jobs SET status='leased',attempts=attempts+1,
                lease_owner=?,lease_until_ns=?,updated_ns=?
            WHERE job_id IN ({placeholders})""",
            (worker_id, lease_until_ns, selected_ns, *job_ids),
        )
        leases: list[EmbeddingJobLease] = []
        for row in rows:
            fingerprint = _fingerprint_from_row(row)
            text = (
                _decode_chunk_text(bytes(row["text_zlib"]), fingerprint)
                if model.modality is EmbeddingModality.TEXT
                else None
            )
            image_path = (
                Path(str(row["path"]))
                if model.modality is EmbeddingModality.IMAGE
                else None
            )
            source_revision: Mapping[str, object] = {}
            if model.modality is EmbeddingModality.IMAGE:
                raw_revision = json.loads(str(row["source_revision_json"]))
                if not isinstance(raw_revision, dict):
                    raise SemanticStateError("source revision is not a JSON object")
                source_revision = raw_revision
            leases.append(
                EmbeddingJobLease(
                    job_id=int(row["job_id"]),
                    generation_id=generation_id,
                    model_signature=model.model_signature,
                    vector_space=str(row["vector_space"]),
                    modality=model.modality,
                    role=EmbeddingRole(str(row["role"])),
                    entity_kind=SemanticEntityKind(str(row["entity_kind"])),
                    entity_id=str(row["entity_id"]),
                    item_id=str(row["item_id"]),
                    fingerprint=fingerprint,
                    attempt=int(row["attempts"]) + 1,
                    lease_until_ns=lease_until_ns,
                    text=text,
                    image_path=image_path,
                    source_revision=source_revision,
                )
            )
        return tuple(leases)


def embedding_request_from_lease(lease: EmbeddingJobLease) -> EmbeddingRequest:
    """Convert a durable lease into the exact backend request contract."""

    return EmbeddingRequest(
        request_id=str(lease.job_id),
        role=lease.role,
        fingerprint=lease.fingerprint,
        text=lease.text,
        image_path=lease.image_path,
        source_revision=lease.source_revision,
    )


def heartbeat_embedding_jobs(
    path: Path,
    job_ids: Iterable[int],
    *,
    worker_id: str,
    lease_seconds: float = 300.0,
    now_ns: int | None = None,
) -> int:
    """Atomically extend one bounded set of live leases owned by one worker."""

    selected_ids = tuple(job_ids)
    if not selected_ids:
        raise ValueError("heartbeat job_ids cannot be empty")
    if len(selected_ids) > MAX_WRITE_BATCH:
        raise ValueError(f"heartbeat cannot exceed {MAX_WRITE_BATCH} embedding jobs")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("heartbeat job_ids must be unique")
    if any(
        isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1
        for job_id in selected_ids
    ):
        raise ValueError("heartbeat job_ids must be positive integers")
    if not worker_id.strip():
        raise ValueError("worker_id cannot be blank")
    if not math.isfinite(lease_seconds) or not 1.0 <= lease_seconds <= 86_400.0:
        raise ValueError("lease_seconds must be between 1 and 86400")
    selected_ns = _now(now_ns)
    until = selected_ns + int(lease_seconds * 1_000_000_000)
    placeholders = ",".join("?" for _ in selected_ids)
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            f"""UPDATE embedding_jobs
            SET lease_until_ns=MAX(lease_until_ns,?),updated_ns=?
            WHERE job_id IN ({placeholders}) AND status='leased'
              AND lease_owner=? AND lease_until_ns>?""",
            (until, selected_ns, *selected_ids, worker_id, selected_ns),
        )
        if cursor.rowcount != len(selected_ids):
            raise SemanticStateError(
                "one or more job leases are absent, expired or owned elsewhere"
            )
    return until


def heartbeat_embedding_job(
    path: Path,
    job_id: int,
    *,
    worker_id: str,
    lease_seconds: float = 300.0,
    now_ns: int | None = None,
) -> int:
    """Extend a live lease owned by exactly one worker."""

    return heartbeat_embedding_jobs(
        path,
        (job_id,),
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now_ns=now_ns,
    )


def _job_is_current(connection: sqlite3.Connection, row: sqlite3.Row) -> bool:
    if str(row["entity_kind"]) == SemanticEntityKind.TEXT_CHUNK.value:
        current = connection.execute(
            """SELECT c.content_xxh3_128,c.content_bytes,c.content_xxh3_64_guard
            FROM text_chunks c JOIN semantic_items i ON i.item_id=c.item_id
            WHERE c.chunk_id=? AND c.active=1 AND i.active=1""",
            (str(row["entity_id"]),),
        ).fetchone()
    else:
        current = connection.execute(
            """SELECT content_xxh3_128,content_bytes,content_xxh3_64_guard
            FROM semantic_items WHERE item_id=? AND active=1 AND path IS NOT NULL""",
            (str(row["entity_id"]),),
        ).fetchone()
    return current is not None and _same_fingerprint(
        current, _fingerprint_from_row(row)
    )


def complete_embedding_job(
    path: Path,
    job_id: int,
    *,
    worker_id: str,
    vector: Sequence[float],
    provenance: Mapping[str, object] | None = None,
    now_ns: int | None = None,
) -> int:
    """Atomically persist/reuse a compact vector and complete its lease."""

    if not worker_id.strip():
        raise ValueError("worker_id cannot be blank")
    selected_ns = _now(now_ns)
    provenance_json = canonical_json(provenance)
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT j.* FROM embedding_jobs j WHERE j.job_id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown embedding job {job_id}")
        if (
            str(row["status"]) != "leased"
            or str(row["lease_owner"]) != worker_id
            or row["lease_until_ns"] is None
            or int(row["lease_until_ns"]) <= selected_ns
        ):
            raise SemanticStateError("job lease is absent, expired or owned elsewhere")
        if not _job_is_current(connection, row):
            connection.execute(
                """UPDATE embedding_jobs SET status='stale',lease_owner=NULL,
                lease_until_ns=NULL,error_type='source_changed',
                error_message='source changed before vector completion',updated_ns=?
                WHERE job_id=?""",
                (selected_ns, job_id),
            )
            connection.commit()
            raise StaleEmbeddingJobError("source changed before vector completion")
        model = _load_model(connection, str(row["model_signature"]))
        vector_blob, original_norm = encode_vector(
            vector,
            model.dimensions,
            model.vector_dtype,
        )
        connection.execute(
            """INSERT INTO vector_payloads(
                model_signature,content_xxh3_128,content_bytes,
                content_xxh3_64_guard,dimensions,vector_dtype,vector_blob,
                original_norm,provenance_json,created_ns)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(model_signature,content_xxh3_128,content_bytes,
                        content_xxh3_64_guard) DO NOTHING""",
            (
                model.model_signature,
                str(row["content_xxh3_128"]),
                int(row["content_bytes"]),
                str(row["content_xxh3_64_guard"]),
                model.dimensions,
                model.vector_dtype.value,
                vector_blob,
                original_norm,
                provenance_json,
                selected_ns,
            ),
        )
        payload = connection.execute(
            """SELECT payload_id FROM vector_payloads
            WHERE model_signature=? AND content_xxh3_128=? AND content_bytes=?
              AND content_xxh3_64_guard=?""",
            (
                model.model_signature,
                str(row["content_xxh3_128"]),
                int(row["content_bytes"]),
                str(row["content_xxh3_64_guard"]),
            ),
        ).fetchone()
        if payload is None:
            raise SemanticStateError("vector payload upsert did not produce a row")
        payload_id = int(payload["payload_id"])
        _attach_payload(
            connection,
            row,
            payload_id,
            provenance_json,
            selected_ns,
        )
        connection.execute(
            """UPDATE embedding_jobs SET status='done',lease_owner=NULL,
            lease_until_ns=NULL,error_type=NULL,error_message=NULL,updated_ns=?
            WHERE job_id=?""",
            (selected_ns, job_id),
        )
        return payload_id


def fail_embedding_job(
    path: Path,
    job_id: int,
    *,
    worker_id: str,
    error_type: str,
    error_message: str,
    retryable: bool,
    retry_delay_seconds: float = 0.0,
    now_ns: int | None = None,
) -> str:
    """Release a failed lease to a bounded retry or a terminal error."""

    if not worker_id.strip() or not error_type.strip():
        raise ValueError("worker_id and error_type cannot be blank")
    if (
        not math.isfinite(retry_delay_seconds)
        or retry_delay_seconds < 0
        or retry_delay_seconds > 86_400
    ):
        raise ValueError("retry_delay_seconds must be between 0 and 86400")
    selected_ns = _now(now_ns)
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT attempts,max_attempts,status,lease_owner,lease_until_ns
            FROM embedding_jobs WHERE job_id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown embedding job {job_id}")
        if (
            str(row["status"]) != "leased"
            or str(row["lease_owner"]) != worker_id
            or row["lease_until_ns"] is None
            or int(row["lease_until_ns"]) <= selected_ns
        ):
            raise SemanticStateError("job lease is absent, expired or owned elsewhere")
        should_retry = retryable and int(row["attempts"]) < int(row["max_attempts"])
        status = "pending" if should_retry else "error"
        available = selected_ns + int(retry_delay_seconds * 1_000_000_000)
        updated = connection.execute(
            """UPDATE embedding_jobs SET status=?,available_ns=?,lease_owner=NULL,
            lease_until_ns=NULL,error_type=?,error_message=?,updated_ns=?
            WHERE job_id=? AND status='leased' AND lease_owner=?
              AND lease_until_ns>?""",
            (
                status,
                available,
                error_type[:256],
                error_message[:MAX_ERROR_CHARS],
                selected_ns,
                job_id,
                worker_id,
                selected_ns,
            ),
        )
        if updated.rowcount != 1:  # pragma: no cover - protected by the write lock
            raise SemanticStateError("job lease changed before failure was recorded")
        return status


def update_embedding_generation_cursor(
    path: Path,
    generation_id: int,
    cursor: Mapping[str, object],
) -> None:
    """Persist an explicit source-enumeration checkpoint for resumption."""

    cursor_json = canonical_json(cursor)
    with semantic_database(path) as connection:
        result = connection.execute(
            "UPDATE embedding_generations SET cursor_json=? "
            "WHERE generation_id=? AND status='building'",
            (cursor_json, generation_id),
        )
        if result.rowcount != 1:
            raise SemanticStateError("generation is absent or no longer building")


def _generation_summary_from_row(row: sqlite3.Row) -> GenerationSummary:
    cursor = json.loads(str(row["cursor_json"]))
    if not isinstance(cursor, dict):
        raise SemanticStateError("generation cursor is not a JSON object")
    building = str(row["status"]) == "building"
    return GenerationSummary(
        generation_id=int(row["generation_id"]),
        model_signature=str(row["model_signature"]),
        processing_signature=str(row["processing_signature"]),
        status=str(row["status"]),
        pending=int(row["pending"] if building else row["stored_pending"]),
        leased=int(row["leased"] if building else row["stored_leased"]),
        done=int(row["done"] if building else row["stored_done"]),
        errors=int(row["errors"] if building else row["stored_errors"]),
        stale=int(row["stale"] if building else row["stored_stale"]),
        cursor=cursor,
    )


def _generation_summary_rows(
    connection: sqlite3.Connection,
    generation_ids: Sequence[int],
) -> tuple[GenerationSummary, ...]:
    """Load one bounded ordered summary page with a single aggregate query."""

    if not generation_ids:
        return ()
    ordered_ids = tuple(int(value) for value in generation_ids)
    if len(ordered_ids) > 1_000:
        raise ValueError("generation summary batch cannot exceed 1000 identifiers")
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("generation summary identifiers must be unique")
    placeholders = ",".join("?" for _ in ordered_ids)
    rows = connection.execute(
        """SELECT g.generation_id,g.model_signature,g.processing_signature,g.status,
            g.cursor_json,g.pending_count AS stored_pending,
            g.leased_count AS stored_leased,g.done_count AS stored_done,
            g.error_count AS stored_errors,g.stale_count AS stored_stale,
            COALESCE(SUM(j.status='pending'),0) AS pending,
            COALESCE(SUM(j.status='leased'),0) AS leased,
            COALESCE(SUM(j.status='done'),0) AS done,
            COALESCE(SUM(j.status='error'),0) AS errors,
            COALESCE(SUM(j.status='stale'),0) AS stale
        FROM embedding_generations g
        LEFT JOIN embedding_jobs j ON j.generation_id=g.generation_id
        WHERE g.generation_id IN ({placeholders}) GROUP BY g.generation_id""".format(
            placeholders=placeholders
        ),
        ordered_ids,
    ).fetchall()
    summaries = {
        int(row["generation_id"]): _generation_summary_from_row(row) for row in rows
    }
    missing = tuple(value for value in ordered_ids if value not in summaries)
    if missing:
        raise KeyError(f"unknown embedding generation {missing[0]}")
    return tuple(summaries[value] for value in ordered_ids)


def _generation_summary_row(
    connection: sqlite3.Connection,
    generation_id: int,
) -> GenerationSummary:
    return _generation_summary_rows(connection, (generation_id,))[0]


def generation_summary(path: Path, generation_id: int) -> GenerationSummary:
    with semantic_database(path, readonly=True) as connection:
        return _generation_summary_row(connection, generation_id)


def _remove_obsolete_candidate_members(
    connection: sqlite3.Connection,
    generation_id: int,
) -> int:
    """Remove inherited/current members that no longer match active source state."""

    text = connection.execute(
        """DELETE FROM embedding_generation_members AS member
        WHERE member.generation_id=? AND member.entity_kind='text_chunk'
          AND NOT EXISTS(
            SELECT 1 FROM text_chunks c
            JOIN semantic_items i ON i.item_id=c.item_id
            WHERE c.chunk_id=member.entity_id AND c.item_id=member.item_id
              AND c.active=1 AND i.active=1
              AND c.content_xxh3_128=member.content_xxh3_128
              AND c.content_bytes=member.content_bytes
              AND c.content_xxh3_64_guard=member.content_xxh3_64_guard)""",
        (generation_id,),
    )
    image = connection.execute(
        """DELETE FROM embedding_generation_members AS member
        WHERE member.generation_id=? AND member.entity_kind='image_item'
          AND NOT EXISTS(
            SELECT 1 FROM semantic_items i
            WHERE i.item_id=member.entity_id AND i.active=1 AND i.path IS NOT NULL
              AND i.content_xxh3_128=member.content_xxh3_128
              AND i.content_bytes=member.content_bytes
              AND i.content_xxh3_64_guard=member.content_xxh3_64_guard)""",
        (generation_id,),
    )
    return max(0, int(text.rowcount)) + max(0, int(image.rowcount))


def finalize_embedding_generation(
    path: Path,
    generation_id: int,
    *,
    allow_partial: bool = False,
    completed_ns: int | None = None,
) -> GenerationSummary:
    """Finalize work and atomically publish only a complete CAS-safe snapshot."""

    selected_ns = _now(completed_ns)
    with semantic_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        model = _generation_model(connection, generation_id, require_building=True)
        _mark_stale_jobs(connection, generation_id, model.modality, selected_ns)
        summary = _generation_summary_row(connection, generation_id)
        if summary.unfinished:
            raise SemanticStateError(
                f"generation still has {summary.unfinished} unfinished jobs"
            )
        if (summary.errors or summary.stale) and not allow_partial:
            raise SemanticStateError(
                f"generation has {summary.errors} errors and {summary.stale} stale jobs"
            )
        partial = bool(summary.errors or summary.stale)
        status = "ready_partial" if partial else "ready"
        generation = connection.execute(
            """SELECT base_generation_id,base_clone_complete
            FROM embedding_generations WHERE generation_id=?""",
            (generation_id,),
        ).fetchone()
        if generation is None:  # pragma: no cover - protected by model load
            raise KeyError(f"unknown embedding generation {generation_id}")
        if not bool(generation["base_clone_complete"]):
            raise SemanticStateError("generation base snapshot is not fully cloned")
        if not partial:
            _remove_obsolete_candidate_members(connection, generation_id)
            missing_member = connection.execute(
                """SELECT j.job_id FROM embedding_jobs j
                LEFT JOIN embedding_generation_members member
                  ON member.generation_id=j.generation_id
                 AND member.entity_kind=j.entity_kind
                 AND member.entity_id=j.entity_id
                WHERE j.generation_id=? AND j.status='done'
                  AND member.member_id IS NULL LIMIT 1""",
                (generation_id,),
            ).fetchone()
            if missing_member is not None:
                raise SemanticStateError(
                    f"completed job {int(missing_member[0])} has no candidate member"
                )
            head = connection.execute(
                "SELECT generation_id FROM published_embedding_heads "
                "WHERE model_signature=?",
                (model.model_signature,),
            ).fetchone()
            current_head = None if head is None else int(head[0])
            expected_head = (
                None
                if generation["base_generation_id"] is None
                else int(generation["base_generation_id"])
            )
            if current_head != expected_head:
                raise SemanticStateError(
                    "published embedding head changed; generation must be rebased"
                )
        updated = connection.execute(
            """UPDATE embedding_generations SET status=?,completed_ns=?,
                pending_count=?,leased_count=?,done_count=?,error_count=?,stale_count=?
            WHERE generation_id=? AND status='building'""",
            (
                status,
                selected_ns,
                summary.pending,
                summary.leased,
                summary.done,
                summary.errors,
                summary.stale,
                generation_id,
            ),
        )
        if updated.rowcount != 1:
            raise SemanticStateError("generation changed before finalization")
        if not partial:
            expected_head = (
                None
                if generation["base_generation_id"] is None
                else int(generation["base_generation_id"])
            )
            if expected_head is None:
                connection.execute(
                    """INSERT INTO published_embedding_heads(
                        model_signature,generation_id,published_ns)
                    VALUES(?,?,?)""",
                    (model.model_signature, generation_id, selected_ns),
                )
            else:
                published = connection.execute(
                    """UPDATE published_embedding_heads
                    SET generation_id=?,published_ns=?
                    WHERE model_signature=? AND generation_id=?""",
                    (
                        generation_id,
                        selected_ns,
                        model.model_signature,
                        expected_head,
                    ),
                )
                if published.rowcount != 1:  # protected by BEGIN IMMEDIATE + CAS
                    raise SemanticStateError(
                        "published embedding head changed during finalization"
                    )
        return GenerationSummary(
            generation_id=summary.generation_id,
            model_signature=summary.model_signature,
            processing_signature=summary.processing_signature,
            status=status,
            pending=summary.pending,
            leased=summary.leased,
            done=summary.done,
            errors=summary.errors,
            stale=summary.stale,
            cursor=summary.cursor,
        )


# endregion [05]
