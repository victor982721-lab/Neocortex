"""Incremental, non-destructive source-code analysis over the shared inventory.

The route never walks the filesystem independently.  It consumes immutable
``FileSnapshot`` records, binds every read to the observed physical identity,
and publishes one complete file version and its children in a single SQLite
transaction.  Optional language analyzers are lazy and always degrade to a
searchable textual representation.
"""

from __future__ import annotations

import os
import stat
import time
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, replace
from typing import Iterable, Mapping, Protocol

from _02_Deduplicacion import (
    FileChangedError,
    FileSnapshot,
    stat_matches_snapshot,
)
from _02_Deduplicacion.path_io import native_io_path
from _03_Progreso import (
    ProgressCallback,
    ProgressEvent,
    ProgressMetric,
    emit_progress,
)

from .cancellation import CancellationRequested, CancellationToken
from .code_analyzers import AnalyzerRegistry, builtin_analyzer_registry
from .code_contracts import (
    AnalysisStatus,
    ArtifactClassification,
    CodeAnalysis,
    CodeFileInput,
    CodeRouteConfig,
    CodeRouteSummary,
    DiagnosticRecord,
    DiagnosticSeverity,
)
from .code_detection import (
    DETECTOR_VERSION,
    classify_artifact,
    decode_text,
    likely_code_candidate,
    looks_binary,
)
from .code_state import CachedCodeVersion, CodeState, SkippedCodeObservation
from .semantic_models import fingerprint_bytes


# region [01] Structural collaborators and safe I/O


class CodeFrameworkState(Protocol):
    """Small shared-state surface used for resumable route phases."""

    def begin_route_phase(
        self,
        run_id: int,
        route_name: str,
        phase_name: str,
        *,
        source_run_id: int | None = None,
    ) -> None: ...

    def complete_route_phase(
        self,
        run_id: int,
        route_name: str,
        phase_name: str,
        summary: Mapping[str, object] | None = None,
    ) -> None: ...

    def fail_route_phase(
        self,
        run_id: int,
        route_name: str,
        phase_name: str,
        exc: BaseException,
    ) -> None: ...


class CodeInventory(Protocol):
    """Read-only inventory projection consumed by the code route."""

    def snapshots(self, scan_id: int) -> Iterable[FileSnapshot]: ...


class CodeResourceGate(Protocol):
    """Minimal admission surface; keeps direct route execution independent."""

    def admit(self, estimated_bytes: int) -> AbstractContextManager[None]: ...


_MIB = 1024 * 1024
_CODE_ANALYSIS_FIXED_BYTES = 4 * _MIB
_CODE_ANALYSIS_RAW_BYTES_FACTOR = 2
_CODE_ANALYSIS_TEXT_BYTES_FACTOR = 12
_CODE_GRAPH_FIXED_BYTES = 8 * _MIB
_CODE_GRAPH_DATABASE_BYTES_CAP = 64 * _MIB


def estimate_code_analysis_memory_bytes(observed_size: int, max_text_chars: int) -> int:
    """Estimate one in-memory decode, parser tree and persisted result.

    The route retains raw bytes, decoded text, source maps and structural
    records until atomic publication.  Twelve bytes per possible character is
    a conservative allowance for Unicode storage plus parser/result objects;
    two raw-byte equivalents cover the immutable payload and bounded working
    copies.  The estimate is per candidate, never for the entire route.
    """

    if observed_size < 0 or max_text_chars < 1:
        raise ValueError("code memory estimate requires non-negative size and text")
    text_chars_upper_bound = min(observed_size, max_text_chars)
    return (
        _CODE_ANALYSIS_FIXED_BYTES
        + observed_size * _CODE_ANALYSIS_RAW_BYTES_FACTOR
        + text_chars_upper_bound * _CODE_ANALYSIS_TEXT_BYTES_FACTOR
    )


def estimate_code_graph_memory_bytes(state_path: os.PathLike[str] | str) -> int:
    """Estimate bounded SQLite graph working memory from live database bytes."""

    database = os.fspath(state_path)
    observed_bytes = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            observed_bytes += os.stat(database + suffix).st_size
        except OSError:
            continue
    return _CODE_GRAPH_FIXED_BYTES + min(observed_bytes, _CODE_GRAPH_DATABASE_BYTES_CAP)


