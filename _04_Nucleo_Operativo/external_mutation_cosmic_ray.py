"""Bounded focal mutation evidence backed by Cosmic Ray 8.4.6."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .bounded_subprocess import run_bounded_capture
from .code_external_evidence import ExternalEvidenceFile, validate_external_inputs
from .external_evidence_models import (
    ExternalProviderFinding,
    ExternalProviderMetric,
    ExternalProviderRelation,
    ExternalSubjectKind,
    external_metric_identity,
    external_relation_identity,
    external_signature,
)

COSMIC_RAY_MUTATION_PROVIDER_ID = "cosmic-ray-focal-mutation"
COSMIC_RAY_MUTATION_PROVIDER_SCHEMA = "neocortex.cosmic-ray-focal-mutation/v1"
COSMIC_RAY_MUTATION_REQUEST_SCHEMA = "neocortex.external-mutation-cosmic-ray-request/v1"
COSMIC_RAY_MUTATION_WORKER_SCHEMA = "neocortex.external-mutation-cosmic-ray-worker/v1"

_TOOL_VERSION = "8.4.6"
_MAX_MUTANTS = 100
_MAX_MUTANT_TIMEOUT_SECONDS = 120.0
_MAX_TIME_BUDGET_SECONDS = 900.0
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_STDERR_BYTES = 256 * 1024
_MAX_TEST_OUTPUT_BYTES = 256 * 1024
_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_MEMORY_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


class MutationAbstentionError(ValueError):
    """The requested trusted execution lacks enough evidence to run safely."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FocalMutationConfig:
    target_relative_path: str
    target_symbol: str | None
    test_selectors: tuple[str, ...]
    max_mutants: int
    mutant_timeout_seconds: float
    time_budget_seconds: float
    configuration_signature: str

    def __post_init__(self) -> None:
        target = _relative_path(self.target_relative_path, label="mutation target")
        if PurePosixPath(target).suffix.casefold() != ".py":
            raise ValueError("mutation target must be a Python file")
        object.__setattr__(self, "target_relative_path", target)
        if self.target_symbol is not None:
            if (
                not self.target_symbol
                or not self.target_symbol.isascii()
                or len(self.target_symbol) > 512
                or any(
                    part == "" or not part.isidentifier() for part in self.target_symbol.split(".")
                )
            ):
                raise ValueError("mutation target symbol is invalid")
        if not self.test_selectors:
            raise MutationAbstentionError("mutation_tests_not_declared")
        if tuple(sorted(set(self.test_selectors), key=str.casefold)) != self.test_selectors:
            raise ValueError("mutation test selectors must be unique and sorted")
        for selector in self.test_selectors:
            path = selector.split("::", 1)[0].replace("\\", "/")
            if selector.startswith("-") or len(selector) > 16_384:
                raise ValueError("mutation test selector is invalid")
            _relative_path(path, label="mutation test selector")
        if isinstance(self.max_mutants, bool) or not 1 <= self.max_mutants <= _MAX_MUTANTS:
            raise ValueError("mutation max_mutants must be within 1..100")
        if (
            isinstance(self.mutant_timeout_seconds, bool)
            or not 1 <= float(self.mutant_timeout_seconds) <= _MAX_MUTANT_TIMEOUT_SECONDS
        ):
            raise ValueError("mutation timeout must be within 1..120 seconds")
        if (
            isinstance(self.time_budget_seconds, bool)
            or not 10 <= float(self.time_budget_seconds) <= _MAX_TIME_BUDGET_SECONDS
        ):
            raise ValueError("mutation time budget must be within 10..900 seconds")
        if not self.configuration_signature or len(self.configuration_signature) > 512:
            raise ValueError("mutation configuration signature is invalid")
        object.__setattr__(self, "mutant_timeout_seconds", float(self.mutant_timeout_seconds))
        object.__setattr__(self, "time_budget_seconds", float(self.time_budget_seconds))


@dataclass(frozen=True, slots=True)
class FocalMutationExecution:
    findings: tuple[ExternalProviderFinding, ...]
    metrics: tuple[ExternalProviderMetric, ...]
    relations: tuple[ExternalProviderRelation, ...]
    stdout_bytes: int
    stderr_bytes: int
    process_invocations: int
    counters: Mapping[str, int]
    limitations: tuple[str, ...]
    measurement_scope_signature: str
    measurement_complete: bool


def cosmic_ray_tool_version() -> str | None:
    try:
        version = importlib.metadata.version("cosmic-ray")
    except importlib.metadata.PackageNotFoundError:
        return None
    return version if version == _TOOL_VERSION else None


