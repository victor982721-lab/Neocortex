"""Read-only publication comparison over isolated completed Code states."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

from _04_Nucleo_Operativo.code_architecture_analysis import (
    ArchitectureContract,
    ArchitectureCycle,
    ArchitectureImportEdge,
    ArchitectureModule,
    ArchitectureProviderStatus,
    CodeArchitectureAnalysis,
)
from _04_Nucleo_Operativo.code_contracts import (
    DiagnosticRecord,
    DiagnosticSeverity,
    ReferenceRecord,
    SymbolRecord,
)
from _04_Nucleo_Operativo.code_publication_diff import (
    _architecture_delta,
    compare_code_publications,
)
from _04_Nucleo_Operativo.code_schema import (
    checkpoint_code_wal,
    initialize_code_state,
    remove_checkpointed_code_sidecars,
)
from _04_Nucleo_Operativo.code_state import CodeState
from tests.test_code_review import _analysis, _source_range
from tests.test_external_provider_platform import _run as _run_provider_publication
from tests.test_external_provider_platform import _tree as _provider_source_tree


PROCESSING_SIGNATURE = "code-publication-diff-fixture-v1"


def _diagnostic(
    code: str,
    symbol_range,
    *,
    value: int,
    threshold: int,
) -> DiagnosticRecord:
    return DiagnosticRecord(
        "fixture",
        code,
        DiagnosticSeverity.WARNING,
        f"fixture {code}",
        symbol_range,
        tool_name="fixture-analyzer",
        tool_version="1",
        metadata={"value": value, "threshold": threshold},
    )


def _build_publication(
    state_directory: Path,
    source_root: Path,
    *,
    assignments: dict[str, str | None],
    hotspot: str,
    probable_dead: tuple[str, ...],
) -> Path:
    state_directory.mkdir(parents=True)
    source_root.mkdir(parents=True, exist_ok=True)
    database = state_directory / "code.sqlite3"
    caller_range = _source_range(0, 20)
    first_range = _source_range(1, 230)
    second_range = _source_range(2, 80)
    ranges = {"pkg.first": first_range, "pkg.second": second_range}
    symbols = (
        SymbolRecord(
            "function",
            "caller",
            "pkg.caller",
            "caller()",
            caller_range,
            visibility="public",
            complexity=2,
        ),
        SymbolRecord(
            "function",
            "first",
            "pkg.first",
            "first()",
            first_range,
            visibility="public",
            complexity=30,
        ),
        SymbolRecord(
            "function",
            "second",
            "pkg.second",
            "second()",
            second_range,
            visibility="public",
            complexity=24,
        ),
    )
    references = tuple(
        ReferenceRecord(
            "call",
            name,
            _source_range(index + 3, 1),
            source_qualified_name="pkg.caller",
            target_hint=f"external.{name}",
            confirmed=True,
            confidence=1.0,
            evidence="fixture-call",
        )
        for index, name in enumerate(assignments)
    )
    diagnostics = [
        _diagnostic(
            "high_complexity",
            ranges[hotspot],
            value=30 if hotspot == "pkg.first" else 24,
            threshold=15,
        )
    ]
    if hotspot == "pkg.first":
        diagnostics.append(_diagnostic("long_function", first_range, value=230, threshold=200))
    for symbol in probable_dead:
        diagnostics.append(
            _diagnostic(
                "probable_dead_symbol",
                ranges[symbol],
                value=1,
                threshold=1,
            )
        )

    with CodeState(database) as state:
        analysis_run_id = state.begin_run(1, 1, PROCESSING_SIGNATURE)
        state.store_analysis(
            _analysis(
                source_root / "source.py",
                100,
                symbols=symbols,
                diagnostics=tuple(diagnostics),
                references=references,
            ),
            1,
        )
        state.finalize_graph(1)
        symbol_ids = {
            str(row["qualified_name"]): (int(row["symbol_id"]), int(row["version_id"]))
            for row in state.connection.execute(
                "SELECT symbol_id,version_id,qualified_name FROM symbols"
            )
        }
        for name, target in assignments.items():
            target_ids = None if target is None else symbol_ids[target]
            state.connection.execute(
                "UPDATE code_references SET target_symbol_id=?,target_version_id=? WHERE name=?",
                (
                    None if target_ids is None else target_ids[0],
                    None if target_ids is None else target_ids[1],
                    name,
                ),
            )
        state.complete_run(
            analysis_run_id,
            {
                "candidates": 1,
                "processed": 1,
                "cache_hits": 0,
                "errors": 0,
            },
            partial=False,
            graph_current=True,
        )
        checkpoint_code_wal(state.connection)
    remove_checkpointed_code_sidecars(database)
    return database


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _retain_only_legacy_ruff_and_migrate_v2_to_v3(database: Path) -> None:
    """Model an rc22 Ruff publication copied and migrated for read-only diff."""

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """DELETE FROM external_tool_runs WHERE tool_run_id IN (
            SELECT tool_run_id FROM external_run_contracts)"""
        )
        for table in (
            "external_relations",
            "external_metrics",
            "external_run_counters",
            "external_run_replays",
            "external_findings",
            "external_run_inputs",
            "external_run_contracts",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version>=3")
        connection.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()

    initialize_code_state(database)
    connection = sqlite3.connect(database)
    try:
        checkpoint_code_wal(connection)
    finally:
        connection.close()
    remove_checkpointed_code_sidecars(database)


def test_migrated_legacy_ruff_compares_with_current_protected_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    baseline_state = tmp_path / "rc22-copy"
    current_state = tmp_path / "current"
    paths = _provider_source_tree(root)
    paths[1].write_text("value = 1\n", encoding="utf-8")

    _run_provider_publication(root, baseline_state, paths, 1, "protected")
    _retain_only_legacy_ruff_and_migrate_v2_to_v3(baseline_state / "code.sqlite3")
    _run_provider_publication(root, current_state, paths, 1, "trusted-static")

    result = compare_code_publications(baseline_state, current_state)

    providers = {item.provider_id: item for item in result.providers}
    protected = providers["ruff-protected-basic"]
    assert result.status == "ready"
    assert result.analysis_profile == "trusted-static"
    assert protected.baseline is not None
    assert protected.current is not None
    assert protected.baseline.profile == protected.current.profile == "protected"
    assert protected.baseline.provider_schema == protected.current.provider_schema
    assert protected.baseline.tool_version == protected.current.tool_version
    assert protected.baseline.comparability_signature.startswith("external-ruff-v1:xxh3_128:")
    assert protected.current.comparability_signature.startswith(
        "ruff-protected-basic-comparable-v1:xxh3_128:"
    )
    assert protected.baseline.comparability_signature != protected.current.comparability_signature
    assert protected.status == "ready"
    assert protected.reason is None
    assert protected.common == 0
    assert protected.added == 0
    assert protected.resolved == 0
    assert protected.gate == "passed"
    assert result.verdict == "equivalent_under_observed_metrics"
    assert {
        provider_id for provider_id, delta in providers.items() if delta.status == "not_evaluated"
    } == {
        "mypy-trusted-project",
        "pyright-trusted-project",
        "ruff-trusted-project",
    }
    assert all(
        delta.gate == "not_evaluated"
        for provider_id, delta in providers.items()
        if provider_id != "ruff-protected-basic"
    )


def test_publication_diff_reports_only_common_unchanged_call_sites(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    baseline_state = tmp_path / "baseline"
    current_state = tmp_path / "current"
    baseline_database = _build_publication(
        baseline_state,
        source_root,
        assignments={
            "new": None,
            "corrected": "pkg.first",
            "lost": "pkg.second",
            "stable": "pkg.first",
            "unresolved": None,
        },
        hotspot="pkg.first",
        probable_dead=("pkg.first", "pkg.second"),
    )
    current_database = _build_publication(
        current_state,
        source_root,
        assignments={
            "new": "pkg.first",
            "corrected": "pkg.second",
            "lost": None,
            "stable": "pkg.first",
            "unresolved": None,
        },
        hotspot="pkg.second",
        probable_dead=("pkg.second",),
    )
    before = (_sha256(baseline_database), _sha256(current_database))

    first = compare_code_publications(baseline_state, current_state)
    second = compare_code_publications(baseline_state, current_state)

    assert first.status == "ready"
    assert first == second
    assert first.calls is not None
    assert first.calls.common_call_sites == 5
    assert first.calls.baseline_only_call_sites == 0
    assert first.calls.current_only_call_sites == 0
    assert first.calls.newly_resolved == 1
    assert first.calls.corrected == 1
    assert first.calls.lost == 1
    assert first.calls.unchanged_resolved == 1
    assert first.calls.still_unresolved == 1
    assert {example.change for example in first.calls.examples} == {
        "newly_resolved",
        "corrected",
        "lost",
    }
    assert first.hotspots is not None
    assert first.hotspots.added == 1
    assert first.hotspots.removed == 1
    assert first.hotspots.changed_evidence == 0
    assert first.probable_dead_delta == -1
    assert first.digest is not None
    assert (_sha256(baseline_database), _sha256(current_database)) == before
    assert not Path(f"{baseline_database}-wal").exists()
    assert not Path(f"{current_database}-wal").exists()


def test_publication_diff_of_the_same_state_is_stable_and_empty(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    state = tmp_path / "state"
    _build_publication(
        state,
        source_root,
        assignments={"stable": "pkg.first", "unresolved": None},
        hotspot="pkg.first",
        probable_dead=("pkg.first",),
    )

    result = compare_code_publications(state, state)

    assert result.status == "ready"
    assert result.calls is not None
    assert result.calls.common_call_sites == 2
    assert result.calls.newly_resolved == 0
    assert result.calls.corrected == 0
    assert result.calls.lost == 0
    assert result.hotspots is not None
    assert result.hotspots.added == 0
    assert result.hotspots.removed == 0
    assert result.hotspots.changed_evidence == 0
    assert result.probable_dead_delta == 0


def _architecture_provider(provider_id: str) -> ArchitectureProviderStatus:
    return ArchitectureProviderStatus(
        provider_id=provider_id,
        status="ready",
        reason=None,
        tool_name=provider_id,
        tool_version="1",
        provider_schema=f"neocortex.{provider_id}/v1",
        comparability_signature=f"fixture:{provider_id}",
        provider_gate="passed",
        execution="full",
        tool_run_id=1,
        source_tool_run_id=1,
        metrics=1,
        relations=1,
    )


def _architecture_publications() -> tuple[
    CodeArchitectureAnalysis,
    CodeArchitectureAnalysis,
]:
    providers = tuple(
        _architecture_provider(provider)
        for provider in (
            "complexipy-cognitive",
            "grimp-architecture",
            "ruff-analyze-imports",
        )
    )
    baseline = CodeArchitectureAnalysis(
        "baseline",
        1,
        "ready",
        None,
        "observed",
        (),
        providers,
        None,
        (
            ArchitectureModule("pkg.helper", 1, 0, 5.0, 5.0, 1, (), (), None, None, None, None),
            ArchitectureModule("pkg.target", 0, 1, 10.0, 10.0, 1, (), (), None, None, None, None),
        ),
        (),
        (ArchitectureImportEdge("pkg.target", "pkg.helper", "both", True, True, True, 1.0),),
        (),
        (ArchitectureContract("layers", "passed", True, 0, (), (), (), "v1"),),
        (),
    )
    current = CodeArchitectureAnalysis(
        "current",
        2,
        "ready",
        None,
        "observed",
        (),
        providers,
        None,
        (
            ArchitectureModule(
                "pkg.helper",
                1,
                1,
                8.0,
                8.0,
                1,
                ("cycle",),
                ("layers",),
                None,
                None,
                None,
                None,
            ),
            ArchitectureModule(
                "pkg.target",
                1,
                1,
                8.0,
                8.0,
                1,
                ("cycle",),
                ("layers",),
                None,
                None,
                None,
                None,
            ),
        ),
        (),
        (
            ArchitectureImportEdge("pkg.target", "pkg.helper", "both", True, True, True, 1.0),
            ArchitectureImportEdge("pkg.helper", "pkg.target", "both", True, True, True, 1.0),
        ),
        (ArchitectureCycle("cycle", ("pkg.helper", "pkg.target")),),
        (
            ArchitectureContract(
                "layers",
                "failed",
                True,
                1,
                ("pkg.helper",),
                ("pkg.target",),
                (("pkg.helper", "pkg.target"),),
                "v1",
            ),
        ),
        (),
    )
    return baseline, current


def test_architecture_delta_reports_module_cycle_contract_and_displacement() -> None:
    baseline, current = _architecture_publications()

    delta = _architecture_delta(baseline, current)

    assert delta.status == "ready"
    modules = {item.module_id: item for item in delta.modules}
    assert modules["pkg.target"].cognitive_complexity_delta == -2
    assert modules["pkg.helper"].cognitive_complexity_delta == 3
    assert delta.added_failed_contracts == ("layers",)
    assert delta.added_cycles == (("pkg.helper", "pkg.target"),)
    assert len(delta.displaced_complexity) == 1
    assert delta.displaced_complexity[0].target_module == "pkg.target"
    assert delta.displaced_complexity[0].recipient_modules == ("pkg.helper",)
    assert delta.architecture_contracts_not_degraded == "failed"
    assert delta.no_new_import_cycles == "failed"
    assert delta.module_complexity_not_displaced == "failed"


def test_architecture_delta_never_passes_when_provider_evidence_is_missing() -> None:
    baseline, current = _architecture_publications()
    missing = replace(
        baseline,
        status="abstained",
        reason="required_provider_not_ready:grimp-architecture:provider_missing",
        providers=(),
    )

    delta = _architecture_delta(missing, current)

    assert delta.status == "not_evaluated"
    assert delta.architecture_contracts_not_degraded == "not_evaluated"
    assert delta.no_new_import_cycles == "not_evaluated"
    assert delta.module_complexity_not_displaced == "not_evaluated"
