"""Calibration and relationship coverage for Code review work packages."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from _04_Nucleo_Operativo.code_architecture_analysis import (
    ArchitectureContract,
    ArchitectureImportEdge,
    ArchitectureModule,
    CodeArchitectureAnalysis,
)
from _04_Nucleo_Operativo.code_coverage_analysis import (
    CODE_COVERAGE_PROVIDER_ID,
    CodeCoverageAnalysis,
    CoverageGateEvaluation,
    CoverageScopeSummary,
    CoverageTestOutcomes,
    CoverageToolVersion,
    CoverageTotals,
    TestToSymbolRelation as CoverageTestToSymbolRelation,
)
from _04_Nucleo_Operativo.code_review_models import (
    CodeReviewFinding,
    CodeReviewImpact,
    CodeReviewResult,
    build_code_review_recommendations,
)
from _04_Nucleo_Operativo.code_review_work_packages import (
    build_code_review_work_packages,
    read_code_review_planning_links,
)


HISTORY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "code_review" / "rc14_rc19_work_package_outcomes_v1.json"
)


def _impact(symbol: str) -> CodeReviewImpact:
    return CodeReviewImpact(
        call_sites=1,
        production_callers=1,
        test_callers=1,
        fixture_callers=0,
        tool_callers=0,
        compatibility_callers=0,
        consumer_modules=2,
        production_consumer_modules=1,
        test_consumer_modules=1,
        consumer_module_examples=(f"C:\\repo\\consumer_{symbol}.py",),
    )


def _finding(
    rank: int,
    symbol: str,
    path: str,
    *,
    actionability: str,
    construction: str,
    risk: str = "medium",
) -> CodeReviewFinding:
    return CodeReviewFinding(
        finding_id=f"finding:{symbol}",
        hotspot_id=f"hotspot:{symbol}",
        rank=rank,
        category="complex_and_long_hotspot",
        path=path,
        symbol=symbol,
        symbol_kind="function",
        signature=f"{symbol.rsplit('.', 1)[-1]}()",
        start_line=10,
        end_line=240,
        start_column=0,
        end_column=0,
        start_byte=100,
        end_byte=2_000,
        complexity=30,
        function_lines=231,
        complexity_ratio_basis_points=20_000,
        length_ratio_basis_points=11_550,
        score_basis_points=23_137,
        incoming_references=2,
        incoming_calls=2,
        resolved_static_callers=2,
        impact=_impact(symbol),
        source_role="production",
        construction=construction,  # type: ignore[arg-type]
        actionability=actionability,  # type: ignore[arg-type]
        change_risk=risk,  # type: ignore[arg-type]
        recommended_change=actionability == "act_now",
        actionability_evidence=(f"actionability:{actionability}",),
        contracts_to_preserve=(f"contract:{symbol}",),
        recommended_validation=(f"validation:{symbol}",),
        analyzer_id="fixture-python",
        analyzer_version="1",
        file_xxh3_128="a" * 32,
        file_xxh3_64_guard="b" * 16,
        diagnostics=(),
        callers=(),
        reasons=("fixture",),
    )


def _planning_findings() -> tuple[CodeReviewFinding, ...]:
    return (
        _finding(
            1,
            "document_taxonomy.classify_document",
            r"C:\repo\document_taxonomy.py",
            actionability="act_now",
            construction="classifier",
        ),
        _finding(
            2,
            "document_taxonomy_kinds._normative_document_evidence",
            r"C:\repo\document_taxonomy_kinds.py",
            actionability="characterize_first",
            construction="rule",
        ),
        _finding(
            3,
            "document_taxonomy_references._plausible_authority_identifier",
            r"C:\repo\document_taxonomy_references.py",
            actionability="characterize_first",
            construction="validator",
        ),
        _finding(
            4,
            "document_taxonomy_overlay.load_overlay",
            r"C:\repo\document_taxonomy_overlay.py",
            actionability="characterize_first",
            construction="validator",
        ),
        _finding(
            5,
            "knowledge_exact.lookup_exact",
            r"C:\repo\knowledge_exact.py",
            actionability="act_now",
            construction="retrieval",
        ),
    )


def _create_graph(*, project_root: str = r"C:\repo") -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE files(
          file_id INTEGER PRIMARY KEY,current_version_id INTEGER,status TEXT,
          current_path TEXT
        );
        CREATE TABLE file_versions(
          version_id INTEGER PRIMARY KEY,invalidated_ns INTEGER,
          analysis_status TEXT,language TEXT,generated INTEGER,vendored INTEGER
        );
        CREATE TABLE symbols(
          symbol_id INTEGER PRIMARY KEY,version_id INTEGER,qualified_name TEXT,
          confirmed INTEGER
        );
        CREATE TABLE code_references(
          reference_id INTEGER PRIMARY KEY,source_symbol_id INTEGER,
          target_symbol_id INTEGER,kind TEXT,confirmed INTEGER,
          confidence REAL,evidence TEXT
        );
        CREATE TABLE projects(
          project_id INTEGER PRIMARY KEY,probable_root TEXT,status TEXT
        );
        CREATE TABLE project_memberships(
          version_id INTEGER,project_id INTEGER,selected INTEGER,confidence REAL
        );
        """
    )
    connection.execute(
        "INSERT INTO projects VALUES(1,?,'current')",
        (project_root,),
    )
    symbols = (
        (1, "document_taxonomy.classify_document", "document_taxonomy.py"),
        (2, "document_taxonomy._kind_evidence", "document_taxonomy.py"),
        (3, "document_taxonomy_kinds._normative_document_evidence", "kinds.py"),
        (4, "document_taxonomy_references._plausible_authority_identifier", "refs.py"),
        (5, "document_taxonomy_overlay.load_overlay", "overlay.py"),
        (6, "knowledge_exact.lookup_exact", "knowledge_exact.py"),
    )
    for symbol_id, qualified_name, filename in symbols:
        connection.execute(
            "INSERT INTO file_versions VALUES(?,NULL,'complete','python',0,0)",
            (symbol_id,),
        )
        connection.execute(
            "INSERT INTO files VALUES(?,?,'current',?)",
            (symbol_id, symbol_id, f"C:\\repo\\{filename}"),
        )
        connection.execute(
            "INSERT INTO symbols VALUES(?,?,?,1)",
            (symbol_id, symbol_id, qualified_name),
        )
        connection.execute(
            "INSERT INTO project_memberships VALUES(?,1,1,1.0)",
            (symbol_id,),
        )
    references = (
        (1, 1, 2, 0.9, "root-to-kind"),
        (2, 2, 3, 0.9, "kind-to-normative"),
        (3, 1, 4, 1.0, "root-to-authority-guard"),
    )
    connection.executemany(
        "INSERT INTO code_references VALUES(?,?,?,'call',1,?,?)",
        references,
    )
    return connection


