"""Explainable, advisory consensus for potentially unused Python symbols.

The consumer correlates already-published static findings with the current Code
graph and optional trusted-deep coverage.  It never mutates source content and
never treats a tool confidence value as deletion authority.
"""

from __future__ import annotations

import ast
import json
import math
import sqlite3
import zlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal, cast

from .code_coverage_analysis import read_code_coverage_analysis
from .external_evidence_models import ExternalProviderEvidence, ExternalProviderFinding
from .external_evidence_store import (
    read_external_evidence_suite,
    read_external_provider_evidence,
)
from .semantic_models import canonical_json, fingerprint_text

CODE_UNUSED_SCHEMA = "neocortex.code-unused-analysis/v1"
VULTURE_UNUSED_PROVIDER_ID = "vulture-unused-static"
PYRIGHT_UNUSED_PROVIDER_ID = "pyright-trusted-project"

CODE_UNUSED_CANDIDATE_LIMIT = 20_000
CODE_UNUSED_FINDING_LIMIT = 25_000
CODE_UNUSED_SOURCE_BYTES_LIMIT = 32 * 1024 * 1024
CODE_UNUSED_SOURCE_FILE_BYTES_LIMIT = 2 * 1024 * 1024
CODE_UNUSED_SAMPLE_LIMIT = 1_000
CODE_UNUSED_TEXT_LIMIT = 8_192
VULTURE_HIGH_CONFIDENCE = 0.90

UnusedState = Literal[
    "explained_usage",
    "dynamic_usage_possible",
    "insufficient_evidence",
    "probable_unused_high_consensus",
]
CoverageState = Literal["complete", "partial", "missing"]
ProviderState = Literal["ready", "abstained", "missing"]
AnalysisState = Literal["ready", "abstained"]
GateState = Literal["passed", "failed", "not_evaluated"]
CalibrationLabel = Literal["used", "unused"]


@dataclass(frozen=True, slots=True)
class UnusedEvidenceSignals:
    vulture_reported: bool
    vulture_confidence: float | None
    pyright_reported: bool
    vulture_complete: bool
    pyright_complete: bool
    providers_aligned: bool
    graph_references: int
    graph_calls: int
    graph_imports: int
    in_all: bool
    reexported: bool
    entry_point: bool
    callback: bool
    registry: bool
    fixture: bool
    protocol: bool
    special: bool
    coverage_observed: bool
    coverage_status: CoverageState
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnusedConsensusCandidate:
    candidate_id: str
    version_id: int
    symbol_id: int
    relative_path: str
    module_id: str
    symbol: str
    name: str
    kind: str
    start_line: int
    end_line: int
    state: UnusedState
    provider_ids: tuple[str, ...]
    signals: UnusedEvidenceSignals
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    authority: Literal["advisory"] = "advisory"
    mutation_authority: bool = False


@dataclass(frozen=True, slots=True)
class UnusedCalibrationSample:
    sample_id: str
    label: CalibrationLabel
    signals: UnusedEvidenceSignals


@dataclass(frozen=True, slots=True)
class UnusedCalibrationReport:
    signature: str
    dataset_id: str
    total: int
    positive_labels: int
    negative_labels: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    abstained_positive: int
    abstained_negative: int
    precision: float | None
    recall: float | None
    abstention_rate: float | None
    precision_denominator: int
    recall_denominator: int
    abstention_denominator: int


@dataclass(frozen=True, slots=True)
class UnusedProviderStatus:
    provider_id: str
    status: ProviderState
    reason: str | None
    tool_run_id: int | None
    effective_tool_run_id: int | None
    findings: int


@dataclass(frozen=True, slots=True)
class UnusedGateEvaluation:
    gate: Literal["probable_unused_precision"]
    status: GateState
    reason: str | None


@dataclass(frozen=True, slots=True)
class CodeUnusedAnalysis:
    database: str
    analysis_run_id: int | None
    status: AnalysisState
    reason: str | None
    policy_signature: str
    provider_signature: str
    evidence_signature: str
    calibration_signature: str
    coverage_status: CoverageState
    providers: tuple[UnusedProviderStatus, ...]
    candidates: tuple[UnusedConsensusCandidate, ...]
    counts: Mapping[str, int]
    calibration: UnusedCalibrationReport
    holdout: UnusedCalibrationReport
    gates: tuple[UnusedGateEvaluation, ...]
    limitations: tuple[str, ...]
    authority: Literal["advisory"] = "advisory"
    mutation_authority: bool = False

    def as_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["counts"] = dict(sorted(self.counts.items()))
        return {"kind": "code-unused-analysis", "schema": CODE_UNUSED_SCHEMA, **payload}

    def digest_payload(self) -> dict[str, object]:
        """Return replay-stable evidence without database-local identities."""

        payload = self.as_payload()
        payload.pop("database")
        payload.pop("analysis_run_id")
        providers = cast(list[dict[str, object]], payload["providers"])
        for provider in providers:
            provider.pop("tool_run_id")
            provider.pop("effective_tool_run_id")
        candidates = cast(list[dict[str, object]], payload["candidates"])
        for candidate in candidates:
            candidate.pop("version_id")
            candidate.pop("symbol_id")
        return payload


