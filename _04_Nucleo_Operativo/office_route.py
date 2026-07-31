"""Incremental, bounded extraction for XLSX, PPTX and ODT technical documents."""

from __future__ import annotations

import io
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, cast

import xxhash

from _02_Deduplicacion import FileSnapshot, snapshot_path
from _03_Progreso import (
    ProgressCallback,
    ProgressEvent,
    ProgressMetric,
    emit_progress,
)

from .action_policy import same_snapshot
from .cancellation import CancellationToken
from .file_identity import file_key_from_snapshot as _file_key
from .memory_runtime import MemoryResourceLimits, WeightedMemoryGate
from .office_state import initialize_office_state, office_database
from .processing_provenance import (
    ROUTE_SUMMARY_SCHEMA,
    ProcessingProvenance,
    build_processing_provenance,
    distribution_component,
    python_runtime_component,
)
from .review import ReviewCandidate, ReviewRecommendation
from .route_filters import CandidateSelection
from .state import FrameworkRouteState, ReviewCandidateReconciliation
from .zip_safety import ZipStructureError, inspect_zip_structure


# region [01] Stable route contracts and explicit safety bounds


OFFICE_ROUTE_VERSION = "office-route-v1"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ODT_MIME = "application/vnd.oasis.opendocument.text"
OFFICE_MIME_FORMATS: Mapping[str, Literal["xlsx", "pptx", "odt"]] = {
    XLSX_MIME: "xlsx",
    PPTX_MIME: "pptx",
    ODT_MIME: "odt",
}
MAX_ZIP_MEMBERS = 20_000
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_CORE_PROPERTIES_BYTES = 2 * 1024 * 1024
OFFICE_COMMIT_BATCH = 16
OFFICE_REVIEW_REASON_CODES = frozenset(
    {
        "office_corrupt_container",
        "office_duplicate_member",
        "office_encrypted_member",
        "office_io_error",
        "office_member_limit",
        "office_metadata_limit",
        "office_missing_required_part",
        "office_source_changed",
        "office_text_limit",
        "office_uncompressed_limit",
        "office_unsafe_member_name",
    }
)


@dataclass(frozen=True, slots=True)
class OfficeRouteConfig:
    state_path: Path
    max_file_bytes: int | None = None
    max_documents: int | None = None
    max_text_chars: int = 20_000_000
    retry_errors: bool = False
    selection: CandidateSelection = field(default_factory=CandidateSelection)
    memory_budget_bytes: int = 512 * 1024 * 1024
    min_free_memory_bytes: int = 1024 * 1024 * 1024
    min_free_commit_bytes: int = 1024 * 1024 * 1024
    memory_wait_timeout_seconds: float = 60.0

    @property
    def processing_signature(self) -> str:
        return self.processing_provenance.signature

    @property
    def processing_provenance(self) -> ProcessingProvenance:
        return _office_processing_provenance(self.max_text_chars)


@lru_cache(maxsize=64)
def _office_processing_provenance(max_text_chars: int) -> ProcessingProvenance:
    return build_processing_provenance(
        "office-route",
        OFFICE_ROUTE_VERSION,
        {"max_text_chars": max_text_chars},
        (
            python_runtime_component(),
            distribution_component("xxhash", "xxhash"),
        ),
        compatibility_tag=OFFICE_ROUTE_VERSION,
    )


@dataclass(frozen=True, slots=True)
class OfficeRouteSummary:
    candidate_pool: int = 0
    candidates: int = 0
    skipped_by_size: int = 0
    skipped_by_count: int = 0
    processed: int = 0
    cache_hits: int = 0
    cached_errors: int = 0
    extracted: int = 0
    errors: int = 0
    cache_documents_pruned: int = 0
    review_candidates: int = 0
    deletion_candidates: int = 0
    retryable_errors: int = 0
    peak_reserved_bytes: int = 0
    memory_waits: int = 0
    catalog_candidates: int = 0
    catalog_classified: int = 0
    catalog_cache_hits: int = 0
    catalog_review_required: int = 0
    catalog_errors: int = 0
    catalog_source_stale: int = 0
    catalog_stale_marked: int = 0
    processing_signature: str | None = None
    processing_provenance: dict[str, Any] | None = None
    summary_schema: str = ROUTE_SUMMARY_SCHEMA