def test_planner_uses_only_confirmed_direct_and_two_hop_relationships() -> None:
    findings = _planning_findings()
    recommendations = build_code_review_recommendations(findings, limit=3)
    with _create_graph() as connection:
        links = read_code_review_planning_links(
            connection,
            {
                1: findings[0].finding_id,
                3: findings[1].finding_id,
                4: findings[2].finding_id,
                5: findings[3].finding_id,
                6: findings[4].finding_id,
            },
        )

    packages = build_code_review_work_packages(findings, recommendations, links)
    repeated = build_code_review_work_packages(findings, recommendations, links)

    assert packages == repeated
    assert len(packages) == 1
    package = packages[0]
    assert package.primary_symbol == "document_taxonomy.classify_document"
    assert package.change_risk == "high"
    assert package.confidence == "confirmed_static_relationship"
    assert [member.symbol for member in package.members] == [
        "document_taxonomy.classify_document",
        "document_taxonomy_references._plausible_authority_identifier",
        "document_taxonomy_kinds._normative_document_evidence",
    ]
    assert [member.role for member in package.members] == [
        "primary_change_target",
        "contract_guard",
        "contract_guard",
    ]
    assert all(
        step.target == package.primary_symbol for step in package.steps if step.phase == "change"
    )
    assert "document_taxonomy_overlay.load_overlay" not in {
        member.symbol for member in package.members
    }
    assert "knowledge_exact.lookup_exact" not in {member.symbol for member in package.members}


