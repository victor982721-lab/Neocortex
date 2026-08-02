"""Public CLI contracts for the integrated code route and direct queries."""
# region [00] Contexto del módulo
# Módulo: tests/test_code_cli.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]

# region [01] Dependencias del módulo
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from _04_Nucleo_Operativo import cli_code
from _04_Nucleo_Operativo.cli_app import dispatch_direct
from _04_Nucleo_Operativo.cli_config import framework_config_from_args
from _04_Nucleo_Operativo.cli_parser import build_parser
from _04_Nucleo_Operativo.cli_validation import validate_arguments
from _04_Nucleo_Operativo.code_schema import initialize_code_state
# endregion [01]

# region [02] Implementación


def _validated(*arguments: str):
    args = build_parser().parse_args(arguments)
    validate_arguments(args)
    return args


def test_code_route_configuration_is_translated_without_eager_analyzers(
    tmp_path: Path,
) -> None:
    args = _validated(
        "--state-directory",
        str(tmp_path),
        "--route",
        "code",
        "--code-max-mb",
        "2",
        "--code-max-count",
        "7",
        "--code-cache-validation",
        "full",
        "--no-code-generated",
        "--no-code-vendored",
    )

    config = framework_config_from_args(args)

    assert config.route == "code"
    assert config.code_database == tmp_path / "code.sqlite3"
    assert config.code_max_file_bytes == 2_000_000
    assert config.code_max_documents == 7
    assert config.code_cache_validation == "full"
    assert not config.code_include_generated
    assert not config.code_include_vendored


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (("--code-search", " "), "--code-search must be non-empty"),
        (
            ("--code-language", "python"),
            "code search filters require --code-search",
        ),
        (
            ("--code-reconstruct-strategy", "branches"),
            "--code-reconstruct-strategy requires --code-reconstruct",
        ),
        (
            ("--code-status", "--apply"),
            "direct code operations are read-only and reject --apply",
        ),
        (
            ("--code-projects", "--route", "code"),
            "direct code operations cannot be combined with --route",
        ),
    ),
)
def test_code_direct_options_reject_ambiguous_or_unsafe_combinations(
    arguments: tuple[str, ...],
    message: str,
) -> None:
    args = build_parser().parse_args(arguments)

    with pytest.raises(SystemExit, match=message):
        validate_arguments(args)


def test_code_status_and_doctor_do_not_initialize_absent_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status_args = _validated(
        "--state-directory", str(tmp_path), "--code-status", "--code-json"
    )
    assert dispatch_direct(status_args) == 0
    status = json.loads(capsys.readouterr().out)

    doctor_args = _validated(
        "--state-directory", str(tmp_path), "--code-doctor", "--code-json"
    )
    assert dispatch_direct(doctor_args) == 0
    doctor = json.loads(capsys.readouterr().out)

    assert status["kind"] == "code-status" and not status["exists"]
    assert status["self_analysis"] is None
    assert doctor["kind"] == "code-doctor"
    assert doctor["schema"] == "not-initialized"
    assert not (tmp_path / "code.sqlite3").exists()
    assert not (tmp_path / "framework.sqlite3").exists()
    assert not (tmp_path / "dedup.sqlite3").exists()


def test_code_review_abstains_without_initializing_absent_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _validated(
        "--state-directory", str(tmp_path), "--code-review", "--code-json"
    )

    assert dispatch_direct(args) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["kind"] == "code-review"
    assert payload["status"] == "abstained"
    assert payload["reason"] == "code_state_missing"
    assert not (tmp_path / "code.sqlite3").exists()
    assert not (tmp_path / "framework.sqlite3").exists()
    assert not (tmp_path / "dedup.sqlite3").exists()


def test_code_publication_diff_abstains_without_initializing_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    args = _validated(
        "--state-directory",
        str(current),
        "--code-publication-diff",
        str(baseline),
        "--code-json",
    )

    assert dispatch_direct(args) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["kind"] == "code-publication-diff"
    assert payload["status"] == "abstained"
    assert payload["reason"].startswith("baseline_unavailable:FileNotFoundError:")
    assert not baseline.exists()
    assert not current.exists()


def test_semantic_cli_accepts_code_as_an_explicit_text_source() -> None:
    args = build_parser().parse_args(
        ("--semantic-index", "text", "--semantic-source", "code")
    )

    validate_arguments(args)

    assert args.semantic_source == ["code"]


def test_code_semantic_search_accepts_its_model_runtime_options(
    tmp_path: Path,
) -> None:
    model_cache = tmp_path / "models"
    args = _validated(
        "--code-search",
        "protección diferencial",
        "--code-search-mode",
        "semantic",
        "--semantic-model-cache",
        str(model_cache),
        "--semantic-threads",
        "2",
    )

    assert args.semantic_model_cache == model_cache
    assert args.semantic_threads == 2

    lexical_only = build_parser().parse_args(
        (
            "--code-search",
            "protección diferencial",
            "--code-search-mode",
            "fts",
            "--semantic-model-cache",
            str(model_cache),
        )
    )
    with pytest.raises(SystemExit, match="semantic options require"):
        validate_arguments(lexical_only)


def test_code_json_is_safe_on_a_cp1252_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", newline="")
    monkeypatch.setattr(cli_code.sys, "stdout", stream)

    cli_code._emit({"snippet": "señal 🚀"}, json_output=True)
    stream.flush()
    payload = raw.getvalue().decode("cp1252")

    assert json.loads(payload) == {"snippet": "señal 🚀"}
    assert "\\ud83d\\ude80" in payload.lower()


def test_explicit_code_semantic_search_abstains_with_exact_coverage_gap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_directory = tmp_path / "state"
    initialize_code_state(state_directory / "code.sqlite3")
    args = build_parser().parse_args(
        [
            "--state-directory",
            str(state_directory),
            "--code-search",
            "dónde se valida el acceso",
            "--code-search-mode",
            "semantic",
        ]
    )
    validate_arguments(args)

    assert dispatch_direct(args) == 2
    output = capsys.readouterr().out
    assert "CODE_SEARCH_CHANNEL name=semantic available=0" in output
    assert "reason=semantic_state_missing" in output
    assert "calibration=uncalibrated_similarity" in output


# endregion [02]
