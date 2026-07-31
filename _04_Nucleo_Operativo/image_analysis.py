"""Compatibility facade for the modular image-analysis pipeline.

Existing imports remain stable while models, feature extraction, semantic
evidence and decision policy evolve independently.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .image_adult import (
    DEFAULT_ADULT_CLASSIFIER,
    AdultContentClassifier,
)

from .image_decision import (
    add,
    attribute_confidence,
    classify as _classify,
    classify_photo_attributes,
    requires_document_verification,
)
from .image_document import (
    DocumentVerifierRuntime,
    verify_document_text,
)
from .image_features import (
    ImageMemoryGate,
    ImageResourceLimits,
    entropy,
    estimated_image_memory_bytes,
    exif_number,
    extract_features,
    is_skin_tone,
    projection_features,
)
from .image_models import (
    AdultContentEvidence,
    AdultDetection,
    Decision,
    DocumentCandidate,
    Features,
    IndustrialContext,
    PhotoAttributes,
    SemanticLabel,
    VisualSemanticEvidence,
)
from .image_visual import (
    DEFAULT_VISUAL_CLASSIFIER,
    FeatureVisualClassifier,
    VisualSemanticClassifier,
)
from .image_policy import (
    ANALYSIS_VERSION,
    CATEGORY_DIRS,
    DECISION_VERSION,
    FEATURE_VERSION,
    GENERATED_DIRS,
    IMAGE_SUFFIXES,
    MIB,
    NAME_HINT_POINTS,
    NAME_HINTS,
    PROFILE_EXCLUDED_DIRS,
    SAMPLE_SIDE,
)
from .image_semantics import (
    cached_features_are_compatible,
    classify_industrial_context,
    normalize_text,
    phrase_matches,
    textual_context,
)


# region [01] Stable public entry point


def classify(
    path: Path,
    root: Path,
    memory_gate: ImageMemoryGate | None = None,
    *,
    features: Features | None = None,
    document_verifier: DocumentVerifierRuntime | None = None,
    visual_classifier: VisualSemanticClassifier = DEFAULT_VISUAL_CLASSIFIER,
    adult_classifier: AdultContentClassifier = DEFAULT_ADULT_CLASSIFIER,
    analyze_adult: bool = True,
) -> Decision:
    """Classify through modular components while preserving patchable seams."""

    decision = _classify(
        path,
        root,
        memory_gate,
        features=features,
        document_verifier=document_verifier,
        feature_extractor=extract_features,
        verifier=verify_document_text,
        visual_classifier=visual_classifier,
    )
    if not analyze_adult:
        return decision
    adult_content = adult_classifier.classify(
        path,
        decision.category,
        decision.features,
        decision.document_candidate,
    )
    return replace(decision, adult_content=adult_content)


# endregion [01]


__all__ = [
    "ANALYSIS_VERSION",
    "AdultContentClassifier",
    "AdultContentEvidence",
    "AdultDetection",
    "CATEGORY_DIRS",
    "DECISION_VERSION",
    "Decision",
    "DocumentCandidate",
    "FEATURE_VERSION",
    "Features",
    "GENERATED_DIRS",
    "IMAGE_SUFFIXES",
    "ImageMemoryGate",
    "ImageResourceLimits",
    "IndustrialContext",
    "MIB",
    "NAME_HINT_POINTS",
    "NAME_HINTS",
    "PROFILE_EXCLUDED_DIRS",
    "PhotoAttributes",
    "SAMPLE_SIDE",
    "SemanticLabel",
    "VisualSemanticEvidence",
    "VisualSemanticClassifier",
    "FeatureVisualClassifier",
    "DEFAULT_VISUAL_CLASSIFIER",
    "add",
    "attribute_confidence",
    "cached_features_are_compatible",
    "classify",
    "classify_industrial_context",
    "classify_photo_attributes",
    "entropy",
    "estimated_image_memory_bytes",
    "exif_number",
    "extract_features",
    "is_skin_tone",
    "normalize_text",
    "phrase_matches",
    "projection_features",
    "requires_document_verification",
    "textual_context",
    "verify_document_text",
]