def test_link_reader_collapses_repeated_calls_before_the_pair_bound() -> None:
    findings = _planning_findings()
    with _create_graph() as connection:
        reference_id = 10
        repeated = []
        for index in range(101):
            repeated.append((reference_id, 1, 2, 0.8, f"root-kind-{index:03d}"))
            reference_id += 1
            repeated.append((reference_id, 2, 3, 0.8, f"kind-target-{index:03d}"))
            reference_id += 1
        connection.executemany(
            "INSERT INTO code_references VALUES(?,?,?,'call',1,?,?)",
            repeated,
        )
        links = read_code_review_planning_links(
            connection,
            {
                1: findings[0].finding_id,
                3: findings[1].finding_id,
                4: findings[2].finding_id,
            },
        )

    assert {(link.source_finding_id, link.target_finding_id) for link in links} == {
        (findings[0].finding_id, findings[1].finding_id),
        (findings[0].finding_id, findings[2].finding_id),
    }


def test_bridge_role_is_relative_to_its_project_root() -> None:
    findings = _planning_findings()
    project_root = r"C:\work\tests\app"
    with _create_graph(project_root=project_root) as connection:
        connection.execute(
            "UPDATE files SET current_path=REPLACE(current_path,'C:\\repo',?)",
            (project_root,),
        )
        links = read_code_review_planning_links(
            connection,
            {
                1: findings[0].finding_id,
                3: findings[1].finding_id,
            },
        )

    assert len(links) == 1
    assert links[0].target_finding_id == findings[1].finding_id
    assert links[0].via_symbol == "document_taxonomy._kind_evidence"


def test_planner_abstains_without_an_act_now_recommendation() -> None:
    findings = tuple(
        _finding(
            finding.rank,
            finding.symbol,
            finding.path,
            actionability="characterize_first",
            construction=finding.construction,
        )
        for finding in _planning_findings()[:2]
    )

    assert build_code_review_work_packages(findings, (), ()) == ()


def test_work_package_adds_bounded_architecture_context_without_changing_identity() -> None:
    findings = _planning_findings()
    recommendations = build_code_review_recommendations(findings, limit=3)
    architecture = CodeArchitectureAnalysis(
        database="fixture",
        analysis_run_id=1,
        status="ready",
        reason=None,
        gate="observed",
        gates=(),
        providers=(),
        summary=None,
        modules=(
            ArchitectureModule(
                "consumer",
                0,
                1,
                3.0,
                3.0,
                1,
                (),
                ("layers",),
                None,
                None,
                None,
                None,
            ),
            ArchitectureModule(
                "document_taxonomy",
                1,
                0,
                20.0,
                12.0,
                2,
                (),
                ("layers",),
                None,
                None,
                None,
                None,
            ),
        ),
        symbols=(),
        imports=(
            ArchitectureImportEdge(
                "consumer",
                "document_taxonomy",
                "both",
                True,
                True,
                True,
                1.0,
            ),
        ),
        cycles=(),
        contracts=(
            ArchitectureContract(
                "layers",
                "failed",
                True,
                1,
                ("consumer",),
                ("document_taxonomy",),
                (("consumer", "document_taxonomy"),),
                "neocortex.architecture-contract/v1",
            ),
        ),
        limitations=(),
    )
    with _create_graph() as connection:
        links = read_code_review_planning_links(
            connection,
            {1: findings[0].finding_id, 3: findings[1].finding_id},
        )

    legacy = build_code_review_work_packages(findings, recommendations, links)[0]
    enriched = build_code_review_work_packages(
        findings,
        recommendations,
        links,
        architecture=architecture,
        architecture_root=r"C:\repo",
    )[0]

    assert enriched.package_id == legacy.package_id
    assert enriched.primary_module == "document_taxonomy"
    assert enriched.import_chains == (("consumer", "document_taxonomy"),)
    assert enriched.affected_architecture_contracts == ("layers",)
    assert {
        "architecture_contracts_not_degraded",
        "no_new_import_cycles",
        "module_complexity_not_displaced",
    }.issubset(enriched.acceptance_gates)
    assert "architecture:ready" in enriched.evidence


