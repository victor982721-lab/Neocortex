"""Deterministic actionability assessment over published Code hotspot evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CODE_REVIEW_ACTIONABILITY = "python-maintenance-actionability-v1"

SourceRole = Literal["production", "test", "fixture", "tool", "compatibility"]
Construction = Literal[
    "algorithm",
    "builder",
    "classifier",
    "initializer",
    "lifecycle",
    "orchestrator",
    "persistence",
    "retrieval",
    "rule",
    "unknown",
    "validator",
]
Actionability = Literal[
    "act_now",
    "characterize_first",
    "intentional_complexity",
    "defer",
    "insufficient_evidence",
]
ChangeRisk = Literal["low", "medium", "high", "unknown"]


@dataclass(frozen=True, slots=True)
class CodeReviewActionabilityInput:
    """Bounded deterministic evidence available for one published hotspot."""

    path: str
    symbol: str
    root: str | None
    complexity_ratio_basis_points: int
    length_ratio_basis_points: int
    production_callers: int
    test_callers: int
    fixture_callers: int
    tool_callers: int
    compatibility_callers: int
    consumer_modules: int
    outgoing_calls: tuple[str, ...] = ()
    outgoing_calls_truncated: bool = False


@dataclass(frozen=True, slots=True)
class CodeReviewActionabilityAssessment:
    """One advisory interpretation; it never authorizes a code modification."""

    source_role: SourceRole
    construction: Construction
    actionability: Actionability
    change_risk: ChangeRisk
    recommended_change: bool
    evidence: tuple[str, ...]
    contracts_to_preserve: tuple[str, ...]
    recommended_validation: tuple[str, ...]


_FIXTURE_PARTS = frozenset({"fixture", "fixtures", "testdata", "test_data"})
_TEST_PARTS = frozenset({"test", "tests", "testing"})
_TOOL_PARTS = frozenset(
    {"benchmark", "benchmarks", "dev", "script", "scripts", "tools"}
)
_COMPATIBILITY_PARTS = frozenset({"compat", "compatibility", "legacy"})


def _normalized_parts(path: str) -> tuple[str, ...]:
    return tuple(
        part.casefold()
        for part in path.replace("\\", "/").split("/")
        if part and part not in {"."}
    )


def classify_source_role(path: str, root: str | None = None) -> SourceRole:
    """Classify a path without assuming Windows or Linux separators."""

    parts = _normalized_parts(path)
    root_parts = _normalized_parts(root) if root else ()
    if root_parts and parts[: len(root_parts)] == root_parts:
        parts = parts[len(root_parts) :]
    filename = parts[-1] if parts else ""
    if _FIXTURE_PARTS.intersection(parts):
        return "fixture"
    if (
        _TEST_PARTS.intersection(parts)
        or filename.startswith("test_")
        or filename.endswith("_test.py")
    ):
        return "test"
    if _TOOL_PARTS.intersection(parts):
        return "tool"
    if _COMPATIBILITY_PARTS.intersection(parts) or "_compat" in filename:
        return "compatibility"
    return "production"


def _module_stem(path: str) -> str:
    filename = _normalized_parts(path)[-1] if _normalized_parts(path) else ""
    return filename[:-3] if filename.endswith(".py") else filename


def _symbol_name(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1].casefold()


def _starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def _declarative_construction(
    name: str,
    module: str,
) -> tuple[Construction, str] | None:
    if name == "__init__":
        return "initializer", "symbol_special_method"
    if name == "__post_init__":
        return "validator", "post_init_invariant_validation"
    if _starts_with_any(name, ("validate", "_validate", "assert", "_assert")):
        return "validator", "symbol_validation_prefix"
    if _starts_with_any(name, ("build", "_build", "make", "_make")) or (
        module == "review_evidence" and "materialized_values" in name
    ):
        return "builder", "symbol_construction_prefix"
    return None


def _domain_construction(
    name: str,
    module: str,
) -> tuple[Construction, str] | None:
    rule_module_markers = (
        "taxonomy_kinds",
        "taxonomy_references",
        "image_decision",
        "retry_policy",
    )
    rule_symbol_markers = (
        "normative_document_evidence",
        "plausible_authority",
        "classify_document_candidate",
        "classify_pdf_failure",
    )
    if any(marker in module for marker in rule_module_markers) or any(
        marker in name for marker in rule_symbol_markers
    ):
        return "rule", "domain_policy_marker"
    if _starts_with_any(name, ("classify", "_classify")):
        return "classifier", "symbol_classifier_prefix"
    return None


def _workflow_construction(
    name: str,
    module: str,
) -> tuple[Construction, str] | None:
    if module == "bounded_subprocess" or "bounded_capture" in name:
        return "lifecycle", "bounded_lifecycle_marker"
    workflow_prefixes = (
        "run",
        "_run",
        "execute",
        "_execute",
        "index",
        "_index",
        "plan",
        "_plan",
        "apply",
        "_apply",
    )
    if _starts_with_any(name, workflow_prefixes) or name in {
        "_rename_mismatch",
        "verify_document_text",
    }:
        return "orchestrator", "workflow_verb_marker"
    retrieval_markers = (
        "search",
        "lookup",
        "ranking",
        "rankings",
        "resolve_search_hits",
        "semantic_rows",
    )
    if any(marker in name for marker in retrieval_markers) or _starts_with_any(
        name, ("list_", "_list_")
    ):
        return "retrieval", "retrieval_symbol_marker"
    return None


def _stateful_construction(
    evidence: CodeReviewActionabilityInput,
    name: str,
    module: str,
    outgoing: frozenset[str],
) -> tuple[Construction, str] | None:
    persistence_module_markers = (
        "_store",
        "_repository",
        "_persistence",
        "_state_writer",
    )
    persistence_symbol_markers = (
        "enqueue",
        "mutation",
        "persist",
        "queue",
        "record",
        "store",
    )
    persistence_calls = {"begin", "commit", "executemany", "rollback"}.intersection(
        outgoing
    )
    if (
        any(marker in module for marker in persistence_module_markers)
        or any(marker in name for marker in persistence_symbol_markers)
        or persistence_calls
    ):
        reason = (
            "persistence_call_evidence"
            if persistence_calls
            else "persistence_module_or_symbol"
        )
        return "persistence", reason
    algorithm_markers = (
        "analyze",
        "cached_status_decision",
        "compute",
        "derive",
        "extract",
        "normalize",
        "parse",
        "regex",
        "token",
        "transform",
    )
    if evidence.complexity_ratio_basis_points > 0 and any(
        marker in name for marker in algorithm_markers
    ):
        return "algorithm", "algorithm_symbol_marker"
    return None


def _construction(
    evidence: CodeReviewActionabilityInput,
) -> tuple[Construction, str]:
    name = _symbol_name(evidence.symbol)
    module = _module_stem(evidence.path)
    outgoing = frozenset(value.casefold() for value in evidence.outgoing_calls)
    assessments = (
        _declarative_construction(name, module),
        _domain_construction(name, module),
        _workflow_construction(name, module),
        _stateful_construction(evidence, name, module, outgoing),
    )
    for assessment in assessments:
        if assessment is not None:
            return assessment
    return "unknown", "no_deterministic_construction_evidence"


def _actionability(
    source_role: SourceRole,
    construction: Construction,
) -> tuple[Actionability, str]:
    if source_role != "production":
        return "defer", "non_production_hotspot"
    if construction == "builder":
        return "defer", "declarative_construction_requires_functional_need"
    if construction in {"initializer", "rule", "validator"}:
        return "characterize_first", "contract_sensitive_structure"
    if construction == "unknown":
        return "insufficient_evidence", "construction_not_determined"
    return "act_now", "production_behavior_hotspot"


def _change_risk(construction: Construction, symbol: str) -> ChangeRisk:
    if construction == "unknown":
        return "unknown"
    if construction in {"initializer", "persistence", "rule", "validator"}:
        return "high"
    name = _symbol_name(symbol)
    if construction == "orchestrator" and any(
        marker in name for marker in ("apply", "index", "rename")
    ):
        return "high"
    if construction == "builder":
        return "low"
    return "medium"


def _contracts(construction: Construction, symbol: str) -> tuple[str, ...]:
    name = _symbol_name(symbol)
    values: list[str] = ["call_signature_and_return_contract"]
    by_construction: dict[Construction, tuple[str, ...]] = {
        "algorithm": ("deterministic_behavior", "edge_case_semantics"),
        "builder": ("declarative_surface_compatibility",),
        "classifier": ("labeled_domain_behavior", "abstention_and_uncertainty"),
        "initializer": ("construction_invariants",),
        "lifecycle": ("timeout_and_process_cleanup", "bounded_output"),
        "orchestrator": ("phase_order", "cancellation_and_result_completeness"),
        "persistence": ("transaction_atomicity", "resume_idempotency"),
        "retrieval": (
            "ranking_and_limit_semantics",
            "provenance_and_read_only_behavior",
        ),
        "rule": ("policy_precedence", "labeled_domain_behavior"),
        "unknown": (),
        "validator": ("fail_closed_validation", "error_precedence"),
    }
    values.extend(by_construction[construction])
    if "queue" in name or "enqueue" in name:
        values.extend(("high_watermark_progress", "bounded_queue_work"))
    return tuple(dict.fromkeys(values))


def _validation(construction: Construction) -> tuple[str, ...]:
    by_construction: dict[Construction, tuple[str, ...]] = {
        "algorithm": ("representative_edge_cases", "before_after_publication_diff"),
        "builder": ("surface_snapshot_and_help_contract",),
        "classifier": ("representative_labeled_fixture", "abstention_regression"),
        "initializer": ("construction_characterization_matrix",),
        "lifecycle": ("timeout_interrupt_and_cleanup_regressions",),
        "orchestrator": (
            "phase_order_and_cancellation_regressions",
            "before_after_publication_diff",
        ),
        "persistence": (
            "transaction_rollback_retry_and_resume",
            "bounded_work_regression",
        ),
        "retrieval": (
            "representative_query_baseline",
            "ranking_and_abstention_regression",
        ),
        "rule": ("characterization_matrix_before_refactor",),
        "unknown": ("collect_more_structural_evidence",),
        "validator": ("characterization_matrix_before_refactor",),
    }
    return by_construction[construction]


def assess_code_review_actionability(
    evidence: CodeReviewActionabilityInput,
) -> CodeReviewActionabilityAssessment:
    """Interpret a hotspot conservatively from deterministic published evidence."""

    source_role = classify_source_role(evidence.path, evidence.root)
    construction, construction_reason = _construction(evidence)
    actionability, actionability_reason = _actionability(source_role, construction)
    assessment_evidence = [
        f"source_role:{source_role}",
        f"construction:{construction}:{construction_reason}",
        f"actionability:{actionability}:{actionability_reason}",
        f"complexity_ratio_basis_points:{evidence.complexity_ratio_basis_points}",
        f"length_ratio_basis_points:{evidence.length_ratio_basis_points}",
        f"production_callers:{evidence.production_callers}",
        f"test_callers:{evidence.test_callers + evidence.fixture_callers}",
        f"consumer_modules:{evidence.consumer_modules}",
    ]
    if evidence.outgoing_calls_truncated:
        assessment_evidence.append("outgoing_calls:truncated")
    return CodeReviewActionabilityAssessment(
        source_role=source_role,
        construction=construction,
        actionability=actionability,
        change_risk=_change_risk(construction, evidence.symbol),
        recommended_change=actionability == "act_now",
        evidence=tuple(assessment_evidence),
        contracts_to_preserve=_contracts(construction, evidence.symbol),
        recommended_validation=_validation(construction),
    )


__all__ = [
    "CODE_REVIEW_ACTIONABILITY",
    "Actionability",
    "ChangeRisk",
    "CodeReviewActionabilityAssessment",
    "CodeReviewActionabilityInput",
    "Construction",
    "SourceRole",
    "assess_code_review_actionability",
    "classify_source_role",
]