_POLICY_PAYLOAD = {
    "schema": CODE_UNUSED_SCHEMA,
    "vulture_high_confidence": VULTURE_HIGH_CONFIDENCE,
    "precedence": [
        "confirmed_usage",
        "dynamic_usage_possible",
        "high_consensus",
        "insufficient_evidence",
    ],
    "states": [
        "explained_usage",
        "dynamic_usage_possible",
        "insufficient_evidence",
        "probable_unused_high_consensus",
    ],
    "mutation_authority": False,
}
CODE_UNUSED_POLICY_SIGNATURE = (
    "unused-policy-v1:xxh3_128:"
    + fingerprint_text(canonical_json(_POLICY_PAYLOAD)).xxh3_128
)


def _signal(
    *,
    vulture: bool = False,
    confidence: float | None = None,
    pyright: bool = False,
    complete: bool = True,
    references: int = 0,
    calls: int = 0,
    imports: int = 0,
    in_all: bool = False,
    reexported: bool = False,
    entry_point: bool = False,
    callback: bool = False,
    registry: bool = False,
    fixture: bool = False,
    protocol: bool = False,
    special: bool = False,
    coverage_observed: bool = False,
    coverage_status: CoverageState = "missing",
) -> UnusedEvidenceSignals:
    return UnusedEvidenceSignals(
        vulture,
        confidence,
        pyright,
        complete,
        complete,
        complete,
        references,
        calls,
        imports,
        in_all,
        reexported,
        entry_point,
        callback,
        registry,
        fixture,
        protocol,
        special,
        coverage_observed,
        coverage_status,
        (),
    )


DEFAULT_CALIBRATION_SAMPLES = (
    UnusedCalibrationSample("cal-used-reference", "used", _signal(vulture=True, confidence=1.0, pyright=True, references=2)),
    UnusedCalibrationSample("cal-used-import", "used", _signal(vulture=True, confidence=0.9, pyright=True, imports=1)),
    UnusedCalibrationSample("cal-used-export", "used", _signal(vulture=True, confidence=1.0, pyright=True, in_all=True)),
    UnusedCalibrationSample("cal-used-coverage", "used", _signal(vulture=True, confidence=1.0, pyright=True, coverage_observed=True, coverage_status="complete")),
    UnusedCalibrationSample("cal-dynamic-registry", "used", _signal(vulture=True, confidence=1.0, pyright=True, registry=True)),
    UnusedCalibrationSample("cal-unused-both-100", "unused", _signal(vulture=True, confidence=1.0, pyright=True)),
    UnusedCalibrationSample("cal-unused-both-90", "unused", _signal(vulture=True, confidence=0.9, pyright=True)),
    UnusedCalibrationSample("cal-unused-vulture-only", "unused", _signal(vulture=True, confidence=1.0)),
)

DEFAULT_HOLDOUT_SAMPLES = (
    UnusedCalibrationSample("hold-used-call", "used", _signal(vulture=True, confidence=1.0, pyright=True, calls=1)),
    UnusedCalibrationSample("hold-used-entry", "used", _signal(vulture=True, confidence=1.0, pyright=True, entry_point=True)),
    UnusedCalibrationSample("hold-dynamic-protocol", "used", _signal(vulture=True, confidence=1.0, pyright=True, protocol=True)),
    UnusedCalibrationSample("hold-unused-both", "unused", _signal(vulture=True, confidence=1.0, pyright=True)),
    UnusedCalibrationSample("hold-unused-low", "unused", _signal(vulture=True, confidence=0.6, pyright=True)),
    UnusedCalibrationSample("hold-unused-incomplete", "unused", _signal(vulture=True, confidence=1.0, pyright=True, complete=False)),
)


def _validate_signals(signals: UnusedEvidenceSignals) -> None:
    if signals.vulture_confidence is not None and (
        isinstance(signals.vulture_confidence, bool)
        or not math.isfinite(signals.vulture_confidence)
        or not 0.0 <= signals.vulture_confidence <= 1.0
    ):
        raise ValueError("unused Vulture confidence is invalid")
    for value in (signals.graph_references, signals.graph_calls, signals.graph_imports):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("unused graph count is invalid")
    if len(signals.evidence_ids) > CODE_UNUSED_FINDING_LIMIT:
        raise ValueError("unused evidence identity bound exceeded")
    if len(set(signals.evidence_ids)) != len(signals.evidence_ids):
        raise ValueError("unused evidence identities are duplicated")