@dataclass(frozen=True, slots=True)
class ExtractedOfficeDocument:
    format: str
    title: str
    author: str
    subject: str
    text: str
    part_count: int


class OfficeExtractionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        recommendation: ReviewRecommendation,
        retryable: bool,
    ):
        super().__init__(message)
        self.code = code
        self.recommendation = recommendation
        self.retryable = retryable


# endregion [01]


# region [02] Bounded OOXML and ODF text extraction


class _TextAccumulator:
    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("office text limit must be positive")
        self._limit = limit
        self._buffer = io.StringIO()
        self._chars = 0

    def add(self, value: str | None) -> None:
        if not value:
            return
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return
        extra = len(normalized) + (1 if self._chars else 0)
        if self._chars + extra > self._limit:
            raise OfficeExtractionError(
                "office_text_limit",
                f"extracted office text exceeds {self._limit} characters",
                recommendation="manual_review",
                retryable=False,
            )
        if self._chars:
            self._buffer.write("\n")
        self._buffer.write(normalized)
        self._chars += extra

    def value(self) -> str:
        return self._buffer.getvalue()


class _ReadBudget:
    def __init__(self, limit: int):
        self.remaining = limit

    def consume(self, amount: int) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise OfficeExtractionError(
                "office_uncompressed_limit",
                "selected office XML exceeds the uncompressed byte limit",
                recommendation="manual_review",
                retryable=False,
            )


class _BoundedZipMember:
    def __init__(
        self,
        source,
        *,
        member_limit: int,
        budget: _ReadBudget,
    ):
        self._source = source
        self._remaining = member_limit
        self._budget = budget

    def read(self, size: int = -1) -> bytes:
        request = self._remaining + 1 if size < 0 else min(size, self._remaining + 1)
        payload = self._source.read(request)
        self._remaining -= len(payload)
        self._budget.consume(len(payload))
        if self._remaining < 0:
            raise OfficeExtractionError(
                "office_member_limit",
                "office XML member exceeds its uncompressed byte limit",
                recommendation="manual_review",
                retryable=False,
            )
        return payload

    def close(self) -> None:
        self._source.close()


def extract_office_document(
    path: Path,
    format_name: Literal["xlsx", "pptx", "odt"],
    *,
    max_text_chars: int,
    cancellation: CancellationToken,
) -> ExtractedOfficeDocument:
    """Extract only classification-relevant XML through bounded streams."""

    try:
        inspect_zip_structure(path, max_members=MAX_ZIP_MEMBERS)
        with zipfile.ZipFile(path) as archive:
            infos = _validated_members(archive)
            names = {info.filename.casefold(): info for info in infos}
            _validate_required_parts(names, format_name)
            metadata = _core_properties(archive, names, cancellation)
            accumulator = _TextAccumulator(max_text_chars)
            budget = _ReadBudget(MAX_TOTAL_UNCOMPRESSED_BYTES)
            selected = tuple(
                info
                for info in infos
                if _is_classification_part(info.filename, format_name)
            )
            for info in selected:
                cancellation.checkpoint()
                _extract_part_text(
                    archive,
                    info,
                    format_name=format_name,
                    accumulator=accumulator,
                    budget=budget,
                )
    except OfficeExtractionError:
        raise
    except (
        ET.ParseError,
        ZipStructureError,
        zipfile.BadZipFile,
        RuntimeError,
        zlib.error,
    ) as exc:
        raise OfficeExtractionError(
            "office_corrupt_container",
            f"{type(exc).__name__}: {exc}",
            recommendation="deletion_candidate",
            retryable=False,
        ) from exc
    return ExtractedOfficeDocument(
        format=format_name,
        title=metadata.get("title", "") or path.stem,
        author=metadata.get("creator", ""),
        subject=metadata.get("subject", ""),
        text=accumulator.value(),
        part_count=len(selected),
    )


