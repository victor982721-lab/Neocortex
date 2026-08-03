"""Fail-closed CLI contract for protected source-tree analysis."""
# region [00] Contexto del módulo
# Módulo: tests/test_self_analysis_cli.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]

# region [01] Dependencias del módulo
from __future__ import annotations

import os
from pathlib import Path

import pytest

from _04_Nucleo_Operativo.cli_config import framework_config_from_args
from _04_Nucleo_Operativo.cli_parser import build_parser
from _04_Nucleo_Operativo.cli_validation import validate_arguments
from _04_Nucleo_Operativo import cli_validation

# endregion [01]

# region [02] Implementación


def _validate(*arguments: str):
    args = build_parser().parse_args(arguments)
    validate_arguments(args)
    return args


def _extended_drive_alias(path: Path) -> Path:
    return Path("\\\\?\\" + os.path.abspath(path))


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--self-analysis",), "requires explicit --root"),
        (
            ("--self-analysis", "--root", "."),
            "requires explicit --state-directory",
        ),
    ],
)
def test_self_analysis_requires_explicit_paths(
    arguments: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(SystemExit, match=message):
        _validate(*arguments)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--apply",), "cannot be combined with --apply"),
        (("--all",), "cannot be combined with --all"),
        (("--route", "pdf"), "permits only --route code"),
        (("--semantic-index", "text"), "cannot be combined with direct operations"),
        (("--organization-apply",), "cannot be combined with direct operations"),
        (("--code-review",), "cannot be combined with direct operations"),
        (("--pdf-workers", "1"), "is not consumed by --self-analysis"),
        (("--code-generated",), "rejects --code-generated"),
        (("--code-vendored",), "rejects --code-vendored"),
    ],
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
    assert config.analysis_profile == "protected"
    assert config.deep_test_selectors == ()
    assert config.deep_max_tests == 3000
    assert config.deep_time_budget_seconds == 600
    assert config.deep_shard_size == 20


def test_self_analysis_trusted_static_profile_is_explicit(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    args = _validate(
        "--self-analysis",
        "--analysis-profile",
        "trusted-static",
        "--root",
        str(root),
        "--state-directory",
        str(tmp_path / "state"),
    )

    assert framework_config_from_args(args).analysis_profile == "trusted-static"


def test_analysis_profile_requires_self_analysis(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--analysis-profile requires --self-analysis"):
        _validate(
            "--analysis-profile",
            "trusted-static",
            "--root",
            str(tmp_path),
        )


@pytest.mark.parametrize(
    "option",
    (
        "--deep-test-selector",
        "--deep-max-tests",
        "--deep-time-budget-seconds",
        "--deep-shard-size",
    ),
)
def test_deep_controls_require_trusted_deep(
    tmp_path: Path,
    option: str,
) -> None:
    value = "tests/test_sample.py" if option == "--deep-test-selector" else "40"
    with pytest.raises(SystemExit, match="requires --analysis-profile trusted-deep"):
        _validate(
            "--self-analysis",
            "--root",
            str(tmp_path),
            "--state-directory",
            str(tmp_path / "state"),
            option,
            value,
        )


def test_trusted_deep_requires_exact_canonical_root(tmp_path: Path) -> None:
    root = tmp_path / "other-project"
    root.mkdir()

    with pytest.raises(SystemExit, match="requires the exact canonical root"):
        _validate(
            "--self-analysis",
            "--analysis-profile",
            "trusted-deep",
            "--root",
            str(root),
            "--state-directory",
            str(tmp_path / "state"),
        )


def test_trusted_deep_normalizes_bounded_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setattr(cli_validation, "trusted_deep_expected_root", lambda: root)

    args = _validate(
        "--self-analysis",
        "--analysis-profile",
        "trusted-deep",
        "--root",
        str(root),
        "--state-directory",
        str(tmp_path / "state"),
        "--deep-test-selector",
        r"tests\test_z.py::test_last",
        "--deep-test-selector",
        "tests/test_a.py",
        "--deep-max-tests",
        "5000",
        "--deep-time-budget-seconds",
        "900",
        "--deep-shard-size",
        "50",
    )
    config = framework_config_from_args(args)

    assert config.analysis_profile == "trusted-deep"
    assert config.deep_test_selectors == (
        "tests/test_a.py",
        "tests/test_z.py::test_last",
    )
    assert config.deep_max_tests == 5000
    assert config.deep_time_budget_seconds == 900
    assert config.deep_shard_size == 50


@pytest.mark.parametrize(
    ("option", "value", "message"),
    (
        ("--deep-max-tests", "0", "between 1 and 5000"),
        ("--deep-max-tests", "5001", "between 1 and 5000"),
        ("--deep-time-budget-seconds", "29", "between 30 and 900"),
        ("--deep-time-budget-seconds", "901", "between 30 and 900"),
        ("--deep-shard-size", "0", "between 1 and 50"),
        ("--deep-shard-size", "51", "between 1 and 50"),
    ),
)
def test_trusted_deep_rejects_out_of_bounds_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
    message: str,
) -> None:
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setattr(cli_validation, "trusted_deep_expected_root", lambda: root)

    with pytest.raises(SystemExit, match=message):
        _validate(
            "--self-analysis",
            "--analysis-profile",
            "trusted-deep",
            "--root",
            str(root),
            "--state-directory",
            str(tmp_path / "state"),
            option,
            value,
        )


@pytest.mark.parametrize(
    "selector",
    (
        r"C:\repo\tests\test_absolute.py",
        "../tests/test_parent.py",
        "tests/../tests/test_parent.py",
        "src/test_not_a_test_root.py",
        "tests/helper.py",
        "tests/test_control.py\n::test_bad",
    ),
)
def test_trusted_deep_rejects_unsafe_test_selectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
) -> None:
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setattr(cli_validation, "trusted_deep_expected_root", lambda: root)

    with pytest.raises(SystemExit, match="invalid --deep-test-selector"):
        _validate(
            "--self-analysis",
            "--analysis-profile",
            "trusted-deep",
            "--root",
            str(root),
            "--state-directory",
            str(tmp_path / "state"),
            "--deep-test-selector",
            selector,
        )


def test_trusted_deep_rejects_duplicate_normalized_selectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setattr(cli_validation, "trusted_deep_expected_root", lambda: root)

    with pytest.raises(SystemExit, match="duplicate"):
        _validate(
            "--self-analysis",
            "--analysis-profile",
            "trusted-deep",
            "--root",
            str(root),
            "--state-directory",
            str(tmp_path / "state"),
            "--deep-test-selector",
            r"tests\test_sample.py",
            "--deep-test-selector",
            "tests/test_sample.py",
        )


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


# endregion [02]
