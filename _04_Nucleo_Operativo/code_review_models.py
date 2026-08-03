"""Public immutable contracts for deterministic Code review."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

from .code_architecture_analysis import CodeArchitectureAnalysis
from .code_coverage_analysis import (
    CodeCoverageAnalysis,
    CoverageScopeSummary,
    TestToSymbolRelation,
    WorkPackageCoverageProjection,
)
from .code_engineering_analytics import (
    CODE_ENGINEERING_ANALYTICS_SCHEMA,
    CodeEngineeringAnalytics,
    EngineeringDimension,
    EngineeringGate,
    ModuleEngineeringProfile,
)
from .code_external_evidence import ExternalEvidenceStatus
from .code_supply_chain_analysis import (
    CodeSupplyChainAnalysis,
    SupplyChainGateEvaluation,
    SupplyChainObservation,
)
from .code_unused_analysis import CodeUnusedAnalysis, UnusedConsensusCandidate
from .code_review_actionability import (
    Actionability,
    ChangeRisk,
    Construction,
    SourceRole,
)
from .external_evidence_models import ExternalEvidenceSuiteStatus

CODE_REVIEW_SCHEMA = "neocortex.code-review/v10"
CODE_REVIEW_COMPATIBLE_SCHEMAS = (
    "neocortex.code-review/v2",
    "neocortex.code-review/v3",
    "neocortex.code-review/v4",
    "neocortex.code-review/v5",
    "neocortex.code-review/v6",
    "neocortex.code-review/v7",
    "neocortex.code-review/v8",
    "neocortex.code-review/v9",
)
CODE_REVIEW_COVERAGE_EXAMPLE_LIMIT = 20
CODE_REVIEW_ENGINEERING_EXAMPLE_LIMIT = 20
CODE_REVIEW_UNUSED_EXAMPLE_LIMIT = 20

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
    "unused_high_consensus_advisory",
]
WorkPackagePhase = Literal["characterize", "change", "validate", "publish"]
WorkPackageKind = Literal["hotspot_maintenance", "unused_characterization"]
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
    primary_module: str | None
    change_risk: ChangeRisk
    members: tuple[CodeReviewWorkPackageMember, ...]
    members_truncated: bool
    consumer_module_examples: tuple[str, ...]
    import_chains: tuple[tuple[str, ...], ...]
    affected_architecture_contracts: tuple[str, ...]
    test_coverage: WorkPackageCoverageProjection | None
    test_coverage_scope: CoverageScopeSummary | None
    contracts_to_preserve: tuple[str, ...]
    steps: tuple[CodeReviewWorkPackageStep, ...]
    recommended_validation: tuple[str, ...]
    acceptance_gates: tuple[str, ...]
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    confidence: WorkPackageConfidence
    package_kind: WorkPackageKind = "hotspot_maintenance"
    unused_candidates: tuple[UnusedConsensusCandidate, ...] = ()
    supply_chain_observations: tuple[SupplyChainObservation, ...] = ()
    supply_chain_relations: tuple[SupplyChainObservation, ...] = ()
    supply_chain_gates: tuple[SupplyChainGateEvaluation, ...] = ()
    engineering_profile: ModuleEngineeringProfile | None = None
    engineering_gates: tuple[EngineeringGate, ...] = ()
    requires_human_confirmation: bool = False
    mutation_authority: Literal[False] = False


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
    external_evidence: ExternalEvidenceStatus | None
    external_evidence_suite: ExternalEvidenceSuiteStatus | None
    architecture: CodeArchitectureAnalysis | None
    test_coverage: CodeCoverageAnalysis | None
    limitations: tuple[str, ...]
    digest: CodeReviewDigest | None
    unused_analysis: CodeUnusedAnalysis | None = None
    supply_chain: CodeSupplyChainAnalysis | None = None
    engineering_analytics: CodeEngineeringAnalytics | None = None

    def as_payload(self) -> dict[str, object]:
        payload = asdict(
            replace(
                self,
                work_packages=(),
                test_coverage=None,
                unused_analysis=None,
                supply_chain=None,
                engineering_analytics=None,
            )
        )
        payload["work_packages"] = [
            _bounded_work_package_payload(item) for item in self.work_packages
        ]
        if self.external_evidence is not None:
            payload["external_evidence"] = self.external_evidence.as_payload()
        if self.external_evidence_suite is not None:
            payload["external_evidence_suite"] = self.external_evidence_suite.as_payload()
        if self.test_coverage is not None:
            payload["test_coverage"] = bounded_code_coverage_payload(self.test_coverage)
        if self.unused_analysis is not None:
            payload["unused_analysis"] = bounded_code_unused_payload(self.unused_analysis)
        if self.supply_chain is not None:
            payload["supply_chain"] = self.supply_chain.as_payload()
        if self.engineering_analytics is not None:
            payload["engineering_analytics"] = bounded_code_engineering_payload(
                self.engineering_analytics
            )
        return {
            "kind": "code-review",
            "schema": CODE_REVIEW_SCHEMA,
            "compatible_schemas": list(CODE_REVIEW_COMPATIBLE_SCHEMAS),
            **payload,
        }


def _bounded_scope_payload(scope: CoverageScopeSummary) -> dict[str, object]:
    limit = CODE_REVIEW_COVERAGE_EXAMPLE_LIMIT
    return {
        "subject_kind": scope.subject_kind,
        "subject_key": scope.subject_key,
        "module_key": scope.module_key,
        "symbol_key": scope.symbol_key,
        "qualified_name": scope.qualified_name,
        "start_line": scope.start_line,
        "end_line": scope.end_line,
        "relative_path": scope.relative_path,
        "totals": asdict(scope.totals),
        "missing_line_ranges": [list(item) for item in scope.missing_line_ranges[:limit]],
        "missing_line_ranges_total": len(scope.missing_line_ranges),
        "missing_line_ranges_truncated": (
            scope.missing_line_ranges_truncated or len(scope.missing_line_ranges) > limit
        ),
        "missing_branch_arcs": [list(item) for item in scope.missing_branch_arcs[:limit]],
        "missing_branch_arcs_total": len(scope.missing_branch_arcs),
        "missing_branch_arcs_truncated": (
            scope.missing_branch_arcs_truncated or len(scope.missing_branch_arcs) > limit
        ),
        "protecting_tests": list(scope.protecting_tests[:limit]),
        "protecting_tests_total": len(scope.protecting_tests),
        "protecting_tests_truncated": len(scope.protecting_tests) > limit,
    }


def _bounded_unused_candidate_payload(
    candidate: UnusedConsensusCandidate,
) -> dict[str, object]:
    limit = CODE_REVIEW_UNUSED_EXAMPLE_LIMIT
    payload = asdict(candidate)
    for field_name in ("provider_ids", "reasons", "evidence", "limitations"):
        values = getattr(candidate, field_name)
        payload[field_name] = list(values[:limit])
        payload[f"{field_name}_total"] = len(values)
        payload[f"{field_name}_truncated"] = len(values) > limit
    signals = payload.get("signals")
    if isinstance(signals, dict):
        evidence_ids = candidate.signals.evidence_ids
        signals["evidence_ids"] = list(evidence_ids[:limit])
        signals["evidence_ids_total"] = len(evidence_ids)
        signals["evidence_ids_truncated"] = len(evidence_ids) > limit
    return payload


def bounded_code_unused_payload(analysis: CodeUnusedAnalysis) -> dict[str, object]:
    """Project small public examples while the digest retains every candidate."""

    limit = CODE_REVIEW_UNUSED_EXAMPLE_LIMIT
    payload = analysis.as_payload()
    payload["candidates"] = [
        _bounded_unused_candidate_payload(candidate) for candidate in analysis.candidates[:limit]
    ]
    payload["candidates_total"] = len(analysis.candidates)
    payload["candidates_truncated"] = len(analysis.candidates) > limit
    counts = dict(analysis.counts)
    counts["total"] = len(analysis.candidates)
    payload["counts"] = dict(sorted(counts.items()))
    payload["limitations"] = list(analysis.limitations[:limit])
    payload["limitations_total"] = len(analysis.limitations)
    payload["limitations_truncated"] = len(analysis.limitations) > limit
    return payload


def _bounded_relation_payload(relation: TestToSymbolRelation) -> dict[str, object]:
    limit = CODE_REVIEW_COVERAGE_EXAMPLE_LIMIT
    return {
        "relation_id": relation.relation_id,
        "test_key": relation.test_key,
        "production_symbol": relation.production_symbol,
        "test_nodeids": list(relation.test_nodeids[:limit]),
        "test_nodeids_total": len(relation.test_nodeids),
        "test_nodeids_truncated": len(relation.test_nodeids) > limit,
        "lines": list(relation.lines[:limit]),
        "lines_total": len(relation.lines),
        "lines_truncated": len(relation.lines) > limit,
        "contexts": list(relation.contexts[:limit]),
        "contexts_total": len(relation.contexts),
        "contexts_truncated": len(relation.contexts) > limit,
        "relative_path": relation.relative_path,
        "module_key": relation.module_key,
        "symbol_key": relation.symbol_key,
    }


def _missing_scope_examples(
    scopes: tuple[CoverageScopeSummary, ...],
) -> tuple[CoverageScopeSummary, ...]:
    return tuple(
        item
        for item in scopes
        if item.totals.missing_lines > 0 or item.totals.missing_branch_exits > 0
    )


def bounded_code_coverage_payload(
    analysis: CodeCoverageAnalysis,
) -> dict[str, object]:
    limit = CODE_REVIEW_COVERAGE_EXAMPLE_LIMIT
    missing_modules = _missing_scope_examples(analysis.modules)
    missing_symbols = _missing_scope_examples(analysis.symbols)
    return {
        "kind": "code-coverage-analysis",
        "schema": "neocortex.code-coverage-analysis/v1",
        "database": analysis.database,
        "analysis_run_id": analysis.analysis_run_id,
        "provider_id": analysis.provider_id,
        "tool_run_id": analysis.tool_run_id,
        "effective_tool_run_id": analysis.effective_tool_run_id,
        "status": analysis.status,
        "reason": analysis.reason,
        "suite_selection": analysis.suite_selection,
        "measurement_complete": analysis.measurement_complete,
        "content_executed": analysis.content_executed,
        "tool_versions": [asdict(item) for item in analysis.tool_versions],
        "suite_signature": analysis.suite_signature,
        "configuration_signature": analysis.configuration_signature,
        "measurement_scope_signature": analysis.measurement_scope_signature,
        "outcomes": None if analysis.outcomes is None else asdict(analysis.outcomes),
        "totals": None if analysis.totals is None else asdict(analysis.totals),
        "counts": {
            "modules": len(analysis.modules),
            "symbols": len(analysis.symbols),
            "test_relations": len(analysis.test_relations),
            "failed_tests": len(analysis.failed_test_nodeids),
            "modules_with_missing": len(missing_modules),
            "symbols_with_missing": len(missing_symbols),
        },
        "failed_test_examples": list(analysis.failed_test_nodeids[:limit]),
        "failed_test_examples_truncated": len(analysis.failed_test_nodeids) > limit,
        "module_missing_examples": [
            _bounded_scope_payload(item) for item in missing_modules[:limit]
        ],
        "module_missing_examples_truncated": len(missing_modules) > limit,
        "symbol_missing_examples": [
            _bounded_scope_payload(item) for item in missing_symbols[:limit]
        ],
        "symbol_missing_examples_truncated": len(missing_symbols) > limit,
        "test_relation_examples": [
            _bounded_relation_payload(item) for item in analysis.test_relations[:limit]
        ],
        "test_relation_examples_truncated": len(analysis.test_relations) > limit,
        "gates": [asdict(item) for item in analysis.gates],
        "limitations": list(analysis.limitations),
    }


def _bounded_engineering_dimension_payload(
    dimension: EngineeringDimension,
) -> dict[str, object]:
    limit = CODE_REVIEW_ENGINEERING_EXAMPLE_LIMIT
    return {
        "dimension": dimension.dimension,
        "status": dimension.status,
        "reason": dimension.reason,
        "metrics": [asdict(item) for item in dimension.metrics[:limit]],
        "metrics_total": len(dimension.metrics),
        "metrics_truncated": len(dimension.metrics) > limit,
        "provenance": list(dimension.provenance[:limit]),
        "provenance_total": len(dimension.provenance),
        "provenance_truncated": len(dimension.provenance) > limit,
        "limitations": list(dimension.limitations[:limit]),
        "limitations_total": len(dimension.limitations),
        "limitations_truncated": len(dimension.limitations) > limit,
    }


def _bounded_engineering_profile_payload(
    profile: ModuleEngineeringProfile,
) -> dict[str, object]:
    return {
        "module_id": profile.module_id,
        "owner_id": profile.owner_id,
        "complexity": _bounded_engineering_dimension_payload(profile.complexity),
        "coverage": _bounded_engineering_dimension_payload(profile.coverage),
        "mutation": _bounded_engineering_dimension_payload(profile.mutation),
        "history": _bounded_engineering_dimension_payload(profile.history),
        "graph": _bounded_engineering_dimension_payload(profile.graph),
    }


def bounded_code_engineering_payload(
    analysis: CodeEngineeringAnalytics,
) -> dict[str, object]:
    """Project bounded module examples without changing analytics authority."""

    limit = CODE_REVIEW_ENGINEERING_EXAMPLE_LIMIT
    return {
        "kind": "code-engineering-analytics",
        "schema": CODE_ENGINEERING_ANALYTICS_SCHEMA,
        "database": analysis.database,
        "analysis_run_id": analysis.analysis_run_id,
        "status": analysis.status,
        "reason": analysis.reason,
        "providers": [asdict(item) for item in analysis.providers[:limit]],
        "providers_total": len(analysis.providers),
        "providers_truncated": len(analysis.providers) > limit,
        "modules": [
            _bounded_engineering_profile_payload(item) for item in analysis.modules[:limit]
        ],
        "modules_total": len(analysis.modules),
        "modules_truncated": len(analysis.modules) > limit,
        "gates": [asdict(item) for item in analysis.gates[:limit]],
        "gates_total": len(analysis.gates),
        "gates_truncated": len(analysis.gates) > limit,
        "mutation_scope_signature": analysis.mutation_scope_signature,
        "mutation_score": analysis.mutation_score,
        "limitations": list(analysis.limitations[:limit]),
        "limitations_total": len(analysis.limitations),
        "limitations_truncated": len(analysis.limitations) > limit,
        "digest": analysis.digest,
        "authority": analysis.authority,
        "mutation_authority": analysis.mutation_authority,
        "aggregate_score": analysis.aggregate_score,
        "defect_probability": analysis.defect_probability,
    }


def _bounded_work_package_payload(package: CodeReviewWorkPackage) -> dict[str, object]:
    payload = asdict(
        replace(
            package,
            test_coverage=None,
            test_coverage_scope=None,
            unused_candidates=(),
            engineering_profile=None,
            engineering_gates=(),
        )
    )
    projection = package.test_coverage
    if projection is not None:
        limit = CODE_REVIEW_COVERAGE_EXAMPLE_LIMIT
        payload["test_coverage"] = {
            "primary_symbol": projection.primary_symbol,
            "status": projection.status,
            "protecting_tests": list(projection.protecting_tests[:limit]),
            "protecting_tests_total": len(projection.protecting_tests),
            "protecting_tests_truncated": len(projection.protecting_tests) > limit,
            "relation_ids": list(projection.relation_ids[:limit]),
            "relation_ids_total": len(projection.relation_ids),
            "relation_ids_truncated": len(projection.relation_ids) > limit,
            "gate": asdict(projection.gate),
        }
    if package.test_coverage_scope is not None:
        payload["test_coverage_scope"] = _bounded_scope_payload(package.test_coverage_scope)
    payload["unused_candidates"] = [
        _bounded_unused_candidate_payload(item)
        for item in package.unused_candidates[:CODE_REVIEW_UNUSED_EXAMPLE_LIMIT]
    ]
    payload["unused_candidates_total"] = len(package.unused_candidates)
    payload["unused_candidates_truncated"] = (
        len(package.unused_candidates) > CODE_REVIEW_UNUSED_EXAMPLE_LIMIT
    )
    if package.engineering_profile is not None:
        payload["engineering_profile"] = _bounded_engineering_profile_payload(
            package.engineering_profile
        )
    payload["engineering_gates"] = [
        asdict(item)
        for item in package.engineering_gates[:CODE_REVIEW_ENGINEERING_EXAMPLE_LIMIT]
    ]
    payload["engineering_gates_total"] = len(package.engineering_gates)
    payload["engineering_gates_truncated"] = (
        len(package.engineering_gates) > CODE_REVIEW_ENGINEERING_EXAMPLE_LIMIT
    )
    return payload


def build_code_review_recommendations(
    findings: tuple[CodeReviewFinding, ...],
    *,
    limit: int,
) -> tuple[CodeReviewRecommendation, ...]:
    """Select act-now results while preserving the observable raw ranking."""

    selected = tuple(finding for finding in findings if finding.actionability == "act_now")[:limit]
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
    "CodeReviewDiagnostic",
    "CodeReviewDigest",
    "CodeReviewFinding",
    "CodeReviewImpact",
    "CodeReviewRecommendation",
    "CodeReviewResult",
    "CodeReviewSnapshot",
    "CodeReviewWorkPackage",
    "CodeReviewWorkPackageMember",
    "CodeReviewWorkPackageStep",
    "FindingCategory",
    "RecommendationStatus",
    "ReviewFreshness",
    "WorkPackageConfidence",
    "WorkPackageKind",
    "WorkPackageMemberRole",
    "WorkPackagePhase",
    "WorkPackageRelationship",
    "bounded_code_coverage_payload",
    "bounded_code_engineering_payload",
    "bounded_code_unused_payload",
    "build_code_review_recommendations",
]
