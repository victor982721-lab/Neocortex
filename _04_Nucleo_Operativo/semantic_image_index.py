"""Joint visual and bounded OCR indexing in separate vector spaces."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from .semantic_chunking import TextChunkingConfig, iter_text_chunks
from .semantic_config import (
    SEMANTIC_PIPELINE_VERSION,
    clip_image_model,
    clip_text_model,
    multilingual_text_model,
    text_chunking_for_model,
)
from .semantic_generation_worker import GenerationRunner, batches
from .semantic_models import (
    EmbeddingModality,
    EmbeddingModelSpec,
    TextSection,
    fingerprint_text,
)
from .semantic_preparation import (
    BackendFactory,
    image_probe,
    initialize_models,
    model_cache,
    require_source_databases,
    text_probe,
)
from .semantic_service_contracts import (
    IMAGE_OCR_TEXT_CHANNEL,
    SEMANTIC_DATABASE_NAME,
    STAGING_BATCH_SIZE,
    SemanticIndexResult,
)
from .semantic_sources import (
    IMAGE_SOURCE_KIND,
    SOURCE_ADAPTER_VERSION,
    ImageSourceRecord,
)
from .semantic_state import (
    deactivate_text_chunks_for_item,
    enqueue_image_item_jobs,
    enqueue_text_chunk_jobs,
    finalize_semantic_item_refresh,
    finalize_text_chunk_refresh,
    publish_text_channel_revision,
    stage_semantic_items,
    stage_text_chunks,
    start_embedding_generation,
    update_embedding_generation_cursor,
)

ImageRecordIterator = Callable[[Path], Iterator[ImageSourceRecord]]


# region [01] Bounded image and OCR staging


def stage_image_batch(
    database: Path,
    records: Sequence[ImageSourceRecord],
    *,
    refresh_token: str,
    image_generation_id: int,
    ocr_generation_id: int | None,
    include_ocr_text: bool,
    chunking: TextChunkingConfig,
) -> tuple[int, int, int, int]:
    items = tuple(record.item for record in records)
    item_count = stage_semantic_items(
        database,
        items,
        source_kind=IMAGE_SOURCE_KIND,
        refresh_token=refresh_token,
        batch_size=STAGING_BATCH_SIZE,
        invalidate_text_on_fingerprint_change=False,
    )
    image_jobs = enqueue_image_item_jobs(
        database,
        image_generation_id,
        (item.item_id for item in items),
        batch_size=STAGING_BATCH_SIZE,
    )
    chunks_staged = text_jobs = 0
    for record in records:
        if not include_ocr_text or record.ocr_section is None:
            deactivate_text_chunks_for_item(
                database,
                item_id=record.item.item_id,
            )
            continue
        if record.ocr_section.section_kind != IMAGE_OCR_TEXT_CHANNEL:
            raise ValueError(
                "image OCR sections must use the stable image_ocr text channel"
            )
        ocr_fingerprint = fingerprint_text(record.ocr_section.text)
        publish_text_channel_revision(
            database,
            item_id=record.item.item_id,
            channel=IMAGE_OCR_TEXT_CHANNEL,
            revision_token=(
                f"xxh3-128={ocr_fingerprint.xxh3_128};"
                f"bytes={ocr_fingerprint.byte_count};"
                f"xxh3-64-guard={ocr_fingerprint.xxh3_64_guard}"
            ),
        )
        sections: tuple[TextSection, ...] = (record.ocr_section,)
        chunks = tuple(iter_text_chunks(record.item.item_id, sections, chunking))
        if chunks:
            chunks_staged += stage_text_chunks(
                database,
                chunks,
                refresh_token=refresh_token,
                batch_size=STAGING_BATCH_SIZE,
            )
            if ocr_generation_id is not None:
                text_jobs += enqueue_text_chunk_jobs(
                    database,
                    ocr_generation_id,
                    (chunk.chunk_id for chunk in chunks),
                    batch_size=STAGING_BATCH_SIZE,
                )
        finalize_text_chunk_refresh(
            database,
            item_id=record.item.item_id,
            chunking_signature=chunking.signature,
            refresh_token=refresh_token,
        )
    return item_count, chunks_staged, image_jobs, text_jobs


# endregion [01]


# region [02] Generation setup and execution


def _start_image_generations(
    database: Path,
    *,
    image_model: EmbeddingModelSpec,
    text_model: EmbeddingModelSpec,
    embed_ocr_text: bool,
    chunking: TextChunkingConfig,
) -> tuple[int, int | None]:
    image_generation_id = start_embedding_generation(
        database,
        model_signature=image_model.model_signature,
        processing_signature=(
            f"{SEMANTIC_PIPELINE_VERSION}|{SOURCE_ADAPTER_VERSION}|images"
        ),
        provenance={"pipeline": SEMANTIC_PIPELINE_VERSION, "source": "image"},
    )
    if not embed_ocr_text:
        return image_generation_id, None
    ocr_generation_id = start_embedding_generation(
        database,
        model_signature=text_model.model_signature,
        processing_signature=(
            f"{SEMANTIC_PIPELINE_VERSION}|{SOURCE_ADAPTER_VERSION}|image-ocr|"
            f"{chunking.signature}"
        ),
        provenance={
            "pipeline": SEMANTIC_PIPELINE_VERSION,
            "source": "image-ocr",
            "chunking_signature": chunking.signature,
        },
    )
    return image_generation_id, ocr_generation_id


def index_image_embeddings(
    state_directory: Path,
    *,
    model_cache_override: Path | None,
    local_files_only: bool,
    threads: int | None,
    embed_ocr_text: bool,
    ocr_model: EmbeddingModelSpec | None,
    chunking: TextChunkingConfig | None,
    backend_factory: BackendFactory,
    source_record_iterator: ImageRecordIterator,
    generation_runner: GenerationRunner,
) -> SemanticIndexResult:
    """Index visual CLIP vectors and retained OCR in separate compatible spaces."""

    require_source_databases(state_directory, (IMAGE_SOURCE_KIND,))
    image_model = clip_image_model()
    query_model = clip_text_model()
    text_model = ocr_model or multilingual_text_model()
    if text_model.modality is not EmbeddingModality.TEXT:
        raise ValueError("image OCR indexing requires a text model")
    active_chunking = chunking or text_chunking_for_model(text_model)
    cache = model_cache(state_directory, model_cache_override)
    image_backend = backend_factory(
        image_model,
        cache_dir=cache,
        local_files_only=local_files_only,
        threads=threads,
    )
    image_probe(image_backend)
    text_backend = None
    if embed_ocr_text:
        text_backend = backend_factory(
            text_model,
            cache_dir=cache,
            local_files_only=local_files_only,
            threads=threads,
        )
        text_probe(text_backend)

    database = state_directory / SEMANTIC_DATABASE_NAME
    models = [image_model, query_model]
    if embed_ocr_text:
        models.append(text_model)
    initialize_models(database, models)
    image_generation_id, ocr_generation_id = _start_image_generations(
        database,
        image_model=image_model,
        text_model=text_model,
        embed_ocr_text=embed_ocr_text,
        chunking=active_chunking,
    )

    refresh_token = f"generation:{image_generation_id}:source:image"
    items_staged = chunks_staged = 0
    image_queued = ocr_queued = 0
    records = source_record_iterator(state_directory)
    for batch in batches(records, STAGING_BATCH_SIZE):
        item_count, chunk_count, queued_images, queued_ocr = stage_image_batch(
            database,
            batch,
            refresh_token=refresh_token,
            image_generation_id=image_generation_id,
            ocr_generation_id=ocr_generation_id,
            include_ocr_text=embed_ocr_text,
            chunking=active_chunking,
        )
        items_staged += item_count
        chunks_staged += chunk_count
        image_queued += queued_images
        ocr_queued += queued_ocr
    finalize_semantic_item_refresh(
        database,
        source_kind=IMAGE_SOURCE_KIND,
        refresh_token=refresh_token,
    )
    update_embedding_generation_cursor(
        database,
        image_generation_id,
        cursor={"completed_source": "image", "items": items_staged},
    )
    generation_results = [
        generation_runner(
            database,
            image_generation_id,
            image_backend,
            queued=image_queued,
        )
    ]
    if ocr_generation_id is not None and text_backend is not None:
        update_embedding_generation_cursor(
            database,
            ocr_generation_id,
            cursor={"completed_source": "image-ocr", "chunks": chunks_staged},
        )
        generation_results.append(
            generation_runner(
                database,
                ocr_generation_id,
                text_backend,
                queued=ocr_queued,
            )
        )
    return SemanticIndexResult(
        database,
        ("image", "image-ocr") if embed_ocr_text else ("image",),
        items_staged,
        chunks_staged,
        tuple(generation_results),
    )


# endregion [02]
