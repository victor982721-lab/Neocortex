"""Acceptance evidence for the suppressed probable-dead diagnostic."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "code_review"
    / "rc11_probable_dead_sample_v1.json"
)


def _selection_key(label: dict[str, object]) -> str:
    identity = f"{str(label['path']).casefold()}::{label['symbol']}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def test_probable_dead_sample_fails_the_enablement_gate_reproducibly() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    labels = payload["labels"]
    summary = payload["review_summary"]
    gate = payload["gate"]

    assert payload["schema"] == "neocortex-probable-dead-calibration/v1"
    assert payload["source"]["population"] == 246
    assert payload["source"]["selection"] == (
        "lowest_sha256_of_relative_path_casefold_and_symbol_v1"
    )
    assert payload["source"]["ground_truth_status"] == (
        "provisional_not_human_validated"
    )
    assert len(labels) == summary["sample_size"] == 40
    assert [label["rank"] for label in labels] == list(range(1, 41))
    assert len({(label["path"], label["symbol"]) for label in labels}) == 40
    assert all(":" not in label["path"] for label in labels)
    assert [_selection_key(label) for label in labels] == sorted(
        _selection_key(label) for label in labels
    )

    classifications = Counter(label["classification"] for label in labels)
    assert classifications == {
        "demonstrably_used": 36,
        "external_contract": 1,
        "review_candidate": 3,
    }
    assert summary["demonstrably_used"] == 36
    assert summary["external_contract"] == 1
    assert summary["review_candidate"] == 3

    zero_name_references = [label for label in labels if label["name_references"] == 0]
    assert len(zero_name_references) == summary["zero_indexed_name_references"] == 30
    assert (
        sum(
            label["classification"] == "demonstrably_used"
            for label in zero_name_references
        )
        == summary["zero_indexed_name_references_but_demonstrably_used"]
        == 26
    )
    assert (
        sum(label["name_references"] > 0 for label in labels)
        == summary["indexed_name_references_but_marked_probable_dead"]
        == 10
    )

    candidate_upper_bound = (10_000 * classifications["review_candidate"]) // len(
        labels
    )
    required_abstentions = len(labels) - classifications["review_candidate"]
    abstention_basis_points = (10_000 * required_abstentions) // len(labels)
    assert (
        candidate_upper_bound
        == summary["candidate_precision_upper_bound_basis_points"]
        == 750
    )
    assert required_abstentions == summary["minimum_required_abstentions"] == 37
    assert (
        abstention_basis_points
        == summary["minimum_required_abstention_basis_points"]
        == 9250
    )
    assert (
        candidate_upper_bound
        < gate["required_confirmed_precision_basis_points"]
        == 9000
    )
    assert gate["status"] == "failed_keep_suppressed"

    review_candidates = {
        (label["path"], label["symbol"])
        for label in labels
        if label["classification"] == "review_candidate"
    }
    assert review_candidates == {
        (
            "_04_Nucleo_Operativo/semantic_generation_repository.py",
            "semantic_generation_repository._enqueue_text_chunk_batch",
        ),
        (
            "_04_Nucleo_Operativo/semantic_service.py",
            "semantic_service._query_vector",
        ),
        (
            "_04_Nucleo_Operativo/semantic_service.py",
            "semantic_service._text_probe",
        ),
    }
    assert [
        (label["path"], label["symbol"])
        for label in labels
        if label["classification"] == "external_contract"
    ] == [("Orquestador.py", "Orquestador._run_pdf_search")]
