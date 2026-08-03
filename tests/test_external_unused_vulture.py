"""Focused contracts for the isolated advisory Vulture adapter."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

import _04_Nucleo_Operativo.external_unused_vulture as adapter
from _04_Nucleo_Operativo.code_external_evidence import ExternalEvidenceFile
from _04_Nucleo_Operativo.semantic_models import fingerprint_bytes


def _stage(
    stage_root: Path,
    files: dict[str, str],
    *,
    include: tuple[str, ...] | None = None,
) -> dict[str, ExternalEvidenceFile]:
    source = stage_root / "source"
    source.mkdir()
    for relative_path, content in files.items():
        path = source / Path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    selected = tuple(sorted(files)) if include is None else include
    staged: dict[str, ExternalEvidenceFile] = {}
    for version_id, relative_path in enumerate(selected, start=1):
        path = source / Path(relative_path)
        raw = path.read_bytes()
        metadata = path.stat()
        fingerprint = fingerprint_bytes(raw)
        staged[os.path.normcase(os.path.abspath(path))] = ExternalEvidenceFile(
            version_id,
            str(path),
            relative_path,
            metadata.st_size,
            metadata.st_mtime_ns,
            fingerprint.xxh3_128,
            fingerprint.xxh3_64_guard,
        )
    return staged


def _environment() -> dict[str, str]:
    return dict(os.environ)


def test_real_vulture_reports_unused_definitions_and_preserves_used_and_exports(
    tmp_path: Path,
) -> None:
    staged = _stage(
        tmp_path,
        {
            "pkg/module.py": """\
import os
import math as used_math

__all__ = ["Public"]

class Public:
    pass

class DeadClass:
    def still_dead_method(self):
        return 1

def dead_function(value):
    return value

def used_function():
    return used_math.ceil(1.2)

used_function()
""",
        },
    )

    result = adapter.execute_vulture_unused(tmp_path, staged, _environment())
    records = {
        (item.metadata["symbol_kind"], item.metadata["symbol_name"]): item
        for item in result.findings
    }

    assert {("import", "os"), ("class", "DeadClass"), ("function", "dead_function")} <= set(
        records
    )
    assert ("class", "Public") not in records
    assert ("import", "used_math") not in records
    assert ("function", "used_function") not in records
    dead_function = records[("function", "dead_function")]
    assert dead_function.category == "unused_code"
    assert dead_function.code == "VULTURE_UNUSED_FUNCTION"
    assert dead_function.tool_confidence == 0.6
    assert dead_function.calibrated_confidence is None
    assert dead_function.gate_authority == "advisory"
    assert dead_function.metadata["size"] == 2
    assert dead_function.metadata["line_span"] == [13, 14]
    assert dead_function.mutation_authority is False
    assert result.process_invocations == 1
    assert result.stdout_bytes > 0
    assert result.stderr_bytes == 0


def test_decorated_candidate_is_retained_for_dynamic_correlation_but_direct_callback_is_used(
    tmp_path: Path,
) -> None:
    staged = _stage(
        tmp_path,
        {
            "callbacks.py": """\
def register(function):
    return function

@register
def decorated_callback():
    return "dynamic"

def direct_callback():
    return "direct"

CALLBACKS = {"direct": direct_callback}
""",
        },
    )

    result = adapter.execute_vulture_unused(tmp_path, staged, _environment())
    names = {str(item.metadata["symbol_name"]): item for item in result.findings}

    assert names["decorated_callback"].metadata["dynamic_correlation_required"] is True
    assert "direct_callback" not in names
    assert (
        "decorators_callbacks_registries_reexports_and_dynamic_access_require_correlation"
        in result.limitations
    )


def test_worker_analyzes_only_exact_manifest_paths(tmp_path: Path) -> None:
    staged = _stage(
        tmp_path,
        {
            "included.py": "def included_dead():\n    return 1\n",
            "ignored.py": "def ignored_dead():\n    return 2\n",
        },
        include=("included.py",),
    )

    result = adapter.execute_vulture_unused(tmp_path, staged, _environment())
    names = {item.metadata["symbol_name"] for item in result.findings}

    assert "included_dead" in names
    assert "ignored_dead" not in names
    assert {item.relative_path for item in result.findings} == {"included.py"}


def test_raw_candidates_preserve_dynamic_protocol_fixture_and_reexport_boundaries(
    tmp_path: Path,
) -> None:
    staged = _stage(
        tmp_path,
        {
            "pkg/__init__.py": """\
from .public import exported

