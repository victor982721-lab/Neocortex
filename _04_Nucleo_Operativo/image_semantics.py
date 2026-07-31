"""Path-derived semantic evidence for image classification.

This module never claims visual recognition. It preserves the provenance and
uncertainty of labels inferred only from file and directory names.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Iterable

from .image_document import DocumentTextEvidence
from .image_models import (
    IndustrialContext,
    SemanticLabel,
    VisualSemanticEvidence,
)
from .image_policy import (
    COMPATIBLE_FEATURE_PREFIXES,
    FEATURE_VERSION,
    GENERATED_DIRS,
    INDUSTRIAL_ACTIVITY_HINTS,
    INDUSTRIAL_ENTITY_HINTS,
    LEGACY_FEATURE_SIGNATURES,
    OPERATIONAL_CONTEXT_HINTS,
    SAFETY_CONDITION_HINTS,
    TOKEN_RE,
)


# region [01] Normalized path evidence


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(TOKEN_RE.findall(value))


def textual_context(path: Path, root: Path) -> str:
    """Omit generated folders so reclassification does not bias itself."""

    try:
        parts = path.relative_to(root).with_suffix("").parts
    except ValueError:
        parts = path.with_suffix("").parts
    generated = {part.casefold() for part in GENERATED_DIRS}
    useful = [part for part in parts if part.casefold() not in generated]
    return normalize_text(" ".join(useful))


def phrase_matches(text: str, phrases: Iterable[str]) -> list[str]:
    padded = f" {text} "
    matches = []
    for phrase in phrases:
        normalized = normalize_text(phrase)
        if normalized and f" {normalized} " in padded:
            matches.append(phrase)
    return matches


def cached_features_are_compatible(processing_signature: str | None) -> bool:
    """Allow decision-only upgrades to reuse bounded v3 pixel features."""

    if processing_signature in LEGACY_FEATURE_SIGNATURES:
        return True
    return bool(
        processing_signature
        and (
            processing_signature.startswith(COMPATIBLE_FEATURE_PREFIXES)
            or processing_signature.startswith(f"image-route-v4|{FEATURE_VERSION}|")
            or processing_signature.startswith(f"psig-v1|image|{FEATURE_VERSION}|")
        )
    )


# endregion [01]


# region [02] Industrial vocabulary classification


def _semantic_labels(
    context: str,
    hints: dict[str, tuple[str, ...]],
) -> tuple[SemanticLabel, ...]:
    labels: list[SemanticLabel] = []
    for label, phrases in hints.items():
        matches = phrase_matches(context, phrases)
        if not matches:
            continue
        score = min(0.78, 0.56 + 0.07 * (len(matches) - 1))
        labels.append(
            SemanticLabel(
                label=label,
                score=round(score, 3),
                evidence=tuple(f"nombre/ruta:{value}" for value in matches[:3]),
                provenance="path-keywords-v1",
            )
        )
    return tuple(sorted(labels, key=lambda item: (-item.score, item.label)))


def _ocr_semantic_labels(labels: tuple[str, ...]) -> tuple[SemanticLabel, ...]:
    return tuple(
        SemanticLabel(
            label=label,
            score=0.64,
            evidence=(f"ocr-keywords:{label}",),
            provenance="ocr-keywords-v1",
        )
        for label in sorted(set(labels))
    )


def _merge_semantic_labels(
    *sources: tuple[SemanticLabel, ...],
) -> tuple[SemanticLabel, ...]:
    merged: dict[str, SemanticLabel] = {}
    for source in sources:
        for item in source:
            prior = merged.get(item.label)
            if prior is None:
                merged[item.label] = item
                continue
            provenances = tuple(
                dict.fromkeys(
                    (*prior.provenance.split("+"), *item.provenance.split("+"))
                )
            )
            merged[item.label] = SemanticLabel(
                label=item.label,
                score=round(min(0.86, max(prior.score, item.score) + 0.08), 3),
                evidence=tuple(dict.fromkeys((*prior.evidence, *item.evidence))),
                provenance="+".join(provenances),
            )
    return tuple(sorted(merged.values(), key=lambda value: (-value.score, value.label)))


def classify_industrial_context(
    context: str,
    document_text: DocumentTextEvidence | None = None,
    visual: VisualSemanticEvidence | None = None,
) -> IndustrialContext:
    """Fuse path, OCR and visual labels while retaining their provenance."""

    entities = _merge_semantic_labels(
        _semantic_labels(context, INDUSTRIAL_ENTITY_HINTS),
        _ocr_semantic_labels(
            document_text.industrial_entities
            if document_text is not None and document_text.available
            else ()
        ),
        visual.entities if visual is not None else (),
    )
    activities = _merge_semantic_labels(
        _semantic_labels(context, INDUSTRIAL_ACTIVITY_HINTS),
        _ocr_semantic_labels(
            document_text.industrial_activities
            if document_text is not None and document_text.available
            else ()
        ),
        visual.activities if visual is not None else (),
    )
    operational = _merge_semantic_labels(
        _semantic_labels(context, OPERATIONAL_CONTEXT_HINTS),
        _ocr_semantic_labels(
            document_text.industrial_operational_contexts
            if document_text is not None and document_text.available
            else ()
        ),
        visual.operational_contexts if visual is not None else (),
    )
    safety = _merge_semantic_labels(
        _semantic_labels(context, SAFETY_CONDITION_HINTS),
        _ocr_semantic_labels(
            document_text.industrial_safety_conditions
            if document_text is not None and document_text.available
            else ()
        ),
        visual.safety_conditions if visual is not None else (),
    )
    has_evidence = bool(entities or activities or operational or safety)
    provenances = {
        item.provenance
        for group in (entities, activities, operational, safety)
        for item in group
    }
    has_path = any("path-keywords-v1" in value for value in provenances)
    has_ocr = any("ocr-keywords-v1" in value for value in provenances)
    has_visual = any("visual-" in value for value in provenances)
    if has_visual and (has_path or has_ocr):
        uncertainty = "evidencia_multifuente_con_componente_visual_no_calibrado"
    elif has_path and has_ocr:
        uncertainty = "evidencia_semantica_indirecta_de_ruta_y_ocr"
    elif has_visual:
        uncertainty = (
            visual.uncertainty
            if visual is not None
            else "evidencia_visual_no_calibrada"
        )
    elif has_ocr:
        uncertainty = "evidencia_semantica_limitada_a_ocr"
    elif has_path:
        uncertainty = "evidencia_semantica_limitada_a_nombre_y_ruta"
    else:
        uncertainty = "sin_evidencia_semantica_suficiente"
    return IndustrialContext(
        entities=entities,
        activities=activities,
        operational_contexts=operational,
        safety_conditions=safety,
        uncertainty=uncertainty,
        provenance=tuple(sorted(provenances)) if has_evidence else (),
    )


# endregion [02]
