"""Subprocess contracts for the static architecture worker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import _04_Nucleo_Operativo.external_architecture_worker as worker


def _production_tree(root: Path, sentinel: Path) -> None:
    for package in worker._contracts.PRODUCTION_ROOT_PACKAGES:
        package_root = root / package
        package_root.mkdir(parents=True)
        source = ""
        if package == "neocortex":
            source = (
                "from pathlib import Path\n"
                f"Path({os.fspath(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
            )
        (package_root / "__init__.py").write_text(source, encoding="utf-8")
    (root / "_04_Nucleo_Operativo" / "service.py").write_text(
        "from _05_Interfaz import view\n", encoding="utf-8"
    )
    (root / "_05_Interfaz" / "view.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "_04_Nucleo_Operativo" / "logic.py").write_text(
        """# complexipy: ignore
def tangled(values):
    if values:
        for value in values:
            if value:
                return value
    return None
""",
        encoding="utf-8",
    )


def _run_worker(root: Path, mode: str, *extra: str) -> subprocess.CompletedProcess[str]:
    assert worker.__file__ is not None
    executable = os.environ.get("NEOCORTEX_ARCHITECTURE_TEST_PYTHON", sys.executable)
    return subprocess.run(
        [
            executable,
            "-I",
            worker.__file__,
            mode,
            "--root",
            os.fspath(root),
            *extra,
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def test_grimp_worker_is_deterministic_static_and_cacheless(tmp_path: Path) -> None:
    root = tmp_path / "project"
    sentinel = tmp_path / "content-executed.txt"
    _production_tree(root, sentinel)

    first = _run_worker(root, "grimp")
    second = _run_worker(root, "grimp")

    assert first.returncode == second.returncode == 0, first.stderr or first.stdout
    assert first.stdout == second.stdout
    assert not sentinel.exists()
    assert not (root / ".grimp_cache").exists()
    payload = json.loads(first.stdout)
    assert payload["schema"] == worker.GRIMP_WORKER_SCHEMA
    assert payload["analysis_contract"]["cache"] == "disabled"
    assert payload["counters"]["production_relations"] == 1
    assert payload["relations"][0]["details"] == [
        {"line_contents": "from _05_Interfaz import view", "line_number": 1}
    ]
    contracts = {item["contract"]["contract_id"]: item for item in payload["contract_evaluations"]}
    assert contracts["core-does-not-depend-on-ui-v1"]["status"] == "failed"
    assert contracts["core-does-not-depend-on-ui-v1"]["violations"][0]["import_chain"] == [
        "_04_Nucleo_Operativo.service",
        "_05_Interfaz.view",
    ]


def test_complexipy_worker_reports_module_function_and_line_metrics(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _production_tree(root, tmp_path / "content-executed.txt")

    completed = _run_worker(root, "complexipy")

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["schema"] == worker.COMPLEXIPY_WORKER_SCHEMA
    assert payload["analysis_contract"]["check_script"] is True
    assert payload["analysis_contract"]["no_ignore"] is True
    metric = next(
        item
        for item in payload["function_metrics"]
        if item["relative_path"] == "_04_Nucleo_Operativo/logic.py" and item["symbol"] == "tangled"
    )
    assert metric["value"] > 0
    assert sum(item["complexity"] for item in metric["lines"]) == metric["value"]
    module = next(
        item for item in payload["module_metrics"] if item["module"] == "_04_Nucleo_Operativo.logic"
    )
    assert module["total"] >= module["maximum"] == metric["value"]


def test_worker_fails_with_bounded_json_when_domain_is_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "neocortex").mkdir()
    (root / "neocortex" / "__init__.py").write_text("", encoding="utf-8")

    completed = _run_worker(root, "grimp")

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload == {
        "error": {
            "code": "missing_production_package",
            "message": "exact production package is unavailable: _01_Enumeracion",
        },
        "schema": worker.WORKER_ERROR_SCHEMA,
        "status": "error",
    }
