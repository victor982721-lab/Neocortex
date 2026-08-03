"""Deterministic digest construction for the Code review envelope."""

from __future__ import annotations

from dataclasses import asdict

from .code_architecture_analysis import CodeArchitectureAnalysis
from .code_coverage_analysis import CodeCoverageAnalysis
from .code_external_evidence import (
    ExternalEvidenceStatus,
    external_status_digest_payload,
)
from .code_review_models import (
    CODE_REVIEW_SCHEMA,
    CodeReviewCoverage,
    CodeReviewDigest,
    CodeReviewFinding,
    CodeReviewRecommendation,
    CodeReviewSnapshot,
    CodeReviewWorkPackage,
    RecommendationStatus,
)
from .external_evidence_models import ExternalEvidenceSuiteStatus
from .semantic_models import canonical_json, fingerprint_text


def build_code_review_digest(
    snapshot: CodeReviewSnapshot,
    coverage: CodeReviewCoverage,
    findings: tuple[CodeReviewFinding, ...],
    *,
    ranking: str,
    actionability_version: str,
    recommendation_status: RecommendationStatus,
    recommendation_reason: str | None,
    recommendations: tuple[CodeReviewRecommendation, ...],
    planning_version: str,
    work_package_status: RecommendationStatus,
    work_package_reason: str | None,
    work_packages: tuple[CodeReviewWorkPackage, ...],
    external_evidence: ExternalEvidenceStatus,
    external_evidence_suite: ExternalEvidenceSuiteStatus,
    architecture: CodeArchitectureAnalysis,
    test_coverage: CodeCoverageAnalysis,
    limitations: tuple[str, ...],
) -> CodeReviewDigest:
    """Hash every decision-bearing field while excluding local database paths."""

    payload = canonical_json(
        {
            "schema": CODE_REVIEW_SCHEMA,
            "ranking": ranking,
            "actionability_version": actionability_version,
            "recommendation_status": recommendation_status,
            "recommendation_reason": recommendation_reason,
            "planning_version": planning_version,
            "work_package_status": work_package_status,
            "work_package_reason": work_package_reason,
            "snapshot": {
                "processing_signature": snapshot.processing_signature,
                "freshness": snapshot.freshness,
                "current": snapshot.current,
                "journal_status": snapshot.journal_status,
            },
            "coverage": asdict(coverage),
            "findings": [asdict(finding) for finding in findings],
            "recommendations": [asdict(recommendation) for recommendation in recommendations],
            "work_packages": [asdict(package) for package in work_packages],
            "external_evidence": external_status_digest_payload(external_evidence),
            "external_evidence_suite": external_evidence_suite.as_payload(),
            "architecture": architecture.digest_payload(),
            "test_coverage": test_coverage.digest_payload(),
            "limitations": list(limitations),
        }
    )
    fingerprint = fingerprint_text(payload)
    return CodeReviewDigest(
        fingerprint.xxh3_128,
        fingerprint.xxh3_64_guard,
        fingerprint.byte_count,
    )


__all__ = ["build_code_review_digest"]
