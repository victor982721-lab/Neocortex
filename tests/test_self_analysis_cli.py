"""Fail-closed CLI contract for protected source-tree analysis."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _04_Nucleo_Operativo.cli_config import framework_config_from_args
from _04_Nucleo_Operativo.cli_parser import build_parser
from _04_Nucleo_Operativo.cli_validation import validate_arguments


def _validate(*arguments: str):
    args = build_parser().parse_args(arguments)
    validate_arguments(args)
    return args


def _extended_drive_alias(path: Path) -> Path:
    return Path("\\\\?\\" + os.path.abspath(path))


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (("--self-analysis",), "requires explicit --root"),
        (
            ("--self-analysis", "--root", "."),
            "requires explicit --state-directory",
        ),
    ),
)
def test_self_analysis_requires_explicit_paths(
    arguments: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(SystemExit, match=message):
        _validate(*arguments)


@pytest.mark.parametrize(
    ("extra", "message"),
    (
        (("--apply",), "cannot be combined with --apply"),
        (("--all",), "cannot be combined with --all"),
        (("--route", "pdf"), "permits only --route code"),
        (("--semantic-index", "text"), "cannot be combined with direct operations"),
        (("--organization-apply",), "cannot be combined with direct operations"),
        (("--pdf-workers", "1"), "is not consumed by --self-analysis"),
        (("--code-generated",), "rejects --code-generated"),
        (("--code-vendored",), "rejects --code-vendored"),
    ),
)
def test_self_analysis_rejects_unsafe_or_unused_combinations(
    tmp_path: Path,
    extra: tuple[str, ...],
    message: str,
) -> None:
    state = tmp_path / "state"
    with pytest.raises(SystemExit, match=message):
        _validate(
            "--self-analysis",
            "--root",
            str(tmp_path),
            "--state-directory",
            str(state),
            *extra,
        )


def test_self_analysis_preset_is_code_only_and_analyze_only(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    state = tmp_path / "state"
    args = _validate(
        "--self-analysis",
        "--root",
        str(root),
        "--state-directory",
        str(state),
    )

    config = framework_config_from_args(args)

    assert config.root == root.resolve()
    assert config.state_directory == state
    assert config.self_analysis
    assert config.corpus_access_mode == "analyze_only"
    assert config.route == "code"
    assert not config.apply_actions
    assert not config.document_catalog_enabled
    assert not config.code_include_generated
    assert not config.code_include_vendored


def test_self_analysis_rejects_intersecting_state_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    state_directories = (root, tmp_path, root / "state")
    for state_directory in (
        *state_directories,
        *(_extended_drive_alias(path) for path in state_directories),
    ):
        with pytest.raises(SystemExit, match="must be disjoint"):
            _validate(
                "--self-analysis",
                "--root",
                str(root),
                "--state-directory",
                str(state_directory),
            )
    with pytest.raises(SystemExit, match="must be disjoint"):
        _validate(
            "--self-analysis",
            "--root",
            str(_extended_drive_alias(root)),
            "--state-directory",
            str(root / "state"),
        )


def test_self_analysis_rejects_unsupported_state_namespace(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(SystemExit, match="boundary cannot be verified"):
        _validate(
            "--self-analysis",
            "--root",
            str(root),
            "--state-directory",
            r"\\.\PhysicalDrive0",
        )


def test_self_analysis_rejects_non_directory_root(tmp_path: Path) -> None:
    source = tmp_path / "not-a-directory.py"
    source.write_text("pass\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="invalid --self-analysis root"):
        _validate(
            "--self-analysis",
            "--root",
            str(source),
            "--state-directory",
            str(tmp_path / "state"),
        )