__all__ = ["exported"]

def __getattr__(name):
    raise AttributeError(name)

def __dir__():
    return list(__all__)
""",
            "pkg/public.py": """\
from typing import Protocol

def exported():
    return "public"

class CallbackProtocol(Protocol):
    def invoke(self, payload): ...

class Managed:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False
""",
            "tests/test_fixture.py": """\
import pytest

pytestmark = pytest.mark.unit

@pytest.fixture
def fixture_value():
    return 1
""",
        },
    )

    result = adapter.execute_vulture_unused(tmp_path, staged, _environment())
    records = {
        (item.relative_path, item.metadata["symbol_kind"], item.metadata["symbol_name"]): item
        for item in result.findings
    }

    assert ("pkg/__init__.py", "import", "exported") not in records
    assert ("pkg/__init__.py", "function", "__getattr__") in records
    assert ("pkg/__init__.py", "function", "__dir__") in records
    assert ("pkg/public.py", "class", "CallbackProtocol") in records
    assert ("pkg/public.py", "method", "invoke") in records
    assert ("pkg/public.py", "variable", "payload") in records
    assert ("pkg/public.py", "variable", "exc_type") in records
    assert ("pkg/public.py", "variable", "exc") in records
    assert ("pkg/public.py", "variable", "traceback") in records
    assert ("tests/test_fixture.py", "variable", "pytestmark") in records
    assert ("tests/test_fixture.py", "function", "fixture_value") in records
    assert records[("pkg/public.py", "variable", "payload")].tool_confidence == 1.0


def test_adapter_rejects_malformed_worker_output_and_uses_bounded_direct_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = _stage(tmp_path, {"module.py": "value = 1\n"})

    def run(arguments, **kwargs):
        command = tuple(str(item) for item in arguments)
        assert command[1] == "-I"
        assert command[2].endswith("external_unused_vulture_worker.py")
        assert "-m" not in command
        assert kwargs["input_bytes"]
        assert kwargs["timeout_seconds"] == 180.0
        assert kwargs["stdout_limit_bytes"] == 8 * 1024 * 1024
        assert kwargs["stderr_limit_bytes"] == 128 * 1024
        return subprocess.CompletedProcess(command, 0, b"{not-json", b"")

    monkeypatch.setattr(adapter, "run_bounded_capture", run)

    with pytest.raises(ValueError, match="JSON output is malformed"):
        adapter.execute_vulture_unused(tmp_path, staged, _environment())


def test_adapter_rejects_unowned_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = _stage(tmp_path, {"module.py": "value = 1\n"})
    manifest, digest, file_count, total_bytes = adapter._input_manifest(tmp_path, staged)
    assert manifest
    payload = {
        "schema": "neocortex.external-unused-vulture-worker/v1",
        "status": "ready",
        "tool": {
            "name": "vulture",
            "version": importlib.metadata.version("vulture"),
            "api": "Vulture.scavenge/get_unused_code",
        },
        "inputs": {
            "file_count": file_count,
            "total_bytes": total_bytes,
            "content_manifest_sha256": digest,
        },
        "findings": [
            {
                "relative_path": "outside.py",
                "kind": "function",
                "name": "outside",
                "message": "unused function 'outside'",
                "confidence_percent": 60,
                "size": 1,
                "start_line": 1,
                "end_line": 1,
            }
        ],
        "limitations": [
            "vulture_confidence_below_100_is_heuristic",
            "static_name_analysis_cannot_prove_runtime_unused",
            "decorators_callbacks_registries_reexports_and_dynamic_access_require_correlation",
            "advisory_only_no_mutation_authority",
        ],
    }
    monkeypatch.setattr(
        adapter,
        "run_bounded_capture",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(payload).encode("utf-8"),
            b"",
        ),
    )

    with pytest.raises(ValueError, match="unowned path"):
        adapter.execute_vulture_unused(tmp_path, staged, _environment())


def test_invalid_python_fails_closed(tmp_path: Path) -> None:
    staged = _stage(tmp_path, {"broken.py": "def broken(:\n    pass\n"})

    with pytest.raises(ValueError, match="unexpected_exit"):
        adapter.execute_vulture_unused(tmp_path, staged, _environment())


def test_vulture_is_a_canonical_runtime_dependency() -> None:
    payload = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))
    runtime = payload["project"]["dependencies"]
    development = payload["project"]["optional-dependencies"]["dev"]

    assert "vulture>=2.16,<2.17" in runtime
    assert not any(str(item).startswith("vulture") for item in development)