def _relative_path(value: str, *, label: str) -> str:
    if not value or "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"{label} is invalid")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise ValueError(f"{label} is not a safe relative path")
    return path.as_posix()


def _canonical_repository_root() -> Path:
    return Path.home() / "Neocortex" / "Repository"


def _validate_trusted_root(root: Path) -> Path:
    try:
        observed = root.resolve(strict=True)
        expected = _canonical_repository_root().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MutationAbstentionError("mutation_trusted_root_not_canonical") from exc
    if not observed.is_dir() or os.path.normcase(os.path.abspath(observed)) != os.path.normcase(
        os.path.abspath(expected)
    ):
        raise MutationAbstentionError("mutation_trusted_root_not_canonical")
    return observed


def _owners_by_relative(
    staged: Mapping[str, ExternalEvidenceFile],
) -> dict[str, tuple[Path, ExternalEvidenceFile]]:
    owners: dict[str, tuple[Path, ExternalEvidenceFile]] = {}
    for staged_path, owner in staged.items():
        relative = _relative_path(owner.relative_path, label="mutation staged path")
        key = relative.casefold()
        if key in owners:
            raise ValueError("mutation staged path is duplicated")
        owners[key] = (Path(staged_path), owner)
    return owners


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _request_signature(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "mutation-request-v1:sha256:" + hashlib.sha256(encoded).hexdigest()


def mutation_input_signature(
    files: Sequence[ExternalEvidenceFile], config: FocalMutationConfig
) -> str:
    """Return a portable replay key including the exact focal suite and limits."""

    selected = sorted(
        (
            {
                "path": item.relative_path,
                "size": item.size,
                "xxh3_128": item.raw_xxh3_128,
                "xxh3_64_guard": item.raw_xxh3_64_guard,
            }
            for item in files
        ),
        key=lambda item: str(item["path"]).casefold(),
    )
    return external_signature(
        "cosmic-ray-mutation-input-v1",
        {
            "files": selected,
            "target": config.target_relative_path,
            "symbol": config.target_symbol,
            "test_selectors": list(config.test_selectors),
            "max_mutants": config.max_mutants,
            "mutant_timeout_seconds": config.mutant_timeout_seconds,
            "time_budget_seconds": config.time_budget_seconds,
            "configuration_signature": config.configuration_signature,
            "tool_version": _TOOL_VERSION,
        },
    )


def _required_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not an object")
    return value


def _required_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} is not a list")
    return value


