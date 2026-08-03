"""Deterministic work packages over bounded published Code review evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from typing import Literal, cast

from .code_architecture_analysis import (
    CodeArchitectureAnalysis,
    bounded_import_chains,
    module_id_from_path,
)
from .code_coverage_analysis import (
    CodeCoverageAnalysis,
    project_work_package_coverage,
    project_work_package_coverage_scope,
)
from .code_unused_analysis import CodeUnusedAnalysis, UnusedConsensusCandidate
from .code_supply_chain_analysis import CodeSupplyChainAnalysis, SupplyChainObservation
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

CODE_REVIEW_PLANNING = "python-maintenance-work-packages-v4"
_CODE_REVIEW_PACKAGE_ID_PLANNING = "python-maintenance-work-packages-v1"
CODE_REVIEW_PLANNING_FINDING_LIMIT = 50
CODE_REVIEW_WORK_PACKAGE_LIMIT = 4
CODE_REVIEW_WORK_PACKAGE_MEMBER_LIMIT = 5
CODE_REVIEW_UNUSED_WORK_PACKAGE_LIMIT = 3
CODE_REVIEW_SUPPLY_CHAIN_EVIDENCE_LIMIT = 20

_ACCEPTANCE_GATES = (
    "characterization_fixture_exact",
    "target_hotspot_removed",
    "no_added_hotspots",
    "no_changed_hotspot_evidence",
    "no_corrected_or_lost_call_resolutions",
    "full_cache_hit_replay",
    "no_added_ruff_diagnostics",
    "no_added_ruff_basic_diagnostics",
    "no_added_ruff_project_diagnostics",
    "no_added_mypy_errors",
    "no_added_pyright_errors",
    "public_type_surface_not_degraded",
    "type_coverage_not_degraded",
    "provider_cache_or_rerun_explained",
    "architecture_contracts_not_degraded",
    "no_new_import_cycles",
    "module_complexity_not_displaced",
    "tests_passed",
    "coverage_available",
    "work_package_target_protected",
    "line_coverage_not_degraded",
    "branch_coverage_not_degraded",
)
_RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
_UNUSED_REQUIRED_PRECISION_GATES = frozenset(
    {
        "calibration_probable_unused_precision",
        "holdout_probable_unused_precision",
    }
)
_UNUSED_ACCEPTANCE_GATES = (
    "unused_analysis_comparable",
    "candidate_remains_probable_unused_high_consensus",
    "dynamic_usage_ruled_out_by_human_review",
    "human_confirmation_recorded",
    "tests_passed",
    "public_import_surface_preserved",
    "architecture_contracts_not_degraded",
    "no_new_import_cycles",
    "unused_coverage_status_honest",
)
_SUPPLY_CHAIN_ACCEPTANCE_GATES = (
    "semgrep_invariants",
    "dependency_declaration_integrity",
    "vulnerability_snapshot_current",
    "no_known_vulnerabilities",
    "installed_package_integrity",
    "license_inventory_available",
)


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
        if candidate.finding_id in recommendation_ids or candidate.source_role != "production":
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
            # The architecture context enriches validation but does not change
            # the legacy finding membership identity of the package.
            "planning": _CODE_REVIEW_PACKAGE_ID_PLANNING,
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


def _normalized_path(value: str) -> str:
    return value.replace("\\", "/").strip("/").casefold()


def _candidate_matches_member(
    candidate: UnusedConsensusCandidate,
    member: CodeReviewWorkPackageMember,
) -> bool:
    candidate_path = _normalized_path(candidate.relative_path)
    member_path = _normalized_path(member.path)
    path_matches = member_path == candidate_path or member_path.endswith("/" + candidate_path)
    symbol_matches = (
        candidate.symbol == member.symbol or member.symbol.rsplit(".", 1)[-1] == candidate.name
    )
    return path_matches and symbol_matches


def _annotate_unused_candidates(
    package: CodeReviewWorkPackage,
    unused_analysis: CodeUnusedAnalysis | None,
) -> CodeReviewWorkPackage:
    if unused_analysis is None or unused_analysis.status != "ready":
        return package
    matching = tuple(
        candidate
        for candidate in unused_analysis.candidates
        if any(_candidate_matches_member(candidate, member) for member in package.members)
    )[:CODE_REVIEW_WORK_PACKAGE_MEMBER_LIMIT]
    if not matching:
        return package
    return replace(
        package,
        unused_candidates=matching,
        requires_human_confirmation=True,
        acceptance_gates=tuple(
            dict.fromkeys(
                (
                    *package.acceptance_gates,
                    "unused_analysis_comparable",
                    "unused_evidence_not_degraded",
                    "human_confirmation_recorded",
                )
            )
        ),
        evidence=(
            *package.evidence,
            *(f"unused_candidate:{item.candidate_id}:{item.state}" for item in matching),
        ),
        limitations=(
            *package.limitations,
            "unused_evidence_is_advisory_and_requires_human_confirmation",
            "unused_evidence_has_zero_delete_or_mutation_authority",
        ),
    )


def _path_matches_package(path: str, package: CodeReviewWorkPackage) -> bool:
    candidate_path = _normalized_path(path)
    package_paths = {_normalized_path(member.path) for member in package.members} | {
        _normalized_path(candidate.relative_path) for candidate in package.unused_candidates
    }
    return any(
        item == candidate_path
        or item.endswith("/" + candidate_path)
        or candidate_path.endswith("/" + item)
        for item in package_paths
    )


def _key_matches_package(value: str | None, package: CodeReviewWorkPackage) -> bool:
    if not value:
        return False
    normalized = value.replace("\\", "/").casefold()
    terms = {
        package.primary_symbol.casefold(),
        package.primary_symbol.rsplit(".", 1)[-1].casefold(),
        *(() if package.primary_module is None else (package.primary_module.casefold(),)),
    }
    return any(term and (normalized == term or term in normalized) for term in terms)


def _supply_chain_observation_relevant(
    observation: SupplyChainObservation,
    package: CodeReviewWorkPackage,
) -> bool:
    return (
        (observation.path is not None and _path_matches_package(observation.path, package))
        or _key_matches_package(observation.subject_key, package)
        or _key_matches_package(observation.target_key, package)
        or observation.subject_kind == "project"
        or observation.target_kind == "project"
        or observation.gate_authority != "advisory"
    )


def _annotate_supply_chain(
    package: CodeReviewWorkPackage,
    analysis: CodeSupplyChainAnalysis | None,
) -> CodeReviewWorkPackage:
    gates = () if analysis is None else analysis.gates
    observations = (
        ()
        if analysis is None
        else tuple(
            item
            for item in analysis.observations
            if _supply_chain_observation_relevant(item, package)
        )[:CODE_REVIEW_SUPPLY_CHAIN_EVIDENCE_LIMIT]
    )
    relations = (
        ()
        if analysis is None
        else tuple(
            item
            for item in analysis.observations
            if item.evidence_kind == "relation"
            and (
                item.subject_kind in {"project", "module", "package", "dependency"}
                or item.target_kind in {"project", "module", "package", "dependency"}
            )
        )[:CODE_REVIEW_SUPPLY_CHAIN_EVIDENCE_LIMIT]
    )
    status = "not_evaluated" if analysis is None else analysis.status
    reason = "supply_chain_result_missing" if analysis is None else analysis.reason
    limitations = [
        *package.limitations,
        "supply_chain_evidence_is_advisory_and_has_zero_mutation_authority",
    ]
    if status != "ready":
        limitations.append("supply_chain_gates_require_ready_evidence:" + (reason or status))
    return replace(
        package,
        supply_chain_observations=observations,
        supply_chain_relations=relations,
        supply_chain_gates=gates,
        acceptance_gates=tuple(
            dict.fromkeys((*package.acceptance_gates, *_SUPPLY_CHAIN_ACCEPTANCE_GATES))
        ),
        evidence=(
            *package.evidence,
            f"supply_chain:{status}",
            f"supply_chain_observations:{len(observations)}",
            f"supply_chain_relations:{len(relations)}",
        ),
        limitations=tuple(dict.fromkeys(limitations)),
    )


def _unused_package_id(candidate: UnusedConsensusCandidate) -> str:
    payload = canonical_json(
        {
            "planning": "unused-characterization-work-packages-v1",
            "candidate_id": candidate.candidate_id,
        }
    )
    return "code-unused-work-package-v1:xxh3_128:" + fingerprint_text(payload).xxh3_128


def _unused_package_steps(
    candidate: UnusedConsensusCandidate,
) -> tuple[CodeReviewWorkPackageStep, ...]:
    target = candidate.symbol or candidate.name
    return (
        CodeReviewWorkPackageStep(
            1,
            "characterize",
            target,
            "verify_import_reexport_callback_registry_protocol_and_entry_point_usage",
        ),
        CodeReviewWorkPackageStep(
            2,
            "validate",
            target,
            "run_targeted_tests_and_public_import_smoke_without_mutating_code",
        ),
        CodeReviewWorkPackageStep(
            3,
            "validate",
            target,
            "record_explicit_human_confirmation_or_reclassify_with_new_evidence",
        ),
        CodeReviewWorkPackageStep(
            4,
            "publish",
            target,
            "require_comparable_unused_analysis_replay_before_any_separate_change",
        ),
    )


def _unused_characterization_packages(
    analysis: CodeUnusedAnalysis | None,
    *,
    architecture: CodeArchitectureAnalysis | None,
    test_coverage: CodeCoverageAnalysis | None,
    excluded_candidate_ids: frozenset[str],
    supply_chain: CodeSupplyChainAnalysis | None = None,
) -> tuple[CodeReviewWorkPackage, ...]:
    precision_gates: dict[str, str] = {}
    if analysis is not None:
        precision_gates = {
            gate.gate: gate.status
            for gate in analysis.gates
            if gate.gate in _UNUSED_REQUIRED_PRECISION_GATES
        }
    if (
        analysis is None
        or analysis.status != "ready"
        or any(precision_gates.get(gate) != "passed" for gate in _UNUSED_REQUIRED_PRECISION_GATES)
    ):
        return ()
    selected = tuple(
        candidate
        for candidate in analysis.candidates
        if candidate.state == "probable_unused_high_consensus"
        and candidate.candidate_id not in excluded_candidate_ids
    )[:CODE_REVIEW_UNUSED_WORK_PACKAGE_LIMIT]
    packages: list[CodeReviewWorkPackage] = []
    for candidate in selected:
        target = candidate.symbol or candidate.name
        module_id = candidate.module_id
        import_chains = (
            ()
            if architecture is None or module_id is None
            else bounded_import_chains(architecture, module_id)
        )
        affected_contracts = (
            ()
            if architecture is None or module_id is None
            else tuple(
                sorted(
                    contract.contract_id
                    for contract in architecture.contracts
                    if module_id in contract.importer_modules
                    or module_id in contract.imported_modules
                )
            )
        )
        coverage_projection = (
            None if test_coverage is None else project_work_package_coverage(test_coverage, target)
        )
        coverage_scope = (
            None
            if test_coverage is None
            else project_work_package_coverage_scope(test_coverage, target)
        )
        packages.append(
            _annotate_supply_chain(
                CodeReviewWorkPackage(
                    package_rank=0,
                    package_id=_unused_package_id(candidate),
                    title=f"{target} unused-code characterization",
                    objective="characterize_high_consensus_unused_candidate_without_mutation",
                    primary_finding_id=candidate.candidate_id,
                    primary_hotspot_id=candidate.candidate_id,
                    primary_symbol=target,
                    primary_module=module_id,
                    change_risk="unknown",
                    members=(),
                    members_truncated=False,
                    consumer_module_examples=(),
                    import_chains=import_chains,
                    affected_architecture_contracts=affected_contracts,
                    test_coverage=coverage_projection,
                    test_coverage_scope=coverage_scope,
                    contracts_to_preserve=(
                        "public_import_and_reexport_surface",
                        "callbacks_registries_protocols_and_entry_points",
                        "runtime_and_test_fixture_behavior",
                    ),
                    steps=_unused_package_steps(candidate),
                    recommended_validation=(
                        "inspect_import_reexport_and___all___usage",
                        "inspect_callbacks_registries_protocols_and_entry_points",
                        "run_targeted_tests_and_public_import_smoke",
                        "record_human_confirmation_before_any_separate_change",
                    ),
                    acceptance_gates=_UNUSED_ACCEPTANCE_GATES,
                    evidence=(
                        f"unused_candidate:{candidate.candidate_id}:{candidate.state}",
                        *(f"provider:{item}" for item in candidate.provider_ids),
                        *(f"reason:{item}" for item in candidate.reasons),
                        f"calibration_signature:{analysis.calibration_signature}",
                        f"coverage_status:{analysis.coverage_status}",
                        "architecture:"
                        + (architecture.status if architecture is not None else "not_evaluated"),
                    ),
                    limitations=(
                        "characterization_package_is_advice_not_change_authorization",
                        "candidate_requires_explicit_human_confirmation",
                        "dynamic_usage_may_remain_unobserved",
                        "coverage_can_explain_usage_but_never_strengthens_missing_evidence",
                        "package_has_zero_delete_or_mutation_authority",
                        *(
                            ()
                            if architecture is not None and architecture.status == "ready"
                            else ("architecture_gates_require_comparable_ready_evidence",)
                        ),
                    ),
                    confidence="unused_high_consensus_advisory",
                    package_kind="unused_characterization",
                    unused_candidates=(candidate,),
                    requires_human_confirmation=True,
                    mutation_authority=False,
                ),
                supply_chain,
            )
        )
    return tuple(packages)


def build_code_review_work_packages(
    findings: tuple[CodeReviewFinding, ...],
    recommendations: tuple[CodeReviewRecommendation, ...],
    links: tuple[CodeReviewPlanningLink, ...],
    *,
    architecture: CodeArchitectureAnalysis | None = None,
    architecture_root: str | None = None,
    test_coverage: CodeCoverageAnalysis | None = None,
    unused_analysis: CodeUnusedAnalysis | None = None,
    supply_chain: CodeSupplyChainAnalysis | None = None,
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
                "resolved_static_call" if link.depth == 1 else "resolved_static_call_via",
                _link_evidence(link),
            )
            for finding, link in related
        ),
    )
    relationship_evidence = tuple(
        item for _finding, link in related for item in _link_evidence(link)
    )
    primary_module = (
        None if architecture_root is None else module_id_from_path(primary.path, architecture_root)
    )
    import_chains = (
        ()
        if architecture is None or primary_module is None
        else bounded_import_chains(architecture, primary_module)
    )
    affected_contracts = (
        ()
        if architecture is None or primary_module is None
        else tuple(
            sorted(
                contract.contract_id
                for contract in architecture.contracts
                if primary_module in contract.importer_modules
                or primary_module in contract.imported_modules
            )
        )
    )
    architecture_evidence = (
        "architecture:not_evaluated"
        if architecture is None
        else (
            "architecture:ready"
            if architecture.status == "ready"
            else f"architecture:abstained:{architecture.reason}"
        )
    )
    architecture_limitations = (
        ()
        if architecture is not None and architecture.status == "ready"
        else ("architecture_gates_require_a_comparable_ready_publication_diff",)
    )
    coverage_projection = (
        None
        if test_coverage is None
        else project_work_package_coverage(test_coverage, primary.symbol)
    )
    coverage_status = "not_evaluated" if coverage_projection is None else coverage_projection.status
    coverage_scope = (
        None
        if test_coverage is None
        else project_work_package_coverage_scope(test_coverage, primary.symbol)
    )
    coverage_limitations = {
        "protected": (),
        "unprotected": ("work_package_target_has_no_observed_protecting_test",),
        "not_evaluated": ("coverage_gates_require_ready_trusted_deep_evidence",),
    }[coverage_status]
    package = CodeReviewWorkPackage(
        package_rank=1,
        package_id=_package_id(primary, related),
        title=f"{primary.symbol} maintenance package",
        objective="reduce_confirmed_hotspots_without_contract_regression",
        primary_finding_id=primary.finding_id,
        primary_hotspot_id=primary.hotspot_id,
        primary_symbol=primary.symbol,
        primary_module=primary_module,
        change_risk=_package_risk(primary, related),
        members=members,
        members_truncated=members_truncated,
        consumer_module_examples=_ordered_union(
            tuple(finding.impact.consumer_module_examples for finding in package_findings)
        ),
        import_chains=import_chains,
        affected_architecture_contracts=affected_contracts,
        test_coverage=coverage_projection,
        test_coverage_scope=coverage_scope,
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
            architecture_evidence,
            f"test_coverage:{coverage_status}",
            *(
                ()
                if coverage_projection is None
                else (f"test_coverage_subject:{coverage_projection.primary_symbol}",)
            ),
            *relationship_evidence,
        ),
        limitations=(
            "work_package_is_advice_not_authorization",
            "relationship_graph_is_bounded_to_two_static_call_hops",
            "dynamic_dispatch_is_not_observed",
            "related_members_require_characterization_before_change",
            *architecture_limitations,
            *coverage_limitations,
        ),
        confidence=("confirmed_static_relationship" if related else "primary_finding_only"),
    )
    return (
        _annotate_supply_chain(
            _annotate_unused_candidates(package, unused_analysis),
            supply_chain,
        ),
    )


def plan_code_review_work_packages(
    findings: tuple[CodeReviewFinding, ...],
    recommendations: tuple[CodeReviewRecommendation, ...],
    links: tuple[CodeReviewPlanningLink, ...],
    *,
    architecture: CodeArchitectureAnalysis | None = None,
    architecture_root: str | None = None,
    test_coverage: CodeCoverageAnalysis | None = None,
    unused_analysis: CodeUnusedAnalysis | None = None,
    supply_chain: CodeSupplyChainAnalysis | None = None,
) -> tuple[
    tuple[CodeReviewWorkPackage, ...],
    RecommendationStatus,
    str | None,
]:
    """Return packages and their explicit ready or abstained envelope state."""

    packages = build_code_review_work_packages(
        findings,
        recommendations,
        links,
        architecture=architecture,
        architecture_root=architecture_root,
        test_coverage=test_coverage,
        unused_analysis=unused_analysis,
        supply_chain=supply_chain,
    )
    annotated_candidate_ids = frozenset(
        candidate.candidate_id for package in packages for candidate in package.unused_candidates
    )
    unused_packages = _unused_characterization_packages(
        unused_analysis,
        architecture=architecture,
        test_coverage=test_coverage,
        excluded_candidate_ids=annotated_candidate_ids,
        supply_chain=supply_chain,
    )
    packages = tuple(
        replace(package, package_rank=rank)
        for rank, package in enumerate((*packages, *unused_packages), start=1)
    )
    if packages:
        return packages, "ready", None
    return (
        (),
        "abstained",
        "no_primary_act_now_or_high_consensus_unused_candidate_within_bounded_evidence",
    )


__all__ = [
    "CODE_REVIEW_PLANNING",
    "CODE_REVIEW_PLANNING_FINDING_LIMIT",
    "CODE_REVIEW_UNUSED_WORK_PACKAGE_LIMIT",
    "CODE_REVIEW_WORK_PACKAGE_LIMIT",
    "CodeReviewPlanningLink",
    "build_code_review_work_packages",
    "plan_code_review_work_packages",
    "read_code_review_planning_links",
]
