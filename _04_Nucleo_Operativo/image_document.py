"""Bounded OCR evidence for document-like raster images.

The verifier is deliberately auxiliary: it records compact layout/semantic
signals, returns only a bounded prefix of recognized text for persistence, and
never turns an OCR failure into an image-route failure.  Tesseract runs inside
the existing isolated image worker, so the parent can cancel and contain the
complete process tree.
"""

# region [01] Imports, policy and result models

from __future__ import annotations

import csv
import io
import re
import subprocess
import unicodedata
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

from .bounded_subprocess import run_bounded_capture
from .image_decode import pillow_decode_scope
from .image_policy import (
    DOCUMENT_OCR_TEXT_MAX_UTF8_BYTES,
    INDUSTRIAL_ACTIVITY_HINTS,
    INDUSTRIAL_ENTITY_HINTS,
    OPERATIONAL_CONTEXT_HINTS,
    SAFETY_CONDITION_HINTS,
)
from .processing_provenance import (
    build_processing_provenance,
    resolve_tesseract_runtime,
)

DOCUMENT_OCR_VERSION = "document-text-tesseract-v2"
DOCUMENT_OCR_SAMPLE_SIDE = 768
DOCUMENT_OCR_MEMORY_BYTES = 64 * 1024 * 1024
DOCUMENT_OCR_TSV_MAX_BYTES = 8 * 1024 * 1024
DOCUMENT_OCR_DIAGNOSTIC_MAX_BYTES = 256 * 1024
OCR_WORD_CONFIDENCE = 30.0
TOKEN_RE = re.compile(r"[a-z0-9]+")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DOCUMENT_TERMS = {
    "acta": ("acta",),
    "certificado": ("certificado", "certificate"),
    "contrato": ("contrato", "contract"),
    "estado_cuenta": ("estado de cuenta", "account statement"),
    "factura": ("factura", "invoice", "cfdi"),
    "folio": ("folio",),
    "informe": ("informe", "reporte", "report"),
    "recibo": ("recibo", "receipt"),
    "tabla": ("subtotal", "cantidad", "quantity"),
}

UI_TERMS = {
    "busqueda": ("buscar", "search", "no se encontraron resultados"),
    "configuracion": ("configuracion", "settings"),
    "descarga": ("descargar", "download"),
    "inicio_sesion": ("iniciar sesion", "sign in", "log in"),
    "instrument_controls": (
        "ramp wizard",
        "report all ramps",
        "akimi",
        "gerilim",
    ),
    "navegacion": ("home", "menu", "back"),
    "operacion_app": ("enviar dinero", "share", "compartir", "cancelar", "cancel"),
}


@dataclass(frozen=True, slots=True)
class DocumentVerifierConfig:
    mode: Literal["auto", "never"] = "auto"
    lang: str = "spa+eng"
    timeout_seconds: float = 12.0
    tesseract_cmd: str | None = None
    tessdata_dir: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentVerifierRuntime:
    enabled: bool
    lang: str
    timeout_seconds: float
    tesseract_cmd: str | None
    tessdata_dir: str | None
    signature: str
    provenance: str | None = None
    unavailable_reason: str | None = None
    processing_provenance_json: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentTextEvidence:
    attempted: bool
    available: bool
    word_count: int = 0
    line_count: int = 0
    character_count: int = 0
    recognized_text: str = ""
    recognized_text_truncated: bool = False
    text_coverage: float = 0.0
    mean_confidence: float = 0.0
    document_terms: tuple[str, ...] = ()
    ui_terms: tuple[str, ...] = ()
    industrial_entities: tuple[str, ...] = ()
    industrial_activities: tuple[str, ...] = ()
    industrial_operational_contexts: tuple[str, ...] = ()
    industrial_safety_conditions: tuple[str, ...] = ()
    provenance: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def dense_text(self) -> bool:
        return (
            self.available
            and self.word_count >= 15
            and self.line_count >= 8
            and self.character_count >= 80
        )


# endregion [01]


# region [02] Runtime resolution


