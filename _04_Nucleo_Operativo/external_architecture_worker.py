"""Isolated JSON worker for static architecture and complexity evidence.

Providers execute this file directly with isolated Python.  Executing the file
rather than ``python -m`` keeps the installed ``_04_Nucleo_Operativo`` package
out of ``sys.modules`` while Grimp locates the staged package with the same
name.  Project content is parsed statically and is never imported.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
import stat
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from . import code_architecture_contracts as _contracts
elif __package__:
    from . import code_architecture_contracts as _contracts
else:  # Direct isolated worker execution; do not import the staged package.
    _contract_path = Path(__file__).with_name("code_architecture_contracts.py")
    _contract_spec = importlib.util.spec_from_file_location(
        "_neocortex_code_architecture_contracts", _contract_path
    )
    if _contract_spec is None or _contract_spec.loader is None:
        raise RuntimeError("architecture contract module is unavailable")
    _contracts = importlib.util.module_from_spec(_contract_spec)
    sys.modules[_contract_spec.name] = _contracts
    _contract_spec.loader.exec_module(_contracts)

GRIMP_WORKER_SCHEMA = "neocortex.external-architecture-worker/grimp-v1"
COMPLEXIPY_WORKER_SCHEMA = "neocortex.external-architecture-worker/complexipy-v1"
WORKER_ERROR_SCHEMA = "neocortex.external-architecture-worker/error-v1"

DEFAULT_MAX_FILES = 4096
DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
HARD_MAX_FILES = 16_384
HARD_MAX_INPUT_BYTES = 512 * 1024 * 1024
HARD_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_IMPORT_LINE_CHARS = 1000
_EXCLUDED_PATH_PARTS = frozenset((*_contracts.EXCLUDED_ARCHITECTURE_NAMESPACES, "__pycache__"))


class WorkerContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    max_files: int
    max_input_bytes: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= HARD_MAX_FILES:
            raise WorkerContractError("invalid_limit", "max-files is outside its hard bound")
        if not 1 <= self.max_input_bytes <= HARD_MAX_INPUT_BYTES:
            raise WorkerContractError("invalid_limit", "max-input-bytes is outside its hard bound")
        if not 1024 <= self.max_output_bytes <= HARD_MAX_OUTPUT_BYTES:
            raise WorkerContractError("invalid_limit", "max-output-bytes is outside its hard bound")

    def as_payload(self) -> dict[str, int]:
        return {
            "max_files": self.max_files,
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass(frozen=True, slots=True)
class StagedPythonFile:
    path: Path
    relative_path: str
    module: str
    size: int
    sha256: str


def _is_reparse_point(path: Path) -> bool:
    metadata = os.lstat(path)
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(int(getattr(metadata, "st_file_attributes", 0)) & reparse)


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _module_from_relative_path(relative_path: str) -> str:
    parts = relative_path.split("/")
    filename = parts.pop()
    if filename == "__init__.py":
        return ".".join(parts)
    if not filename.endswith(".py"):
        raise WorkerContractError("unsupported_input", "architecture input is not Python")
    return ".".join((*parts, filename[:-3]))


def _read_stable_file(path: Path, *, expected_size: int | None = None) -> bytes:
    before = os.lstat(path)
    if _is_reparse_point(path) or not stat.S_ISREG(before.st_mode):
        raise WorkerContractError("unsafe_input", "architecture input is not a regular file")
    if expected_size is not None and before.st_size != expected_size:
        raise WorkerContractError("input_changed", "architecture input changed during analysis")
    raw = path.read_bytes()
    after = os.lstat(path)
    if (
        len(raw) != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise WorkerContractError("input_changed", "architecture input changed during analysis")
    return raw


def _validate_root(root: Path) -> Path:
    absolute = root.absolute()
    if not absolute.exists() or not absolute.is_dir() or _is_reparse_point(absolute):
        raise WorkerContractError("invalid_root", "staged root must be a regular directory")
    resolved = absolute.resolve(strict=True)
    for package in _contracts.PRODUCTION_ROOT_PACKAGES:
        package_root = resolved / package
        initializer = package_root / "__init__.py"
        if (
            not package_root.is_dir()
            or _is_reparse_point(package_root)
            or not initializer.is_file()
            or _is_reparse_point(initializer)
        ):
            raise WorkerContractError(
                "missing_production_package",
                f"exact production package is unavailable: {package}",
            )
    return resolved


def _collect_inputs(root: Path, limits: WorkerLimits) -> tuple[StagedPythonFile, ...]:
    candidates: list[Path] = []
    checked_directories: set[Path] = set()
    for package in _contracts.PRODUCTION_ROOT_PACKAGES:
        package_root = root / package
        for path in package_root.rglob("*.py"):
            relative = path.relative_to(root)
            if any(part in _EXCLUDED_PATH_PARTS for part in relative.parts[:-1]):
                continue
            resolved = path.resolve(strict=True)
            if not _inside_root(resolved, root):
                raise WorkerContractError("unsafe_input", "architecture input escapes staged root")
            for parent in (path.parent, *path.parents):
                if parent == root:
                    break
                if parent in checked_directories:
                    continue
                checked_directories.add(parent)
                if _is_reparse_point(parent):
                    raise WorkerContractError(
                        "unsafe_input", "architecture input traverses a reparse point"
                    )
            candidates.append(path)
    candidates.sort(key=lambda item: item.relative_to(root).as_posix())
    if len(candidates) > limits.max_files:
        raise WorkerContractError("input_file_bound_exceeded", "Python file bound exceeded")

    total_bytes = 0
    inputs: list[StagedPythonFile] = []
    for path in candidates:
        metadata = os.lstat(path)
        if metadata.st_size < 0:
            raise WorkerContractError("unsafe_input", "architecture input has invalid size")
        total_bytes += metadata.st_size
        if total_bytes > limits.max_input_bytes:
            raise WorkerContractError("input_byte_bound_exceeded", "Python byte bound exceeded")
        raw = _read_stable_file(path, expected_size=metadata.st_size)
        relative_path = path.relative_to(root).as_posix()
        inputs.append(
            StagedPythonFile(
                path,
                relative_path,
                _module_from_relative_path(relative_path),
                len(raw),
                hashlib.sha256(raw).hexdigest(),
            )
        )
    if not inputs:
        raise WorkerContractError("empty_domain", "production Python domain is empty")
    return tuple(inputs)


def _validate_inputs_unchanged(inputs: Sequence[StagedPythonFile]) -> None:
    for item in inputs:
        raw = _read_stable_file(item.path, expected_size=item.size)
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise WorkerContractError("input_changed", "architecture input changed during analysis")


def _input_manifest(inputs: Sequence[StagedPythonFile]) -> dict[str, object]:
    digest = hashlib.sha256()
    for item in inputs:
        digest.update(item.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(item.sha256.encode("ascii"))
        digest.update(b"\n")
    return {
        "file_count": len(inputs),
        "total_bytes": sum(item.size for item in inputs),
        "content_manifest_sha256": digest.hexdigest(),
    }


@contextmanager
def _staged_import_path(root: Path) -> Iterator[None]:
    value = os.fspath(root)
    sys.path.insert(0, value)
    try:
        yield
    finally:
        try:
            sys.path.remove(value)
        except ValueError:
            pass


def _tool_version(distribution: str) -> str:
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as error:
        raise WorkerContractError(
            "tool_unavailable", f"required static tool is unavailable: {distribution}"
        ) from error
    if not version or len(version.encode("utf-8")) > 256:
        raise WorkerContractError("invalid_tool_version", "static tool version is invalid")
    return version


def _normalize_import_details(
    raw_details: Iterable[Mapping[str, object]],
) -> tuple[_contracts.ImportLineDetail, ...]:
    details: set[_contracts.ImportLineDetail] = set()
    for raw in raw_details:
        line_number = raw.get("line_number")
        if not isinstance(line_number, int) or line_number < 1:
            continue
        line_contents = raw.get("line_contents")
        normalized = " ".join(str(line_contents or "").split())[:_MAX_IMPORT_LINE_CHARS]
        details.add(_contracts.ImportLineDetail(line_number, normalized))
    return tuple(sorted(details))


def _module_path(module: str, inputs_by_module: Mapping[str, StagedPythonFile]) -> str | None:
    item = inputs_by_module.get(module)
    return None if item is None else item.relative_path


def _grimp_imports(graph: Any, modules: Sequence[str]) -> tuple[_contracts.ModuleImport, ...]:
    imports: list[_contracts.ModuleImport] = []
    for importer in modules:
        try:
            imported_modules = graph.find_modules_directly_imported_by(importer)
        except Exception as error:  # Grimp owns the concrete extension exception types.
            raise WorkerContractError("grimp_query_failed", "Grimp import query failed") from error
        for imported in sorted(imported_modules):
            if not (
                _contracts.is_production_module(imported)
                or imported.partition(".")[0] in _contracts.EXCLUDED_ARCHITECTURE_NAMESPACES
            ):
                continue
            try:
                raw_details = graph.get_import_details(importer=importer, imported=imported)
            except Exception as error:
                raise WorkerContractError(
                    "grimp_query_failed", "Grimp detail query failed"
                ) from error
            imports.append(
                _contracts.ModuleImport(importer, imported, _normalize_import_details(raw_details))
            )
    return tuple(sorted(imports, key=lambda item: (item.importer, item.imported)))


def _cycle_payloads(
    modules: Sequence[str], imports: Sequence[_contracts.ModuleImport]
) -> tuple[dict[str, object], ...]:
    known = set(_contracts.KNOWN_CYCLE_BASELINE)
    payloads: list[dict[str, object]] = []
    for component in _contracts.cyclic_strongly_connected_components(modules, imports):
        cycle_chain = _contracts.shortest_cycle_chain(component, imports)
        payloads.append(
            {
                "cycle_id": _contracts.stable_architecture_id("import-cycle-v1", *component),
                "modules": list(component),
                "module_count": len(component),
                "shortest_cycle_chain": list(cycle_chain),
                "baseline_state": "known_baseline" if component in known else "new",
            }
        )
    return tuple(payloads)


def analyze_grimp(root: Path, limits: WorkerLimits) -> dict[str, object]:
    inputs = _collect_inputs(root, limits)
    version = _tool_version("grimp")
    try:
        import grimp  # type: ignore[import-not-found]
    except ImportError as error:
        raise WorkerContractError("tool_unavailable", "Grimp cannot be imported") from error

    already_loaded = [name for name in _contracts.PRODUCTION_ROOT_PACKAGES if name in sys.modules]
    if already_loaded:
        raise WorkerContractError(
            "unsafe_worker_invocation",
            "worker must execute directly so staged packages remain unloaded",
        )
    try:
        with _staged_import_path(root):
            graph = grimp.build_graph(
                *_contracts.PRODUCTION_ROOT_PACKAGES,
                include_external_packages=True,
                exclude_type_checking_imports=False,
                cache_dir=None,
            )
    except Exception as error:
        raise WorkerContractError("grimp_analysis_failed", "Grimp graph build failed") from error
    if any(name in sys.modules for name in _contracts.PRODUCTION_ROOT_PACKAGES):
        raise WorkerContractError(
            "target_content_imported", "static graph build imported staged project content"
        )

    modules = tuple(
        sorted(module for module in graph.modules if _contracts.is_production_module(module))
    )
    imports = _grimp_imports(graph, modules)
    production_imports = tuple(
        item for item in imports if _contracts.is_production_module(item.imported)
    )
    incoming: dict[str, int] = dict.fromkeys(modules, 0)
    outgoing: dict[str, int] = dict.fromkeys(modules, 0)
    for item in production_imports:
        outgoing[item.importer] += 1
        incoming[item.imported] += 1
    cycles = _cycle_payloads(modules, production_imports)
    cycle_ids: dict[str, list[str]] = {module: [] for module in modules}
    for cycle in cycles:
        raw_modules = cycle["modules"]
        if not isinstance(raw_modules, list):
            raise WorkerContractError("internal_contract_error", "cycle modules are invalid")
        for module in raw_modules:
            cycle_ids[str(module)].append(str(cycle["cycle_id"]))
    inputs_by_module = {item.module: item for item in inputs}
    module_metrics = [
        {
            "module": module,
            "relative_path": _module_path(module, inputs_by_module),
            "fan_in": incoming[module],
            "fan_out": outgoing[module],
            "cycle_ids": sorted(cycle_ids[module]),
        }
        for module in modules
    ]
    evaluations = _contracts.evaluate_architecture_contracts(modules, imports)
    _validate_inputs_unchanged(inputs)
    return {
        "schema": GRIMP_WORKER_SCHEMA,
        "status": "ready",
        "mode": "grimp",
        "tool": {"name": "grimp", "version": version, "api": "build_graph"},
        "analysis_contract": {
            "static_only": True,
            "imports_project_content": False,
            "executes_project_content": False,
            "uses_network": False,
            "cache": "disabled",
            "include_external_packages": True,
            "exclude_type_checking_imports": False,
            "comparable_relations": "production_to_production_module_import",
        },
        "limits": limits.as_payload(),
        "inputs": _input_manifest(inputs),
        "architecture": _contracts.architecture_contract_manifest(),
        "counters": {
            "modules": len(modules),
            "production_relations": len(production_imports),
            "policy_only_external_relations": len(imports) - len(production_imports),
            "cyclic_components": len(cycles),
            "contract_violations": sum(len(item.violations) for item in evaluations),
        },
        "module_metrics": module_metrics,
        "relations": [item.as_payload() for item in production_imports],
        "cycles": list(cycles),
        "contract_evaluations": [item.as_payload() for item in evaluations],
    }


def _complexity_lines(function: Any) -> list[dict[str, int]]:
    contributions = {
        (int(item.line), int(item.complexity))
        for item in function.line_complexities
        if int(item.complexity) != 0
    }
    return [{"line": line, "complexity": complexity} for line, complexity in sorted(contributions)]


def _required_metric_int(metric: Mapping[str, object], field: str) -> int:
    value = metric.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkerContractError("internal_contract_error", "complexity metric is invalid")
    return value


def _function_metric_order(metric: Mapping[str, object]) -> tuple[str, int, int, str]:
    return (
        str(metric["relative_path"]),
        _required_metric_int(metric, "start_line"),
        _required_metric_int(metric, "end_line"),
        str(metric["symbol"]),
    )


def analyze_complexipy(root: Path, limits: WorkerLimits) -> dict[str, object]:
    inputs = _collect_inputs(root, limits)
    version = _tool_version("complexipy")
    try:
        import complexipy  # type: ignore[import-not-found]
    except ImportError as error:
        raise WorkerContractError("tool_unavailable", "complexipy cannot be imported") from error

    module_metrics: list[dict[str, object]] = []
    function_metrics: list[dict[str, object]] = []
    for item in inputs:
        try:
            result = complexipy.file_complexity(
                os.fspath(item.path), check_script=True, no_ignore=True
            )
        except Exception as error:
            raise WorkerContractError(
                "complexipy_analysis_failed",
                f"complexipy could not analyze {item.relative_path}",
            ) from error
        functions = sorted(
            result.functions,
            key=lambda value: (
                int(value.line_start),
                int(value.line_end),
                str(value.name),
            ),
        )
        values = [int(function.complexity) for function in functions]
        module_total = int(result.complexity)
        if module_total != sum(values):
            raise WorkerContractError(
                "complexipy_contract_mismatch",
                "complexipy file total does not equal its function observations",
            )
        module_metrics.append(
            {
                "metric_id": _contracts.stable_architecture_id(
                    "cognitive-complexity-module-v1", item.relative_path
                ),
                "metric": "cognitive_complexity",
                "scope": "module",
                "module": item.module,
                "relative_path": item.relative_path,
                "total": module_total,
                "maximum": max(values, default=0),
                "function_count": len(functions),
            }
        )
        for function in functions:
            name = str(function.name)
            start_line = int(function.line_start)
            end_line = int(function.line_end)
            value = int(function.complexity)
            function_metrics.append(
                {
                    "metric_id": _contracts.stable_architecture_id(
                        "cognitive-complexity-symbol-v1",
                        item.relative_path,
                        name,
                        start_line,
                        end_line,
                    ),
                    "metric": "cognitive_complexity",
                    "scope": "module_script" if name == "<module>" else "symbol",
                    "module": item.module,
                    "relative_path": item.relative_path,
                    "symbol": name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "value": value,
                    "lines": _complexity_lines(function),
                }
            )
    _validate_inputs_unchanged(inputs)
    return {
        "schema": COMPLEXIPY_WORKER_SCHEMA,
        "status": "ready",
        "mode": "complexipy",
        "tool": {"name": "complexipy", "version": version, "api": "file_complexity"},
        "analysis_contract": {
            "static_only": True,
            "imports_project_content": False,
            "executes_project_content": False,
            "uses_network": False,
            "loads_project_configuration": False,
            "check_script": True,
            "no_ignore": True,
            "snapshot": False,
            "diff": False,
            "autofix": False,
        },
        "limits": limits.as_payload(),
        "inputs": _input_manifest(inputs),
        "architecture_contract_schema": _contracts.ARCHITECTURE_CONTRACT_SCHEMA,
        "architecture_baseline_id": _contracts.ARCHITECTURE_BASELINE_ID,
        "counters": {
            "modules": len(module_metrics),
            "function_observations": len(function_metrics),
            "cognitive_complexity_total": sum(
                _required_metric_int(item, "total") for item in module_metrics
            ),
        },
        "module_metrics": sorted(module_metrics, key=lambda value: str(value["module"])),
        "function_metrics": sorted(
            function_metrics,
            key=_function_metric_order,
        ),
    }


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _emit(payload: Mapping[str, object], max_output_bytes: int) -> None:
    encoded = _canonical_json(payload)
    if len(encoded) > max_output_bytes:
        error = {
            "schema": WORKER_ERROR_SCHEMA,
            "status": "error",
            "error": {
                "code": "output_byte_bound_exceeded",
                "message": "architecture worker output exceeds its declared bound",
                "required_bytes": len(encoded),
                "max_output_bytes": max_output_bytes,
            },
        }
        encoded = _canonical_json(error)
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.write(b"\n")
        raise SystemExit(2)
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.write(b"\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("mode", choices=("grimp", "complexipy"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    return parser


def _fail(error: WorkerContractError, max_output_bytes: int) -> NoReturn:
    payload = {
        "schema": WORKER_ERROR_SCHEMA,
        "status": "error",
        "error": {"code": error.code, "message": str(error)},
    }
    encoded = _canonical_json(payload)
    if len(encoded) <= max_output_bytes:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.write(b"\n")
    raise SystemExit(2)


def main(arguments: Sequence[str] | None = None) -> int:
    namespace = _parser().parse_args(arguments)
    try:
        limits = WorkerLimits(
            namespace.max_files,
            namespace.max_input_bytes,
            namespace.max_output_bytes,
        )
        root = _validate_root(namespace.root)
        if namespace.mode == "grimp":
            payload = analyze_grimp(root, limits)
        else:
            payload = analyze_complexipy(root, limits)
        _emit(payload, limits.max_output_bytes)
    except WorkerContractError as error:
        _fail(error, max(1024, min(namespace.max_output_bytes, HARD_MAX_OUTPUT_BYTES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
