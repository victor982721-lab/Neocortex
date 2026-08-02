"""Joint visual and bounded OCR indexing in separate vector spaces."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path

from .semantic_chunking import TextChunkingConfig, TextTokenCounter, iter_text_chunks
from .semantic_config import (
    SEMANTIC_PIPELINE_VERSION,
    clip_image_model,
    clip_text_model,
    multilingual_text_model,
    text_chunking_for_model,
)
from .semantic_generation_worker import GenerationRunner
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
    resolve_text_token_guard,
    text_probe,
)
from .semantic_service_contracts import (
    IMAGE_OCR_TEXT_CHANNEL,
    SEMANTIC_DATABASE_NAME,
    STAGING_BATCH_SIZE,
    GenerationWorkResult,
    SemanticIndexResult,
)
from .semantic_sources import (
    IMAGE_SOURCE_KIND,
    SOURCE_ADAPTER_VERSION,
    ImageSourceRecord,
)
from .semantic_state import (
    deactivate_text_chunks_for_item,
    enqueue_image_item_jobs_bounded,
    enqueue_text_chunk_jobs_bounded,
    finalize_semantic_item_refresh,
    finalize_text_chunk_refresh,
    generation_summary,
    prepare_embedding_generation,
    publish_text_channel_revision,
    stage_semantic_items,
    stage_text_chunks,
    start_embedding_generation,
    update_embedding_generation_cursor,
)
from .semantic_work_budget import (
    SemanticIndexDeadlineExceeded,
    SemanticWorkBudget,
    unlimited_semantic_work_budget,
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
    token_counter: TextTokenCounter | None = None,
    work_budget: SemanticWorkBudget | None = None,
) -> tuple[int, int, int, int]:
    budget = work_budget or unlimited_semantic_work_budget()
    item_count = chunks_staged = image_jobs = text_jobs = 0
    for record in records:
        budget.checkpoint()
        item_count += stage_semantic_items(
            database,
            (record.item,),
            source_kind=IMAGE_SOURCE_KIND,
            refresh_token=refresh_token,
            batch_size=STAGING_BATCH_SIZE,
            invalidate_text_on_fingerprint_change=False,
        )
        budget.checkpoint()
        image_result = enqueue_image_item_jobs_bounded(
            database,
            image_generation_id,
            (record.item.item_id,),
            max_new_jobs=budget.new_job_allowance(STAGING_BATCH_SIZE),
            batch_size=STAGING_BATCH_SIZE,
        )
        image_jobs += image_result.touched
        budget.record_new_jobs(image_result.new_jobs)
        budget.record_rebound_members(image_result.rebound_members)
        budget.checkpoint()
        if not image_result.complete:
            budget.mark_job_limit()
            return item_count, chunks_staged, image_jobs, text_jobs
        if not include_ocr_text or record.ocr_section is None:
            budget.checkpoint()
            deactivate_text_chunks_for_item(
                database,
                item_id=record.item.item_id,
            )
            budget.checkpoint()
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
        chunks = tuple(
            iter_text_chunks(
                record.item.item_id,
                sections,
                chunking,
                token_counter=token_counter,
            )
        )
        if chunks:
            chunks_staged += stage_text_chunks(
                database,
                chunks,
                refresh_token=refresh_token,
                batch_size=STAGING_BATCH_SIZE,
            )
            budget.checkpoint()
            if ocr_generation_id is not None:
                text_result = enqueue_text_chunk_jobs_bounded(
                    database,
                    ocr_generation_id,
                    (chunk.chunk_id for chunk in chunks),
                    max_new_jobs=budget.new_job_allowance(len(chunks)),
                    batch_size=STAGING_BATCH_SIZE,
                )
                text_jobs += text_result.touched
                budget.record_new_jobs(text_result.new_jobs)
                budget.record_rebound_members(text_result.rebound_members)
                budget.checkpoint()
                if not text_result.complete:
                    budget.mark_job_limit()
                    return item_count, chunks_staged, image_jobs, text_jobs
        budget.checkpoint()
        finalize_text_chunk_refresh(
            database,
            item_id=record.item.item_id,
            chunking_signature=chunking.signature,
            refresh_token=refresh_token,
        )
        budget.checkpoint()
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
            f"{SEMANTIC_PIPELINE_VERSION}|{SOURCE_ADAPTER_VERSION}|images|"
            "enumeration=bounded-v1"
        ),
        provenance={"pipeline": SEMANTIC_PIPELINE_VERSION, "source": "image"},
        materialize_base=False,
    )
    if not embed_ocr_text:
        return image_generation_id, None
    ocr_generation_id = start_embedding_generation(
        database,
        model_signature=text_model.model_signature,
        processing_signature=(
            f"{SEMANTIC_PIPELINE_VERSION}|{SOURCE_ADAPTER_VERSION}|image-ocr|"
            f"{chunking.signature}|enumeration=bounded-v1"
        ),
        provenance={
            "pipeline": SEMANTIC_PIPELINE_VERSION,
            "source": "image-ocr",
            "chunking_signature": chunking.signature,
            "tokenizer_signature": chunking.tokenizer_signature,
            "model_token_limit": chunking.model_token_limit,
        },
        materialize_base=False,
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
    work_budget: SemanticWorkBudget | None = None,
) -> SemanticIndexResult:
    """Index visual CLIP vectors and retained OCR in separate compatible spaces."""

    budget = work_budget or unlimited_semantic_work_budget()
    new_jobs_before = budget.new_jobs_admitted
    require_source_databases(state_directory, (IMAGE_SOURCE_KIND,))
    image_model = clip_image_model()
    query_model = clip_text_model()
    text_model = ocr_model or multilingual_text_model()
    if text_model.modality is not EmbeddingModality.TEXT:
        raise ValueError("image OCR indexing requires a text model")
    base_chunking = chunking or text_chunking_for_model(text_model)
    active_chunking = base_chunking
    cache = model_cache(state_directory, model_cache_override)
    image_backend = backend_factory(
        image_model,
        cache_dir=cache,
        local_files_only=local_files_only,
        threads=threads,
    )
    image_probe(image_backend)
    text_backend = None
    exact_token_counter = None
    if embed_ocr_text:
        text_backend = backend_factory(
            text_model,
            cache_dir=cache,
            local_files_only=local_files_only,
            threads=threads,
        )
        text_probe(text_backend)
        token_guard = resolve_text_token_guard(text_backend, text_model)
        exact_token_counter = token_guard.counter
        active_chunking = replace(
            base_chunking,
            model_token_limit=token_guard.token_limit,
            tokenizer_signature=token_guard.tokenizer_signature,
        )

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
    initial_cursor = {
        "protocol": "bounded-v1",
        "enumeration_complete": False,
        "selected_sources": ["image", "image-ocr"] if embed_ocr_text else ["image"],
    }
    update_embedding_generation_cursor(
        database,
        image_generation_id,
        cursor=initial_cursor,
    )
    if ocr_generation_id is not None:
        update_embedding_generation_cursor(
            database,
            ocr_generation_id,
            cursor=initial_cursor,
        )

    refresh_token = f"generation:{image_generation_id}:source:image"
    items_staged = chunks_staged = 0
    image_queued = ocr_queued = 0
    records = source_record_iterator(state_directory)
    enumeration_complete = True
    for record in records:
        if not budget.try_admit_item():
            enumeration_complete = False
            break
        item_new_jobs_before = budget.new_jobs_admitted
        item_rebounds_before = budget.rebound_members
        item_count, chunk_count, queued_images, queued_ocr = stage_image_batch(
            database,
            (record,),
            refresh_token=refresh_token,
            image_generation_id=image_generation_id,
            ocr_generation_id=ocr_generation_id,
            include_ocr_text=embed_ocr_text,
            chunking=active_chunking,
            token_counter=exact_token_counter,
            work_budget=budget,
        )
        items_staged += item_count
        chunks_staged += chunk_count
        image_queued += queued_images
        ocr_queued += queued_ocr
        if budget.truncated:
            enumeration_complete = False
            break
        if (
            budget.new_jobs_admitted == item_new_jobs_before
            and budget.rebound_members == item_rebounds_before
        ):
            budget.refund_replayed_item()
    if enumeration_complete and budget.deadline_expired():
        enumeration_complete = False
    if enumeration_complete:
        finalize_semantic_item_refresh(
            database,
            source_kind=IMAGE_SOURCE_KIND,
            refresh_token=refresh_token,
        )
        if budget.deadline_expired():
            enumeration_complete = False
    if enumeration_complete:
        update_embedding_generation_cursor(
            database,
            image_generation_id,
            cursor={
                "protocol": "bounded-v1",
                "enumeration_complete": True,
                "selected_sources": ["image", "image-ocr"]
                if embed_ocr_text
                else ["image"],
                "completed_source": "image",
                "items": items_staged,
            },
        )
    else:
        paused_cursor = {
            **initial_cursor,
            "truncation_reason": budget.truncation_reason,
            "items": items_staged,
        }
        update_embedding_generation_cursor(
            database,
            image_generation_id,
            cursor=paused_cursor,
        )
        if ocr_generation_id is not None:
            update_embedding_generation_cursor(
                database,
                ocr_generation_id,
                cursor=paused_cursor,
            )
    try:
        image_reused = prepare_embedding_generation(
            database,
            image_generation_id,
            enumeration_complete=enumeration_complete,
            work_budget=budget,
        )
    except SemanticIndexDeadlineExceeded:
        image_result = GenerationWorkResult(
            generation_summary(database, image_generation_id),
            image_queued,
            0,
            0,
            0,
        )
    else:
        image_result = (
            GenerationWorkResult(image_reused, 0, 0, 0, 0)
            if image_reused is not None
            else generation_runner(
                database,
                image_generation_id,
                image_backend,
                queued=image_queued,
                work_budget=budget,
                publish_if_complete=enumeration_complete,
            )
        )
    generation_results = [image_result]
    if ocr_generation_id is not None and text_backend is not None:
        if enumeration_complete:
            update_embedding_generation_cursor(
                database,
                ocr_generation_id,
                cursor={
                    "protocol": "bounded-v1",
                    "enumeration_complete": True,
                    "selected_sources": ["image", "image-ocr"],
                    "completed_source": "image-ocr",
                    "chunks": chunks_staged,
                },
            )
        try:
            ocr_reused = prepare_embedding_generation(
                database,
                ocr_generation_id,
                work_budget=budget,
                enumeration_complete=enumeration_complete,
            )
        except SemanticIndexDeadlineExceeded:
            ocr_result = GenerationWorkResult(
                generation_summary(database, ocr_generation_id),
                ocr_queued,
                0,
                0,
                0,
            )
        else:
            ocr_result = (
                GenerationWorkResult(ocr_reused, 0, 0, 0, 0)
                if ocr_reused is not None
                else generation_runner(
                    database,
                    ocr_generation_id,
                    text_backend,
                    queued=ocr_queued,
                    work_budget=budget,
                    publish_if_complete=enumeration_complete,
                )
            )
        generation_results.append(ocr_result)
    return SemanticIndexResult(
        database,
        ("image", "image-ocr") if embed_ocr_text else ("image",),
        items_staged,
        chunks_staged,
        tuple(generation_results),
        new_jobs_staged=budget.new_jobs_admitted - new_jobs_before,
        truncated=budget.truncated,
        truncation_reason=budget.truncation_reason,
    )


# endregion [02]
