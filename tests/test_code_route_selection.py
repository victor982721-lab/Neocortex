"""Regression coverage for code-route candidate selection semantics."""
# region [00] Contexto del módulo
# Módulo: tests/test_code_route_selection.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from _02_Deduplicacion import FileSnapshot
from _04_Nucleo_Operativo.code_contracts import CodeRouteConfig
from _04_Nucleo_Operativo.code_route import CodeRoute
from _04_Nucleo_Operativo.route_filters import CandidateSelection
# endregion [01]

# region [02] Implementación


def _snapshot(path: Path) -> FileSnapshot:
    observed = path.stat()
    return FileSnapshot(
        str(path),
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        getattr(observed, "st_birthtime_ns", observed.st_ctime_ns),
    )


class _Inventory:
    def __init__(self, paths: Iterable[Path]):
        self.paths = tuple(paths)

    def snapshots(self, _scan_id: int):
        return iter(_snapshot(path) for path in self.paths)


class _FrameworkState:
    def begin_route_phase(
        self,
        _run_id: int,
        _route: str,
        _phase: str,
        *,
        source_run_id: int | None = None,
    ) -> None:
        del source_run_id

    def complete_route_phase(
        self,
        _run_id: int,
        _route: str,
        _phase: str,
        summary: Mapping[str, object] | None = None,
    ) -> None:
        assert summary is not None

    def fail_route_phase(
        self,
        _run_id: int,
        _route: str,
        _phase: str,
        _exc: BaseException,
    ) -> None:
        raise AssertionError("selection fixture route must not fail")


def _config(tmp_path: Path, selection: CandidateSelection) -> CodeRouteConfig:
    return CodeRouteConfig(
        state_path=tmp_path / "state" / "code.sqlite3",
        dedup_path=tmp_path / "state" / "dedup.sqlite3",
        selection=selection,
    )


def test_path_only_selection_can_publish_a_first_observation(tmp_path: Path) -> None:
    source = tmp_path / "selected.py"
    source.write_text("def selected():\n    return True\n", encoding="utf-8")
    selection = CandidateSelection.from_values(paths=(source,))

    summary = CodeRoute(
        _config(tmp_path, selection),
        _Inventory((source,)),
        _FrameworkState(),
        1,
        1,
    ).run()

    assert summary.candidates == 1
    assert summary.processed == 1


def test_status_and_diagnostic_selection_uses_current_code_state(tmp_path: Path) -> None:
    valid = tmp_path / "valid.py"
    invalid = tmp_path / "invalid.py"
    valid.write_text("def valid():\n    return True\n", encoding="utf-8")
    invalid.write_text("def invalid(:\n    pass\n", encoding="utf-8")
    inventory = _Inventory((valid, invalid))
    CodeRoute(
        _config(tmp_path, CandidateSelection()),
        inventory,
        _FrameworkState(),
        1,
        1,
    ).run()

    selected = CandidateSelection.from_values(
        statuses=("partial",),
        error_types=("python_parse_error",),
    )
    summary = CodeRoute(
        _config(tmp_path, selected),
        inventory,
        _FrameworkState(),
        2,
        2,
    ).run()

    assert summary.candidates == 1
    assert summary.cache_hits == 1
    assert summary.processed == 0

# endregion [02]
