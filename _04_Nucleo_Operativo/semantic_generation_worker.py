"""Bounded, resumable embedding generation worker with renewable leases."""

from __future__ import annotations

import itertools
import math
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from .semantic_backends import (
    EmbeddingBackend,
    SourceRevisionMismatchError,
    TextTokenLimitExceededError,
)
from .semantic_config import SEMANTIC_PIPELINE_VERSION
from .semantic_models import BackendEmbedding, EmbeddingJobLease, EmbeddingRequest
from .semantic_service_contracts import (
    JOB_BATCH_SIZE,
    LEASE_HEARTBEAT_INTERVAL_SECONDS,
    LEASE_HEARTBEAT_JOIN_TIMEOUT_SECONDS,
    WORKER_LEASE_SECONDS,
    GenerationWorkResult,
)
from .semantic_state import (
    StaleEmbeddingJobError,
    claim_embedding_jobs,
    complete_embedding_job,
    deactivate_semantic_item_if_fingerprint,
    embedding_request_from_lease,
    fail_embedding_job,
    finalize_embedding_generation,
    generation_summary,
    heartbeat_embedding_jobs,
    reuse_cached_jobs,
)

_T = TypeVar("_T")
EmbeddingOutcome = tuple[
    tuple[tuple[int, BackendEmbedding], ...],
    tuple[tuple[int, Exception], ...],
]


class GenerationRunner(Protocol):
    def __call__(
        self,
        database: Path,
        generation_id: int,
        backend: EmbeddingBackend,
        *,
        queued: int,
    ) -> GenerationWorkResult: ...


# region [01] Bounded batches and failure isolation


def batches(values: Iterable[_T], size: int) -> Iterator[tuple[_T, ...]]:
    if size < 1:
        raise ValueError("batch size must be positive")
    iterator = iter(values)
    while batch := tuple(itertools.islice(iterator, size)):
        yield batch


def safe_error(exc: BaseException) -> str:
    return str(exc).encode("utf-8", "replace").decode("utf-8")[:8_000]


def embed_requests_isolated(
    backend: EmbeddingBackend,
    requests: Sequence[EmbeddingRequest],
) -> EmbeddingOutcome:
    """Isolate only failures proven local to one mutable or oversized payload."""

    successes: list[tuple[int, BackendEmbedding]] = []
    failures: list[tuple[int, Exception]] = []

    def submit(start: int, batch: Sequence[EmbeddingRequest]) -> None:
        try:
            outputs = tuple(backend.embed(batch))
            if len(outputs) != len(batch):
                raise RuntimeError("embedding backend returned an incomplete batch")
            if any(
                output.request_id != request.request_id
                for request, output in zip(batch, outputs, strict=True)
            ):
                raise RuntimeError("embedding backend changed request order")
        except Exception as exc:
            payload_local = isinstance(
                exc,
                (
                    SourceRevisionMismatchError,
                    TextTokenLimitExceededError,
                ),
            )
            if len(batch) == 1 or not payload_local:
                failures.extend((start + offset, exc) for offset in range(len(batch)))
                return
            midpoint = len(batch) // 2
            submit(start, batch[:midpoint])
            submit(start + midpoint, batch[midpoint:])
            return
        successes.extend(
            (start + offset, output) for offset, output in enumerate(outputs)
        )

    submit(0, requests)
    successes.sort(key=lambda value: value[0])
    failures.sort(key=lambda value: value[0])
    return tuple(successes), tuple(failures)


# endregion [01]


# region [02] Lease heartbeat


