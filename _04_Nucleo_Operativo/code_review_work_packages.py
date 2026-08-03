"""Deterministic work packages over bounded published Code review evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, cast

from .code_review_actionability import classify_source_role
from .code_review_models import (
    CodeReviewFinding,
    CodeReviewRecommendation,
    CodeReviewWorkPackage,
    CodeReviewWorkPackageMember,
    CodeReviewWorkPackageStep,
    RecommendationStatus,
    WorkPackageRelationship,
)
from .semantic_models import canonical_json, fingerprint_text


CODE_REVIEW_PLANNING = "python-maintenance-work-packages-v1"
CODE_REVIEW_PLANNING_FINDING_LIMIT = 50
CODE_REVIEW_WORK_PACKAGE_LIMIT = 1
CODE_REVIEW_WORK_PACKAGE_MEMBER_LIMIT = 5

_ACCEPTANCE_GATES = (
    "characterization_fixture_exact",
    "target_hotspot_removed",
    "no_added_hotspots",
    "no_changed_hotspot_evidence",
    "no_corrected_or_lost_call_resolutions",
    "full_cache_hit_replay",
    "no_added_ruff_diagnostics",
)
_RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True, slots=True)
class CodeReviewPlanningLink:
    """One confirmed direct or two-hop call path between review findings."""

    source_finding_id: str
    target_finding_id: str
    depth: Literal[1, 2]
    via_symbol: str | None
    confidence: float
    provenance: tuple[str, ...]


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _planning_link_query(symbol_count: int) -> str:
    placeholders = _placeholders(symbol_count)
    pair_limit = symbol_count * max(symbol_count - 1, 0)
    return f"""
    WITH current_python_symbols AS (
        SELECT s.symbol_id,s.qualified_name,f.current_path,
               (SELECT p.probable_root FROM project_memberships pm
                JOIN projects p ON p.project_id=pm.project_id
                WHERE pm.version_id=v.version_id AND pm.selected=1
                  AND p.status='current' AND p.probable_root IS NOT NULL
                ORDER BY pm.confidence DESC,LENGTH(p.probable_root) DESC,
                         p.project_id
                LIMIT 1) AS project_root
        FROM symbols s
        JOIN file_versions v ON v.version_id=s.version_id
        JOIN files f ON f.current_version_id=v.version_id AND f.status='current'
        WHERE v.invalidated_ns IS NULL AND v.analysis_status='complete'
          AND v.language='python' AND v.generated=0 AND v.vendored=0
          AND s.confirmed=1
    ), confirmed_calls AS (
        SELECT r.source_symbol_id,r.target_symbol_id,
               MAX(r.confidence) AS confidence,
               MIN(r.evidence) AS evidence
        FROM code_references r
        JOIN current_python_symbols source
          ON source.symbol_id=r.source_symbol_id
        JOIN current_python_symbols target
          ON target.symbol_id=r.target_symbol_id
        WHERE r.kind='call' AND r.confirmed=1
        GROUP BY r.source_symbol_id,r.target_symbol_id
    ), paths AS (
        SELECT r1.source_symbol_id AS source_id,
               r1.target_symbol_id AS target_id,
               1 AS depth,NULL AS via_symbol,NULL AS via_path,
               NULL AS via_project_root,
               r1.confidence AS confidence,
               r1.evidence AS first_evidence,NULL AS second_evidence
        FROM confirmed_calls r1
        WHERE r1.source_symbol_id IN ({placeholders})
          AND r1.target_symbol_id IN ({placeholders})
        UNION ALL
        SELECT r1.source_symbol_id AS source_id,
               r2.target_symbol_id AS target_id,
               2 AS depth,bridge.qualified_name AS via_symbol,
               bridge.current_path AS via_path,
               bridge.project_root AS via_project_root,
               MIN(r1.confidence,r2.confidence) AS confidence,
               r1.evidence AS first_evidence,
               r2.evidence AS second_evidence
        FROM confirmed_calls r1
        JOIN confirmed_calls r2
          ON r2.source_symbol_id=r1.target_symbol_id
        JOIN current_python_symbols bridge
          ON bridge.symbol_id=r1.target_symbol_id
        WHERE r1.source_symbol_id IN ({placeholders})
          AND r2.target_symbol_id IN ({placeholders})
    ), ranked_paths AS (
        SELECT source_id,target_id,depth,via_symbol,via_path,confidence,
               first_evidence,second_evidence,
               ROW_NUMBER() OVER(
                   PARTITION BY source_id,target_id
                   ORDER BY depth,COALESCE(via_symbol,''),first_evidence,
                            COALESCE(second_evidence,'')
               ) AS route_rank
        FROM paths
        WHERE source_id<>target_id
          AND (via_path IS NULL OR
               neocortex_source_role(via_path,via_project_root)='production')
    )
    SELECT source_id,target_id,depth,via_symbol,via_path,confidence,
           first_evidence,second_evidence
    FROM ranked_paths
    WHERE route_rank=1
    ORDER BY source_id,target_id,depth,COALESCE(via_symbol,''),
             first_evidence,COALESCE(second_evidence,'')
    LIMIT {pair_limit}
    """


def read_code_review_planning_links(
    connection: sqlite3.Connection,
    finding_ids_by_symbol_id: dict[int, str],
) -> tuple[CodeReviewPlanningLink, ...]:
    """Read bounded confirmed call paths without mutating the Code database."""

    symbol_ids = tuple(sorted(finding_ids_by_symbol_id))
    if not symbol_ids:
        return ()
    connection.create_function(
        "neocortex_source_role",
        2,
        lambda path, root: classify_source_role(
            str(path),
            None if root is None else str(root),
        ),
        deterministic=True,
    )
    parameters = (*symbol_ids, *symbol_ids, *symbol_ids, *symbol_ids)
    rows = connection.execute(
        _planning_link_query(len(symbol_ids)),
        parameters,
    ).fetchall()
    links: dict[tuple[str, str], CodeReviewPlanningLink] = {}
    for row in rows:
        source = finding_ids_by_symbol_id[int(row["source_id"])]
        target = finding_ids_by_symbol_id[int(row["target_id"])]
        key = (source, target)
        if key in links:
            continue
        depth = cast(Literal[1, 2], int(row["depth"]))
        evidence = [str(row["first_evidence"])]
        if row["second_evidence"] is not None:
            evidence.append(str(row["second_evidence"]))
        links[key] = CodeReviewPlanningLink(
            source_finding_id=source,
            target_finding_id=target,
            depth=depth,
            via_symbol=None if row["via_symbol"] is None else str(row["via_symbol"]),
            confidence=float(row["confidence"]),
            provenance=tuple(evidence),
        )
    return tuple(links[key] for key in sorted(links))


def _member(
    finding: CodeReviewFinding,
    role: Literal["primary_change_target", "contract_guard"],
    relationship: WorkPackageRelationship,
    evidence: tuple[str, ...],
) -> CodeReviewWorkPackageMember:
    return CodeReviewWorkPackageMember(
        finding_id=finding.finding_id,
        hotspot_id=finding.hotspot_id,
        hotspot_rank=finding.rank,
        path=finding.path,
        symbol=finding.symbol,
        construction=finding.construction,
        actionability=finding.actionability,
        role=role,
        relationship=relationship,
        relationship_evidence=evidence,
    )


def _link_evidence(link: CodeReviewPlanningLink) -> tuple[str, ...]:
    evidence = [f"resolved_static_call_depth:{link.depth}"]
    if link.via_symbol is not None:
        evidence.append(f"via_symbol:{link.via_symbol}")
    evidence.append(f"minimum_confidence:{link.confidence:.6f}")
    evidence.extend(f"provenance:{item}" for item in link.provenance)
    return tuple(evidence)


def _related_findings(
    primary: CodeReviewFinding,
    findings: tuple[CodeReviewFinding, ...],
    links: tuple[CodeReviewPlanningLink, ...],
    recommendation_ids: frozenset[str],
) -> tuple[tuple[CodeReviewFinding, CodeReviewPlanningLink], ...]:
    by_id = {finding.finding_id: finding for finding in findings}
    related = []
    for link in links:
        candidate = by_id.get(link.target_finding_id)
        if link.source_finding_id != primary.finding_id or candidate is None:
            continue
        if (
            candidate.finding_id in recommendation_ids
            or candidate.source_role != "production"
        ):
            continue
        related.append((candidate, link))
    related.sort(
        key=lambda item: (
            item[1].depth,
            item[0].rank,
            item[0].path.casefold(),
            item[0].symbol,
        )
    )
    return tuple(related)


def _ordered_union(values: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in values for item in group))


def _package_steps(
    primary: CodeReviewFinding,
    related: tuple[tuple[CodeReviewFinding, CodeReviewPlanningLink], ...],
) -> tuple[CodeReviewWorkPackageStep, ...]:
    characterization_targets = ",".join(
        finding.symbol for finding in (primary, *(item[0] for item in related))
    )
    steps = [
        CodeReviewWorkPackageStep(
            1,
            "characterize",
            characterization_targets,
            "freeze_order_evidence_confidence_uncertainty_and_abstention",
        ),
        CodeReviewWorkPackageStep(
            2,
            "change",
            primary.symbol,
            "preserve_public_contract_and_characterized_behavior",
        ),
    ]
    steps.extend(
        (
            CodeReviewWorkPackageStep(
                len(steps) + 1,
                "validate",
                primary.symbol,
                "run_representative_fixture_and_consumer_regressions",
            ),
            CodeReviewWorkPackageStep(
                len(steps) + 2,
                "publish",
                primary.symbol,
                "compare_publication_and_require_incremental_replay",
            ),
        )
    )
    return tuple(steps)


def _package_id(
    primary: CodeReviewFinding,
    related: tuple[tuple[CodeReviewFinding, CodeReviewPlanningLink], ...],
) -> str:
    payload = canonical_json(
        {
            "planning": CODE_REVIEW_PLANNING,
            "primary_hotspot_id": primary.hotspot_id,
            "members": [
                {
                    "hotspot_id": finding.hotspot_id,
                    "depth": link.depth,
                    "via_symbol": link.via_symbol,
                }
                for finding, link in related
            ],
        }
    )
    return "code-review-work-package-v1:xxh3_128:" + fingerprint_text(payload).xxh3_128


def _package_risk(
    primary: CodeReviewFinding,
    related: tuple[tuple[CodeReviewFinding, CodeReviewPlanningLink], ...],
) -> Literal["low", "medium", "high", "unknown"]:
    risks = (primary.change_risk, *(finding.change_risk for finding, _link in related))
    selected = max(risks, key=lambda risk: _RISK_ORDER[risk])
    if related and selected == "medium":
        return "high"
    return selected


def build_code_review_work_packages(
    findings: tuple[CodeReviewFinding, ...],
    recommendations: tuple[CodeReviewRecommendation, ...],
    links: tuple[CodeReviewPlanningLink, ...],
) -> tuple[CodeReviewWorkPackage, ...]:
    """Build the single next coherent package; never batch independent roots."""

    if not recommendations:
        return ()
    by_id = {finding.finding_id: finding for finding in findings}
    primary = by_id.get(recommendations[0].finding_id)
    if primary is None:
        return ()
    recommendation_ids = frozenset(item.finding_id for item in recommendations)
    all_related = _related_findings(primary, findings, links, recommendation_ids)
    related = all_related[: CODE_REVIEW_WORK_PACKAGE_MEMBER_LIMIT - 1]
    members_truncated = len(all_related) > len(related)
    package_findings = (primary, *(item[0] for item in related))
    members = (
        _member(
            primary,
            "primary_change_target",
            "primary",
            ("primary_actionability:act_now",),
        ),
        *(
            _member(
                finding,
                "contract_guard",
                "resolved_static_call"
                if link.depth == 1
                else "resolved_static_call_via",
                _link_evidence(link),
            )
            for finding, link in related
        ),
    )
    relationship_evidence = tuple(
        item for _finding, link in related for item in _link_evidence(link)
    )
    return (
        CodeReviewWorkPackage(
            package_rank=1,
            package_id=_package_id(primary, related),
            title=f"{primary.symbol} maintenance package",
            objective="reduce_confirmed_hotspots_without_contract_regression",
            primary_finding_id=primary.finding_id,
            primary_hotspot_id=primary.hotspot_id,
            primary_symbol=primary.symbol,
            change_risk=_package_risk(primary, related),
            members=members,
            members_truncated=members_truncated,
            consumer_module_examples=_ordered_union(
                tuple(
                    finding.impact.consumer_module_examples
                    for finding in package_findings
                )
            ),
            contracts_to_preserve=_ordered_union(
                tuple(finding.contracts_to_preserve for finding in package_findings)
            ),
            steps=_package_steps(primary, related),
            recommended_validation=_ordered_union(
                tuple(finding.recommended_validation for finding in package_findings)
            ),
            acceptance_gates=_ACCEPTANCE_GATES,
            evidence=(
                "bounded_planning_horizon:50",
                "primary_recommendation_rank:1",
                *relationship_evidence,
            ),
            limitations=(
                "work_package_is_advice_not_authorization",
                "relationship_graph_is_bounded_to_two_static_call_hops",
                "dynamic_dispatch_is_not_observed",
                "related_members_require_characterization_before_change",
            ),
            confidence=(
                "confirmed_static_relationship" if related else "primary_finding_only"
            ),
        ),
    )


def plan_code_review_work_packages(
    findings: tuple[CodeReviewFinding, ...],
    recommendations: tuple[CodeReviewRecommendation, ...],
    links: tuple[CodeReviewPlanningLink, ...],
) -> tuple[
    tuple[CodeReviewWorkPackage, ...],
    RecommendationStatus,
    str | None,
]:
    """Return packages and their explicit ready or abstained envelope state."""

    packages = build_code_review_work_packages(findings, recommendations, links)
    if packages:
        return packages, "ready", None
    return (
        (),
        "abstained",
        "no_primary_act_now_recommendation_within_bounded_findings",
    )


__all__ = [
    "CODE_REVIEW_PLANNING",
    "CODE_REVIEW_PLANNING_FINDING_LIMIT",
    "CODE_REVIEW_WORK_PACKAGE_LIMIT",
    "CodeReviewPlanningLink",
    "build_code_review_work_packages",
    "plan_code_review_work_packages",
    "read_code_review_planning_links",
]
