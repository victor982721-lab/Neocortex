"""Incremental text indexing from durable extraction caches."""

from __future__ import annotations

import itertools
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path

from .semantic_chunking import TextChunkingConfig, iter_text_chunks
from .semantic_config import (
    SEMANTIC_PIPELINE_VERSION,
    multilingual_text_model,
    text_chunking_for_model,
)
from .semantic_generation_repository import _enqueue_text_chunk_batch
from .semantic_generation_worker import GenerationRunner
from .semantic_item_repository import (
    _finalize_semantic_item_refresh,
    _finalize_text_chunk_refresh,
    _stage_text_chunk_batch,
    _upsert_item,
)
from .semantic_models import (
    EmbeddingModality,
    EmbeddingModelSpec,
    SemanticItem,
    TextSection,
)
from .semantic_preparation import (
    BackendFactory,
    initialize_models,
    model_cache,
    require_source_databases,
    text_probe,
)
from .semantic_service_contracts import (
    SEMANTIC_DATABASE_NAME,
    STAGING_BATCH_SIZE,
    SemanticIndexResult,
)
from .semantic_sources import (
    SOURCE_ADAPTER_VERSION,
    TEXT_SOURCE_KINDS,
    TextSourceRecord,
)
from .semantic_schema import semantic_database
from .semantic_state import (
    start_embedding_generation,
    update_embedding_generation_cursor,
)
from .sqlite_cancellation import (
    CancellationCheck,
    SQLiteCancellationBridge,
    sqlite_cancellation_scope,
)

TextRecordIterator = Callable[[Path, str], Iterator[TextSourceRecord]]


# region [01] Source grouping and staging


def grouped_text_records(
    state_directory: Path,
    source_kind: str,
    *,
    source_record_iterator: TextRecordIterator,
) -> Iterator[tuple[str, Iterator[TextSourceRecord]]]:
    records = source_record_iterator(state_directory, source_kind)
    return itertools.groupby(records, key=lambda record: record.item.item_id)


