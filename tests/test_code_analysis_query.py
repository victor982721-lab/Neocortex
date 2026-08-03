"""Functional, adversarial and property-style contracts for Code queries."""

from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from _04_Nucleo_Operativo.code_analysis_query import (
    CODE_ANALYSIS_QUERY_SCHEMA,
    CodeAnalysisQuery,
    query_code_analysis,
)

FIXTURE = Path(__file__).parent / "fixtures" / "code_analysis_query" / "public_surfaces_v1.json"


def _fixture() -> dict[str, object]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _surface(name: str) -> dict[str, object]:
    value = _fixture()[name]
    assert isinstance(value, dict)
    return value


def test_review_query_combines_all_dimensions_without_a_magic_score() -> None:
    query = CodeAnalysisQuery(
        surface="review",
        providers=("COSMIC-RAY-FOCAL-MUTATION",),
        categories=("mutation",),
        modules=("PKG.WORKER",),
        statuses=("passed",),
        work_packages=("WP-1",),
        limit=10,
    )

    first = query_code_analysis(_surface("review"), query)
    second = query_code_analysis(_surface("review"), query)

    assert first == second
    assert first["schema"] == CODE_ANALYSIS_QUERY_SCHEMA
    assert first["status"] == "ready"
    assert first["authority"] == "advisory"
    assert first["mutation_authority"] is False
    assert first["aggregate_score"] is None
    assert first["defect_probability"] is None
    assert first["filters"] == {
        "providers": ["cosmic-ray-focal-mutation"],
        "categories": ["mutation"],
        "modules": ["pkg.worker"],
        "statuses": ["passed"],
        "deltas": [],
        "work_packages": ["wp-1"],
    }
    assert first["counts"] == {
        "available": 4,
        "matched": 1,
        "returned": 1,
        "truncated": False,
    }
    matches = first["matches"]
    assert isinstance(matches, list)
    assert matches[0]["record_type"] == "work_package"
    assert matches[0]["facts"]["primary_module"] == "pkg.worker"
    serialized = json.dumps(first, sort_keys=True)
    assert '"aggregate_score": null' in serialized
    assert '"defect_probability": null' in serialized


def test_status_query_supports_exact_or_descendant_module_matching() -> None:
    result = query_code_analysis(
        _surface("status"),
        CodeAnalysisQuery(surface="status", modules=("pkg.worker",), limit=100),
    )

    modules = {module for match in result["matches"] for module in match["dimensions"]["modules"]}
    assert "pkg.worker" in modules
    assert "pkg.worker.child" in modules
    assert all(module == "pkg.worker" or module.startswith("pkg.worker.") for module in modules)
    assert result["source"] == {
        "kind": "code-status",
        "schema": "neocortex.code-status/schema-v4",
        "digest": "fixture-processing-v1",
    }


def test_diff_query_exposes_provider_category_status_and_delta_filters() -> None:
    provider = query_code_analysis(
        _surface("diff"),
        CodeAnalysisQuery(
            surface="diff",
            providers=("cosmic-ray-focal-mutation",),
            statuses=("comparable",),
            deltas=("added",),
        ),
    )
    category = query_code_analysis(
        _surface("diff"),
        CodeAnalysisQuery(
            surface="diff",
            categories=("dependency_hygiene",),
            deltas=("increased",),
        ),
    )

    assert provider["counts"]["matched"] == 1
    assert provider["matches"][0]["record_type"] == "provider_delta"
    assert category["counts"]["matched"] == 1
    assert category["matches"][0]["record_type"] == "supply_chain_category_delta"


def test_limit_is_hard_and_reports_honest_truncation() -> None:
    result = query_code_analysis(
        _surface("status"),
        CodeAnalysisQuery(surface="status", limit=1),
    )

    assert result["counts"]["available"] > 1
    assert result["counts"]["matched"] == result["counts"]["available"]
    assert result["counts"]["returned"] == len(result["matches"]) == 1
    assert result["counts"]["truncated"] is True


def test_normalization_is_idempotent_and_filtering_is_monotonic() -> None:
    normalized = CodeAnalysisQuery(
        surface=cast("object", " REVIEW "),  # type: ignore[arg-type]
        providers=("MYPY-TRUSTED-PROJECT", "mypy-trusted-project"),
        categories=(" Finding ", "finding"),
    )
    again = CodeAnalysisQuery(
        surface=normalized.surface,
        providers=normalized.providers,
        categories=normalized.categories,
    )
    assert normalized == again
    assert normalized.providers == ("mypy-trusted-project",)
    assert normalized.categories == ("finding",)

    payload = _surface("status")
    unfiltered = query_code_analysis(payload, CodeAnalysisQuery(surface="status", limit=500))
    randomizer = random.Random(20260803)
    choices = (
        ("providers", "cosmic-ray-focal-mutation"),
        ("providers", "complexipy-cognitive"),
        ("categories", "engineering"),
        ("categories", "dependency_hygiene"),
        ("modules", "pkg.worker"),
        ("statuses", "ready"),
    )
    for _ in range(40):
        name, value = randomizer.choice(choices)
        arguments: dict[str, object] = {name: (value,)}
        query = CodeAnalysisQuery(surface="status", limit=500, **arguments)  # type: ignore[arg-type]
        first = query_code_analysis(payload, query)
        second = query_code_analysis(payload, query)
        assert first == second
        assert first["counts"]["matched"] <= unfiltered["counts"]["matched"]


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: CodeAnalysisQuery(surface=cast("object", "unknown")),
        lambda: CodeAnalysisQuery(surface="status", providers=(" ",)),
        lambda: CodeAnalysisQuery(surface="status", providers=cast("object", ["ruff"])),
        lambda: CodeAnalysisQuery(surface="status", limit=0),
        lambda: CodeAnalysisQuery(surface="status", limit=501),
        lambda: CodeAnalysisQuery(surface="status", limit=cast("object", True)),
    ),
)
def test_malformed_queries_fail_closed(constructor: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        cast("object", constructor)()  # type: ignore[operator]


def test_wrong_surface_and_arbitrary_nested_fields_are_not_projected() -> None:
    payload = _surface("review")
    payload["private_extension"] = {
        "secret": "must-not-leak",
        "aggregate_score": 999,
        "defect_probability": 1.0,
    }
    result = query_code_analysis(payload, CodeAnalysisQuery(surface="review", limit=500))
    serialized = json.dumps(result, sort_keys=True)

    assert "must-not-leak" not in serialized
    assert "999" not in serialized
    assert result["aggregate_score"] is None
    assert result["defect_probability"] is None

    wrong = deepcopy(payload)
    wrong["kind"] = "code-status"
    with pytest.raises(ValueError, match="requires kind"):
        query_code_analysis(wrong, CodeAnalysisQuery(surface="review"))


def test_missing_status_state_abstains_without_creating_evidence() -> None:
    result = query_code_analysis(
        {
            "kind": "code-status",
            "schema_version": 4,
            "exists": False,
            "limitations": ["code_state_missing"],
        },
        CodeAnalysisQuery(surface="status"),
    )

    assert result["status"] == "abstained"
    assert result["matches"] == []
    assert "code_state_missing" in result["limitations"]