def _classify(signals: UnusedEvidenceSignals) -> tuple[UnusedState, tuple[str, ...]]:
    _validate_signals(signals)
    confirmed_usage = []
    if signals.graph_references:
        confirmed_usage.append("indexed_reference")
    if signals.graph_calls:
        confirmed_usage.append("indexed_call")
    if signals.graph_imports:
        confirmed_usage.append("indexed_import")
    if signals.in_all:
        confirmed_usage.append("declared_in___all__")
    if signals.reexported:
        confirmed_usage.append("reexported")
    if signals.entry_point:
        confirmed_usage.append("declared_entry_point")
    if signals.coverage_observed and signals.coverage_status == "complete":
        confirmed_usage.append("executed_by_complete_coverage")
    if confirmed_usage:
        return "explained_usage", tuple(confirmed_usage)
    dynamic = []
    for observed, reason in (
        (signals.callback, "callback_binding"),
        (signals.registry, "registry_binding"),
        (signals.fixture, "fixture_binding"),
        (signals.protocol, "protocol_contract"),
        (signals.special, "special_runtime_protocol"),
    ):
        if observed:
            dynamic.append(reason)
    if dynamic:
        return "dynamic_usage_possible", tuple(dynamic)
    if (
        signals.vulture_reported
        and signals.pyright_reported
        and signals.vulture_confidence is not None
        and signals.vulture_confidence >= VULTURE_HIGH_CONFIDENCE
        and signals.vulture_complete
        and signals.pyright_complete
        and signals.providers_aligned
    ):
        return "probable_unused_high_consensus", (
            "vulture_high_confidence",
            "pyright_reportUnused_consensus",
            "no_observed_usage_or_dynamic_contract",
        )
    reasons = []
    if not signals.vulture_reported:
        reasons.append("vulture_did_not_report")
    elif signals.vulture_confidence is None or signals.vulture_confidence < VULTURE_HIGH_CONFIDENCE:
        reasons.append("vulture_confidence_below_calibrated_threshold")
    if not signals.pyright_reported:
        reasons.append("pyright_did_not_report")
    if not signals.vulture_complete or not signals.pyright_complete:
        reasons.append("provider_measurement_incomplete")
    if not signals.providers_aligned:
        reasons.append("provider_domains_not_aligned")
    if signals.coverage_status != "complete":
        reasons.append(f"coverage_{signals.coverage_status}_does_not_support_usage")
    return "insufficient_evidence", tuple(sorted(set(reasons)))


def classify_unused_candidate(signals: UnusedEvidenceSignals) -> UnusedState:
    """Classify one candidate with evidence precedence and no mutation authority."""

    return _classify(signals)[0]


def _sample_signature(samples: Sequence[UnusedCalibrationSample], dataset_id: str) -> str:
    payload = {
        "dataset_id": dataset_id,
        "samples": [asdict(item) for item in sorted(samples, key=lambda item: item.sample_id)],
    }
    return "unused-calibration-v1:xxh3_128:" + fingerprint_text(
        canonical_json(payload)
    ).xxh3_128


def evaluate_unused_calibration(
    samples: Sequence[UnusedCalibrationSample],
    *,
    dataset_id: str = "neocortex-unused-calibration-custom/v1",
) -> UnusedCalibrationReport:
    """Evaluate precision, recall and abstention on an explicit labeled set."""

    if not dataset_id or len(dataset_id) > CODE_UNUSED_TEXT_LIMIT:
        raise ValueError("unused calibration dataset id is invalid")
    if len(samples) > CODE_UNUSED_SAMPLE_LIMIT:
        raise ValueError("unused calibration sample bound exceeded")
    ordered = tuple(sorted(samples, key=lambda item: item.sample_id))
    if any(not item.sample_id or len(item.sample_id) > CODE_UNUSED_TEXT_LIMIT for item in ordered):
        raise ValueError("unused calibration sample id is invalid")
    if len({item.sample_id for item in ordered}) != len(ordered):
        raise ValueError("unused calibration sample id is duplicated")
    counts: Counter[str] = Counter()
    for sample in ordered:
        state = classify_unused_candidate(sample.signals)
        positive = state == "probable_unused_high_consensus"
        negative = state == "explained_usage"
        if positive:
            counts["true_positive" if sample.label == "unused" else "false_positive"] += 1
        elif negative:
            counts["false_negative" if sample.label == "unused" else "true_negative"] += 1
        else:
            counts[
                "abstained_positive" if sample.label == "unused" else "abstained_negative"
            ] += 1
    positives = sum(item.label == "unused" for item in ordered)
    negatives = len(ordered) - positives
    precision_denominator = counts["true_positive"] + counts["false_positive"]
    recall_denominator = positives
    abstention_denominator = len(ordered)
    abstained = counts["abstained_positive"] + counts["abstained_negative"]
    return UnusedCalibrationReport(
        _sample_signature(ordered, dataset_id),
        dataset_id,
        len(ordered),
        positives,
        negatives,
        counts["true_positive"],
        counts["false_positive"],
        counts["true_negative"],
        counts["false_negative"],
        counts["abstained_positive"],
        counts["abstained_negative"],
        None
        if precision_denominator == 0
        else counts["true_positive"] / precision_denominator,
        None if recall_denominator == 0 else counts["true_positive"] / recall_denominator,
        None if abstention_denominator == 0 else abstained / abstention_denominator,
        precision_denominator,
        recall_denominator,
        abstention_denominator,
    )