def test_work_package_projects_protecting_tests_and_missing_target_coverage() -> None:
    findings = _planning_findings()
    recommendations = build_code_review_recommendations(findings, limit=3)
    subject_key = "symbol:document_taxonomy.classify_document:10:240"
    totals = CoverageTotals(20, 17, 3, 8, 6, 2, 85.0, 75.0)
    coverage = CodeCoverageAnalysis(
        database="fixture",
        analysis_run_id=1,
        provider_id=CODE_COVERAGE_PROVIDER_ID,
        tool_run_id=1,
        effective_tool_run_id=1,
        status="ready",
        reason=None,
        suite_selection="selected",
        measurement_complete=True,
        content_executed=True,
        tool_versions=(CoverageToolVersion("coverage", "7.14.1"),),
        suite_signature="suite",
        configuration_signature="configuration",
        measurement_scope_signature="scope",
        outcomes=CoverageTestOutcomes(2, 2, 2, 0, 0),
        totals=totals,
        modules=(),
        symbols=(
            CoverageScopeSummary(
                "symbol",
                subject_key,
                "document_taxonomy",
                subject_key,
                "classify_document",
                10,
                240,
                "document_taxonomy.py",
                totals,
                ((30, 31), (80, 80)),
                ((25, 30), (70, 80)),
                False,
                False,
                ("tests/test_document_taxonomy.py::test_classify",),
            ),
        ),
        test_relations=(
            CoverageTestToSymbolRelation(
                "relation:classify",
                "test:test_classify",
                subject_key,
                ("tests/test_document_taxonomy.py::test_classify",),
                (10, 11, 12),
                ("tests/test_document_taxonomy.py::test_classify|run",),
                "document_taxonomy.py",
                "document_taxonomy",
                subject_key,
            ),
        ),
        failed_test_nodeids=(),
        gates=(
            CoverageGateEvaluation("tests_passed", "passed", None),
            CoverageGateEvaluation("coverage_available", "passed", None),
        ),
        limitations=("selected_suite_is_not_claimed_as_full_project_coverage",),
    )

    legacy = build_code_review_work_packages(findings, recommendations, ())[0]
    package = build_code_review_work_packages(
        findings,
        recommendations,
        (),
        test_coverage=coverage,
    )[0]

    assert package.package_id == legacy.package_id
    assert package.test_coverage is not None
    assert package.test_coverage.status == "protected"
    assert package.test_coverage.primary_symbol == subject_key
    assert package.test_coverage.protecting_tests == (
        "tests/test_document_taxonomy.py::test_classify",
    )
    assert package.test_coverage.gate.status == "passed"
    assert package.test_coverage_scope is not None
    assert package.test_coverage_scope.missing_line_ranges == ((30, 31), (80, 80))
    assert package.test_coverage_scope.missing_branch_arcs == ((25, 30), (70, 80))
    assert "work_package_target_protected" in package.acceptance_gates
    assert "coverage_gates_require_ready_trusted_deep_evidence" not in package.limitations