def _safe_error(exc: BaseException) -> str:
    return str(exc).encode("utf-8", "replace").decode("utf-8")[:500]


def _document_ocr_processing_provenance(
    config: DocumentVerifierConfig,
    component: dict[str, object],
):
    return build_processing_provenance(
        "image-document-ocr",
        DOCUMENT_OCR_VERSION,
        {
            "language": config.lang,
            "mode": config.mode,
            "sample_side": DOCUMENT_OCR_SAMPLE_SIDE,
            "text_max_utf8_bytes": DOCUMENT_OCR_TEXT_MAX_UTF8_BYTES,
        },
        (component,),
        compatibility_tag=DOCUMENT_OCR_VERSION,
    )


def resolve_document_verifier(
    config: DocumentVerifierConfig,
) -> DocumentVerifierRuntime:
    """Resolve executable, languages and version once in the parent process."""

    if config.mode not in {"auto", "never"}:
        raise ValueError(f"unsupported image document OCR mode: {config.mode}")
    if config.timeout_seconds <= 0:
        raise ValueError("image document OCR timeout must be positive")
    requested = tuple(part for part in config.lang.split("+") if part)
    if not requested:
        raise ValueError("image document OCR language must not be empty")
    if config.mode == "never":
        processing = _document_ocr_processing_provenance(
            config,
            {
                "name": "tesseract",
                "kind": "native-executable",
                "status": "disabled",
            },
        )
        return DocumentVerifierRuntime(
            False,
            config.lang,
            config.timeout_seconds,
            None,
            config.tessdata_dir,
            processing.signature,
            unavailable_reason="disabled_by_configuration",
            processing_provenance_json=processing.manifest_json,
        )

    runtime = resolve_tesseract_runtime(
        command=config.tesseract_cmd,
        tessdata_dir=config.tessdata_dir,
        language=config.lang,
        timeout_seconds=config.timeout_seconds,
    )
    processing = _document_ocr_processing_provenance(config, runtime.component)
    if runtime.available:
        provenance = f"tesseract-{runtime.version}|layout-keywords-v2"
        return DocumentVerifierRuntime(
            True,
            config.lang,
            config.timeout_seconds,
            runtime.command,
            runtime.tessdata_dir,
            processing.signature,
            provenance=provenance,
            processing_provenance_json=processing.manifest_json,
        )
    return DocumentVerifierRuntime(
        False,
        config.lang,
        config.timeout_seconds,
        None,
        runtime.tessdata_dir,
        processing.signature,
        unavailable_reason=runtime.unavailable_reason,
        processing_provenance_json=processing.manifest_json,
    )


# endregion [02]


# region [03] Bounded OCR and compact semantics


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(TOKEN_RE.findall(normalized))