def _required_int(value: object, *, label: str, maximum: int = 10_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{label} is invalid")
    return value


def _required_text(value: object, *, label: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} is invalid")
    return value


def _metric(
    owner: ExternalEvidenceFile,
    config: FocalMutationConfig,
    *,
    canonical_symbol: str | None,
    scope: str,
    name: str,
    value: float,
    unit: str,
    common_metadata: Mapping[str, object],
) -> ExternalProviderMetric:
    subject_kind: ExternalSubjectKind = "symbol" if canonical_symbol is not None else "file"
    subject_key = canonical_symbol or owner.relative_path
    return ExternalProviderMetric(
        external_metric_identity(
            COSMIC_RAY_MUTATION_PROVIDER_ID,
            subject_kind=subject_kind,
            subject_key=subject_key,
            category="mutation_testing",
            metric_name=name,
            unit=unit,
        ),
        subject_kind,
        subject_key,
        "mutation_testing",
        name,
        value,
        unit,
        version_id=owner.version_id,
        metadata={**common_metadata, "measurement_scope_signature": scope},
    )


def _finding(
    owner: ExternalEvidenceFile,
    raw: Mapping[str, object],
    *,
    code: str,
    message: str,
    scope: str,
) -> ExternalProviderFinding:
    line = _required_int(raw.get("start_line"), label="mutation start line")
    column = _required_int(raw.get("start_column"), label="mutation start column")
    end_line = _required_int(raw.get("end_line"), label="mutation end line")
    end_column = _required_int(raw.get("end_column"), label="mutation end column")
    mutation_id = _required_text(raw.get("mutation_id"), label="mutation id", maximum=128)
    metadata = {
        "provider_schema": COSMIC_RAY_MUTATION_PROVIDER_SCHEMA,
        "mutation_id": mutation_id,
        "operator": _required_text(raw.get("operator"), label="mutation operator", maximum=256),
        "occurrence": _required_int(raw.get("occurrence"), label="mutation occurrence"),
        "definition_name": raw.get("definition_name"),
        "duration_milliseconds": _required_int(
            raw.get("duration_milliseconds"), label="mutation duration"
        ),
        "output_sha256": _required_text(
            raw.get("output_sha256"), label="mutation output digest", maximum=128
        ),
        "measurement_scope_signature": scope,
        "mutation_authority": False,
    }
    identity = external_signature(
        "external-finding-v1",
        {
            "provider_id": COSMIC_RAY_MUTATION_PROVIDER_ID,
            "path": owner.relative_path,
            "category": "mutation_testing",
            "code": code,
            "mutation_id": mutation_id,
            "start_line": line,
            "start_column": column,
            "end_line": end_line,
            "end_column": end_column,
        },
    )
    return ExternalProviderFinding(
        identity,
        owner.version_id,
        owner.relative_path,
        "mutation_testing",
        code,
        "warning" if code == "MUTATION_SURVIVED" else "info",
        message,
        True,
        1.0,
        None,
        "advisory",
        max(1, line),
        column,
        max(max(1, line), end_line),
        end_column,
        metadata=metadata,
        mutation_authority=False,
    )


def _relations(
    owner: ExternalEvidenceFile,
    config: FocalMutationConfig,
    *,
    canonical_symbol: str | None,
    scope: str,
) -> tuple[ExternalProviderRelation, ...]:
    common = {
        "provider_schema": COSMIC_RAY_MUTATION_PROVIDER_SCHEMA,
        "target_relative_path": owner.relative_path,
        "target_symbol": canonical_symbol,
        "test_selectors": list(config.test_selectors),
        "measurement_scope_signature": scope,
        "mutation_authority": False,
    }
    result = [
        ExternalProviderRelation(
            external_relation_identity(
                COSMIC_RAY_MUTATION_PROVIDER_ID,
                relation_kind="mutation_targets_file",
                source_kind="run",
                source_key=scope,
                target_kind="file",
                target_key=owner.relative_path,
            ),
            "mutation_targets_file",
            "run",
            scope,
            "file",
            owner.relative_path,
            target_version_id=owner.version_id,
            metadata=common,
        )
    ]
    source_kind: ExternalSubjectKind = "file"
    source_key = owner.relative_path
    if canonical_symbol is not None:
        source_kind = "symbol"
        source_key = canonical_symbol
        result.append(
            ExternalProviderRelation(
                external_relation_identity(
                    COSMIC_RAY_MUTATION_PROVIDER_ID,
                    relation_kind="mutation_targets_symbol",
                    source_kind="run",
                    source_key=scope,
                    target_kind="symbol",
                    target_key=canonical_symbol,
                ),
                "mutation_targets_symbol",
                "run",
                scope,
                "symbol",
                canonical_symbol,
                metadata=common,
            )
        )
    selectors_by_path: dict[str, list[str]] = {}
    for selector in config.test_selectors:
        test_path = _relative_path(selector.split("::", 1)[0], label="mutation test path")
        selectors_by_path.setdefault(test_path, []).append(selector)
    for test_path, selectors in sorted(selectors_by_path.items()):
        result.append(
            ExternalProviderRelation(
                external_relation_identity(
                    COSMIC_RAY_MUTATION_PROVIDER_ID,
                    relation_kind="mutation_tested_by",
                    source_kind=source_kind,
                    source_key=source_key,
                    target_kind="file",
                    target_key=test_path,
                ),
                "mutation_tested_by",
                source_kind,
                source_key,
                "file",
                test_path,
                metadata={**common, "selectors": selectors},
            )
        )
    return tuple(sorted(result, key=lambda item: item.portable_relation_id))


def execute_cosmic_ray_mutation(
    stage_root: Path,
    staged: Mapping[str, ExternalEvidenceFile],
    environment: Mapping[str, str],
    *,
    trusted_root: Path,
    scratch_root: Path,
    config: FocalMutationConfig,
) -> FocalMutationExecution:
    """Execute one explicit target and suite in a disposable staged copy."""

    _validate_trusted_root(trusted_root)
    validate_external_inputs(tuple(staged.values()))
    owners = _owners_by_relative(staged)
    selected = owners.get(config.target_relative_path.casefold())
    if selected is None:
        raise MutationAbstentionError("mutation_target_not_indexed")
    target_path, owner = selected
    if not target_path.is_file():
        raise MutationAbstentionError("mutation_target_missing")
    project_root = (stage_root / "source").resolve(strict=True)
    scratch = scratch_root.resolve(strict=True)
    if os.path.commonpath(
        (
            os.path.normcase(os.path.abspath(project_root)),
            os.path.normcase(os.path.abspath(scratch)),
        )
    ) == os.path.normcase(os.path.abspath(project_root)):
        raise ValueError("mutation scratch cannot be inside staged project")
    scratch.mkdir(parents=True, exist_ok=True)
    manifest = []
    for _relative, (path, _item) in sorted(owners.items()):
        if not path.is_file():
            raise ValueError("mutation staged input is missing")
        manifest.append(
            {
                "relative_path": _item.relative_path,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    version = cosmic_ray_tool_version()
    if version is None:
        raise MutationAbstentionError("cosmic_ray_8_4_6_unavailable")
    try:
        pytest_version = importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError as exc:
        raise MutationAbstentionError("pytest_unavailable") from exc
    scope = mutation_input_signature(tuple(staged.values()), config)
    request: dict[str, object] = {
        "schema": COSMIC_RAY_MUTATION_REQUEST_SCHEMA,
        "project_root": str(project_root),
        "scratch_root": str(scratch),
        "target": config.target_relative_path,
        "symbol": config.target_symbol,
        "test_selectors": list(config.test_selectors),
        "configuration_signature": config.configuration_signature,
        "measurement_scope_signature": scope,
        "source_manifest": manifest,
        "tool_versions": {
            "cosmic-ray": version,
            "pytest": pytest_version,
            "python": sys.version.split()[0],
        },
        "limits": {
            "max_mutants": config.max_mutants,
            "mutant_timeout_seconds": config.mutant_timeout_seconds,
            "time_budget_seconds": config.time_budget_seconds,
            "max_output_bytes": _MAX_TEST_OUTPUT_BYTES,
        },
    }
    signature = _request_signature(request)
    request["request_signature"] = signature
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > _MAX_REQUEST_BYTES:
        raise ValueError("mutation request exceeds its bound")
    request_root = scratch / "mutation-requests"
    request_root.mkdir(parents=True, exist_ok=True)
    request_path = request_root / f"{signature.rsplit(':', 1)[-1]}.json"
    temporary = request_path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, request_path)
    worker = Path(__file__).with_name("external_mutation_cosmic_ray_worker.py").resolve(strict=True)
    started = time.monotonic()
    completed = run_bounded_capture(
        (sys.executable, "-I", str(worker), "--request", str(request_path)),
        timeout_seconds=config.time_budget_seconds + 15.0,
        stdout_limit_bytes=_MAX_OUTPUT_BYTES,
        stderr_limit_bytes=_MAX_STDERR_BYTES,
        cwd=scratch,
        environment=environment,
        memory_limit_bytes=_MEMORY_LIMIT_BYTES if os.name == "nt" else None,
    )
    try:
        payload = _required_mapping(
            json.loads(completed.stdout.decode("utf-8")), label="mutation worker"
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("mutation worker JSON is malformed") from exc
    if completed.returncode != 0 or payload.get("status") != "ready":
        error = _required_mapping(payload.get("error"), label="mutation worker error")
        code = _required_text(error.get("code"), label="mutation worker error code", maximum=128)
        if code == "time_budget_exhausted":
            raise subprocess.TimeoutExpired(("cosmic-ray",), config.time_budget_seconds)
        if code in {
            "baseline_failed",
            "baseline_timeout",
            "empty_mutation_selection",
            "symbol_not_found",
            "test_missing",
        }:
            raise MutationAbstentionError(f"mutation_{code}")
        raise ValueError(f"mutation_worker_exit:{completed.returncode}:{code}")
    if (
        payload.get("schema") != COSMIC_RAY_MUTATION_WORKER_SCHEMA
        or payload.get("request_signature") != signature
        or payload.get("measurement_scope_signature") != scope
    ):
        raise ValueError("mutation worker contract is incompatible")
    canonical_symbol_value = payload.get("canonical_symbol")
    canonical_symbol = None
    if canonical_symbol_value is not None:
        canonical_symbol = _required_text(
            canonical_symbol_value, label="mutation canonical symbol", maximum=512
        )
    raw_counts = _required_mapping(payload.get("counts"), label="mutation counts")
    counts = {
        name: _required_int(raw_counts.get(name), label=f"mutation {name}")
        for name in (
            "generated",
            "selected",
            "completed",
            "killed",
            "survived",
            "timed_out",
            "incompetent",
            "reused",
            "process_invocations",
        )
    }
    measurement_complete = bool(payload.get("measurement_complete"))
    selection_truncated = bool(payload.get("selection_truncated"))
    common = {
        "provider_schema": COSMIC_RAY_MUTATION_PROVIDER_SCHEMA,
        "target_relative_path": owner.relative_path,
        "target_symbol": canonical_symbol,
        "test_selectors": list(config.test_selectors),
        "selection_truncated": selection_truncated,
        "measurement_complete": measurement_complete,
        "baseline_passed": True,
        "mutation_authority": False,
    }
    metric_specs: list[tuple[str, float, str]] = [
        ("mutants_generated", counts["generated"], "count"),
        ("mutants_selected", counts["selected"], "count"),
        ("mutants_completed", counts["completed"], "count"),
        ("mutants_killed", counts["killed"], "count"),
        ("mutants_survived", counts["survived"], "count"),
        ("mutants_timed_out", counts["timed_out"], "count"),
        ("mutants_incompetent", counts["incompetent"], "count"),
        ("mutants_reused", counts["reused"], "count"),
        (
            "duration_milliseconds",
            _required_int(payload.get("duration_milliseconds"), label="mutation duration"),
            "milliseconds",
        ),
        (
            "baseline_duration_milliseconds",
            _required_int(
                payload.get("baseline_duration_milliseconds"), label="mutation baseline duration"
            ),
            "milliseconds",
        ),
        ("baseline_passed", 1, "boolean"),
        ("measurement_complete", int(measurement_complete), "boolean"),
    ]
    denominator = counts["killed"] + counts["survived"]
    limitations = [
        _required_text(item, label="mutation limitation", maximum=256)
        for item in _required_list(payload.get("limitations"), label="mutation limitations")
    ]
    if denominator:
        metric_specs.append(("mutation_score", counts["killed"] / denominator, "ratio"))
    else:
        limitations.append("mutation_score_undefined_no_killed_or_survived")
    metrics = tuple(
        _metric(
            owner,
            config,
            canonical_symbol=canonical_symbol,
            scope=scope,
            name=name,
            value=float(value),
            unit=unit,
            common_metadata=common,
        )
        for name, value, unit in metric_specs
    )
    findings = []
    for item in _required_list(payload.get("mutations"), label="mutation results"):
        raw = _required_mapping(item, label="mutation result")
        outcome = _required_text(raw.get("outcome"), label="mutation outcome", maximum=32)
        if outcome == "survived":
            findings.append(
                _finding(
                    owner,
                    raw,
                    code="MUTATION_SURVIVED",
                    message="Selected mutant survived the declared focal tests.",
                    scope=scope,
                )
            )
        elif outcome == "timeout":
            findings.append(
                _finding(
                    owner,
                    raw,
                    code="MUTATION_TIMEOUT",
                    message="Selected mutant exceeded its declared per-mutant timeout.",
                    scope=scope,
                )
            )
        elif outcome == "incompetent":
            findings.append(
                _finding(
                    owner,
                    raw,
                    code="MUTATION_INCOMPETENT",
                    message="Selected mutant could not produce a valid test outcome.",
                    scope=scope,
                )
            )
        elif outcome != "killed":
            raise ValueError("mutation worker emitted an unknown outcome")
    validate_external_inputs(tuple(staged.values()))
    if _sha256(target_path) != next(
        str(item["sha256"]) for item in manifest if item["relative_path"] == owner.relative_path
    ):
        raise ValueError("mutation staged target changed after execution")
    counters = {
        **{
            f"mutants_{key}": value for key, value in counts.items() if key != "process_invocations"
        },
        "process_invocations": 1 + counts["process_invocations"],
        "measurement_complete": int(measurement_complete),
        "selection_truncated": int(selection_truncated),
        "source_hashes_verified": len(manifest),
        "wall_milliseconds": int((time.monotonic() - started) * 1000),
    }
    return FocalMutationExecution(
        tuple(sorted(findings, key=lambda item: item.portable_finding_id)),
        tuple(sorted(metrics, key=lambda item: item.portable_metric_id)),
        _relations(owner, config, canonical_symbol=canonical_symbol, scope=scope),
        len(completed.stdout),
        len(completed.stderr),
        counters["process_invocations"],
        counters,
        tuple(sorted({str(item) for item in limitations})),
        scope,
        measurement_complete,
    )


__all__ = [
    "COSMIC_RAY_MUTATION_PROVIDER_ID",
    "COSMIC_RAY_MUTATION_PROVIDER_SCHEMA",
    "COSMIC_RAY_MUTATION_REQUEST_SCHEMA",
    "COSMIC_RAY_MUTATION_WORKER_SCHEMA",
    "FocalMutationConfig",
    "FocalMutationExecution",
    "MutationAbstentionError",
    "cosmic_ray_tool_version",
    "execute_cosmic_ray_mutation",
    "mutation_input_signature",
]
