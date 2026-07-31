"""Regression for cache-validation policy and analysis compatibility."""

from __future__ import annotations

from pathlib import Path

from _04_Nucleo_Operativo.code_analyzers import AnalyzerRegistry, AnalyzerSpec
from _04_Nucleo_Operativo.code_contracts import CodeRouteConfig


def test_cache_validation_strength_does_not_invalidate_analysis_results(
    tmp_path: Path,
) -> None:
    common = {
        "state_path": tmp_path / "code.sqlite3",
        "dedup_path": tmp_path / "dedup.sqlite3",
    }
    metadata = CodeRouteConfig(**common, cache_validation="metadata")
    full = CodeRouteConfig(**common, cache_validation="full")

    assert metadata.processing_signature == full.processing_signature


def test_analyzer_version_changes_the_registry_processing_signature() -> None:
    def registry(version: str) -> AnalyzerRegistry:
        return AnalyzerRegistry(
            (
                AnalyzerSpec(
                    "fixture-parser",
                    frozenset({"fixture"}),
                    ".fixture_parser",
                    "FixtureParser",
                    version,
                ),
            )
        )

    assert registry("1").processing_signature != registry("2").processing_signature
