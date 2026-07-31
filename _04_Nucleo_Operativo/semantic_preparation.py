"""Embedding backend construction, readiness probes and source prerequisites."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from .semantic_backends import (
    EmbeddingBackend,
    FastEmbedBackend,
    fastembed_availability,
)
from .semantic_config import (
    COMPACT_TEXT_MODEL_SIGNATURE,
    FASTEMBED_RUNTIME_VERSION,
    default_semantic_model_cache,
    default_semantic_threads,
    fastembed_cache_contract,
    production_models,
)
from .semantic_models import (
    BackendEmbedding,
    EmbeddingModality,
    EmbeddingModelSpec,
    EmbeddingRequest,
    EmbeddingRole,
    fingerprint_bytes,
    fingerprint_text,
)
from .semantic_schema import initialize_semantic_state
from .semantic_service_contracts import ModelPreparation
from .semantic_sources import semantic_source_database
from .semantic_state import register_embedding_model


# region [01] Backend contracts and construction


class BackendFactory(Protocol):
    def __call__(
        self,
        model: EmbeddingModelSpec,
        *,
        cache_dir: Path,
        local_files_only: bool,
        threads: int | None,
    ) -> EmbeddingBackend: ...


class SemanticModelUnavailableError(RuntimeError):
    """Typed optional-runtime failure safe to degrade during read-only search."""

    def __init__(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("semantic model unavailability reason cannot be blank")
        self.reason = reason
        super().__init__(reason)


def _is_local_model_runtime_error(exc: Exception) -> bool:
    module = type(exc).__module__
    return isinstance(exc, (OSError, EOFError, UnicodeError)) or module.startswith(
        (
            "fastembed.",
            "huggingface_hub.",
            "json.",
            "onnxruntime.",
            "tokenizers.",
        )
    )


class _ReadOnlyFastEmbedBackend:
    """Translate only recognized local model-load failures to optional status."""

    def __init__(self, delegate: EmbeddingBackend) -> None:
        self._delegate = delegate

    @property
    def model(self) -> EmbeddingModelSpec:
        return self._delegate.model

    @property
    def max_batch_size(self) -> int:
        return self._delegate.max_batch_size

    def embed(
        self,
        requests: Sequence[EmbeddingRequest],
    ) -> Sequence[BackendEmbedding]:
        try:
            return self._delegate.embed(requests)
        except Exception as exc:  # optional runtime types are dependency-defined
            if not _is_local_model_runtime_error(exc):
                raise
            raise SemanticModelUnavailableError(
                "semantic_query_model_unloadable"
            ) from exc


def model_cache(state_directory: Path, override: Path | None) -> Path:
    return (
        default_semantic_model_cache(state_directory) if override is None else override
    )


def require_local_fastembed_model(
    model: EmbeddingModelSpec,
    cache_dir: Path,
) -> None:
    """Validate one exact local snapshot without creating or enumerating paths."""

    availability = fastembed_availability()
    if not availability.installed:
        raise SemanticModelUnavailableError("semantic_backend_unavailable")
    expected_version = FASTEMBED_RUNTIME_VERSION.removeprefix("fastembed-")
    if availability.version != expected_version:
        raise SemanticModelUnavailableError("semantic_backend_version_mismatch")
    if "CPUExecutionProvider" not in availability.providers:
        raise SemanticModelUnavailableError("semantic_backend_unavailable")

    contract = fastembed_cache_contract(model.model_signature)
    if not cache_dir.is_dir():
        raise SemanticModelUnavailableError("semantic_model_cache_missing")
    repository = cache_dir / (
        "models--" + contract.repository_id.replace("/", "--")
    )
    reference = repository / "refs" / "main"
    if not reference.is_file():
        raise SemanticModelUnavailableError("semantic_query_model_not_cached")
    try:
        reference_size = reference.stat().st_size
        if not 1 <= reference_size <= 256:
            raise SemanticModelUnavailableError(
                "semantic_query_model_cache_invalid"
            )
        commit = reference.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise SemanticModelUnavailableError(
            "semantic_query_model_cache_invalid"
        ) from exc
    if not 40 <= len(commit) <= 64 or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise SemanticModelUnavailableError("semantic_query_model_cache_invalid")

    snapshot = repository / "snapshots" / commit
    if not snapshot.is_dir():
        raise SemanticModelUnavailableError("semantic_query_model_not_cached")
    for relative_path in contract.required_files:
        candidate = snapshot.joinpath(*relative_path.split("/"))
        try:
            valid = candidate.is_file() and candidate.stat().st_size > 0
        except OSError as exc:
            raise SemanticModelUnavailableError(
                "semantic_query_model_cache_invalid"
            ) from exc
        if not valid:
            raise SemanticModelUnavailableError(
                "semantic_query_model_cache_incomplete"
            )


def backend(
    model: EmbeddingModelSpec,
    *,
    cache_dir: Path,
    local_files_only: bool,
    threads: int | None,
) -> EmbeddingBackend:
    if local_files_only:
        require_local_fastembed_model(model, cache_dir)
    try:
        embedding_backend = FastEmbedBackend(
            model,
            cache_dir=cache_dir,
            local_files_only=local_files_only,
            threads=default_semantic_threads() if threads is None else threads,
            providers=("CPUExecutionProvider",),
        )
    except Exception as exc:  # optional runtime types are dependency-defined
        if not local_files_only or not _is_local_model_runtime_error(exc):
            raise
        raise SemanticModelUnavailableError(
            "semantic_query_model_unloadable"
        ) from exc
    return (
        _ReadOnlyFastEmbedBackend(embedding_backend)
        if local_files_only
        else embedding_backend
    )


# endregion [01]


# region [02] Explicit readiness probes


def text_probe(embedding_backend: EmbeddingBackend) -> None:
    text = "Neocortex industrial electrical semantic readiness probe"
    request = EmbeddingRequest(
        request_id="readiness-text",
        role=EmbeddingRole.QUERY,
        fingerprint=fingerprint_text(text),
        text=text,
    )
    embedding_backend.embed((request,))


def image_probe(embedding_backend: EmbeddingBackend) -> None:
    """Load the vision model against a temporary non-user raster and remove it."""

    from PIL import Image, ImageDraw

    handle = tempfile.NamedTemporaryFile(
        prefix="neocortex-semantic-probe-",
        suffix=".png",
        delete=False,
    )
    probe_path = Path(handle.name)
    handle.close()
    try:
        image = Image.new("RGB", (64, 64), "white")
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((12, 16, 52, 48), fill="gray", outline="black", width=2)
        image.save(probe_path, format="PNG")
        image.close()
        payload = probe_path.read_bytes()
        fingerprint = fingerprint_bytes(payload)
        source_stat = probe_path.stat()
        embedding_backend.embed(
            (
                EmbeddingRequest(
                    request_id="readiness-image",
                    role=EmbeddingRole.IMAGE,
                    fingerprint=fingerprint,
                    image_path=probe_path,
                    source_revision={
                        "size_bytes": source_stat.st_size,
                        "mtime_ns": source_stat.st_mtime_ns,
                        "birthtime_ns": getattr(
                            source_stat,
                            "st_birthtime_ns",
                            source_stat.st_ctime_ns,
                        ),
                        "raw_content_xxh3_128": fingerprint.xxh3_128,
                    },
                ),
            )
        )
    finally:
        probe_path.unlink(missing_ok=True)


def prepare_semantic_models(
    state_directory: Path,
    *,
    model_cache_override: Path | None = None,
    include_compact: bool = False,
    local_files_only: bool = False,
    threads: int | None = None,
    backend_factory: BackendFactory = backend,
) -> tuple[ModelPreparation, ...]:
    """Acquire/load explicit production models; this never indexes user content."""

    cache = model_cache(state_directory, model_cache_override)
    cache.mkdir(parents=True, exist_ok=True)
    models = [
        model
        for model in production_models()
        if include_compact or model.model_signature != COMPACT_TEXT_MODEL_SIGNATURE
    ]
    results: list[ModelPreparation] = []
    for model in models:
        started = time.perf_counter()
        embedding_backend = backend_factory(
            model,
            cache_dir=cache,
            local_files_only=local_files_only,
            threads=threads,
        )
        if model.modality is EmbeddingModality.TEXT:
            text_probe(embedding_backend)
        else:
            image_probe(embedding_backend)
        results.append(
            ModelPreparation(
                model.model_signature,
                model.model_id,
                model.dimensions,
                time.perf_counter() - started,
            )
        )
    return tuple(results)


# endregion [02]


# region [03] State and source prerequisites


def initialize_models(
    database: Path,
    models: Iterable[EmbeddingModelSpec],
) -> None:
    initialize_semantic_state(database)
    for model in models:
        register_embedding_model(database, model)


def require_source_databases(
    state_directory: Path,
    source_kinds: Iterable[str],
) -> None:
    selected_paths = {
        source_kind: semantic_source_database(state_directory, source_kind)
        for source_kind in source_kinds
    }
    invalid = {
        source_kind: path
        for source_kind, path in selected_paths.items()
        if path.exists() and not path.is_file()
    }
    if invalid:
        details = ", ".join(
            f"{source_kind}={path}" for source_kind, path in invalid.items()
        )
        raise ValueError(f"semantic source state is not a regular file: {details}")
    missing = {
        source_kind: path
        for source_kind, path in selected_paths.items()
        if not path.is_file()
    }
    if missing:
        details = ", ".join(
            f"{source_kind}={path}" for source_kind, path in missing.items()
        )
        raise FileNotFoundError(f"semantic source state is missing: {details}")


# endregion [03]