class _SemanticTextStagingSession:
    """Stage one source through one connection and bounded transactions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        generation_id: int,
        source_kind: str,
        refresh_token: str,
        chunking: TextChunkingConfig,
        cancellation: SQLiteCancellationBridge,
    ) -> None:
        self._connection = connection
        self._generation_id = generation_id
        self._source_kind = source_kind
        self._refresh_token = refresh_token
        self._chunking = chunking
        self._cancellation = cancellation
        self._transaction_chunks = 0
        self._transaction_items = 0

    def _begin(self) -> None:
        self._cancellation.checkpoint()
        if not self._connection.in_transaction:
            self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        if not self._connection.in_transaction:
            return
        self._cancellation.checkpoint()
        self._connection.commit()
        self._transaction_chunks = 0
        self._transaction_items = 0

    def stage_item(
        self,
        item: SemanticItem,
        sections: Iterable[TextSection],
    ) -> tuple[int, int]:
        """Stage one item while committing oversized work in bounded slices."""

        self._begin()
        _upsert_item(
            self._connection,
            item,
            refresh_token=self._refresh_token,
            updated_ns=time.time_ns(),
            invalidate_text_on_fingerprint_change=True,
        )
        chunks_staged = queued = 0
        chunks = iter_text_chunks(item.item_id, sections, self._chunking)
        while True:
            capacity = STAGING_BATCH_SIZE - self._transaction_chunks
            batch = tuple(itertools.islice(chunks, capacity))
            if not batch:
                break
            self._begin()
            selected_ns = time.time_ns()
            chunks_staged += _stage_text_chunk_batch(
                self._connection,
                batch,
                refresh_token=self._refresh_token,
                updated_ns=selected_ns,
            )
            queued += _enqueue_text_chunk_batch(
                self._connection,
                self._generation_id,
                tuple(chunk.chunk_id for chunk in batch),
                now_ns=selected_ns,
            )
            self._transaction_chunks += len(batch)
            self._cancellation.checkpoint()
            if self._transaction_chunks >= STAGING_BATCH_SIZE:
                self._commit()

        self._begin()
        _finalize_text_chunk_refresh(
            self._connection,
            item_id=item.item_id,
            chunking_signature=self._chunking.signature,
            refresh_token=self._refresh_token,
            updated_ns=time.time_ns(),
        )
        self._transaction_items += 1
        self._cancellation.checkpoint()
        if self._transaction_items >= STAGING_BATCH_SIZE:
            self._commit()
        return chunks_staged, queued

    def finalize_source(self) -> None:
        """Deactivate unseen source members only after the refresh completed."""

        self._begin()
        _finalize_semantic_item_refresh(
            self._connection,
            source_kind=self._source_kind,
            refresh_token=self._refresh_token,
            updated_ns=time.time_ns(),
        )
        self._commit()


def _stage_source(
    database: Path,
    state_directory: Path,
    source_kind: str,
    *,
    generation_id: int,
    refresh_token: str,
    chunking: TextChunkingConfig,
    source_record_iterator: TextRecordIterator,
    cancellation_check: CancellationCheck | None = None,
) -> tuple[int, int, int]:
    if not source_kind.strip() or not refresh_token.strip():
        raise ValueError("source_kind and refresh_token cannot be blank")
    source_items = chunks_staged = queued = 0
    groups = grouped_text_records(
        state_directory,
        source_kind,
        source_record_iterator=source_record_iterator,
    )
    bridge = SQLiteCancellationBridge(cancellation_check)
    with semantic_database(database) as connection:
        with sqlite_cancellation_scope(connection, bridge):
            session = _SemanticTextStagingSession(
                connection,
                generation_id=generation_id,
                source_kind=source_kind,
                refresh_token=refresh_token,
                chunking=chunking,
                cancellation=bridge,
            )
            for _item_id, grouped in groups:
                bridge.checkpoint()
                iterator = iter(grouped)
                first = next(iterator)
                item = first.item
                sections = itertools.chain(
                    (first.section,),
                    (record.section for record in iterator),
                )
                item_chunks, item_jobs = session.stage_item(item, sections)
                source_items += 1
                chunks_staged += item_chunks
                queued += item_jobs
            session.finalize_source()
    return source_items, chunks_staged, queued


# endregion [01]


# region [02] Text indexing orchestration


def index_text_embeddings(
    state_directory: Path,
    *,
    source_kinds: Sequence[str],
    model: EmbeddingModelSpec | None,
    model_cache_override: Path | None,
    local_files_only: bool,
    threads: int | None,
    chunking: TextChunkingConfig | None,
    backend_factory: BackendFactory,
    source_record_iterator: TextRecordIterator,
    generation_runner: GenerationRunner,
) -> SemanticIndexResult:
    """Incrementally embed extracted text; source files are never rescanned."""

    selected_sources = tuple(dict.fromkeys(source_kinds))
    if not selected_sources or any(
        kind not in TEXT_SOURCE_KINDS for kind in selected_sources
    ):
        raise ValueError("semantic text sources must name supported durable caches")
    selected_model = model or multilingual_text_model()
    if selected_model.modality is not EmbeddingModality.TEXT:
        raise ValueError("text indexing requires a text model")
    require_source_databases(state_directory, selected_sources)
    active_chunking = chunking or text_chunking_for_model(selected_model)
    cache = model_cache(state_directory, model_cache_override)
    embedding_backend = backend_factory(
        selected_model,
        cache_dir=cache,
        local_files_only=local_files_only,
        threads=threads,
    )
    text_probe(embedding_backend)

    database = state_directory / SEMANTIC_DATABASE_NAME
    initialize_models(database, (selected_model,))
    processing_signature = (
        f"{SEMANTIC_PIPELINE_VERSION}|{SOURCE_ADAPTER_VERSION}|"
        f"{active_chunking.signature}|sources={','.join(selected_sources)}"
    )
    generation_id = start_embedding_generation(
        database,
        model_signature=selected_model.model_signature,
        processing_signature=processing_signature,
        provenance={
            "pipeline": SEMANTIC_PIPELINE_VERSION,
            "sources": list(selected_sources),
            "chunking_signature": active_chunking.signature,
        },
    )
    items_staged = chunks_staged = queued = 0
    for source_kind in selected_sources:
        refresh_token = f"generation:{generation_id}:source:{source_kind}"
        source_items, source_chunks, source_jobs = _stage_source(
            database,
            state_directory,
            source_kind,
            generation_id=generation_id,
            refresh_token=refresh_token,
            chunking=active_chunking,
            source_record_iterator=source_record_iterator,
        )
        items_staged += source_items
        chunks_staged += source_chunks
        queued += source_jobs
        update_embedding_generation_cursor(
            database,
            generation_id,
            cursor={"completed_source": source_kind, "items": source_items},
        )

    result = generation_runner(
        database,
        generation_id,
        embedding_backend,
        queued=queued,
    )
    return SemanticIndexResult(
        database,
        selected_sources,
        items_staged,
        chunks_staged,
        (result,),
    )


# endregion [02]
