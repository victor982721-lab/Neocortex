"""Immutable, versioned contracts for the read-only Knowledge Plane.

The contracts deliberately serialize only documented fields.  They never
expose arbitrary internal objects, and optional locator precision is omitted
when an owner cannot prove it.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .semantic_models import canonical_json, fingerprint_text

# region [01] Versions, bounds and stable vocabulary


KNOWLEDGE_CONTRACT_SCHEMA_VERSION = 1
KNOWLEDGE_TELEMETRY_SCHEMA_VERSION = 1
KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE = "python-perf-counter-ns-v1"
KNOWLEDGE_TELEMETRY_UNIDENTIFIED_CLOCK_SIGNATURE = "python-callable-unidentified-ns-v1"
MAX_KNOWLEDGE_TELEMETRY_PHASES = 512
MAX_KNOWLEDGE_TELEMETRY_RANKINGS_PER_PHASE = 64
MAX_KNOWLEDGE_TELEMETRY_NAME_CHARS = 256
MAX_KNOWLEDGE_TELEMETRY_RANKING_CHARS_PER_PHASE = 4_096
MAX_KNOWLEDGE_TELEMETRY_SNAPSHOT_ID_CHARS = 512
MAX_KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE_CHARS = 128
MAX_KNOWLEDGE_SNIPPET_CHARS = 4_096
MAX_EVIDENCE_IDENTIFIERS = 64
MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS = 512
MAX_EVIDENCE_SYMBOL_CHARS = 1_024
MAX_CONTEXT_PLAN_STEPS = 32
MAX_CONTEXT_PLAN_VALUES = 4_096
MAX_CONTEXT_PLAN_VALUE_CHARS = 4_096
MAX_CONTEXT_PLAN_TOTAL_VALUE_CHARS = 4_096
MAX_CONTEXT_RELATION_PROVENANCE_ITEMS = 16
MAX_CONTEXT_RELATION_PROVENANCE_CHARS = 4_096


class ResourceDisposition(StrEnum):
    CANONICAL = "canonical"
    DUPLICATE = "duplicate"
    SUPERSEDED = "superseded"
    DERIVED = "derived"


class RevisionState(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"
    SUPERSEDED = "superseded"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"


class EvidenceMethod(StrEnum):
    STRUCTURAL = "structural"
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    HUMAN_CONFIRMED = "human_confirmed"
    AMBIGUOUS = "ambiguous"


class OwnerAvailability(StrEnum):
    AVAILABLE = "available"
    ABSENT = "absent"
    FUTURE = "future"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"


class SnapshotConsistency(StrEnum):
    STABLE = "stable"
    SNAPSHOT_CHANGED = "snapshot_changed"


class KnowledgeCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_EVIDENCE = "no_evidence"
    UNSUPPORTED = "unsupported"


class KnowledgeTimingPhase(StrEnum):
    PLANNER = "planner"
    SNAPSHOT_BEFORE = "snapshot_before"
    OWNER_RANKING = "owner_ranking"
    FUSION = "fusion"
    BROKER = "broker"
    SNAPSHOT_AFTER = "snapshot_after"
    CONTEXT_COMPILE = "context_compile"


class KnowledgeTelemetryOperation(StrEnum):
    SEARCH = "search"
    CONTEXT = "context"


def _required_text(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} cannot be blank")
    return normalized


def _optional_text(name: str, value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{name} cannot be blank when present")
    return value


def _base_payload(kind: str) -> dict[str, object]:
    return {
        "schema_version": KNOWLEDGE_CONTRACT_SCHEMA_VERSION,
        "kind": kind,
    }


def _canonical_output(payload: Mapping[str, object]) -> str:
    return canonical_json(payload)


@dataclass(frozen=True, slots=True)
class KnowledgeTelemetryClock:
    """One callable and the explicit timing domain its readings belong to."""

    read_ns: Callable[[], int] = field(
        default=time.perf_counter_ns,
        compare=False,
        repr=False,
    )
    signature: str = KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE

    def __post_init__(self) -> None:
        if not callable(self.read_ns):
            raise ValueError("Knowledge telemetry clock must be callable")
        if not isinstance(self.signature, str):
            raise ValueError("Knowledge telemetry clock signature must be text")
        _required_text("Knowledge telemetry clock signature", self.signature)
        if self.signature != self.signature.strip():
            raise ValueError(
                "Knowledge telemetry clock signature cannot have outer whitespace"
            )
        if len(self.signature) > MAX_KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE_CHARS:
            raise ValueError("Knowledge telemetry clock signature is too long")
        if (
            self.signature == KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE
            and self.read_ns is not time.perf_counter_ns
        ):
            raise ValueError(
                "python-perf-counter-ns-v1 is reserved for time.perf_counter_ns"
            )

    @classmethod
    def from_legacy(
        cls,
        clock_ns: Callable[[], int] | None,
    ) -> KnowledgeTelemetryClock:
        """Preserve callable injection without claiming an unidentified domain."""

        if clock_ns is None or clock_ns is time.perf_counter_ns:
            return cls()
        return cls(
            clock_ns,
            KNOWLEDGE_TELEMETRY_UNIDENTIFIED_CLOCK_SIGNATURE,
        )

    @property
    def identified(self) -> bool:
        return self.signature != KNOWLEDGE_TELEMETRY_UNIDENTIFIED_CLOCK_SIGNATURE

    def compatible_with(
        self,
        signature: str,
        *,
        trust_unidentified: bool = False,
    ) -> bool:
        return self.signature == signature and (self.identified or trust_unidentified)

    def now_ns(self) -> int:
        value = self.read_ns()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("Knowledge telemetry clock returned an invalid value")
        return value


@dataclass(frozen=True, slots=True)
class KnowledgePhaseTiming:
    """One bounded monotonic duration from a successful Knowledge operation."""

    phase: KnowledgeTimingPhase
    duration_ns: int
    service_attempt: int = 0
    owner: str | None = None
    ranking_names: tuple[str, ...] = ()
    snapshot_id: str | None = None
    executed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.phase, KnowledgeTimingPhase):
            raise ValueError("Knowledge timing phase is invalid")
        if (
            isinstance(self.duration_ns, bool)
            or not isinstance(self.duration_ns, int)
            or self.duration_ns < 0
        ):
            raise ValueError("Knowledge timing duration_ns cannot be negative")
        if (
            isinstance(self.service_attempt, bool)
            or not isinstance(self.service_attempt, int)
            or not 0 <= self.service_attempt <= 2
        ):
            raise ValueError(
                "Knowledge timing service_attempt must be zero, one or two"
            )
        if not isinstance(self.executed, bool):
            raise ValueError("Knowledge timing executed must be boolean")
        if self.owner is not None and not isinstance(self.owner, str):
            raise ValueError("Knowledge timing owner must be text when present")
        if self.snapshot_id is not None and not isinstance(self.snapshot_id, str):
            raise ValueError("Knowledge timing snapshot_id must be text when present")
        _optional_text("Knowledge timing owner", self.owner)
        _optional_text("Knowledge timing snapshot_id", self.snapshot_id)
        if self.owner is not None and len(self.owner) > (
            MAX_KNOWLEDGE_TELEMETRY_NAME_CHARS
        ):
            raise ValueError("Knowledge timing owner is too long")
        if self.snapshot_id is not None and len(self.snapshot_id) > (
            MAX_KNOWLEDGE_TELEMETRY_SNAPSHOT_ID_CHARS
        ):
            raise ValueError("Knowledge timing snapshot_id is too long")
        if not isinstance(self.ranking_names, tuple):
            raise ValueError("Knowledge timing ranking_names must be a tuple")
        if len(self.ranking_names) > (
            MAX_KNOWLEDGE_TELEMETRY_RANKINGS_PER_PHASE
        ):
            raise ValueError("Knowledge timing has too many ranking names")
        for ranking_name in self.ranking_names:
            if not isinstance(ranking_name, str):
                raise ValueError("Knowledge timing ranking name must be text")
            _required_text("Knowledge timing ranking name", ranking_name)
            if len(ranking_name) > MAX_KNOWLEDGE_TELEMETRY_NAME_CHARS:
                raise ValueError("Knowledge timing ranking name is too long")
        if sum(len(ranking_name) for ranking_name in self.ranking_names) > (
            MAX_KNOWLEDGE_TELEMETRY_RANKING_CHARS_PER_PHASE
        ):
            raise ValueError("Knowledge timing ranking names are too large")
        if len(set(self.ranking_names)) != len(self.ranking_names):
            raise ValueError("Knowledge timing ranking names must be unique")

        attempt_phases = {
            KnowledgeTimingPhase.SNAPSHOT_BEFORE,
            KnowledgeTimingPhase.OWNER_RANKING,
            KnowledgeTimingPhase.FUSION,
            KnowledgeTimingPhase.BROKER,
            KnowledgeTimingPhase.SNAPSHOT_AFTER,
        }
        if self.phase in attempt_phases and self.service_attempt not in {1, 2}:
            raise ValueError(
                "attempt-scoped Knowledge timing requires attempt one or two"
            )
        if self.phase not in attempt_phases and self.service_attempt != 0:
            raise ValueError(
                "operation-scoped Knowledge timing must use attempt zero"
            )
        if self.phase is KnowledgeTimingPhase.OWNER_RANKING:
            if self.owner is None or not self.ranking_names:
                raise ValueError(
                    "owner_ranking timing requires owner and ranking names"
                )
        elif self.owner is not None or self.ranking_names:
            raise ValueError(
                "only owner_ranking timing may identify owners or rankings"
            )
        if self.phase in {
            KnowledgeTimingPhase.SNAPSHOT_BEFORE,
            KnowledgeTimingPhase.SNAPSHOT_AFTER,
        }:
            if self.snapshot_id is None:
                raise ValueError("snapshot timing requires snapshot_id")
        elif self.snapshot_id is not None:
            raise ValueError("only snapshot timing may identify a snapshot")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "phase": self.phase.value,
            "duration_ns": self.duration_ns,
            "service_attempt": self.service_attempt,
            "executed": self.executed,
        }
        if self.owner is not None:
            payload["owner"] = self.owner
        if self.ranking_names:
            payload["ranking_names"] = list(self.ranking_names)
        if self.snapshot_id is not None:
            payload["snapshot_id"] = self.snapshot_id
        return payload


@dataclass(frozen=True, slots=True)
class KnowledgeQueryTelemetry:
    """Versioned in-memory timing envelope; never participates in identities."""

    operation: KnowledgeTelemetryOperation
    total_duration_ns: int
    phases: tuple[KnowledgePhaseTiming, ...]
    clock_signature: str = KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE

    def __post_init__(self) -> None:
        if not isinstance(self.operation, KnowledgeTelemetryOperation):
            raise ValueError("Knowledge telemetry operation is invalid")
        if (
            isinstance(self.total_duration_ns, bool)
            or not isinstance(self.total_duration_ns, int)
            or self.total_duration_ns < 0
        ):
            raise ValueError("Knowledge telemetry total_duration_ns cannot be negative")
        if not isinstance(self.clock_signature, str):
            raise ValueError("Knowledge telemetry clock signature must be text")
        _required_text("Knowledge telemetry clock signature", self.clock_signature)
        if len(self.clock_signature) > MAX_KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE_CHARS:
            raise ValueError("Knowledge telemetry clock signature is too long")
        if not isinstance(self.phases, tuple):
            raise ValueError("Knowledge telemetry phases must be a tuple")
        if not self.phases:
            raise ValueError("Knowledge telemetry requires at least one phase")
        if len(self.phases) > MAX_KNOWLEDGE_TELEMETRY_PHASES:
            raise ValueError("Knowledge telemetry has too many phase records")
        if any(not isinstance(phase, KnowledgePhaseTiming) for phase in self.phases):
            raise ValueError("Knowledge telemetry phases are invalid")
        context_phases = sum(
            phase.phase is KnowledgeTimingPhase.CONTEXT_COMPILE
            for phase in self.phases
        )
        if self.operation is KnowledgeTelemetryOperation.SEARCH and context_phases:
            raise ValueError("search telemetry cannot contain context compilation")
        if (
            self.operation is KnowledgeTelemetryOperation.CONTEXT
            and context_phases != 1
        ):
            raise ValueError(
                "context telemetry requires one context compilation phase"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": KNOWLEDGE_TELEMETRY_SCHEMA_VERSION,
            "kind": "knowledge_query_telemetry",
            "operation": self.operation.value,
            "clock_signature": self.clock_signature,
            "total_duration_ns": self.total_duration_ns,
            "phases": [phase.to_dict() for phase in self.phases],
        }

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


# endregion [01]


# region [02] Resource, revision and heterogeneous evidence


@dataclass(frozen=True, slots=True)
class PhysicalIdentityRef:
    scheme: str
    value: str
    identity_version: int

    def __post_init__(self) -> None:
        _required_text("physical identity scheme", self.scheme)
        _required_text("physical identity value", self.value)
        if isinstance(self.identity_version, bool) or self.identity_version < 1:
            raise ValueError("physical identity version must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "scheme": self.scheme,
            "value": self.value,
            "identity_version": self.identity_version,
        }


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_id: str
    source_kind: str
    owner: str
    physical_identity: PhysicalIdentityRef | None = None
    current_path: str | None = None
    disposition: ResourceDisposition | None = None
    canonical_resource_id: str | None = None

    def __post_init__(self) -> None:
        _required_text("resource_id", self.resource_id)
        _required_text("source_kind", self.source_kind)
        _required_text("owner", self.owner)
        _optional_text("current_path", self.current_path)
        _optional_text("canonical_resource_id", self.canonical_resource_id)
        if self.disposition is ResourceDisposition.DUPLICATE:
            _required_text(
                "canonical_resource_id",
                "" if self.canonical_resource_id is None else self.canonical_resource_id,
            )
        if self.canonical_resource_id == self.resource_id:
            raise ValueError("a resource cannot name itself as its canonical resource")

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("resource_ref")
        payload.update(
            {
                "resource_id": self.resource_id,
                "source_kind": self.source_kind,
                "owner": self.owner,
            }
        )
        if self.physical_identity is not None:
            payload["physical_identity"] = self.physical_identity.to_dict()
        if self.current_path is not None:
            payload["current_path"] = self.current_path
        if self.disposition is not None:
            payload["disposition"] = self.disposition.value
        if self.canonical_resource_id is not None:
            payload["canonical_resource_id"] = self.canonical_resource_id
        return payload


@dataclass(frozen=True, slots=True)
class RevisionRef:
    resource_id: str
    revision_id: str
    producer: str
    processing_signature: str
    generation: int | None
    state: RevisionState
    observed_at_utc: str | None = None

    def __post_init__(self) -> None:
        _required_text("resource_id", self.resource_id)
        _required_text("revision_id", self.revision_id)
        _required_text("producer", self.producer)
        _required_text("processing_signature", self.processing_signature)
        _optional_text("observed_at_utc", self.observed_at_utc)
        if self.generation is not None and (
            isinstance(self.generation, bool) or self.generation < 0
        ):
            raise ValueError("revision generation cannot be negative")

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("revision_ref")
        payload.update(
            {
                "resource_id": self.resource_id,
                "revision_id": self.revision_id,
                "producer": self.producer,
                "processing_signature": self.processing_signature,
                "state": self.state.value,
            }
        )
        if self.generation is not None:
            payload["generation"] = self.generation
        if self.observed_at_utc is not None:
            payload["observed_at_utc"] = self.observed_at_utc
        return payload


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    resource_id: str
    revision_id: str
    method: EvidenceMethod
    page: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    coordinate_space: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    symbol: str | None = None
    section_kind: str | None = None
    section_id: str | None = None
    snippet: str | None = None
    extractor: str | None = None
    extractor_version: str | None = None
    generation: int | None = None
    identifiers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _required_text("evidence_id", self.evidence_id)
        _required_text("resource_id", self.resource_id)
        _required_text("revision_id", self.revision_id)
        for name, value in (
            ("sheet", self.sheet),
            ("cell_range", self.cell_range),
            ("coordinate_space", self.coordinate_space),
            ("symbol", self.symbol),
            ("section_kind", self.section_kind),
            ("section_id", self.section_id),
            ("extractor", self.extractor),
            ("extractor_version", self.extractor_version),
        ):
            _optional_text(name, value)
        if self.page is not None and (
            isinstance(self.page, bool) or self.page < 0
        ):
            raise ValueError("page cannot be negative")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("line locator requires both start and end")
        if self.start_line is not None and (
            isinstance(self.start_line, bool)
            or isinstance(self.end_line, bool)
            or self.start_line < 1
            or self.end_line is None
            or self.end_line < self.start_line
        ):
            raise ValueError("line locator is invalid")
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("time locator requires both start and end")
        if self.start_ms is not None and (
            isinstance(self.start_ms, bool)
            or isinstance(self.end_ms, bool)
            or self.start_ms < 0
            or self.end_ms is None
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("time locator is invalid")
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("character locator requires both start and end")
        if self.start_char is not None and (
            isinstance(self.start_char, bool)
            or isinstance(self.end_char, bool)
            or self.start_char < 0
            or self.end_char is None
            or self.end_char <= self.start_char
        ):
            raise ValueError("character locator is invalid")
        if self.bounding_box is not None:
            left, top, right, bottom = self.bounding_box
            if not all(math.isfinite(value) for value in self.bounding_box) or (
                right <= left or bottom <= top
            ):
                raise ValueError("bounding box is invalid")
            if self.coordinate_space is None:
                raise ValueError("bounding box requires a coordinate space")
        elif self.coordinate_space is not None:
            raise ValueError("coordinate space requires a bounding box")
        if self.snippet is not None and len(self.snippet) > MAX_KNOWLEDGE_SNIPPET_CHARS:
            raise ValueError(
                f"snippet cannot exceed {MAX_KNOWLEDGE_SNIPPET_CHARS} characters"
            )
        if self.symbol is not None and len(self.symbol) > MAX_EVIDENCE_SYMBOL_CHARS:
            raise ValueError(
                f"symbol cannot exceed {MAX_EVIDENCE_SYMBOL_CHARS} characters"
            )
        if self.generation is not None and (
            isinstance(self.generation, bool) or self.generation < 0
        ):
            raise ValueError("evidence generation cannot be negative")
        if len(self.identifiers) > MAX_EVIDENCE_IDENTIFIERS:
            raise ValueError(
                f"evidence cannot contain more than {MAX_EVIDENCE_IDENTIFIERS} "
                "identifiers"
            )
        if len(set(self.identifiers)) != len(self.identifiers):
            raise ValueError("evidence identifiers must be unique")
        for namespace, value in self.identifiers:
            if not isinstance(namespace, str) or not isinstance(value, str):
                raise ValueError("evidence identifiers must contain strings")
            _required_text("identifier namespace", namespace)
            _required_text("identifier value", value)
            if (
                len(namespace) > MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS
                or len(value) > MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS
            ):
                raise ValueError(
                    "evidence identifier components cannot exceed "
                    f"{MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS} characters"
                )

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("evidence_ref")
        payload.update(
            {
                "evidence_id": self.evidence_id,
                "resource_id": self.resource_id,
                "revision_id": self.revision_id,
                "method": self.method.value,
            }
        )
        optional: tuple[tuple[str, object | None], ...] = (
            ("page", self.page),
            ("start_line", self.start_line),
            ("end_line", self.end_line),
            ("sheet", self.sheet),
            ("cell_range", self.cell_range),
            ("start_ms", self.start_ms),
            ("end_ms", self.end_ms),
            ("coordinate_space", self.coordinate_space),
            ("start_char", self.start_char),
            ("end_char", self.end_char),
            ("symbol", self.symbol),
            ("section_kind", self.section_kind),
            ("section_id", self.section_id),
            ("snippet", self.snippet),
            ("extractor", self.extractor),
            ("extractor_version", self.extractor_version),
            ("generation", self.generation),
        )
        for name, value in optional:
            if value is not None:
                payload[name] = value
        if self.bounding_box is not None:
            payload["bounding_box"] = list(self.bounding_box)
        if self.identifiers:
            payload["identifiers"] = [
                {"namespace": namespace, "value": value}
                for namespace, value in self.identifiers
            ]
        return payload


# endregion [02]


# region [03] Ranking and hit contract


@dataclass(frozen=True, slots=True)
class RankingSignal:
    source: str
    score_kind: str
    raw_score: float
    source_rank: int
    model_signature: str | None = None
    generation: int | None = None
    contribution: float | None = None
    query_model_signature: str | None = None

    def __post_init__(self) -> None:
        _required_text("ranking source", self.source)
        _required_text("score_kind", self.score_kind)
        _optional_text("model_signature", self.model_signature)
        _optional_text("query_model_signature", self.query_model_signature)
        if not math.isfinite(self.raw_score):
            raise ValueError("ranking raw score must be finite")
        if isinstance(self.source_rank, bool) or self.source_rank < 1:
            raise ValueError("ranking source rank must be positive")
        if self.generation is not None and (
            isinstance(self.generation, bool) or self.generation < 0
        ):
            raise ValueError("ranking generation cannot be negative")
        if self.contribution is not None and not math.isfinite(self.contribution):
            raise ValueError("ranking contribution must be finite")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "score_kind": self.score_kind,
            "raw_score": self.raw_score,
            "source_rank": self.source_rank,
        }
        if self.model_signature is not None:
            payload["model_signature"] = self.model_signature
        if self.query_model_signature is not None:
            payload["query_model_signature"] = self.query_model_signature
        if self.generation is not None:
            payload["generation"] = self.generation
        if self.contribution is not None:
            payload["contribution"] = self.contribution
        return payload


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    rank: int
    resource: ResourceRef
    revision: RevisionRef
    evidence: EvidenceRef
    signals: tuple[RankingSignal, ...]
    fused_score: float
    reasons: tuple[str, ...]
    confidence: float | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or self.rank < 1:
            raise ValueError("knowledge hit rank must be positive")
        if not math.isfinite(self.fused_score):
            raise ValueError("knowledge fused score must be finite")
        if self.revision.resource_id != self.resource.resource_id:
            raise ValueError("revision does not belong to hit resource")
        if (
            self.evidence.resource_id != self.resource.resource_id
            or self.evidence.revision_id != self.revision.revision_id
        ):
            raise ValueError("evidence does not belong to hit revision")
        if not self.signals:
            raise ValueError("knowledge hit requires at least one ranking signal")
        if not self.reasons:
            raise ValueError("knowledge hit requires at least one retrieval reason")
        for reason in self.reasons:
            _required_text("retrieval reason", reason)
        for warning in self.warnings:
            _required_text("hit warning", warning)
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("knowledge confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("knowledge_hit")
        payload.update(
            {
                "rank": self.rank,
                "resource": self.resource.to_dict(),
                "revision": self.revision.to_dict(),
                "evidence": self.evidence.to_dict(),
                "signals": [signal.to_dict() for signal in self.signals],
                "fused_score": self.fused_score,
                "reasons": list(self.reasons),
            }
        )
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


# endregion [03]


# region [04] Logical cross-owner snapshot


@dataclass(frozen=True, slots=True)
class PublicationHead:
    scope: str
    publication_id: str
    generation: int
    model_signature: str | None = None

    def __post_init__(self) -> None:
        _required_text("publication scope", self.scope)
        _required_text("publication_id", self.publication_id)
        _optional_text("model_signature", self.model_signature)
        if isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("publication generation cannot be negative")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "scope": self.scope,
            "publication_id": self.publication_id,
            "generation": self.generation,
        }
        if self.model_signature is not None:
            payload["model_signature"] = self.model_signature
        return payload


@dataclass(frozen=True, slots=True)
class LogicalWatermark:
    name: str
    value: str

    def __post_init__(self) -> None:
        _required_text("watermark name", self.name)
        _required_text("watermark value", self.value)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class ActiveModel:
    signature: str
    vector_space: str
    modality: str
    dimensions: int
    generation: int

    def __post_init__(self) -> None:
        _required_text("model signature", self.signature)
        _required_text("vector_space", self.vector_space)
        _required_text("modality", self.modality)
        if isinstance(self.dimensions, bool) or self.dimensions < 1:
            raise ValueError("model dimensions must be positive")
        if isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("model generation cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "signature": self.signature,
            "vector_space": self.vector_space,
            "modality": self.modality,
            "dimensions": self.dimensions,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class OwnerSnapshot:
    owner: str
    state: OwnerAvailability
    expected_schema_version: int
    observed_schema_version: int | None = None
    publications: tuple[PublicationHead, ...] = ()
    watermarks: tuple[LogicalWatermark, ...] = ()
    data_version_before: int | None = None
    data_version_after: int | None = None
    warning: str | None = None
    error_code: str | None = None
    identity_changed: bool = False

    def __post_init__(self) -> None:
        _required_text("owner", self.owner)
        if self.expected_schema_version < 1:
            raise ValueError("expected schema version must be positive")
        if self.observed_schema_version is not None and self.observed_schema_version < 0:
            raise ValueError("observed schema version cannot be negative")
        if self.state is OwnerAvailability.AVAILABLE:
            if self.observed_schema_version != self.expected_schema_version:
                raise ValueError("available owner must expose its expected schema")
        if self.data_version_before is not None and self.data_version_before < 0:
            raise ValueError("data_version cannot be negative")
        if self.data_version_after is not None and self.data_version_after < 0:
            raise ValueError("data_version cannot be negative")
        if not isinstance(self.identity_changed, bool):
            raise ValueError("identity_changed must be boolean")
        _optional_text("owner warning", self.warning)
        _optional_text("owner error code", self.error_code)
        if len({head.scope for head in self.publications}) != len(self.publications):
            raise ValueError("publication scopes must be unique per owner")
        if len({item.name for item in self.watermarks}) != len(self.watermarks):
            raise ValueError("watermark names must be unique per owner")

    @property
    def changed(self) -> bool:
        return self.identity_changed or (
            self.data_version_before is not None
            and self.data_version_after is not None
            and self.data_version_before != self.data_version_after
        )

    def identity_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "owner": self.owner,
            "state": self.state.value,
            "expected_schema_version": self.expected_schema_version,
            "publications": [
                item.to_dict()
                for item in sorted(self.publications, key=lambda value: value.scope)
            ],
            "watermarks": [
                item.to_dict()
                for item in sorted(self.watermarks, key=lambda value: value.name)
            ],
        }
        if self.observed_schema_version is not None:
            payload["observed_schema_version"] = self.observed_schema_version
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.identity_changed:
            payload["identity_changed"] = True
        return payload

    def to_dict(self) -> dict[str, object]:
        payload = self.identity_dict()
        if self.data_version_before is not None:
            payload["data_version_before"] = self.data_version_before
        if self.data_version_after is not None:
            payload["data_version_after"] = self.data_version_after
        if self.warning is not None:
            payload["warning"] = self.warning
        return payload


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    source_version: str
    captured_at_utc: str
    captured_monotonic_ns: int
    owners: tuple[OwnerSnapshot, ...]
    active_models: tuple[ActiveModel, ...]
    snapshot_id: str
    consistency: SnapshotConsistency = SnapshotConsistency.STABLE
    attempts: int = 1
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_text("source_version", self.source_version)
        _required_text("captured_at_utc", self.captured_at_utc)
        _required_text("snapshot_id", self.snapshot_id)
        if not self.captured_at_utc.endswith("Z"):
            raise ValueError("captured_at_utc must be an explicit UTC timestamp")
        if self.captured_monotonic_ns < 0:
            raise ValueError("captured monotonic time cannot be negative")
        if isinstance(self.attempts, bool) or not 1 <= self.attempts <= 2:
            raise ValueError("snapshot attempts must be one or two")
        if len({owner.owner for owner in self.owners}) != len(self.owners):
            raise ValueError("snapshot owners must be unique")
        if len({model.signature for model in self.active_models}) != len(
            self.active_models
        ):
            raise ValueError("active model signatures must be unique")
        changed_owners = tuple(owner for owner in self.owners if owner.changed)
        if self.consistency is SnapshotConsistency.STABLE and changed_owners:
            raise ValueError("a stable snapshot cannot contain a changed owner")
        if self.consistency is SnapshotConsistency.SNAPSHOT_CHANGED:
            if self.attempts != 2:
                raise ValueError("snapshot_changed requires exactly two attempts")
            if not changed_owners:
                raise ValueError("snapshot_changed requires at least one changed owner")
        if self.active_models:
            semantic_owner = next(
                (owner for owner in self.owners if owner.owner == "semantic"),
                None,
            )
            compatible_publications: set[tuple[str, int]] = set()
            if (
                semantic_owner is not None
                and semantic_owner.state is OwnerAvailability.AVAILABLE
            ):
                compatible_publications = {
                    (head.model_signature, head.generation)
                    for head in semantic_owner.publications
                    if head.model_signature is not None
                }
            if any(
                (model.signature, model.generation)
                not in compatible_publications
                for model in self.active_models
            ):
                raise ValueError(
                    "active model must correspond to a compatible semantic publication"
                )
        for warning in self.warnings:
            _required_text("snapshot warning", warning)

    @classmethod
    def create(
        cls,
        *,
        source_version: str,
        captured_at_utc: str,
        captured_monotonic_ns: int,
        owners: tuple[OwnerSnapshot, ...],
        active_models: tuple[ActiveModel, ...] = (),
        consistency: SnapshotConsistency = SnapshotConsistency.STABLE,
        attempts: int = 1,
        warnings: tuple[str, ...] = (),
    ) -> KnowledgeSnapshot:
        ordered_owners = tuple(sorted(owners, key=lambda owner: owner.owner))
        ordered_models = tuple(
            sorted(active_models, key=lambda model: model.signature)
        )
        identity_payload: dict[str, object] = {
            "schema_version": KNOWLEDGE_CONTRACT_SCHEMA_VERSION,
            "source_version": source_version,
            "owners": [owner.identity_dict() for owner in ordered_owners],
            "active_models": [model.to_dict() for model in ordered_models],
            "consistency": consistency.value,
        }
        fingerprint = fingerprint_text(canonical_json(identity_payload))
        snapshot_id = (
            "knowledge-snapshot-v1:"
            f"{fingerprint.xxh3_128}:{fingerprint.byte_count}:"
            f"{fingerprint.xxh3_64_guard}"
        )
        return cls(
            source_version=source_version,
            captured_at_utc=captured_at_utc,
            captured_monotonic_ns=captured_monotonic_ns,
            owners=ordered_owners,
            active_models=ordered_models,
            snapshot_id=snapshot_id,
            consistency=consistency,
            attempts=attempts,
            warnings=warnings,
        )

    @property
    def changed_owners(self) -> tuple[str, ...]:
        return tuple(owner.owner for owner in self.owners if owner.changed)

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("knowledge_snapshot")
        payload.update(
            {
                "source_version": self.source_version,
                "captured_at_utc": self.captured_at_utc,
                "captured_monotonic_ns": self.captured_monotonic_ns,
                "owners": [owner.to_dict() for owner in self.owners],
                "active_models": [model.to_dict() for model in self.active_models],
                "snapshot_id": self.snapshot_id,
                "consistency": self.consistency.value,
                "attempts": self.attempts,
            }
        )
        if self.changed_owners:
            payload["changed_owners"] = list(self.changed_owners)
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


# endregion [04]


# region [05] Context bundle envelope


def _validate_context_plan_values(name: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"context plan {name} must be a tuple")
    if len(values) > MAX_CONTEXT_PLAN_VALUES:
        raise ValueError(
            f"context plan {name} cannot contain more than "
            f"{MAX_CONTEXT_PLAN_VALUES} values"
        )
    if len(set(values)) != len(values):
        raise ValueError(f"context plan {name} must be unique")
    total_characters = 0
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"context plan {name} must contain strings")
        _required_text(f"context plan {name} value", value)
        if len(value) > MAX_CONTEXT_PLAN_VALUE_CHARS:
            raise ValueError(
                f"context plan {name} values cannot exceed "
                f"{MAX_CONTEXT_PLAN_VALUE_CHARS} characters"
            )
        total_characters += len(value)
    if total_characters > MAX_CONTEXT_PLAN_TOTAL_VALUE_CHARS:
        raise ValueError(
            f"context plan {name} cannot exceed "
            f"{MAX_CONTEXT_PLAN_TOTAL_VALUE_CHARS} total characters"
        )


@dataclass(frozen=True, slots=True)
class ContextPlanStepRef:
    channel: str
    ranking_name: str
    reason: str
    candidate_limit: int
    required: bool

    def __post_init__(self) -> None:
        for name, value in (
            ("channel", self.channel),
            ("ranking_name", self.ranking_name),
            ("reason", self.reason),
        ):
            if not isinstance(value, str):
                raise ValueError(f"context plan step {name} must be a string")
            _required_text(f"context plan step {name}", value)
            if len(value) > MAX_CONTEXT_PLAN_VALUE_CHARS:
                raise ValueError(
                    f"context plan step {name} cannot exceed "
                    f"{MAX_CONTEXT_PLAN_VALUE_CHARS} characters"
                )
        if (
            isinstance(self.candidate_limit, bool)
            or not isinstance(self.candidate_limit, int)
            or not 1 <= self.candidate_limit <= 1_000
        ):
            raise ValueError(
                "context plan step candidate_limit must be between 1 and 1000"
            )
        if not isinstance(self.required, bool):
            raise ValueError("context plan step required must be a bool")

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("context_plan_step_ref")
        payload.update(
            {
                "channel": self.channel,
                "ranking_name": self.ranking_name,
                "reason": self.reason,
                "candidate_limit": self.candidate_limit,
                "required": self.required,
            }
        )
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


@dataclass(frozen=True, slots=True)
class ContextPlanRef:
    plan_id: str
    normalized_query: str
    retrieval_mode: str
    intents: tuple[str, ...]
    exact_terms: tuple[str, ...]
    source_kinds: tuple[str, ...]
    formats: tuple[str, ...]
    project: str | None
    date_from: str | None
    date_to: str | None
    include_history: bool
    limit: int
    max_per_resource: int
    min_section_distance: int
    max_vectors: int
    steps: tuple[ContextPlanStepRef, ...]
    notices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("plan_id", self.plan_id),
            ("normalized_query", self.normalized_query),
        ):
            if not isinstance(value, str):
                raise ValueError(f"context plan {name} must be a string")
            _required_text(f"context plan {name}", value)
            if len(value) > MAX_CONTEXT_PLAN_VALUE_CHARS:
                raise ValueError(
                    f"context plan {name} cannot exceed "
                    f"{MAX_CONTEXT_PLAN_VALUE_CHARS} characters"
                )
        if self.retrieval_mode not in {"discovery", "evidence"}:
            raise ValueError("context plan retrieval_mode is invalid")
        for name, values in (
            ("intents", self.intents),
            ("exact_terms", self.exact_terms),
            ("source_kinds", self.source_kinds),
            ("formats", self.formats),
            ("notices", self.notices),
        ):
            _validate_context_plan_values(name, values)
        for name, option_value in (
            ("project", self.project),
            ("date_from", self.date_from),
            ("date_to", self.date_to),
        ):
            if option_value is not None and not isinstance(option_value, str):
                raise ValueError(f"context plan {name} must be a string when present")
            _optional_text(f"context plan {name}", option_value)
            if (
                option_value is not None
                and len(option_value) > MAX_CONTEXT_PLAN_VALUE_CHARS
            ):
                raise ValueError(
                    f"context plan {name} cannot exceed "
                    f"{MAX_CONTEXT_PLAN_VALUE_CHARS} characters"
                )
        if not isinstance(self.include_history, bool):
            raise ValueError("context plan include_history must be a bool")
        for name, numeric_value, minimum, maximum in (
            ("limit", self.limit, 1, 1_000),
            ("max_per_resource", self.max_per_resource, 1, 100),
            ("min_section_distance", self.min_section_distance, 0, 1_000_000),
            ("max_vectors", self.max_vectors, 1, 10_000_000),
        ):
            if (
                isinstance(numeric_value, bool)
                or not isinstance(numeric_value, int)
                or not minimum <= numeric_value <= maximum
            ):
                raise ValueError(
                    f"context plan {name} must be between {minimum} and {maximum}"
                )
        if not isinstance(self.steps, tuple):
            raise ValueError("context plan steps must be a tuple")
        if len(self.steps) > MAX_CONTEXT_PLAN_STEPS:
            raise ValueError(
                f"context plan cannot contain more than {MAX_CONTEXT_PLAN_STEPS} steps"
            )
        if not all(isinstance(step, ContextPlanStepRef) for step in self.steps):
            raise ValueError("context plan steps are invalid")

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("context_plan_ref")
        payload.update(
            {
                "plan_id": self.plan_id,
                "normalized_query": self.normalized_query,
                "retrieval_mode": self.retrieval_mode,
                "intents": list(self.intents),
                "exact_terms": list(self.exact_terms),
                "source_kinds": list(self.source_kinds),
                "formats": list(self.formats),
                "include_history": self.include_history,
                "limit": self.limit,
                "max_per_resource": self.max_per_resource,
                "min_section_distance": self.min_section_distance,
                "max_vectors": self.max_vectors,
                "steps": [step.to_dict() for step in self.steps],
            }
        )
        if self.project is not None:
            payload["project"] = self.project
        if self.date_from is not None:
            payload["date_from"] = self.date_from
        if self.date_to is not None:
            payload["date_to"] = self.date_to
        if self.notices:
            payload["notices"] = list(self.notices)
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


@dataclass(frozen=True, slots=True)
class ContextGraphBudget:
    identifiers_considered: int
    entities_included: int
    relations_included: int
    omitted_identifiers: int = 0
    omitted_entities: int = 0
    omitted_relations: int = 0
    identifier_limit_per_evidence: int = MAX_EVIDENCE_IDENTIFIERS
    measurement_scope: str = "selected_evidence_graph"

    def __post_init__(self) -> None:
        for name, value in (
            ("identifiers_considered", self.identifiers_considered),
            ("entities_included", self.entities_included),
            ("relations_included", self.relations_included),
            ("omitted_identifiers", self.omitted_identifiers),
            ("omitted_entities", self.omitted_entities),
            ("omitted_relations", self.omitted_relations),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"context graph {name} cannot be negative")
        if self.identifier_limit_per_evidence != MAX_EVIDENCE_IDENTIFIERS:
            raise ValueError("context graph identifier limit is invalid")
        if self.measurement_scope != "selected_evidence_graph":
            raise ValueError("context graph measurement_scope is invalid")

    @property
    def omitted_total(self) -> int:
        return (
            self.omitted_identifiers
            + self.omitted_entities
            + self.omitted_relations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "identifiers_considered": self.identifiers_considered,
            "entities_included": self.entities_included,
            "relations_included": self.relations_included,
            "omitted_identifiers": self.omitted_identifiers,
            "omitted_entities": self.omitted_entities,
            "omitted_relations": self.omitted_relations,
            "identifier_limit_per_evidence": self.identifier_limit_per_evidence,
            "measurement_scope": self.measurement_scope,
        }


@dataclass(frozen=True, slots=True)
class ContextBudget:
    character_limit: int
    characters_used: int
    estimated_tokens: int
    estimator_signature: str
    omitted_candidates: int = 0
    truncated_evidence_ids: tuple[str, ...] = ()
    measurement_scope: str = "rendered_context"

    def __post_init__(self) -> None:
        if isinstance(self.character_limit, bool) or self.character_limit < 1:
            raise ValueError("context character limit must be positive")
        if not 0 <= self.characters_used <= self.character_limit:
            raise ValueError("context characters used exceed the limit")
        if self.estimated_tokens < 0:
            raise ValueError("estimated token count cannot be negative")
        if self.omitted_candidates < 0:
            raise ValueError("omitted candidate count cannot be negative")
        _required_text("estimator signature", self.estimator_signature)
        if self.measurement_scope != "rendered_context":
            raise ValueError(
                "context budget measurement_scope must be rendered_context"
            )
        if len(set(self.truncated_evidence_ids)) != len(
            self.truncated_evidence_ids
        ):
            raise ValueError("truncated evidence identifiers must be unique")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "character_limit": self.character_limit,
            "characters_used": self.characters_used,
            "estimated_tokens": self.estimated_tokens,
            "estimator_signature": self.estimator_signature,
            "omitted_candidates": self.omitted_candidates,
            "measurement_scope": self.measurement_scope,
        }
        if self.truncated_evidence_ids:
            payload["truncated_evidence_ids"] = list(self.truncated_evidence_ids)
        return payload


def _validate_context_references(
    name: str,
    references: tuple[str, ...],
) -> None:
    if not references:
        raise ValueError(f"context {name} requires at least one reference")
    if len(set(references)) != len(references):
        raise ValueError(f"context {name} references must be unique")
    for reference in references:
        _required_text(f"context {name} reference", reference)


@dataclass(frozen=True, slots=True)
class ContextEntityRef:
    entity_id: str
    entity_kind: str
    label: str
    evidence_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _required_text("context entity_id", self.entity_id)
        _required_text("context entity kind", self.entity_kind)
        _required_text("context entity label", self.label)
        _validate_context_references("entity evidence", self.evidence_ids)
        _validate_context_references("entity resource", self.resource_ids)

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("context_entity_ref")
        payload.update(
            {
                "entity_id": self.entity_id,
                "entity_kind": self.entity_kind,
                "label": self.label,
                "evidence_ids": list(self.evidence_ids),
                "resource_ids": list(self.resource_ids),
            }
        )
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


@dataclass(frozen=True, slots=True)
class ContextContradictionRef:
    contradiction_id: str
    contradiction_kind: str
    topic: str
    values: tuple[str, ...]
    citation_ids: tuple[str, ...]

    @staticmethod
    def _stable_id(
        contradiction_kind: str,
        topic: str,
        values: tuple[str, ...],
    ) -> str:
        identity = {
            "contradiction_kind": contradiction_kind,
            "topic": topic.casefold(),
            "values": [value.casefold() for value in values],
        }
        fingerprint = fingerprint_text(canonical_json(identity))
        return (
            "context-contradiction-v1:"
            f"{fingerprint.xxh3_128}:{fingerprint.byte_count}:"
            f"{fingerprint.xxh3_64_guard}"
        )

    @classmethod
    def create(
        cls,
        *,
        contradiction_kind: str,
        topic: str,
        values: tuple[str, ...],
        citation_ids: tuple[str, ...],
    ) -> "ContextContradictionRef":
        ordered_values = tuple(sorted(values, key=str.casefold))
        return cls(
            contradiction_id=cls._stable_id(
                contradiction_kind,
                topic,
                ordered_values,
            ),
            contradiction_kind=contradiction_kind,
            topic=topic,
            values=ordered_values,
            citation_ids=citation_ids,
        )

    def __post_init__(self) -> None:
        _required_text("context contradiction_id", self.contradiction_id)
        _required_text("context contradiction kind", self.contradiction_kind)
        _required_text("context contradiction topic", self.topic)
        _validate_context_references("contradiction value", self.values)
        if len({value.casefold() for value in self.values}) < 2:
            raise ValueError(
                "context contradictions require at least two distinct values"
            )
        if self.values != tuple(sorted(self.values, key=str.casefold)):
            raise ValueError("context contradiction values must be canonically ordered")
        _validate_context_references("contradiction citation", self.citation_ids)
        if len(self.citation_ids) < 2:
            raise ValueError(
                "context contradictions require at least two distinct citations"
            )
        expected_id = self._stable_id(
            self.contradiction_kind,
            self.topic,
            self.values,
        )
        if self.contradiction_id != expected_id:
            raise ValueError("context contradiction_id does not match its identity")

    @property
    def summary(self) -> str:
        return (
            "Structured claim "
            f"{json.dumps(self.topic, ensure_ascii=False, allow_nan=False)} "
            "has conflicting values: "
            f"{', '.join(json.dumps(value, ensure_ascii=False, allow_nan=False) for value in self.values)}."
        )

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("context_contradiction_ref")
        payload.update(
            {
                "contradiction_id": self.contradiction_id,
                "contradiction_kind": self.contradiction_kind,
                "topic": self.topic,
                "values": list(self.values),
                "summary": self.summary,
                "citation_ids": list(self.citation_ids),
            }
        )
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


@dataclass(frozen=True, slots=True)
class ContextRelationRef:
    relation_id: str
    source_entity_id: str
    target_entity_id: str
    relation_kind: str
    method: EvidenceMethod
    provenance: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    confidence: float | None = None

    def __post_init__(self) -> None:
        _required_text("context relation_id", self.relation_id)
        _required_text("context relation source", self.source_entity_id)
        _required_text("context relation target", self.target_entity_id)
        _required_text("context relation kind", self.relation_kind)
        if not isinstance(self.method, EvidenceMethod):
            raise ValueError("context relation method is invalid")
        _validate_context_references("relation provenance", self.provenance)
        if len(self.provenance) > MAX_CONTEXT_RELATION_PROVENANCE_ITEMS:
            raise ValueError(
                "context relation provenance cannot contain more than "
                f"{MAX_CONTEXT_RELATION_PROVENANCE_ITEMS} items"
            )
        if sum(len(item) for item in self.provenance) > (
            MAX_CONTEXT_RELATION_PROVENANCE_CHARS
        ):
            raise ValueError(
                "context relation provenance cannot exceed "
                f"{MAX_CONTEXT_RELATION_PROVENANCE_CHARS} total characters"
            )
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("context relation confidence must be between 0 and 1")
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("context relations require different entities")
        _validate_context_references("relation evidence", self.evidence_ids)

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("context_relation_ref")
        payload.update(
            {
                "relation_id": self.relation_id,
                "source_entity_id": self.source_entity_id,
                "target_entity_id": self.target_entity_id,
                "relation_kind": self.relation_kind,
                "method": self.method.value,
                "provenance": list(self.provenance),
                "evidence_ids": list(self.evidence_ids),
            }
        )
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


@dataclass(frozen=True, slots=True)
class ContextBundle:
    normalized_query: str
    intents: tuple[str, ...]
    plan_id: str
    plan: ContextPlanRef
    snapshot: KnowledgeSnapshot
    selected_hits: tuple[KnowledgeHit, ...]
    citation_ids: tuple[tuple[str, str], ...]
    graph_budget: ContextGraphBudget
    budget: ContextBudget
    rendered_context: str
    completeness: KnowledgeCompleteness
    entities: tuple[ContextEntityRef, ...] = ()
    relations: tuple[ContextRelationRef, ...] = ()
    contradictions: tuple[ContextContradictionRef, ...] = ()
    missing_information: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    telemetry: KnowledgeQueryTelemetry | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        _required_text("normalized query", self.normalized_query)
        _required_text("plan_id", self.plan_id)
        for intent in self.intents:
            _required_text("intent", intent)
        if self.plan.plan_id != self.plan_id:
            raise ValueError("context plan_id must match the normalized plan")
        if self.plan.normalized_query != self.normalized_query:
            raise ValueError("context query must match the normalized plan")
        if self.plan.intents != self.intents:
            raise ValueError("context intents must match the normalized plan")
        selected_evidence_ids = tuple(
            hit.evidence.evidence_id for hit in self.selected_hits
        )
        evidence_ids = set(selected_evidence_ids)
        evidence_resources: dict[str, set[str]] = {}
        for hit in self.selected_hits:
            grounded_resources = evidence_resources.setdefault(
                hit.evidence.evidence_id,
                set(),
            )
            grounded_resources.add(hit.resource.resource_id)
            for namespace, value in hit.evidence.identifiers:
                if namespace.casefold() in {
                    "planned_duplicate_of",
                    "code_relation_source_resource",
                    "code_relation_target_resource",
                }:
                    grounded_resources.add(value)
        resource_ids = {
            resource_id
            for grounded_resources in evidence_resources.values()
            for resource_id in grounded_resources
        }
        if len(evidence_ids) != len(selected_evidence_ids):
            raise ValueError(
                "selected hits require unique evidence identifiers for citations"
            )
        citation_names: set[str] = set()
        cited_evidence_ids: set[str] = set()
        for citation_id, evidence_id in self.citation_ids:
            _required_text("citation_id", citation_id)
            if citation_id in citation_names:
                raise ValueError("citation identifiers must be unique")
            citation_names.add(citation_id)
            if evidence_id not in evidence_ids:
                raise ValueError("citation must reference selected evidence")
            cited_evidence_ids.add(evidence_id)
        if (
            len(self.citation_ids) != len(selected_evidence_ids)
            or cited_evidence_ids != evidence_ids
        ):
            raise ValueError(
                "each selected hit must have exactly one citation by evidence_id"
            )
        entity_ids = [entity.entity_id for entity in self.entities]
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("context entity identifiers must be unique")
        for entity in self.entities:
            if not set(entity.evidence_ids).issubset(cited_evidence_ids):
                raise ValueError("context entities must reference cited evidence")
            if not set(entity.resource_ids).issubset(resource_ids):
                raise ValueError("context entities must reference a grounded resource")
            grounded_resources = {
                resource_id
                for evidence_id in entity.evidence_ids
                for resource_id in evidence_resources.get(evidence_id, set())
            }
            if not set(entity.resource_ids).issubset(grounded_resources):
                raise ValueError(
                    "context entity resources must be grounded by its evidence references"
                )
        relation_ids = [relation.relation_id for relation in self.relations]
        if len(set(relation_ids)) != len(relation_ids):
            raise ValueError("context relation identifiers must be unique")
        logical_relations = [
            (
                relation.source_entity_id,
                relation.target_entity_id,
                relation.relation_kind,
                relation.method,
                relation.provenance,
                relation.evidence_ids,
                relation.confidence,
            )
            for relation in self.relations
        ]
        if len(set(logical_relations)) != len(logical_relations):
            raise ValueError("logical context relations must be unique")
        known_entity_ids = set(entity_ids)
        entities_by_id = {entity.entity_id: entity for entity in self.entities}
        for relation in self.relations:
            if not {
                relation.source_entity_id,
                relation.target_entity_id,
            }.issubset(known_entity_ids):
                raise ValueError("context relations must reference existing entities")
            if not set(relation.evidence_ids).issubset(cited_evidence_ids):
                raise ValueError("context relations must reference cited evidence")
            relation_evidence = set(relation.evidence_ids)
            source_evidence = set(
                entities_by_id[relation.source_entity_id].evidence_ids
            )
            target_evidence = set(
                entities_by_id[relation.target_entity_id].evidence_ids
            )
            if not relation_evidence.issubset(
                source_evidence.intersection(target_evidence)
            ):
                raise ValueError(
                    "context relation evidence must ground both endpoints"
                )
        identifiers_considered = sum(
            len(hit.evidence.identifiers) for hit in self.selected_hits
        )
        if self.graph_budget.identifiers_considered != identifiers_considered:
            raise ValueError(
                "context graph identifier count must match selected evidence"
            )
        if self.graph_budget.entities_included != len(self.entities):
            raise ValueError("context graph entity count must match entities")
        if self.graph_budget.relations_included != len(self.relations):
            raise ValueError("context graph relation count must match relations")
        if (
            self.graph_budget.omitted_total
            and self.completeness is KnowledgeCompleteness.COMPLETE
        ):
            raise ValueError("omitted context graph data requires partial completeness")
        for entity in self.entities:
            if entity.to_json() not in self.rendered_context:
                raise ValueError(
                    "context entities must be rendered inside the character budget"
                )
        for relation in self.relations:
            if relation.to_json() not in self.rendered_context:
                raise ValueError(
                    "context relations must be rendered inside the character budget"
                )
        contradiction_ids: set[str] = set()
        logical_contradictions: set[tuple[str, str, tuple[str, ...]]] = set()
        for contradiction in self.contradictions:
            if contradiction.contradiction_id in contradiction_ids:
                raise ValueError("context contradiction identifiers must be unique")
            contradiction_ids.add(contradiction.contradiction_id)
            if not set(contradiction.citation_ids).issubset(citation_names):
                raise ValueError(
                    "contradictions require at least two existing citations"
                )
            rendered_citations = f"[{', '.join(contradiction.citation_ids)}]"
            if (
                contradiction.summary not in self.rendered_context
                or rendered_citations not in self.rendered_context
            ):
                raise ValueError(
                    "context contradictions must be rendered inside the character budget"
                )
            logical_contradiction = (
                contradiction.contradiction_kind,
                contradiction.topic.casefold(),
                tuple(value.casefold() for value in contradiction.values),
            )
            if logical_contradiction in logical_contradictions:
                raise ValueError("logical context contradictions must be unique")
            logical_contradictions.add(logical_contradiction)
        for item in (*self.missing_information, *self.warnings):
            _required_text("context notice", item)
        if self.graph_budget.omitted_total:
            graph_notices = tuple(
                item
                for item in (*self.missing_information, *self.warnings)
                if "graph" in item.casefold() and "omit" in item.casefold()
            )
            if not graph_notices or not any(
                notice in self.rendered_context for notice in graph_notices
            ):
                raise ValueError(
                    "omitted context graph data requires a rendered visible notice"
                )
        if len(self.rendered_context) != self.budget.characters_used:
            raise ValueError("rendered context and budget character count disagree")
        if self.telemetry is not None and not isinstance(
            self.telemetry,
            KnowledgeQueryTelemetry,
        ):
            raise ValueError("context telemetry is invalid")
        if (
            self.telemetry is not None
            and self.telemetry.operation is not KnowledgeTelemetryOperation.CONTEXT
        ):
            raise ValueError(
                "ContextBundle telemetry must describe a context operation"
            )

    def to_dict(self) -> dict[str, object]:
        payload = _base_payload("context_bundle")
        payload.update(
            {
                "normalized_query": self.normalized_query,
                "intents": list(self.intents),
                "plan_id": self.plan_id,
                "plan": self.plan.to_dict(),
                "snapshot": self.snapshot.to_dict(),
                "selected_hits": [hit.to_dict() for hit in self.selected_hits],
                "citation_ids": [
                    {"citation_id": citation_id, "evidence_id": evidence_id}
                    for citation_id, evidence_id in self.citation_ids
                ],
                "entities": [entity.to_dict() for entity in self.entities],
                "relations": [relation.to_dict() for relation in self.relations],
                "graph_budget": self.graph_budget.to_dict(),
                "budget": self.budget.to_dict(),
                "rendered_context": self.rendered_context,
                "completeness": self.completeness.value,
            }
        )
        if self.contradictions:
            payload["contradictions"] = [
                contradiction.to_dict() for contradiction in self.contradictions
            ]
        if self.missing_information:
            payload["missing_information"] = list(self.missing_information)
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.telemetry is not None:
            payload["telemetry"] = self.telemetry.to_dict()
        return payload

    def to_json(self) -> str:
        return _canonical_output(self.to_dict())


# endregion [05]


__all__ = (
    "KNOWLEDGE_CONTRACT_SCHEMA_VERSION",
    "KNOWLEDGE_TELEMETRY_CLOCK_SIGNATURE",
    "KNOWLEDGE_TELEMETRY_UNIDENTIFIED_CLOCK_SIGNATURE",
    "KNOWLEDGE_TELEMETRY_SCHEMA_VERSION",
    "MAX_EVIDENCE_IDENTIFIER_COMPONENT_CHARS",
    "MAX_EVIDENCE_IDENTIFIERS",
    "MAX_EVIDENCE_SYMBOL_CHARS",
    "ActiveModel",
    "ContextBudget",
    "ContextBundle",
    "ContextContradictionRef",
    "ContextEntityRef",
    "ContextGraphBudget",
    "ContextPlanRef",
    "ContextPlanStepRef",
    "ContextRelationRef",
    "EvidenceMethod",
    "EvidenceRef",
    "KnowledgeCompleteness",
    "KnowledgeHit",
    "KnowledgePhaseTiming",
    "KnowledgeQueryTelemetry",
    "KnowledgeSnapshot",
    "KnowledgeTelemetryClock",
    "KnowledgeTelemetryOperation",
    "KnowledgeTimingPhase",
    "LogicalWatermark",
    "OwnerAvailability",
    "OwnerSnapshot",
    "PhysicalIdentityRef",
    "PublicationHead",
    "RankingSignal",
    "ResourceDisposition",
    "ResourceRef",
    "RevisionRef",
    "RevisionState",
    "SnapshotConsistency",
)
