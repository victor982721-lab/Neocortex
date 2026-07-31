"""Versioned, explainable taxonomy for technical document classification.

This public facade keeps the stable taxonomy contract while focused modules own
value models, built-in vocabulary, bounded overlays, and evidence specialists.
"""

from __future__ import annotations

from .document_naming import NAMING_VERSION, suggest_document_stem
from .document_signals import (
    classification_path_signal,
    fold_signal,
    is_framework_managed_path,
)
from .document_taxonomy_entities import (
    _audio_kind_adjustment,
    _client_evidence,
    _document_subtype_evidence,
    _document_topic_adjustment,
    _managed_top_level_is,
    _organization_evidence,
    _pattern_evidence,
    _project_evidence,
    _specific_equipment_evidence,
    _technical_reference_evidence,
    _workstream_evidence,
)
from .document_taxonomy_kinds import (
    _calibrated_kind_confidence,
    _kind_evidence,
)
from .document_taxonomy_models import (
    AuthoritySpec,
    ClientSpec,
    DocumentClassification,
    DocumentSignals,
    OrganizationSpec,
    ProjectSpec,
    ScoredLabel,
    StandardReference,
    TechnicalTaxonomy,
)
from .document_taxonomy_overlay import (
    MAX_TAXONOMY_BYTES,
    MAX_TAXONOMY_PATTERN_CHARS,
    MAX_TAXONOMY_PATTERNS,
    MAX_TAXONOMY_SEQUENCE_ITEMS,
    MAX_TAXONOMY_TABLES_PER_SECTION,
    MAX_TAXONOMY_TEXT_CHARS,
    load_taxonomy,
)
from .document_taxonomy_references import (
    _authority_evidence,
    _document_authority_adjustment,
    _naming_reference_rank,
)
from .document_taxonomy_vocabulary import (
    BUILTIN_TAXONOMY_VERSION,
    _ACTIVITY_PATTERNS,
    _TOPIC_PATTERNS,
    builtin_taxonomy,
    semantic_label_inventory,
)


__all__ = (
    "AuthoritySpec",
    "BUILTIN_TAXONOMY_VERSION",
    "CLASSIFIER_VERSION",
    "ClientSpec",
    "DocumentClassification",
    "DocumentSignals",
    "MAX_TAXONOMY_BYTES",
    "MAX_TAXONOMY_PATTERNS",
    "MAX_TAXONOMY_PATTERN_CHARS",
    "MAX_TAXONOMY_SEQUENCE_ITEMS",
    "MAX_TAXONOMY_TABLES_PER_SECTION",
    "MAX_TAXONOMY_TEXT_CHARS",
    "OrganizationSpec",
    "ProjectSpec",
    "ScoredLabel",
    "StandardReference",
    "TechnicalTaxonomy",
    "builtin_taxonomy",
    "classify_document",
    "document_classifier_signature",
    "load_taxonomy",
    "semantic_label_inventory",
)


# region [01] Stable public classification contract

CLASSIFIER_VERSION = "technical-document-classifier-v14"


def document_classifier_signature(taxonomy: TechnicalTaxonomy) -> str:
    """Version classification and semantic naming as one durable cache unit."""

    return f"{CLASSIFIER_VERSION}|{taxonomy.signature}|{NAMING_VERSION}"


# endregion [01]


# region [02] Classification orchestration


