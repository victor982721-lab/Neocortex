"""Stable package and command-line version contract."""

from __future__ import annotations

import pytest

from neocortex import __version__
from _04_Nucleo_Operativo.cli_parser import build_parser


def test_cli_reports_the_packaged_version(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(("--version",))

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"Neocortex {__version__}"