def embed_requests_with_heartbeat(
    database: Path,
    leases: Sequence[EmbeddingJobLease],
    *,
    worker_id: str,
    backend: EmbeddingBackend,
    requests: Sequence[EmbeddingRequest],
    lease_seconds: float = WORKER_LEASE_SECONDS,
    heartbeat_interval_seconds: float = LEASE_HEARTBEAT_INTERVAL_SECONDS,
    heartbeat_jobs: Callable[..., int] = heartbeat_embedding_jobs,
) -> EmbeddingOutcome:
    """Keep a bounded lease batch alive only while synchronous inference runs."""

    if not leases or len(leases) != len(requests):
        raise ValueError("heartbeat requires one lease per embedding request")
    if not math.isfinite(lease_seconds) or not 1.0 <= lease_seconds <= 86_400.0:
        raise ValueError("lease_seconds must be between 1 and 86400")
    if not math.isfinite(heartbeat_interval_seconds) or not (
        0.0 < heartbeat_interval_seconds < lease_seconds
    ):
        raise ValueError("heartbeat interval must be positive and below the lease")
    job_ids = tuple(int(lease.job_id) for lease in leases)
    stop = threading.Event()
    heartbeat_errors: list[Exception] = []

    def maintain_leases() -> None:
        while not stop.wait(heartbeat_interval_seconds):
            try:
                heartbeat_jobs(
                    database,
                    job_ids,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
            except Exception as exc:  # surfaced synchronously after inference
                heartbeat_errors.append(exc)
                return

    heartbeat = threading.Thread(
        target=maintain_leases,
        name=f"neocortex-semantic-lease:{leases[0].generation_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        result = embed_requests_isolated(backend, requests)
    finally:
        stop.set()
        heartbeat.join(LEASE_HEARTBEAT_JOIN_TIMEOUT_SECONDS)
    if heartbeat.is_alive():
        raise RuntimeError("semantic lease heartbeat did not stop cleanly")
    if heartbeat_errors:
        raise RuntimeError("semantic lease heartbeat failed") from heartbeat_errors[0]
    return result


# endregion [02]


# region [03] Generation state transitions


def _record_embedding_failures(
    database: Path,
    leases: Sequence[EmbeddingJobLease],
    failures: Sequence[tuple[int, Exception]],
    *,
    worker_id: str,
) -> int:
    for index, exc in failures:
        retryable = isinstance(exc, OSError)
        lease = leases[index]
        fail_embedding_job(
            database,
            lease.job_id,
            worker_id=worker_id,
            error_type=type(exc).__name__,
            error_message=safe_error(exc),
            retryable=retryable,
            retry_delay_seconds=30.0 if retryable else 0.0,
        )
        if isinstance(exc, SourceRevisionMismatchError):
            deactivate_semantic_item_if_fingerprint(
                database,
                item_id=lease.item_id,
                fingerprint=lease.fingerprint,
            )
    return len(failures)


def _record_embedding_successes(
    database: Path,
    leases: Sequence[EmbeddingJobLease],
    successes: Sequence[tuple[int, BackendEmbedding]],
    *,
    worker_id: str,
) -> tuple[int, int]:
    embedded = failed = 0
    for index, output in successes:
        lease = leases[index]
        try:
            complete_embedding_job(
                database,
                lease.job_id,
                worker_id=worker_id,
                vector=output.vector,
                provenance={
                    **dict(output.provenance),
                    "pipeline": SEMANTIC_PIPELINE_VERSION,
                },
            )
        except StaleEmbeddingJobError:
            failed += 1
        except Exception as exc:
            fail_embedding_job(
                database,
                lease.job_id,
                worker_id=worker_id,
                error_type=type(exc).__name__,
                error_message=safe_error(exc),
                retryable=False,
            )
            failed += 1
        else:
            embedded += 1
    return embedded, failed


def _release_interrupted_leases(
    database: Path,
    leases: Sequence[EmbeddingJobLease],
    *,
    worker_id: str,
    interruption: BaseException,
) -> None:
    """Return every still-owned lease to durable retry without masking a cancel."""

    cleanup_errors: list[BaseException] = []
    for lease in leases:
        try:
            fail_embedding_job(
                database,
                lease.job_id,
                worker_id=worker_id,
                error_type=type(interruption).__name__,
                error_message=safe_error(interruption),
                retryable=True,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)
    if cleanup_errors:
        interruption.add_note(
            "semantic lease cleanup encountered "
            f"{len(cleanup_errors)} error(s); first={safe_error(cleanup_errors[0])}"
        )


def run_generation(
    database: Path,
    generation_id: int,
    backend: EmbeddingBackend,
    *,
    queued: int,
    heartbeat_jobs: Callable[..., int] = heartbeat_embedding_jobs,
) -> GenerationWorkResult:
    reused = 0
    embedded = failed = 0
    worker_id = f"semantic-worker:{os.getpid()}:{generation_id}"
    while True:
        while count := reuse_cached_jobs(database, generation_id):
            reused += count
        leases = claim_embedding_jobs(
            database,
            generation_id,
            worker_id=worker_id,
            limit=min(JOB_BATCH_SIZE, backend.max_batch_size),
            lease_seconds=WORKER_LEASE_SECONDS,
        )
        if not leases:
            break
        try:
            requests = tuple(embedding_request_from_lease(lease) for lease in leases)
            successes, embedding_failures = embed_requests_with_heartbeat(
                database,
                leases,
                worker_id=worker_id,
                backend=backend,
                requests=requests,
                heartbeat_jobs=heartbeat_jobs,
            )
            failed += _record_embedding_failures(
                database,
                leases,
                embedding_failures,
                worker_id=worker_id,
            )
            batch_embedded, completion_failures = _record_embedding_successes(
                database,
                leases,
                successes,
                worker_id=worker_id,
            )
        except BaseException as exc:
            _release_interrupted_leases(
                database,
                leases,
                worker_id=worker_id,
                interruption=exc,
            )
            raise
        embedded += batch_embedded
        failed += completion_failures

    summary = generation_summary(database, generation_id)
    if not summary.unfinished:
        summary = finalize_embedding_generation(
            database,
            generation_id,
            allow_partial=True,
        )
    return GenerationWorkResult(summary, queued, reused, embedded, failed)


# endregion [03]