def test_review_json_bounds_coverage_and_work_package_examples_to_twenty() -> None:
    findings = _planning_findings()
    recommendations = build_code_review_recommendations(findings, limit=3)
    subject_key = "symbol:document_taxonomy.classify_document:10:240"
    tests = tuple(f"tests/test_many.py::test_{index:02d}" for index in range(25))
    ranges = tuple((index, index) for index in range(1, 26))
    arcs = tuple((index, index + 1) for index in range(1, 26))
    totals = CoverageTotals(100, 75, 25, 50, 25, 25, 75.0, 50.0)

    def scope(index: int, *, primary: bool = False) -> CoverageScopeSummary:
        key = subject_key if primary else f"symbol:module_{index}.target:1:5"
        return CoverageScopeSummary(
            "symbol",
            key,
            "document_taxonomy" if primary else f"module_{index}",
            key,
            "classify_document" if primary else "target",
            10 if primary else 1,
            240 if primary else 5,
            "document_taxonomy.py" if primary else f"module_{index}.py",
            totals,
            ranges,
            arcs,
            False,
            False,
            tests,
        )

    modules = tuple(
        CoverageScopeSummary(
            "module",
            f"module:{index}",
            f"module_{index}",
            None,
            None,
            None,
            None,
            f"module_{index}.py",
            totals,
            ranges,
            arcs,
            False,
            False,
            tests,
        )
        for index in range(25)
    )
    symbols = (scope(0, primary=True), *(scope(index) for index in range(1, 25)))
    relations = tuple(
        CoverageTestToSymbolRelation(
            f"relation:{index:02d}",
            f"test:{index:02d}",
            subject_key,
            (tests[index],),
            tuple(range(1, 26)),
            tuple(f"context:{item:02d}" for item in range(25)),
            "document_taxonomy.py",
            "document_taxonomy",
            subject_key,
        )
        for index in range(25)
    )
    coverage = CodeCoverageAnalysis(
        "fixture",
        1,
        CODE_COVERAGE_PROVIDER_ID,
        1,
        1,
        "ready",
        None,
        "selected",
        True,
        True,
        (CoverageToolVersion("coverage", "7.14.1"),),
        "suite",
        "configuration",
        "scope",
        CoverageTestOutcomes(25, 25, 25, 0, 0),
        totals,
        modules,
        symbols,
        relations,
        tests,
        (
            CoverageGateEvaluation("tests_passed", "passed", None),
            CoverageGateEvaluation("coverage_available", "passed", None),
        ),
        (),
    )
    package = build_code_review_work_packages(
        findings,
        recommendations,
        (),
        test_coverage=coverage,
    )[0]
    result = CodeReviewResult(
        database="fixture",
        status="ready",
        reason=None,
        ranking="fixture",
        actionability_version="fixture",
        recommendation_status="ready",
        recommendation_reason=None,
        planning_version="fixture",
        work_package_status="ready",
        work_package_reason=None,
        snapshot=None,
        coverage=None,
        findings=(),
        recommendations=(),
        work_packages=(package,),
        external_evidence=None,
        external_evidence_suite=None,
        architecture=None,
        test_coverage=coverage,
        limitations=(),
        digest=None,
    )

    payload = result.as_payload()
    coverage_payload = payload["test_coverage"]
    packages_payload = payload["work_packages"]
    assert isinstance(coverage_payload, dict)
    assert isinstance(packages_payload, list)
    assert len(coverage_payload["failed_test_examples"]) == 20
    assert coverage_payload["failed_test_examples_truncated"] is True
    assert len(coverage_payload["module_missing_examples"]) == 20
    assert coverage_payload["module_missing_examples_truncated"] is True
    assert len(coverage_payload["symbol_missing_examples"]) == 20
    assert coverage_payload["symbol_missing_examples_truncated"] is True
    assert len(coverage_payload["test_relation_examples"]) == 20
    assert coverage_payload["test_relation_examples_truncated"] is True
    package_payload = packages_payload[0]
    package_coverage = package_payload["test_coverage"]
    package_scope = package_payload["test_coverage_scope"]
    assert len(package_coverage["protecting_tests"]) == 20
    assert package_coverage["protecting_tests_total"] == 25
    assert package_coverage["protecting_tests_truncated"] is True
    assert len(package_coverage["relation_ids"]) == 20
    assert package_coverage["relation_ids_total"] == 25
    assert package_coverage["relation_ids_truncated"] is True
    assert len(package_scope["missing_line_ranges"]) == 20
    assert package_scope["missing_line_ranges_total"] == 25
    assert package_scope["missing_line_ranges_truncated"] is True
    assert len(package_scope["missing_branch_arcs"]) == 20
    assert package_scope["missing_branch_arcs_total"] == 25
    assert package_scope["missing_branch_arcs_truncated"] is True


def _passes_history_policy(
    outcome: dict[str, object],
    policy: dict[str, object],
) -> bool:
    return bool(
        outcome["target_hotspot_removed"] == policy["target_hotspot_removed"]
        and int(outcome["added_hotspots"]) <= int(policy["maximum_added_hotspots"])
        and int(outcome["changed_hotspot_evidence"])
        <= int(policy["maximum_changed_hotspot_evidence"])
        and int(outcome["corrected_call_resolutions"])
        <= int(policy["maximum_corrected_call_resolutions"])
        and int(outcome["lost_call_resolutions"]) <= int(policy["maximum_lost_call_resolutions"])
        and (
            not policy["require_full_cache_hit_replay"]
            or outcome["replay_candidates"] == outcome["replay_cache_hits"]
        )
    )


def test_rc14_rc19_history_calibrates_replacement_and_replay_gates() -> None:
    payload = json.loads(HISTORY_FIXTURE.read_text(encoding="utf-8"))
    outcomes = payload["outcomes"]
    policy = payload["acceptance_policy"]

    assert payload["schema"] == "neocortex-code-review-work-package-outcomes/v1"
    assert len(outcomes) == 7
    assert [outcome["id"] for outcome in outcomes if _passes_history_policy(outcome, policy)] == [
        outcome["id"] for outcome in outcomes if outcome["accepted"]
    ]
