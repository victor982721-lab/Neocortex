"""Public CLI contracts for the integrated code route and direct queries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _04_Nucleo_Operativo.cli_app import dispatch_direct
from _04_Nucleo_Operativo.cli_config import framework_config_from_args
from _04_Nucleo_Operativo.cli_parser import build_parser
from _04_Nucleo_Operativo.cli_validation import validate_arguments


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


def test_semantic_cli_accepts_code_as_an_explicit_text_source() -> None:
    args = build_parser().parse_args(
        ("--semantic-index", "text", "--semantic-source", "code")
    )

    validate_arguments(args)

    assert args.semantic_source == ["code"]