def _read_exact_snapshot(
    snapshot: FileSnapshot,
    limit: int,
    cancellation: CancellationToken | None = None,
) -> bytes:
    """Read a regular non-reparse file once, bounded and identity checked."""

    path = native_io_path(snapshot.path)
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise FileChangedError(f"cannot inspect {snapshot.path}: {exc}") from exc
    file_attributes = int(getattr(path_stat, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if stat.S_ISLNK(path_stat.st_mode) or file_attributes & reparse_attribute:
        raise FileChangedError(f"refusing link or reparse point: {snapshot.path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise FileChangedError(f"refusing non-regular file: {snapshot.path}")
    if snapshot.size > limit:
        raise ValueError(
            f"file exceeds configured code limit ({snapshot.size}>{limit})"
        )

    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            descriptor = None
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not stat_matches_snapshot(
                snapshot, before
            ):
                raise FileChangedError(
                    f"inventory identity changed before reading: {snapshot.path}"
                )
            chunks: list[bytes] = []
            remaining = snapshot.size
            while remaining:
                if cancellation is not None:
                    cancellation.checkpoint()
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise FileChangedError(
                        f"unexpected end of file while reading: {snapshot.path}"
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
            if stream.read(1):
                raise FileChangedError(f"file grew while reading: {snapshot.path}")
            if cancellation is not None:
                cancellation.checkpoint()
            after = os.fstat(stream.fileno())
            if not stat_matches_snapshot(snapshot, after):
                raise FileChangedError(
                    f"inventory identity changed while reading: {snapshot.path}"
                )
            return b"".join(chunks)
    except FileChangedError:
        raise
    except OSError as exc:
        raise FileChangedError(f"cannot read {snapshot.path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _diagnostic(
    code: str,
    message: str,
    *,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    confirmed: bool = True,
) -> DiagnosticRecord:
    return DiagnosticRecord(
        source="neocortex-code-route",
        code=code,
        severity=severity,
        message=message[:4096],
        tool_name="neocortex-code-route",
        tool_version="1",
        confirmed=confirmed,
        confidence=1.0 if confirmed else 0.7,
    )


# endregion [01]


# region [02] Bounded route runtime


class CodeRoute:
    """Analyze code artifacts incrementally without mutating source files."""

    route_name = "code"

    def __init__(
        self,
        config: CodeRouteConfig,
        dedup_index: CodeInventory,
        framework_state: CodeFrameworkState,
        framework_run_id: int,
        scan_id: int,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
        analyzers: AnalyzerRegistry | None = None,
        memory_gate: CodeResourceGate | None = None,
    ):
        self.config = config
        self.dedup_index = dedup_index
        self.framework_state = framework_state
        self.framework_run_id = framework_run_id
        self.scan_id = scan_id
        self.progress = progress
        self.cancellation = cancellation or CancellationToken()
        self.analyzers = analyzers or builtin_analyzer_registry()
        self.memory_gate = memory_gate
        self.processing_signature = (
            f"{self.config.processing_signature}|"
            f"artifact-detector={DETECTOR_VERSION}|"
            f"{self.analyzers.processing_signature}"
        )
        self._selected_paths = frozenset(
            os.path.normcase(os.path.abspath(item))
            for item in self.config.selection.paths
        )

    def _emit(
        self,
        completed: int,
        *,
        finished: bool = False,
        errors: int = 0,
        cache_hits: int = 0,
    ) -> None:
        emit_progress(
            self.progress,
            ProgressEvent(
                operation="code",
                phase="analysis",
                description="Análisis incremental de código",
                completed=completed,
                total=self.config.max_documents,
                unit="archivos",
                finished=finished,
                metrics=(
                    ProgressMetric("cache_hits", cache_hits),
                    ProgressMetric("errors", errors),
                ),
            ),
        )

    def _selected_path(self, path: str) -> bool:
        if not self._selected_paths:
            return True
        normalized = os.path.normcase(os.path.abspath(path))
        return normalized in self._selected_paths

    def _candidate_admission(
        self, snapshot: FileSnapshot
    ) -> AbstractContextManager[None]:
        if self.memory_gate is None:
            return nullcontext()
        estimated_bytes = estimate_code_analysis_memory_bytes(
            snapshot.size, self.config.max_text_chars
        )
        return self.memory_gate.admit(estimated_bytes)

    def _graph_admission(self) -> AbstractContextManager[None]:
        if self.memory_gate is None:
            return nullcontext()
        return self.memory_gate.admit(
            estimate_code_graph_memory_bytes(self.config.state_path)
        )

    def _resolve_analyzer_identity(
        self,
        language: str | None,
        generic_only: bool,
    ) -> tuple[str, str]:
        """Resolve the analyzer that would execute under current availability."""

        analyzer = self.analyzers.analyzer_for(None if generic_only else language)
        return analyzer.analyzer_id, analyzer.analyzer_version

    def _record_cache_hit(
        self,
        cached: CachedCodeVersion,
        counters: dict[str, int],
    ) -> None:
        status = AnalysisStatus(cached.status)
        counters["cache_hits"] += 1
        counters["generated"] += int(cached.generated)
        counters["vendored"] += int(cached.vendored)
        counters["symbols"] += cached.symbols
        counters["references"] += cached.references
        counters["diagnostics"] += cached.diagnostics
        counters["binary_skips"] += int(status is AnalysisStatus.BINARY)
        counters["skipped_limit"] += int(status is AnalysisStatus.SKIPPED_LIMIT)
        counters["text_only"] += int(status is AnalysisStatus.TEXT_ONLY)
        counters["partial"] += int(status is AnalysisStatus.PARTIAL)
        counters["errors"] += int(status is AnalysisStatus.ERROR)
        self._emit(
            counters["candidates"],
            errors=counters["errors"],
            cache_hits=counters["cache_hits"],
        )

    def _skipped_observation(
        self,
        snapshot: FileSnapshot,
        classification: ArtifactClassification,
        status: AnalysisStatus,
        diagnostic: DiagnosticRecord,
        *,
        raw: bytes | None = None,
        text: str = "",
        encoding: str | None = None,
        parser_kind: str = "route-guard",
        provenance: dict[str, object] | None = None,
    ) -> SkippedCodeObservation:
        raw_fingerprint = None if raw is None else fingerprint_bytes(raw)
        return SkippedCodeObservation(
            snapshot=snapshot,
            classification=classification,
            processing_signature=self.processing_signature,
            status=status,
            analyzer_id="neocortex-code-route",
            analyzer_version="1",
            parser_kind=parser_kind,
            diagnostic=diagnostic,
            encoding=encoding,
            text_excerpt=text,
            text_truncated=bool(text) and len(text) >= self.config.max_text_chars,
            raw_xxh3_128=(
                None if raw_fingerprint is None else raw_fingerprint.xxh3_128
            ),
            raw_xxh3_64_guard=(
                None if raw_fingerprint is None else raw_fingerprint.xxh3_64_guard
            ),
            provenance=provenance or {},
        )

    def _analyze_bytes(
        self, snapshot: FileSnapshot, raw: bytes
    ) -> tuple[CodeAnalysis | SkippedCodeObservation, int, int]:
        if looks_binary(raw):
            classification = classify_artifact(snapshot.path, "")
            return (
                self._skipped_observation(
                    snapshot,
                    classification,
                    AnalysisStatus.BINARY,
                    _diagnostic(
                        "binary_payload",
                        "candidate contains binary control bytes and was not parsed",
                        severity=DiagnosticSeverity.INFO,
                    ),
                    raw=raw,
                    provenance={"binary_probe_bytes": min(len(raw), 8192)},
                ),
                0,
                0,
            )

        text, encoding, encoding_evidence = decode_text(raw, snapshot.path)
        truncated = len(text) > self.config.max_text_chars
        if truncated:
            text = text[: self.config.max_text_chars]
        classification = classify_artifact(snapshot.path, text)
        classification = replace(
            classification,
            evidence=tuple(
                dict.fromkeys((*classification.evidence, *encoding_evidence))
            ),
        )
        source = CodeFileInput(
            snapshot=snapshot,
            text=text,
            raw_bytes=raw,
            encoding=encoding,
            classification=classification,
            processing_signature=self.processing_signature,
        )

        policy_code = None
        if classification.generated and not self.config.include_generated:
            policy_code = "generated_excluded_by_policy"
        elif classification.vendored and not self.config.include_vendored:
            policy_code = "vendored_excluded_by_policy"
        if policy_code is not None:
            return (
                self._skipped_observation(
                    snapshot,
                    classification,
                    AnalysisStatus.TEXT_ONLY,
                    _diagnostic(
                        policy_code,
                        "artifact remained searchable but structural analysis was disabled",
                        severity=DiagnosticSeverity.INFO,
                    ),
                    raw=raw,
                    text=text,
                    encoding=encoding,
                    parser_kind="policy-text-only",
                    provenance={"policy": policy_code},
                ),
                len(text),
                0,
            )

        started = time.perf_counter_ns()
        analyzer = (
            self.analyzers.analyzer_for(None)
            if truncated
            else self.analyzers.analyzer_for(classification.language)
        )
        analysis = analyzer.analyze(source, self.config)
        analyze_ns = time.perf_counter_ns() - started
        if truncated:
            analysis = replace(
                analysis,
                status=AnalysisStatus.PARTIAL,
                text_truncated=True,
                diagnostics=(
                    *analysis.diagnostics,
                    _diagnostic(
                        "text_limit",
                        "searchable text was bounded by code max_text_chars",
                        severity=DiagnosticSeverity.WARNING,
                    ),
                ),
                provenance={
                    **analysis.provenance,
                    "text_limit_chars": self.config.max_text_chars,
                    "native_parser_skipped": True,
                },
            )
        return analysis, len(text), analyze_ns

    def _process_candidate(
        self,
        state: CodeState,
        snapshot: FileSnapshot,
        counters: dict[str, int],
        elapsed_nanoseconds: dict[str, int],
    ) -> bool:
        """Process one candidate and report whether graph inputs changed."""

        if (
            self.config.cache_validation == "metadata"
            or snapshot.size > self.config.max_file_bytes
        ):
            cached = state.reuse_cached(
                snapshot,
                self.processing_signature,
                self.framework_run_id,
                retry_errors=self.config.retry_errors,
                resolve_analyzer_identity=self._resolve_analyzer_identity,
            )
            if cached is not None:
                self._record_cache_hit(cached, counters)
                return False

        if snapshot.size > self.config.max_file_bytes:
            classification = classify_artifact(snapshot.path, "")
            observation = self._skipped_observation(
                snapshot,
                classification,
                AnalysisStatus.SKIPPED_LIMIT,
                _diagnostic(
                    "file_limit",
                    f"file size {snapshot.size} exceeds "
                    f"limit {self.config.max_file_bytes}",
                    severity=DiagnosticSeverity.WARNING,
                ),
                provenance={
                    "observed_size": snapshot.size,
                    "max_file_bytes": self.config.max_file_bytes,
                },
            )
            persist_started = time.perf_counter_ns()
            _, replaced_version = state.store_skipped(
                observation, self.framework_run_id
            )
            elapsed_nanoseconds["persist"] += time.perf_counter_ns() - persist_started
            counters["processed"] += 1
            counters["skipped_limit"] += 1
            counters["diagnostics"] += 1
            counters["invalidated_versions"] += int(replaced_version)
            return True

        with self._candidate_admission(snapshot):
            self.cancellation.checkpoint()
            preloaded_raw: bytes | None = None
            preload_error: (
                FileChangedError | OSError | UnicodeError | ValueError | None
            ) = None
            if self.config.cache_validation == "full":
                try:
                    read_started = time.perf_counter_ns()
                    preloaded_raw = _read_exact_snapshot(
                        snapshot,
                        self.config.max_file_bytes,
                        self.cancellation,
                    )
                    elapsed_nanoseconds["read"] += time.perf_counter_ns() - read_started
                    counters["bytes_read"] += len(preloaded_raw)
                    raw_fingerprint = fingerprint_bytes(preloaded_raw)
                    self.cancellation.checkpoint()
                    cached = state.reuse_cached(
                        snapshot,
                        self.processing_signature,
                        self.framework_run_id,
                        retry_errors=self.config.retry_errors,
                        raw_xxh3_128=raw_fingerprint.xxh3_128,
                        raw_xxh3_64_guard=raw_fingerprint.xxh3_64_guard,
                        resolve_analyzer_identity=self._resolve_analyzer_identity,
                    )
                    if cached is not None:
                        self._record_cache_hit(cached, counters)
                        return False
                except CancellationRequested:
                    raise
                except (
                    FileChangedError,
                    OSError,
                    UnicodeError,
                    ValueError,
                ) as exc:
                    preload_error = exc

            try:
                if preload_error is not None:
                    raise preload_error
                if preloaded_raw is None:
                    read_started = time.perf_counter_ns()
                    raw = _read_exact_snapshot(
                        snapshot,
                        self.config.max_file_bytes,
                        self.cancellation,
                    )
                    elapsed_nanoseconds["read"] += time.perf_counter_ns() - read_started
                    counters["bytes_read"] += len(raw)
                else:
                    raw = preloaded_raw
                result, text_chars, analyze_ns = self._analyze_bytes(snapshot, raw)
                self.cancellation.checkpoint()
                counters["text_chars"] += text_chars
                elapsed_nanoseconds["analyze"] += analyze_ns
            except CancellationRequested:
                raise
            except (FileChangedError, OSError, UnicodeError, ValueError) as exc:
                classification = classify_artifact(snapshot.path, "")
                result = self._skipped_observation(
                    snapshot,
                    classification,
                    AnalysisStatus.ERROR,
                    _diagnostic(type(exc).__name__, str(exc)),
                    provenance={"transient": isinstance(exc, FileChangedError)},
                )
                if isinstance(exc, FileChangedError):
                    counters["stale_inventory"] += 1
            except Exception as exc:
                classification = classify_artifact(snapshot.path, "")
                result = self._skipped_observation(
                    snapshot,
                    classification,
                    AnalysisStatus.ERROR,
                    _diagnostic("analyzer_failure", f"{type(exc).__name__}: {exc}"),
                    provenance={"analyzer_failure": type(exc).__name__},
                )

            persist_started = time.perf_counter_ns()
            if isinstance(result, CodeAnalysis):
                _, replaced_version = state.store_analysis(
                    result, self.framework_run_id
                )
                counters["symbols"] += len(result.symbols)
                counters["references"] += len(result.references)
                counters["diagnostics"] += len(result.diagnostics)
                counters["text_only"] += int(result.status is AnalysisStatus.TEXT_ONLY)
                counters["partial"] += int(result.status is AnalysisStatus.PARTIAL)
                counters["generated"] += int(result.input.classification.generated)
                counters["vendored"] += int(result.input.classification.vendored)
            else:
                _, replaced_version = state.store_skipped(result, self.framework_run_id)
                counters["diagnostics"] += 1
                counters["binary_skips"] += int(result.status is AnalysisStatus.BINARY)
                counters["text_only"] += int(result.status is AnalysisStatus.TEXT_ONLY)
                counters["generated"] += int(result.classification.generated)
                counters["vendored"] += int(result.classification.vendored)
            elapsed_nanoseconds["persist"] += time.perf_counter_ns() - persist_started
            counters["invalidated_versions"] += int(replaced_version)
            counters["processed"] += 1
            if result.status is AnalysisStatus.ERROR:
                counters["errors"] += 1
            self._emit(
                counters["candidates"],
                errors=counters["errors"],
                cache_hits=counters["cache_hits"],
            )
            return True

    def run(self) -> CodeRouteSummary:
        """Run analysis and graph publication with durable phase boundaries."""

        counters: dict[str, int] = {
            field: 0
            for field in CodeRouteSummary.__dataclass_fields__
            if field != "processing_signature"
        }
        elapsed_nanoseconds = {"read": 0, "analyze": 0, "persist": 0}
        graph_inputs_changed = False
        analysis_run_id: int | None = None
        current_phase = "analysis"
        self.framework_state.begin_route_phase(
            self.framework_run_id, self.route_name, current_phase
        )
        try:
            with CodeState(self.config.state_path) as state:
                analysis_run_id = state.begin_run(
                    self.framework_run_id,
                    self.scan_id,
                    self.processing_signature,
                )
                self._emit(0)
                for snapshot in self.dedup_index.snapshots(self.scan_id):
                    self.cancellation.checkpoint()
                    if not likely_code_candidate(
                        snapshot.path
                    ) or not self._selected_path(snapshot.path):
                        continue
                    if not state.matches_selection(snapshot, self.config.selection):
                        continue
                    if (
                        self.config.max_documents is not None
                        and counters["candidates"] >= self.config.max_documents
                    ):
                        break
                    counters["candidates"] += 1
                    candidate_changed = self._process_candidate(
                        state,
                        snapshot,
                        counters,
                        elapsed_nanoseconds,
                    )
                    graph_inputs_changed = graph_inputs_changed or candidate_changed

                for operation, elapsed in elapsed_nanoseconds.items():
                    counters[f"{operation}_milliseconds"] = elapsed // 1_000_000
                analysis_summary = {
                    "processing_signature": self.processing_signature,
                    **counters,
                }
                self.framework_state.complete_route_phase(
                    self.framework_run_id,
                    self.route_name,
                    current_phase,
                    analysis_summary,
                )
                current_phase = "graph"
                self.framework_state.begin_route_phase(
                    self.framework_run_id, self.route_name, current_phase
                )
                graph_started = time.perf_counter_ns()
                full_reconciliation = (
                    self.config.max_documents is None
                    and not self.config.selection.active
                )
                self.cancellation.checkpoint()
                if full_reconciliation:
                    missing_versions = state.mark_missing(self.framework_run_id)
                    counters["invalidated_versions"] += missing_versions
                    graph_inputs_changed = graph_inputs_changed or missing_versions > 0
                    self.cancellation.checkpoint()
                reusable_projects = None
                if (
                    full_reconciliation
                    and not graph_inputs_changed
                    and counters["processed"] == 0
                    and counters["cache_hits"] == counters["candidates"]
                    and counters["invalidated_versions"] == 0
                ):
                    reusable_projects = state.reusable_graph_project_count(
                        analysis_run_id,
                        self.processing_signature,
                    )
                if reusable_projects is None:
                    with self._graph_admission():
                        self.cancellation.checkpoint()
                        counters["projects"] = state.finalize_graph(
                            self.framework_run_id,
                            cancellation_check=self.cancellation.checkpoint,
                        )
                        self.cancellation.checkpoint()
                else:
                    counters["projects"] = reusable_projects
                    self.cancellation.checkpoint()
                counters["graph_milliseconds"] = (
                    time.perf_counter_ns() - graph_started
                ) // 1_000_000
                summary = CodeRouteSummary(
                    processing_signature=self.processing_signature,
                    **counters,
                )
                payload = asdict(summary)
                state.complete_run(
                    analysis_run_id,
                    payload,
                    partial=(
                        self.config.max_documents is not None
                        or self.config.selection.active
                    ),
                    graph_current=True,
                )
                self.framework_state.complete_route_phase(
                    self.framework_run_id,
                    self.route_name,
                    current_phase,
                    payload,
                )
                self._emit(
                    counters["candidates"],
                    finished=True,
                    errors=counters["errors"],
                    cache_hits=counters["cache_hits"],
                )
                return summary
        except BaseException as exc:
            if analysis_run_id is not None:
                try:
                    with CodeState(self.config.state_path) as state:
                        state.fail_run(analysis_run_id, exc)
                except Exception as cleanup_exc:
                    exc.add_note(
                        "code run failure could not be persisted: "
                        f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            self.framework_state.fail_route_phase(
                self.framework_run_id,
                self.route_name,
                current_phase,
                exc,
            )
            raise


# endregion [02]


__all__ = [
    "CodeRoute",
    "CodeRouteConfig",
    "CodeRouteSummary",
    "estimate_code_analysis_memory_bytes",
    "estimate_code_graph_memory_bytes",
]