def _semantic_hits(text: str, groups: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    normalized = f" {_normalized_text(text)} "
    labels = []
    for label, phrases in groups.items():
        if any(f" {_normalized_text(phrase)} " in normalized for phrase in phrases):
            labels.append(label)
    return tuple(sorted(labels))


def verify_document_text(
    path: Path,
    runtime: DocumentVerifierRuntime,
    memory_gate=None,
) -> DocumentTextEvidence:
    """Return counts, labels and a whole-word UTF-8-bounded text prefix."""

    if not runtime.enabled:
        return DocumentTextEvidence(
            attempted=False,
            available=False,
            error_type="VerifierUnavailable",
            error_message=runtime.unavailable_reason,
        )

    admission = (
        memory_gate.admit(DOCUMENT_OCR_MEMORY_BYTES)
        if memory_gate is not None
        else nullcontext()
    )
    try:
        assert runtime.tesseract_cmd is not None
        with admission:
            with pillow_decode_scope(allow_truncated=False):
                with Image.open(path) as source:
                    oriented = ImageOps.exif_transpose(source)
                    try:
                        sample = oriented.convert("L")
                    finally:
                        if oriented is not source:
                            oriented.close()
                    try:
                        sample.thumbnail(
                            (DOCUMENT_OCR_SAMPLE_SIDE, DOCUMENT_OCR_SAMPLE_SIDE),
                            Image.Resampling.LANCZOS,
                        )
                        contrasted = ImageOps.autocontrast(sample)
                    finally:
                        sample.close()
                    try:
                        width, height = contrasted.size
                        with io.BytesIO() as encoded:
                            contrasted.save(encoded, format="PNG")
                            image_payload = encoded.getvalue()
                    finally:
                        contrasted.close()

            command = [
                runtime.tesseract_cmd,
                "stdin",
                "stdout",
                "-l",
                runtime.lang,
                "--psm",
                "11",
            ]
            if runtime.tessdata_dir:
                command.extend(("--tessdata-dir", runtime.tessdata_dir))
            command.append("tsv")
            result = run_bounded_capture(
                command,
                input_bytes=image_payload,
                timeout_seconds=runtime.timeout_seconds,
                stdout_limit_bytes=DOCUMENT_OCR_TSV_MAX_BYTES,
                stderr_limit_bytes=DOCUMENT_OCR_DIAGNOSTIC_MAX_BYTES,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                detail = result.stderr.decode("utf-8", "replace")[:500]
                raise RuntimeError(
                    detail or f"tesseract exited with code {result.returncode}"
                )

        retained_words: list[str] = []
        retained_utf8_bytes = 0
        text_truncated = False
        word_count = 0
        character_count = 0
        confidence_total = 0.0
        lines: set[tuple[int, int, int]] = set()
        box_area = 0
        decoded_tsv = result.stdout.decode("utf-8", "replace")
        for row in csv.DictReader(
            io.StringIO(decoded_tsv),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        ):
            word = str(row.get("text") or "").strip()
            try:
                confidence = float(row.get("conf") or -1)
            except (TypeError, ValueError):
                confidence = -1.0
            if not word or confidence < OCR_WORD_CONFIDENCE:
                continue
            word_count += 1
            character_count += len(word)
            confidence_total += confidence
            if not text_truncated:
                word_utf8_bytes = len(word.encode("utf-8"))
                separator_bytes = 1 if retained_words else 0
                if (
                    retained_utf8_bytes + separator_bytes + word_utf8_bytes
                    <= DOCUMENT_OCR_TEXT_MAX_UTF8_BYTES
                ):
                    retained_words.append(word)
                    retained_utf8_bytes += separator_bytes + word_utf8_bytes
                else:
                    text_truncated = True
            lines.add(
                (
                    int(row.get("block_num") or 0),
                    int(row.get("par_num") or 0),
                    int(row.get("line_num") or 0),
                )
            )
            box_area += int(row.get("width") or 0) * int(row.get("height") or 0)
        recognized = " ".join(retained_words)
        return DocumentTextEvidence(
            attempted=True,
            available=True,
            word_count=word_count,
            line_count=len(lines),
            character_count=character_count,
            recognized_text=recognized,
            recognized_text_truncated=text_truncated,
            text_coverage=round(min(1.0, box_area / max(1, width * height)), 5),
            mean_confidence=round(
                confidence_total / word_count if word_count else 0.0,
                2,
            ),
            document_terms=_semantic_hits(recognized, DOCUMENT_TERMS),
            ui_terms=_semantic_hits(recognized, UI_TERMS),
            industrial_entities=_semantic_hits(recognized, INDUSTRIAL_ENTITY_HINTS),
            industrial_activities=_semantic_hits(recognized, INDUSTRIAL_ACTIVITY_HINTS),
            industrial_operational_contexts=_semantic_hits(
                recognized, OPERATIONAL_CONTEXT_HINTS
            ),
            industrial_safety_conditions=_semantic_hits(
                recognized, SAFETY_CONDITION_HINTS
            ),
            provenance=runtime.provenance,
        )
    except Exception as exc:
        return DocumentTextEvidence(
            attempted=True,
            available=False,
            provenance=runtime.provenance,
            error_type=type(exc).__name__,
            error_message=_safe_error(exc),
        )


# endregion [03]
