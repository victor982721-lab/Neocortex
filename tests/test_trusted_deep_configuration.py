"""Reproducible configuration contracts for explicitly trusted deep analysis."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from _02_Deduplicacion import InventoryExclusionPolicy
from _04_Nucleo_Operativo.application_config_projections import (
    code_route_config_from_application,
)
from _04_Nucleo_Operativo.code_contracts import CodeRouteConfig
from _04_Nucleo_Operativo.models import FrameworkConfig
from _04_Nucleo_Operativo.self_analysis import (
    build_self_analysis_completion_manifest,
    self_analysis_commands,
)


def _deep_framework_config(root: Path, state: Path) -> FrameworkConfig:
    return FrameworkConfig(
        root=root,
        state_directory=state,
        self_analysis=True,
        analysis_profile="trusted-deep",
        corpus_access_mode="analyze_only",
        route="code",
        document_catalog_enabled=False,
        code_include_generated=False,
        code_include_vendored=False,
        deep_test_selectors=(
            "tests/test_self_analysis_cli.py::test_self_analysis_preset_is_code_only_and_analyze_only",
        ),
        deep_max_tests=120,
        deep_time_budget_seconds=240,
        deep_shard_size=12,
    )


def test_deep_projection_declares_execution_with_separate_signature(tmp_path: Path) -> None:
    config = _deep_framework_config(tmp_path / "root", tmp_path / "state")

    projected = code_route_config_from_application(config)

    assert projected.analysis_profile == "trusted-deep"
    assert projected.deep_test_selectors == config.deep_test_selectors
    assert projected.deep_configuration_payload == {
        "schema": "neocortex.code-deep-configuration/v1",
        "analysis_profile": "trusted-deep",
        "content_executed": True,
        "suite_selection": "selected",
        "test_selectors": list(config.deep_test_selectors),
        "max_tests": 120,
        "time_budget_seconds": 240,
        "shard_size": 12,
    }
    assert projected.deep_configuration_signature.startswith("code-deep-v1:")


def test_suite_controls_do_not_invalidate_ordinary_code_ast_signature(tmp_path: Path) -> None:
    common = {
        "state_path": tmp_path / "code.sqlite3",
        "dedup_path": tmp_path / "dedup.sqlite3",
        "analysis_profile": "trusted-deep",
    }
    full_suite = CodeRouteConfig(**common)
    selected_suite = CodeRouteConfig(
        **common,
        deep_test_selectors=("tests/test_self_analysis_cli.py",),
        deep_max_tests=80,
        deep_time_budget_seconds=90,
        deep_shard_size=8,
    )

    assert full_suite.processing_signature == selected_suite.processing_signature
    assert full_suite.deep_configuration_signature != selected_suite.deep_configuration_signature


def test_non_deep_code_config_rejects_hidden_execution_controls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires trusted-deep"):
        CodeRouteConfig(
            state_path=tmp_path / "code.sqlite3",
            dedup_path=tmp_path / "dedup.sqlite3",
            analysis_profile="trusted-static",
            deep_test_selectors=("tests/test_self_analysis_cli.py",),
        )


def test_trusted_deep_command_and_manifest_are_exact(tmp_path: Path) -> None:
    root = tmp_path / "root"
    state = tmp_path / "state"
    root.mkdir()
    config = _deep_framework_config(root, state)
    commands = self_analysis_commands(config, root, state)

    analyze = commands["analyze"]
    assert analyze.count("--deep-test-selector") == 1
    assert analyze[analyze.index("--deep-max-tests") + 1] == "120"
    assert analyze[analyze.index("--deep-time-budget-seconds") + 1] == "240"
    assert analyze[analyze.index("--deep-shard-size") + 1] == "12"

    manifest, _payload = build_self_analysis_completion_manifest(
        run={"run_id": 1},
        inventory={"scan_id": 2},
        inventory_policy=InventoryExclusionPolicy.compile((state,)),
        code_processing_signature="code-v2:fixture",
        code_summary={"processed": 1},
        safety_counts={
            "route_candidates": 0,
            "file_actions": 0,
            "run_actions": 0,
            "organization_events": 0,
        },
        commands=commands,
    )

    expected_deep = code_route_config_from_application(config)
    assert manifest["deep_analysis"] == {
        **expected_deep.deep_configuration_payload,
        "configuration_signature": expected_deep.deep_configuration_signature,
    }


def test_deep_manifest_abstains_on_missing_or_duplicate_limits(tmp_path: Path) -> None:
    root = tmp_path / "root"
    state = tmp_path / "state"
    root.mkdir()
    commands = self_analysis_commands(_deep_framework_config(root, state), root, state)
    commands["analyze"].extend(("--deep-max-tests", "120"))

    with pytest.raises(ValueError, match="exactly one --deep-max-tests"):
        build_self_analysis_completion_manifest(
            run={"run_id": 1},
            inventory={"scan_id": 2},
            inventory_policy=InventoryExclusionPolicy.compile((state,)),
            code_processing_signature="code-v2:fixture",
            code_summary={"processed": 1},
            safety_counts={
                "route_candidates": 0,
                "file_actions": 0,
                "run_actions": 0,
                "organization_events": 0,
            },
            commands=commands,
        )


def test_deep_full_suite_is_declared_without_selectors(tmp_path: Path) -> None:
    config = replace(
        _deep_framework_config(tmp_path / "root", tmp_path / "state"),
        deep_test_selectors=(),
    )

    projected = code_route_config_from_application(config)

    assert projected.deep_configuration_payload["suite_selection"] == "full"
    assert projected.deep_configuration_payload["content_executed"] is True