def _validated_members(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    infos = tuple(archive.infolist())
    total = 0
    names: set[str] = set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or not name:
            raise OfficeExtractionError(
                "office_unsafe_member_name",
                f"unsafe office member name: {info.filename}",
                recommendation="deletion_candidate",
                retryable=False,
            )
        folded = name.casefold()
        if folded in names:
            raise OfficeExtractionError(
                "office_duplicate_member",
                f"duplicate office member name: {info.filename}",
                recommendation="deletion_candidate",
                retryable=False,
            )
        names.add(folded)
        if info.flag_bits & 0x1:
            raise OfficeExtractionError(
                "office_encrypted_member",
                f"encrypted office member is unsupported: {info.filename}",
                recommendation="manual_review",
                retryable=False,
            )
        if info.file_size > MAX_MEMBER_BYTES:
            raise OfficeExtractionError(
                "office_member_limit",
                f"office member exceeds {MAX_MEMBER_BYTES} bytes: {info.filename}",
                recommendation="manual_review",
                retryable=False,
            )
        total += int(info.file_size)
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise OfficeExtractionError(
                "office_uncompressed_limit",
                "office container exceeds the uncompressed byte limit",
                recommendation="manual_review",
                retryable=False,
            )
    return infos


def _validate_required_parts(
    names: Mapping[str, zipfile.ZipInfo],
    format_name: str,
) -> None:
    required = {
        "xlsx": ("[content_types].xml", "xl/workbook.xml"),
        "pptx": ("[content_types].xml", "ppt/presentation.xml"),
        "odt": ("mimetype", "content.xml"),
    }[format_name]
    missing = tuple(name for name in required if name not in names)
    if missing:
        raise OfficeExtractionError(
            "office_missing_required_part",
            f"office container lacks required parts: {', '.join(missing)}",
            recommendation="deletion_candidate",
            retryable=False,
        )


def _core_properties(
    archive: zipfile.ZipFile,
    names: Mapping[str, zipfile.ZipInfo],
    cancellation: CancellationToken,
) -> dict[str, str]:
    info = names.get("docprops/core.xml") or names.get("meta.xml")
    if info is None:
        return {}
    cancellation.checkpoint()
    with archive.open(info) as source:
        payload = source.read(MAX_CORE_PROPERTIES_BYTES + 1)
    if len(payload) > MAX_CORE_PROPERTIES_BYTES:
        raise OfficeExtractionError(
            "office_metadata_limit",
            "office metadata exceeds its byte limit",
            recommendation="manual_review",
            retryable=False,
        )
    root = ET.fromstring(payload)
    values: dict[str, str] = {}
    aliases = {
        "title": "title",
        "creator": "creator",
        "initial-creator": "creator",
        "subject": "subject",
        "description": "subject",
    }
    for element in root.iter():
        local = _local_name(element.tag)
        key = aliases.get(local)
        if key and element.text and key not in values:
            values[key] = re.sub(r"\s+", " ", element.text).strip()
    return values


def _is_classification_part(name: str, format_name: str) -> bool:
    lower = name.casefold()
    if format_name == "xlsx":
        return (
            lower in {"xl/sharedstrings.xml", "xl/workbook.xml"}
            or lower.startswith("xl/worksheets/")
            or lower.startswith("xl/comments")
            or lower.startswith("xl/tables/")
        ) and lower.endswith(".xml")
    if format_name == "pptx":
        return (
            lower.startswith("ppt/slides/")
            or lower.startswith("ppt/notesslides/")
            or lower.startswith("ppt/comments/")
        ) and lower.endswith(".xml")
    return lower in {"content.xml", "styles.xml"}


def _extract_part_text(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    format_name: str,
    accumulator: _TextAccumulator,
    budget: _ReadBudget,
) -> None:
    source = archive.open(info)
    bounded = _BoundedZipMember(
        source,
        member_limit=min(MAX_MEMBER_BYTES, int(info.file_size) + 1),
        budget=budget,
    )
    try:
        for _event, element in ET.iterparse(bounded, events=("end",)):
            local = _local_name(element.tag)
            if format_name == "xlsx":
                if local in {"t", "f", "definedName"}:
                    accumulator.add(element.text)
                elif local == "sheet":
                    accumulator.add(element.attrib.get("name"))
            elif format_name == "pptx":
                if local == "t":
                    accumulator.add(element.text)
            elif local in {"p", "h", "span", "a"}:
                accumulator.add(element.text)
            element.clear()
    finally:
        bounded.close()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# endregion [02]


# region [03] Incremental route and durable cache writes


@dataclass(frozen=True, slots=True)
class _OfficeCandidateOutcome:
    extracted: int = 0
    errors: int = 0
    reviews: int = 0
    deletion_candidates: int = 0
    retryable_errors: int = 0


class OfficeRoute:
    def __init__(
        self,
        config: OfficeRouteConfig,
        framework_state: FrameworkRouteState,
        run_id: int,
        *,
        progress: ProgressCallback | None = None,
        memory_gate=None,
        cancellation: CancellationToken | None = None,
    ):
        self.config = config
        self.framework_state = framework_state
        self.run_id = run_id
        self.progress = progress
        self.cancellation = cancellation or CancellationToken()
        self.memory_gate = (
            memory_gate
            if memory_gate is not None
            else WeightedMemoryGate(
                MemoryResourceLimits(
                    memory_budget_bytes=config.memory_budget_bytes,
                    min_free_memory_bytes=config.min_free_memory_bytes,
                    min_free_commit_bytes=config.min_free_commit_bytes,
                    wait_timeout_seconds=config.memory_wait_timeout_seconds,
                ),
                self.cancellation,
            )
        )

    def _validate(self) -> None:
        if self.config.max_text_chars < 1:
            raise ValueError("office max_text_chars must be positive")
        if self.config.max_documents is not None and self.config.max_documents < 1:
            raise ValueError("office max_documents must be positive")

    def _selected_counts(self) -> tuple[int, int, int]:
        totals = [
            self.framework_state.selected_route_candidate_counts(
                self.run_id,
                mime,
                self.config.max_file_bytes,
                "office",
                self.config.selection,
            )
            for mime in OFFICE_MIME_FORMATS
        ]
        candidate_pool = sum(item[0] for item in totals)
        eligible = sum(item[1] for item in totals)
        selected = (
            eligible
            if self.config.max_documents is None
            else min(eligible, self.config.max_documents)
        )
        return candidate_pool, eligible, selected

    @staticmethod
    def _queue_success(
        reconciliations: list[ReviewCandidateReconciliation],
        snapshot: FileSnapshot,
        note: str,
    ) -> None:
        reconciliations.append(
            ReviewCandidateReconciliation(
                snapshot=snapshot,
                resolution_note=note,
                evaluated_reason_codes=tuple(sorted(OFFICE_REVIEW_REASON_CODES)),
            )
        )

    def _flush_reviews(
        self,
        reconciliations: list[ReviewCandidateReconciliation],
    ) -> None:
        if not reconciliations:
            return
        self.framework_state.reconcile_review_candidates_batch(
            self.run_id,
            "office",
            tuple(reconciliations),
        )
        reconciliations.clear()

    def _consume_cached(
        self,
        connection: sqlite3.Connection,
        snapshot: FileSnapshot,
        format_name: str,
        cached: sqlite3.Row | None,
        reconciliations: list[ReviewCandidateReconciliation],
    ) -> tuple[bool, bool]:
        if cached is None:
            return False, False
        status = str(cached["status"])
        if status != "complete" and self.config.retry_errors:
            return False, False
        _refresh_cached_path(connection, snapshot, format_name, self.run_id)
        if status == "complete":
            self._queue_success(
                reconciliations,
                snapshot,
                "current Office cache completed without structural errors",
            )
            return True, False
        self.framework_state.store_review_candidates(
            self.run_id,
            (_review_candidate(snapshot, _cached_office_failure(cached)),),
        )
        return True, True

    def _extract_snapshot(
        self,
        snapshot: FileSnapshot,
        format_name: Literal["xlsx", "pptx", "odt"],
    ) -> ExtractedOfficeDocument:
        current = snapshot_path(snapshot.path)
        if not same_snapshot(snapshot, current):
            raise OfficeExtractionError(
                "office_source_changed",
                "office source changed after inventory",
                recommendation="retry",
                retryable=True,
            )
        with self.memory_gate.admit(
            _estimated_office_memory_bytes(snapshot, self.config.max_text_chars)
        ):
            document = extract_office_document(
                Path(snapshot.path),
                format_name,
                max_text_chars=self.config.max_text_chars,
                cancellation=self.cancellation,
            )
        final = snapshot_path(snapshot.path)
        if not same_snapshot(snapshot, final):
            raise OfficeExtractionError(
                "office_source_changed",
                "office source changed during extraction",
                recommendation="retry",
                retryable=True,
            )
        return document

    def _process_candidate(
        self,
        connection: sqlite3.Connection,
        snapshot: FileSnapshot,
        format_name: Literal["xlsx", "pptx", "odt"],
        reconciliations: list[ReviewCandidateReconciliation],
    ) -> _OfficeCandidateOutcome:
        try:
            document = self._extract_snapshot(snapshot, format_name)
            _store_success(
                connection,
                snapshot,
                document,
                self.config.processing_signature,
                self.run_id,
            )
            self._queue_success(
                reconciliations,
                snapshot,
                "Office extraction completed without structural errors",
            )
            return _OfficeCandidateOutcome(extracted=1)
        except OfficeExtractionError as exc:
            failure = exc
        except (OSError, sqlite3.Error) as exc:
            failure = OfficeExtractionError(
                "office_io_error",
                f"{type(exc).__name__}: {exc}",
                recommendation="retry",
                retryable=True,
            )
        _store_error(
            connection,
            snapshot,
            format_name,
            self.config.processing_signature,
            self.run_id,
            failure,
        )
        self.framework_state.store_review_candidates(
            self.run_id,
            (_review_candidate(snapshot, failure),),
        )
        return _OfficeCandidateOutcome(
            errors=1,
            reviews=1,
            deletion_candidates=int(failure.recommendation == "deletion_candidate"),
            retryable_errors=int(failure.retryable),
        )

    def run(self) -> OfficeRouteSummary:
        self.cancellation.checkpoint()
        self._validate()
        initialize_office_state(self.config.state_path)
        candidate_pool, eligible, selected_count = self._selected_counts()
        processed = cache_hits = cached_errors = extracted = errors = 0
        reviews = deletion_candidates = retryable_errors = 0
        review_reconciliations: list[ReviewCandidateReconciliation] = []

        def flush_review_reconciliations() -> None:
            self._flush_reviews(review_reconciliations)

        def report(*, finished: bool = False) -> None:
            emit_progress(
                self.progress,
                ProgressEvent(
                    "office",
                    "extract",
                    "Office indexados" if finished else "Indexando Office",
                    processed,
                    selected_count,
                    "documentos",
                    finished,
                    (
                        ProgressMetric("cache_hits", cache_hits),
                        ProgressMetric("cached_errors", cached_errors),
                        ProgressMetric("errors", errors),
                        ProgressMetric("completed_work", extracted),
                        ProgressMetric("memory_waits", self.memory_gate.wait_count),
                    ),
                ),
            )

        with office_database(self.config.state_path, create=False) as connection:
            for mime, format_name in OFFICE_MIME_FORMATS.items():
                iterator = self.framework_state.iter_selected_route_candidates(
                    self.run_id,
                    mime,
                    "office",
                    self.config.selection,
                )
                for snapshot in iterator:
                    if processed >= selected_count:
                        break
                    self.cancellation.checkpoint()
                    if (
                        self.config.max_file_bytes is not None
                        and snapshot.size > self.config.max_file_bytes
                    ):
                        continue
                    _store_inventory(connection, snapshot, format_name, self.run_id)
                    cached = _cached_document(
                        connection,
                        snapshot,
                        self.config.processing_signature,
                    )
                    cache_consumed, cached_error = self._consume_cached(
                        connection,
                        snapshot,
                        format_name,
                        cached,
                        review_reconciliations,
                    )
                    if cache_consumed:
                        cache_hits += 1
                        cached_errors += int(cached_error)
                        processed += 1
                        if processed % OFFICE_COMMIT_BATCH == 0:
                            connection.commit()
                            flush_review_reconciliations()
                            report()
                        continue
                    outcome = self._process_candidate(
                        connection,
                        snapshot,
                        format_name,
                        review_reconciliations,
                    )
                    extracted += outcome.extracted
                    errors += outcome.errors
                    reviews += outcome.reviews
                    deletion_candidates += outcome.deletion_candidates
                    retryable_errors += outcome.retryable_errors
                    processed += 1
                    if processed % OFFICE_COMMIT_BATCH == 0:
                        connection.commit()
                        flush_review_reconciliations()
                        report()
                if processed >= selected_count:
                    break
            connection.commit()
            flush_review_reconciliations()
            pruned = 0
            if (
                not self.config.selection.active
                and self.config.max_documents is None
                and self.config.max_file_bytes is None
            ):
                pruned = _prune_stale_documents(connection, self.run_id)
                connection.commit()
        report(finished=True)
        processing = self.config.processing_provenance
        return OfficeRouteSummary(
            candidate_pool=candidate_pool,
            candidates=selected_count,
            skipped_by_size=candidate_pool - eligible,
            skipped_by_count=eligible - selected_count,
            processed=processed,
            cache_hits=cache_hits,
            cached_errors=cached_errors,
            extracted=extracted,
            errors=errors,
            cache_documents_pruned=pruned,
            review_candidates=reviews,
            deletion_candidates=deletion_candidates,
            retryable_errors=retryable_errors,
            peak_reserved_bytes=self.memory_gate.peak_reserved_bytes,
            memory_waits=self.memory_gate.wait_count,
            processing_signature=processing.signature,
            processing_provenance=processing.manifest,
        )


def _estimated_office_memory_bytes(
    snapshot: FileSnapshot,
    max_text_chars: int,
) -> int:
    return (
        64 * 1024 * 1024
        + min(snapshot.size * 2, 128 * 1024 * 1024)
        + min(max_text_chars * 4, 128 * 1024 * 1024)
    )


def _cached_office_failure(row: sqlite3.Row) -> OfficeExtractionError:
    recommendation = str(row["review_disposition"])
    if recommendation not in {"retry", "manual_review", "deletion_candidate"}:
        recommendation = "manual_review"
    return OfficeExtractionError(
        str(row["error_type"] or "office_cached_error"),
        str(row["error_message"] or "cached Office error"),
        recommendation=cast(ReviewRecommendation, recommendation),
        retryable=bool(row["retryable"]),
    )


def _store_inventory(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    format_name: str,
    run_id: int,
) -> None:
    connection.execute(
        "DELETE FROM office_inventory WHERE path=? COLLATE NOCASE AND file_key<>?",
        (snapshot.path, _file_key(snapshot)),
    )
    connection.execute(
        """INSERT INTO office_inventory(
        file_key,format,path,size,mtime_ns,birthtime_ns,last_seen_run_id)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(file_key) DO UPDATE SET
        format=excluded.format,path=excluded.path,size=excluded.size,
        mtime_ns=excluded.mtime_ns,birthtime_ns=excluded.birthtime_ns,
        last_seen_run_id=excluded.last_seen_run_id""",
        (
            _file_key(snapshot),
            format_name,
            snapshot.path,
            snapshot.size,
            snapshot.mtime_ns,
            snapshot.birthtime_ns,
            run_id,
        ),
    )


def _cached_document(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    processing_signature: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT status,error_type,error_message,retryable,review_disposition
        FROM documents WHERE file_key=? AND size=?
        AND mtime_ns=? AND birthtime_ns=? AND processing_signature=?""",
        (
            _file_key(snapshot),
            snapshot.size,
            snapshot.mtime_ns,
            snapshot.birthtime_ns,
            processing_signature,
        ),
    ).fetchone()


def _remove_path_conflict(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
) -> None:
    conflict = connection.execute(
        "SELECT file_key FROM documents WHERE path=? COLLATE NOCASE AND file_key<>?",
        (snapshot.path, _file_key(snapshot)),
    ).fetchone()
    if conflict is not None:
        connection.execute(
            "DELETE FROM document_fts WHERE file_key=?", (str(conflict[0]),)
        )
        connection.execute(
            "DELETE FROM documents WHERE file_key=?", (str(conflict[0]),)
        )


def _refresh_cached_path(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    format_name: str,
    run_id: int,
) -> None:
    _remove_path_conflict(connection, snapshot)
    connection.execute(
        """UPDATE documents SET format=?,path=?,last_seen_run_id=?,updated_ns=?
        WHERE file_key=?""",
        (format_name, snapshot.path, run_id, time.time_ns(), _file_key(snapshot)),
    )
    connection.execute(
        "UPDATE document_fts SET path=?,format=? WHERE file_key=?",
        (snapshot.path, format_name, _file_key(snapshot)),
    )


def _store_success(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    document: ExtractedOfficeDocument,
    processing_signature: str,
    run_id: int,
) -> None:
    _remove_path_conflict(connection, snapshot)
    text_bytes = document.text.encode("utf-8")
    fingerprint = xxhash.xxh3_128_hexdigest(text_bytes)
    connection.execute(
        """INSERT INTO documents(
        file_key,format,path,size,mtime_ns,birthtime_ns,processing_signature,status,
        title,author,subject,text_zlib,text_chars,text_xxh3_128,part_count,error_type,
        error_message,retryable,review_disposition,last_seen_run_id,updated_ns)
        VALUES(?,?,?,?,?,?,?,'complete',?,?,?,?,?,?,?,NULL,NULL,0,'none',?,?)
        ON CONFLICT(file_key) DO UPDATE SET format=excluded.format,path=excluded.path,
        size=excluded.size,mtime_ns=excluded.mtime_ns,
        birthtime_ns=excluded.birthtime_ns,
        processing_signature=excluded.processing_signature,status='complete',
        title=excluded.title,author=excluded.author,subject=excluded.subject,
        text_zlib=excluded.text_zlib,text_chars=excluded.text_chars,
        text_xxh3_128=excluded.text_xxh3_128,part_count=excluded.part_count,
        error_type=NULL,error_message=NULL,retryable=0,review_disposition='none',
        last_seen_run_id=excluded.last_seen_run_id,updated_ns=excluded.updated_ns""",
        (
            _file_key(snapshot),
            document.format,
            snapshot.path,
            snapshot.size,
            snapshot.mtime_ns,
            snapshot.birthtime_ns,
            processing_signature,
            document.title,
            document.author,
            document.subject,
            zlib.compress(text_bytes, 6),
            len(document.text),
            fingerprint,
            document.part_count,
            run_id,
            time.time_ns(),
        ),
    )
    connection.execute(
        "DELETE FROM document_fts WHERE file_key=?", (_file_key(snapshot),)
    )
    connection.execute(
        """INSERT INTO document_fts(file_key,format,path,title,author,body)
        VALUES(?,?,?,?,?,?)""",
        (
            _file_key(snapshot),
            document.format,
            snapshot.path,
            document.title,
            document.author,
            document.text,
        ),
    )


def _store_error(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    format_name: str,
    processing_signature: str,
    run_id: int,
    error: OfficeExtractionError,
) -> None:
    _remove_path_conflict(connection, snapshot)
    connection.execute(
        """INSERT INTO documents(
        file_key,format,path,size,mtime_ns,birthtime_ns,processing_signature,status,
        title,author,subject,text_zlib,text_chars,text_xxh3_128,part_count,error_type,
        error_message,retryable,review_disposition,last_seen_run_id,updated_ns)
        VALUES(?,?,?,?,?,?,?,'error',NULL,NULL,NULL,NULL,0,NULL,0,?,?,?, ?,?,?)
        ON CONFLICT(file_key) DO UPDATE SET format=excluded.format,path=excluded.path,
        size=excluded.size,mtime_ns=excluded.mtime_ns,
        birthtime_ns=excluded.birthtime_ns,
        processing_signature=excluded.processing_signature,status='error',
        title=NULL,author=NULL,subject=NULL,text_zlib=NULL,text_chars=0,
        text_xxh3_128=NULL,part_count=0,error_type=excluded.error_type,
        error_message=excluded.error_message,retryable=excluded.retryable,
        review_disposition=excluded.review_disposition,
        last_seen_run_id=excluded.last_seen_run_id,updated_ns=excluded.updated_ns""",
        (
            _file_key(snapshot),
            format_name,
            snapshot.path,
            snapshot.size,
            snapshot.mtime_ns,
            snapshot.birthtime_ns,
            processing_signature,
            error.code,
            str(error)[:2000],
            int(error.retryable),
            error.recommendation,
            run_id,
            time.time_ns(),
        ),
    )
    connection.execute(
        "DELETE FROM document_fts WHERE file_key=?", (_file_key(snapshot),)
    )


def _review_candidate(
    snapshot: FileSnapshot,
    error: OfficeExtractionError,
) -> ReviewCandidate:
    return ReviewCandidate(
        route_name="office",
        snapshot=snapshot,
        reason_code=error.code,
        source_status="error",
        recommendation=error.recommendation,
        retryable=error.retryable,
        confidence=0.98 if error.recommendation == "deletion_candidate" else 0.85,
        evidence={"message": str(error)[:512], "route_version": OFFICE_ROUTE_VERSION},
        detector_version=OFFICE_ROUTE_VERSION,
    )


def _prune_stale_documents(connection: sqlite3.Connection, run_id: int) -> int:
    stale_keys = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT file_key FROM documents WHERE last_seen_run_id<>?", (run_id,)
        )
    )
    for offset in range(0, len(stale_keys), 256):
        batch = stale_keys[offset : offset + 256]
        placeholders = ",".join("?" for _ in batch)
        connection.execute(
            f"DELETE FROM document_fts WHERE file_key IN ({placeholders})", batch
        )
        connection.execute(
            f"DELETE FROM documents WHERE file_key IN ({placeholders})", batch
        )
    connection.execute(
        "DELETE FROM office_inventory WHERE last_seen_run_id<>?", (run_id,)
    )
    return len(stale_keys)


# endregion [03]


# region [04] Read-only search


def search_office_state(path: Path, query: str, limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 1000:
        raise ValueError("Office search limit must be between 1 and 1000")
    with office_database(path, readonly=True) as connection:
        rows = connection.execute(
            """SELECT file_key,format,path,title,author,
            snippet(document_fts,5,'[',']',' … ',24) AS snippet,
            bm25(document_fts) AS rank FROM document_fts
            WHERE document_fts MATCH ? ORDER BY rank,path LIMIT ?""",
            (query, limit),
        )
        return [dict(row) for row in rows]


# endregion [04]
