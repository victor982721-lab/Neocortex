"""Public immutable contracts for deterministic Code review."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from .code_review_actionability import (
    Actionability,
    ChangeRisk,
    Construction,
    SourceRole,
)


CODE_REVIEW_SCHEMA = "neocortex.code-review/v3"
CODE_REVIEW_COMPATIBLE_SCHEMAS = ("neocortex.code-review/v2",)

ReviewStatus = Literal["ready", "abstained"]
ReviewFreshness = Literal["current", "publication_only"]
RecommendationStatus = Literal["ready", "abstained", "not_evaluated"]
WorkPackageRelationship = Literal[
    "primary",
    "resolved_static_call",
    "resolved_static_call_via",
]
WorkPackageConfidence = Literal[
    "primary_finding_only",
    "confirmed_static_relationship",
]
WorkPackagePhase = Literal["characterize", "change", "validate", "publish"]
WorkPackageMemberRole = Literal["primary_change_target", "contract_guard"]
FindingCategory = Literal[
    "complex_and_long_hotspot",
    "high_complexity_hotspot",
    "long_function_hotspot",
]


@dataclass(frozen=True, slots=True)
class CodeReviewDiagnostic:
    """One exact analyzer diagnostic supporting a symbol hotspot."""

    code: Literal["high_complexity", "long_function"]
    value: int
    threshold: int | None
    source: str
    tool_name: str
    tool_version: str
    confirmed: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class CodeReviewCaller:
    """One bounded, resolved static call site used as impact evidence."""

    path: str
    symbol: str | None
    start_line: int
    end_line: int
    confidence: float
    provenance: str
    source_role: SourceRole


@dataclass(frozen=True, slots=True)
class CodeReviewImpact:
    """Separated static impact and bounded test-safety evidence."""

    call_sites: int
    production_callers: int
    test_callers: int
    fixture_callers: int
    tool_callers: int
    compatibility_callers: int
    consumer_modules: int
    production_consumer_modules: int
    test_consumer_modules: int
    consumer_module_examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeReviewFinding:
    """One symbol-level review candidate; rank is advisory, not calibrated risk."""

    finding_id: str
    hotspot_id: str
    rank: int
    category: FindingCategory
    path: str
    symbol: str
    symbol_kind: str
    signature: str | None
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    start_byte: int
    end_byte: int
    complexity: int
    function_lines: int
    complexity_ratio_basis_points: int
    length_ratio_basis_points: int
    score_basis_points: int
    incoming_references: int
    incoming_calls: int
    resolved_static_callers: int
    impact: CodeReviewImpact
    source_role: SourceRole
    construction: Construction
    actionability: Actionability
    change_risk: ChangeRisk
    recommended_change: bool
    actionability_evidence: tuple[str, ...]
    contracts_to_preserve: tuple[str, ...]
    recommended_validation: tuple[str, ...]
    analyzer_id: str
    analyzer_version: str
    file_xxh3_128: str | None
    file_xxh3_64_guard: str | None
    diagnostics: tuple[CodeReviewDiagnostic, ...]
    callers: tuple[CodeReviewCaller, ...]
    reasons: tuple[str, ...]
    confidence: Literal["confirmed_static_evidence"] = "confirmed_static_evidence"


@dataclass(frozen=True, slots=True)
class CodeReviewRecommendation:
    """One act-now recommendation linked to its raw hotspot evidence."""

    recommendation_rank: int
    finding_id: str
    hotspot_id: str
    hotspot_rank: int
    path: str
    symbol: str
    construction: Construction
    change_risk: ChangeRisk
    production_callers: int
    test_callers: int
    evidence: tuple[str, ...]
    contracts_to_preserve: tuple[str, ...]
    recommended_validation: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeReviewWorkPackageMember:
    """One finding retained in a coherent bounded maintenance package."""

    finding_id: str
    hotspot_id: str
    hotspot_rank: int
    path: str
    symbol: str
    construction: Construction
    actionability: Actionability
    role: WorkPackageMemberRole
    relationship: WorkPackageRelationship
    relationship_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeReviewWorkPackageStep:
    """One ordered, non-mutating planning step with an explicit decision gate."""

    order: int
    phase: WorkPackagePhase
    target: str
    requirement: str


@dataclass(frozen=True, slots=True)
class CodeReviewWorkPackage:
    """A deterministic maintenance unit assembled from published evidence."""

    package_rank: int
    package_id: str
    title: str
    objective: str
    primary_finding_id: str
    primary_hotspot_id: str
    primary_symbol: str
    change_risk: ChangeRisk
    members: tuple[CodeReviewWorkPackageMember, ...]
    members_truncated: bool
    consumer_module_examples: tuple[str, ...]
    contracts_to_preserve: tuple[str, ...]
    steps: tuple[CodeReviewWorkPackageStep, ...]
    recommended_validation: tuple[str, ...]
    acceptance_gates: tuple[str, ...]
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    confidence: WorkPackageConfidence


@dataclass(frozen=True, slots=True)
class CodeReviewSnapshot:
    """Published self-analysis snapshot that authorized this review."""

    analysis_run_id: int
    framework_run_id: int
    scan_id: int
    processing_signature: str
    root: str
    freshness: ReviewFreshness
    current: bool
    journal_status: str


@dataclass(frozen=True, slots=True)
class CodeReviewCoverage:
    """Bounded coverage and deliberately suppressed evidence."""

    current_python_files: int
    complete_python_files: int
    incomplete_python_files: int
    candidate_hotspots: int
    enumerated_hotspots: int
    probable_dead_suppressed: int
    call_edges: int
    resolved_call_edges: int


@dataclass(frozen=True, slots=True)
class CodeReviewDigest:
    """Collision-guarded deterministic identity of review evidence."""

    xxh3_128: str
    xxh3_64_guard: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class CodeReviewResult:
    """One JSON-ready review result with a deterministic content digest."""

    database: str
    status: ReviewStatus
    reason: str | None
    ranking: str
    actionability_version: str
    recommendation_status: RecommendationStatus
    recommendation_reason: str | None
    planning_version: str
    work_package_status: RecommendationStatus
    work_package_reason: str | None
    snapshot: CodeReviewSnapshot | None
    coverage: CodeReviewCoverage | None
    findings: tuple[CodeReviewFinding, ...]
    recommendations: tuple[CodeReviewRecommendation, ...]
    work_packages: tuple[CodeReviewWorkPackage, ...]
    limitations: tuple[str, ...]
    digest: CodeReviewDigest | None

    def as_payload(self) -> dict[str, object]:
        return {
            "kind": "code-review",
            "schema": CODE_REVIEW_SCHEMA,
            "compatible_schemas": list(CODE_REVIEW_COMPATIBLE_SCHEMAS),
            **asdict(self),
        }


def build_code_review_recommendations(
    findings: tuple[CodeReviewFinding, ...],
    *,
    limit: int,
) -> tuple[CodeReviewRecommendation, ...]:
    """Select act-now results while preserving the observable raw ranking."""

    selected = tuple(
        finding for finding in findings if finding.actionability == "act_now"
    )[:limit]
    return tuple(
        CodeReviewRecommendation(
            recommendation_rank=recommendation_rank,
            finding_id=finding.finding_id,
            hotspot_id=finding.hotspot_id,
            hotspot_rank=finding.rank,
            path=finding.path,
            symbol=finding.symbol,
            construction=finding.construction,
            change_risk=finding.change_risk,
            production_callers=finding.impact.production_callers,
            test_callers=finding.impact.test_callers + finding.impact.fixture_callers,
            evidence=finding.actionability_evidence,
            contracts_to_preserve=finding.contracts_to_preserve,
            recommended_validation=finding.recommended_validation,
        )
        for recommendation_rank, finding in enumerate(selected, start=1)
    )


__all__ = [
    "CODE_REVIEW_COMPATIBLE_SCHEMAS",
    "CODE_REVIEW_SCHEMA",
    "CodeReviewCaller",
    "CodeReviewCoverage",
    "CodeReviewDigest",
    "CodeReviewDiagnostic",
    "CodeReviewFinding",
    "CodeReviewImpact",
    "CodeReviewRecommendation",
    "CodeReviewResult",
    "CodeReviewSnapshot",
    "CodeReviewWorkPackage",
    "CodeReviewWorkPackageMember",
    "CodeReviewWorkPackageStep",
    "FindingCategory",
    "ReviewFreshness",
    "RecommendationStatus",
    "WorkPackageConfidence",
    "WorkPackageMemberRole",
    "WorkPackagePhase",
    "WorkPackageRelationship",
    "build_code_review_recommendations",
]