def classify_document(
    signals: DocumentSignals,
    taxonomy: TechnicalTaxonomy | None = None,
) -> DocumentClassification:
    """Classify one bounded signal set and retain every supporting rule."""

    active = taxonomy or builtin_taxonomy()
    managed_path = is_framework_managed_path(signals.path)
    managed_normative_path = _managed_top_level_is(signals.path, "NORMATIVA")
    path_signal = classification_path_signal(signals.path)
    scopes = {
        "path": path_signal,
        "title": signals.title,
        "author": signals.author,
        "metadata": signals.metadata,
        "opening": signals.leading_text[:4_000],
        "text": signals.leading_text[4_000:],
    }
    folded = {name: fold_signal(value) for name, value in scopes.items() if value}
    standards, authorities = _authority_evidence(
        folded,
        active.authorities,
        raw_scopes=scopes,
        managed_path=managed_path,
    )
    authorities = _document_authority_adjustment(folded, standards, authorities)
    organizations = _organization_evidence(folded, active.organizations)
    projects = _project_evidence(folded, active.projects)
    clients = _client_evidence(folded, active.clients, active.projects, projects)
    workstreams = _workstream_evidence(folded)
    topics = _pattern_evidence(folded, _TOPIC_PATTERNS, base_score=0.38)
    equipment = _specific_equipment_evidence(folded)
    activities = _pattern_evidence(folded, _ACTIVITY_PATTERNS, base_score=0.40)
    kinds = _kind_evidence(
        folded,
        standards,
        organizations,
        primary_authority=authorities[0].label if authorities else None,
        page_count=signals.page_count,
        managed_path=managed_path,
        managed_normative_path=managed_normative_path,
    )
    if not kinds and signals.source_kind != "audio":
        kinds = _technical_reference_evidence(topics)
    if signals.source_kind == "audio":
        kinds = _audio_kind_adjustment(kinds, folded, managed_path=managed_path)

    primary = kinds[0] if kinds else ScoredLabel("otro", 0.35, ("sin_regla_fuerte",))
    document_subtypes = _document_subtype_evidence(
        folded,
        primary_kind=primary.label,
        primary_authority=authorities[0].label if authorities else None,
        activities=activities,
    )
    topics = _document_topic_adjustment(topics, primary.label)
    score_margin = round(kinds[0].score - kinds[1].score, 6) if len(kinds) > 1 else 1.0
    formal_normative_primary = primary.label == "normativa" and any(
        item.startswith("opening:estructura_normativa=") for item in primary.evidence
    )
    ambiguous = len(kinds) > 1 and score_margin < 0.10 and not formal_normative_primary
    normative_ambiguity = (
        len(kinds) > 1
        and score_margin < 0.12
        and "normativa" in {kinds[0].label, kinds[1].label}
        and not formal_normative_primary
    )
    confidence = primary.score
    if primary.label == "normativa" and standards:
        confidence = max(confidence, 0.92)
    if primary.label in {"formato_empresa", "manual_equipo"} and organizations:
        confidence = min(0.97, confidence + 0.07)
    if signals.source_status != "partial" and not ambiguous:
        confidence = max(confidence, _calibrated_kind_confidence(primary))
    if signals.source_status == "partial":
        confidence = max(0.0, confidence - 0.08)
    if ambiguous:
        confidence = max(0.0, confidence - 0.08)
    if normative_ambiguity:
        confidence = min(confidence, 0.67)
    confidence = round(min(1.0, confidence), 6)
    if normative_ambiguity:
        uncertainty = "alta"
    elif confidence >= 0.82 and not ambiguous:
        uncertainty = "baja"
    elif confidence >= 0.68:
        uncertainty = "media"
    else:
        uncertainty = "alta"
    evidence = tuple(
        dict.fromkeys(
            item
            for label in (
                *kinds[:3],
                *document_subtypes[:2],
                *authorities[:3],
                *organizations[:3],
                *clients[:2],
                *projects[:2],
                *workstreams[:2],
                *equipment[:3],
                *activities[:3],
                *topics[:3],
            )
            for item in label.evidence[:4]
        )
    )
    primary_authority = authorities[0].label if authorities else None
    naming_references = tuple(
        sorted(
            standards,
            key=lambda reference: _naming_reference_rank(
                reference,
                signals=signals,
                primary_authority=primary_authority,
                managed_path=managed_path,
            ),
        )
    )
    naming = suggest_document_stem(
        path=signals.path,
        title=signals.title,
        leading_text=signals.leading_text,
        primary_kind=primary.label,
        standard_identifiers=(reference.identifier for reference in naming_references),
        organization=organizations[0].label if organizations else None,
        topic=topics[0].label if topics else None,
    )
    return DocumentClassification(
        classifier_signature=document_classifier_signature(active),
        primary_kind=primary.label,
        kind_candidates=kinds,
        authorities=authorities,
        standard_references=standards,
        organizations=organizations,
        clients=clients,
        projects=projects,
        workstreams=workstreams,
        topics=topics,
        document_subtypes=document_subtypes,
        equipment=equipment,
        activities=activities,
        confidence=confidence,
        uncertainty=uncertainty,
        evidence=evidence[:24],
        suggested_stem=naming.stem,
        naming_signature=NAMING_VERSION,
        naming_evidence=naming.evidence,
    )


# endregion [02]
