"""Real bounded providers for protected, trusted-static and trusted-deep analysis."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .bounded_subprocess import SubprocessOutputLimitError, run_bounded_capture
from .code_architecture_contracts import (
    ARCHITECTURE_BASELINE_ID,
    ARCHITECTURE_CONTRACT_SCHEMA,
    PRODUCTION_ROOT_PACKAGES,
)
from .code_external_evidence import (
    RUFF_CONFIGURATION_SIGNATURE,
    RUFF_MAX_DIAGNOSTICS,
    RUFF_MAX_TOTAL_BYTES,
    ExternalEvidenceFile,
    ExternalEvidencePublication,
    ExternalRunStatus,
    RuffEvidenceProvider,
    _controlled_environment,
    _parse_diagnostics,
    _stage_external_inputs,
    _validated_staging_parent,
    external_input_signature,
    validate_external_inputs,
)
from .code_contracts import (
    deep_configuration_payload as build_deep_configuration_payload,
    deep_configuration_signature as calculate_deep_configuration_signature,
)
from .external_deep_coverage import (
    DEEP_COVERAGE_PROVIDER_SCHEMA,
    PYTEST_COVERAGE_PROVIDER_ID,
    DeepCoverageConfig,
    DeepCoverageExecution,
    DeepCoveragePreparedInput,
    execute_pytest_coverage,
    prepare_deep_coverage_input,
    trusted_deep_home_directory,
)
from .external_evidence_models import (
    AnalysisProfile,
    ExternalEvidenceProvider,
    ExternalProviderBaseline,
    ExternalProviderFinding,
    ExternalProviderMetric,
    ExternalProviderPublication,
    ExternalProviderRelation,
    ExternalRunInput,
    InvalidationStrategy,
    ProviderDescriptor,
    ProviderLimits,
    ProviderTrust,
    external_provider_result_digest,
    external_root_identity,
    external_signature,
)
from .external_architecture_providers import (
    ArchitectureProviderExecution,
    execute_complexipy_cognitive,
    execute_grimp_architecture,
    execute_ruff_analyze_imports,
)
from .external_unused_vulture import (
    VULTURE_UNUSED_PROVIDER_ID,
    VULTURE_UNUSED_PROVIDER_SCHEMA,
    VultureUnusedExecution,
    execute_vulture_unused,
)
from .semantic_models import fingerprint_bytes

RUFF_PROTECTED_PROVIDER_ID = "ruff-protected-basic"
RUFF_TRUSTED_PROVIDER_ID = "ruff-trusted-project"
MYPY_PROVIDER_ID = "mypy-trusted-project"
PYRIGHT_PROVIDER_ID = "pyright-trusted-project"
RUFF_ANALYZE_PROVIDER_ID = "ruff-analyze-imports"
GRIMP_ARCHITECTURE_PROVIDER_ID = "grimp-architecture"
COMPLEXIPY_COGNITIVE_PROVIDER_ID = "complexipy-cognitive"

_CONFIG_LIMIT_BYTES = 1024 * 1024
_STDOUT_LIMIT_BYTES = 8 * 1024 * 1024
_STDERR_LIMIT_BYTES = 128 * 1024
_TOOL_TIMEOUT_SECONDS = 180.0
_MYPY_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_PYRIGHT_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_RUFF_MEMORY_BYTES = 512 * 1024 * 1024
_GRIMP_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_COMPLEXIPY_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_VULTURE_MEMORY_BYTES = 512 * 1024 * 1024
_DEEP_COVERAGE_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
_DEEP_COVERAGE_OUTPUT_BYTES = 32 * 1024 * 1024
_DEEP_COVERAGE_FINDING_BOUND = 2_000
_VULTURE_LIMITATIONS = (
    "vulture_confidence_below_100_is_heuristic",
    "static_name_analysis_cannot_prove_runtime_unused",
    "decorators_callbacks_registries_reexports_and_dynamic_access_require_correlation",
    "advisory_only_no_mutation_authority",
)


def _unexpected_exit_message(
    tool_name: str,
    completed: subprocess.CompletedProcess[bytes],
) -> str:
    """Return one bounded single-line explanation for a non-contractual exit."""

    raw = completed.stderr or completed.stdout
    detail = " ".join(raw.decode("utf-8", errors="replace").split())[:2048]
    prefix = f"{tool_name}_unexpected_exit:{completed.returncode}"
    return prefix if not detail else f"{prefix}:{detail}"


def _package_version(name: str) -> str | None:
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    return version if version and len(version.encode("utf-8")) <= 256 else None


def _deep_tool_version() -> str | None:
    pytest_version = _package_version("pytest")
    coverage_version = _package_version("coverage")
    if pytest_version is None or coverage_version is None:
        return None
    value = f"pytest={pytest_version};coverage={coverage_version}"
    return value if len(value.encode("utf-8")) <= 256 else None


def _validated_deep_configuration(
    payload: Mapping[str, object] | None,
    signature: str | None,
) -> tuple[dict[str, object], DeepCoverageConfig]:
    if payload is None or signature is None:
        raise ValueError("trusted-deep requires its exact configuration and signature")
    selectors_value = payload.get("test_selectors")
    max_tests = payload.get("max_tests")
    time_budget_seconds = payload.get("time_budget_seconds")
    shard_size = payload.get("shard_size")
    if (
        not isinstance(selectors_value, list)
        or any(not isinstance(item, str) for item in selectors_value)
        or isinstance(max_tests, bool)
        or not isinstance(max_tests, int)
        or isinstance(time_budget_seconds, bool)
        or not isinstance(time_budget_seconds, int)
        or isinstance(shard_size, bool)
        or not isinstance(shard_size, int)
    ):
        raise ValueError("trusted-deep configuration types are invalid")
    selectors = tuple(item for item in selectors_value if isinstance(item, str))
    normalized = build_deep_configuration_payload(
        analysis_profile=str(payload.get("analysis_profile")),
        test_selectors=selectors,
        max_tests=max_tests,
        time_budget_seconds=time_budget_seconds,
        shard_size=shard_size,
    )
    if normalized != dict(payload) or normalized["analysis_profile"] != "trusted-deep":
        raise ValueError("trusted-deep configuration is not canonical")
    observed_signature = calculate_deep_configuration_signature(normalized)
    if observed_signature != signature:
        raise ValueError("trusted-deep configuration signature disagrees")
    return normalized, DeepCoverageConfig(
        selectors,
        max_tests,
        float(time_budget_seconds),
        shard_size,
        signature,
    )


def _read_exact_config(path: Path) -> bytes:
    metadata = os.lstat(path)
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if path.is_symlink() or attributes & reparse or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("trusted project configuration is not a regular file")
    if metadata.st_size > _CONFIG_LIMIT_BYTES:
        raise ValueError("trusted project configuration exceeds its bound")
    raw = path.read_bytes()
    after = os.lstat(path)
    if (
        len(raw) != metadata.st_size
        or after.st_size != metadata.st_size
        or after.st_mtime_ns != metadata.st_mtime_ns
    ):
        raise ValueError("trusted project configuration changed during read")
    return raw


def _project_configuration(root: Path) -> tuple[bytes, Mapping[str, object], str]:
    path = root / "pyproject.toml"
    raw = _read_exact_config(path)
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("trusted pyproject.toml is malformed") from exc
    if not isinstance(parsed, dict):
        raise ValueError("trusted pyproject.toml must be a table")
    digest = "pyproject-v1:xxh3_128:" + fingerprint_bytes(raw).xxh3_128
    return raw, parsed, digest


def _environment_signature(
    *,
    tool_name: str,
    tool_version: str,
    node_path: str | None = None,
    home_directory: str | None = None,
    path_value: str | None = None,
    pathext_value: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "python_executable": os.path.normcase(os.path.abspath(sys.executable)),
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "tool_name": tool_name,
        "tool_version": tool_version,
        "node_path": node_path,
    }
    if home_directory is not None:
        payload["home_directory"] = os.path.normcase(os.path.abspath(home_directory))
    if path_value is not None:
        payload["path"] = path_value
    if pathext_value is not None:
        payload["pathext"] = pathext_value
    return external_signature("external-environment-v1", payload)


def _limits(*, memory: int) -> ProviderLimits:
    return ProviderLimits(
        _TOOL_TIMEOUT_SECONDS,
        memory,
        RUFF_MAX_TOTAL_BYTES,
        _STDOUT_LIMIT_BYTES,
        RUFF_MAX_DIAGNOSTICS,
    )


def _provider_descriptor(
    *,
    provider_id: str,
    provider_schema: str,
    tool_name: str,
    tool_version: str,
    profile: AnalysisProfile,
    source: str,
    configuration_payload: Mapping[str, object],
    project_configuration_digest: str | None,
    environment_signature: str,
    root_identity: str,
    execution_strategy: str,
    invalidation_strategy: InvalidationStrategy,
    memory: int,
    loads_project_configuration: bool,
    scope: str = "current-inventory-python",
    limits: ProviderLimits | None = None,
    loads_plugins: bool = False,
    imports_content: bool = False,
    executes_content: bool = False,
    uses_network: bool = False,
) -> ProviderDescriptor:
    trust_requirement: ProviderTrust
    if profile == "protected":
        trust_requirement = "untrusted-safe"
    elif profile == "trusted-static":
        trust_requirement = "trusted-static"
    else:
        trust_requirement = "trusted-execution"
    configuration_signature = external_signature(
        f"{provider_id}-configuration-v1", configuration_payload
    )
    comparability_signature = external_signature(
        f"{provider_id}-comparable-v1",
        {
            "provider_id": provider_id,
            "provider_schema": provider_schema,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "profile": profile,
            "configuration_signature": configuration_signature,
            "project_configuration_digest": project_configuration_digest,
            "environment_signature": environment_signature,
            "root_identity": root_identity,
            "scope": scope,
            "execution_strategy": execution_strategy,
        },
    )
    return ProviderDescriptor(
        provider_id,
        provider_schema,
        tool_name,
        profile,
        trust_requirement,
        scope,
        source,
        configuration_signature,
        project_configuration_digest,
        environment_signature,
        comparability_signature,
        execution_strategy,
        invalidation_strategy,
        "exact-publication-replay-v1",
        _limits(memory=memory) if limits is None else limits,
        loads_project_configuration=loads_project_configuration,
        loads_plugins=loads_plugins,
        imports_content=imports_content,
        executes_content=executes_content,
        uses_network=uses_network,
    )


def _input_records(
    files: Sequence[ExternalEvidenceFile],
    *,
    covered: bool,
    reason: str | None = None,
) -> tuple[ExternalRunInput, ...]:
    return tuple(
        ExternalRunInput.from_file(item, covered=covered, coverage_reason=reason) for item in files
    )


def _portable_publication_id(
    descriptor: ProviderDescriptor,
    *,
    input_signature: str,
    result_digest: str | None,
) -> str:
    return external_signature(
        "external-publication-v1",
        {
            "provider_id": descriptor.provider_id,
            "provider_schema": descriptor.provider_schema,
            "profile": descriptor.profile,
            "configuration_signature": descriptor.configuration_signature,
            "environment_signature": descriptor.environment_signature,
            "input_signature": input_signature,
            "result_digest": result_digest,
        },
    )


def _result_counters(
    files: Sequence[ExternalEvidenceFile],
    findings: Sequence[ExternalProviderFinding],
    metrics: Sequence[ExternalProviderMetric],
    relations: Sequence[ExternalProviderRelation],
    baseline: ExternalProviderBaseline | None,
    *,
    wall_milliseconds: int,
    stdout_bytes: int,
    stderr_bytes: int,
    process_invocations: int,
    bytes_read: int,
    bytes_staged: int,
) -> dict[str, int]:
    current_ids = {item.portable_finding_id for item in findings}
    baseline_ids = set(() if baseline is None else baseline.portable_finding_ids)
    current_metric_ids = {item.portable_metric_id for item in metrics}
    baseline_metric_ids = set(() if baseline is None else baseline.portable_metric_ids)
    current_relation_ids = {item.portable_relation_id for item in relations}
    baseline_relation_ids = set(() if baseline is None else baseline.portable_relation_ids)
    counters = {
        "eligible_files": len(files),
        "covered_files": len(files),
        "files_verified": len(files),
        "bytes_verified": sum(item.size for item in files),
        "bytes_read": bytes_read,
        "bytes_staged": bytes_staged,
        "process_invocations": process_invocations,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "wall_milliseconds": max(0, wall_milliseconds),
        "findings": len(findings),
        "metrics": len(metrics),
        "relations": len(relations),
        "comparable": int(baseline is not None),
        "cache_hits": 0,
        "cache_misses": 1,
        "errors": 0,
        "timeouts": 0,
        "skipped": 0,
        "unavailable": 0,
    }
    if baseline is not None:
        counters["added"] = len(current_ids - baseline_ids)
        counters["resolved"] = len(baseline_ids - current_ids)
        counters["metrics_added"] = len(current_metric_ids - baseline_metric_ids)
        counters["metrics_resolved"] = len(baseline_metric_ids - current_metric_ids)
        counters["relations_added"] = len(current_relation_ids - baseline_relation_ids)
        counters["relations_resolved"] = len(baseline_relation_ids - current_relation_ids)
    return counters


def _provenance(
    descriptor: ProviderDescriptor,
    root: Path,
    input_signature: str,
    *,
    execution: str,
    result_digest: str | None,
    findings: int,
    metrics: int = 0,
    relations: int = 0,
    reason: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "neocortex.external-code-evidence/v2",
        "provider": descriptor.as_payload(),
        "root": str(root),
        "execution": execution,
        "input": {"signature": input_signature},
        "result": None
        if result_digest is None
        else {
            "digest": result_digest,
            "findings": findings,
            "metrics": metrics,
            "relations": relations,
        },
        "authority": "advisory",
        "mutation_authority": False,
        "content_executed": descriptor.executes_content,
    }
    if reason is not None:
        payload["error"] = {"reason": reason}
    return payload


def _failure(
    descriptor: ProviderDescriptor,
    root: Path,
    files: Sequence[ExternalEvidenceFile],
    *,
    tool_version: str,
    status: ExternalRunStatus,
    reason: str,
    started_ns: int,
) -> ExternalProviderPublication:
    input_signature = external_input_signature(files)
    execution = "unavailable" if status == "unavailable" else "attempted"
    inner = ExternalEvidencePublication(
        descriptor.tool_name,
        tool_version,
        descriptor.configuration_signature,
        status,
        started_ns,
        time.time_ns(),
        _provenance(
            descriptor,
            root,
            input_signature,
            execution=execution,
            result_digest=None,
            findings=0,
            reason=reason,
        ),
    )
    counters = {
        "eligible_files": len(files),
        "covered_files": 0,
        "files_verified": 0,
        "bytes_verified": 0,
        "bytes_read": 0,
        "bytes_staged": 0,
        "process_invocations": int(status != "unavailable"),
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "wall_milliseconds": max(0, (time.time_ns() - started_ns) // 1_000_000),
        "findings": 0,
        "metrics": 0,
        "relations": 0,
        "comparable": 0,
        "cache_hits": 0,
        "cache_misses": 1,
        "errors": int(status == "failed"),
        "timeouts": int(status == "timeout"),
        "skipped": 0,
        "unavailable": int(status == "unavailable"),
    }
    return ExternalProviderPublication(
        descriptor,
        inner,
        str(root),
        external_root_identity(root),
        input_signature,
        _input_records(files, covered=False, reason=reason),
        (),
        counters,
        False,
        None,
        _portable_publication_id(descriptor, input_signature=input_signature, result_digest=None),
        limitations=(reason,),
    )


def _exact_replay(
    descriptor: ProviderDescriptor,
    root: Path,
    files: Sequence[ExternalEvidenceFile],
    baseline: ExternalProviderBaseline,
    *,
    limitations: Sequence[str] = (),
    input_signature_override: str | None = None,
) -> ExternalProviderPublication:
    started_ns = time.time_ns()
    validate_external_inputs(files)
    input_signature = (
        external_input_signature(files)
        if input_signature_override is None
        else input_signature_override
    )
    if not input_signature or len(input_signature.encode("utf-8")) > 512:
        raise ValueError("external provider input signature is invalid")
    if baseline.input_signature != input_signature:
        raise ValueError("external provider replay input signature is not exact")
    counters = {
        "eligible_files": len(files),
        "covered_files": len(files),
        "files_verified": len(files),
        "bytes_verified": sum(item.size for item in files),
        "bytes_read": sum(item.size for item in files),
        "bytes_staged": 0,
        "process_invocations": 0,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "wall_milliseconds": max(0, (time.time_ns() - started_ns) // 1_000_000),
        "findings": len(baseline.portable_finding_ids),
        "metrics": len(baseline.portable_metric_ids),
        "relations": len(baseline.portable_relation_ids),
        "comparable": 1,
        "added": 0,
        "resolved": 0,
        "metrics_added": 0,
        "metrics_resolved": 0,
        "relations_added": 0,
        "relations_resolved": 0,
        "cache_hits": 1,
        "cache_misses": 0,
        "errors": 0,
        "timeouts": 0,
        "skipped": 1,
        "unavailable": 0,
    }
    inner = ExternalEvidencePublication(
        descriptor.tool_name,
        baseline.tool_version,
        descriptor.configuration_signature,
        "skipped",
        started_ns,
        time.time_ns(),
        _provenance(
            descriptor,
            root,
            input_signature,
            execution="cache_replay",
            result_digest=baseline.result_digest,
            findings=len(baseline.portable_finding_ids),
            metrics=len(baseline.portable_metric_ids),
            relations=len(baseline.portable_relation_ids),
        ),
    )
    verification = external_signature(
        "external-replay-verification-v1",
        {
            "provider_id": descriptor.provider_id,
            "source_tool_run_id": baseline.tool_run_id,
            "input_signature": input_signature,
            "files_verified": len(files),
            "bytes_verified": sum(item.size for item in files),
        },
    )
    return ExternalProviderPublication(
        descriptor,
        inner,
        str(root),
        external_root_identity(root),
        input_signature,
        _input_records(files, covered=True),
        (),
        counters,
        True,
        baseline.result_digest,
        _portable_publication_id(
            descriptor,
            input_signature=input_signature,
            result_digest=baseline.result_digest,
        ),
        baseline.tool_run_id,
        verification,
        tuple(limitations),
    )


def _success(
    descriptor: ProviderDescriptor,
    root: Path,
    files: Sequence[ExternalEvidenceFile],
    findings: Sequence[ExternalProviderFinding],
    baseline: ExternalProviderBaseline | None,
    *,
    metrics: Sequence[ExternalProviderMetric] = (),
    relations: Sequence[ExternalProviderRelation] = (),
    tool_version: str,
    started_ns: int,
    stdout_bytes: int,
    stderr_bytes: int,
    process_invocations: int,
    bytes_staged: int,
    limitations: Sequence[str] = (),
    input_signature_override: str | None = None,
) -> ExternalProviderPublication:
    ordered_findings = tuple(sorted(findings, key=lambda item: item.portable_finding_id))
    if len({item.portable_finding_id for item in ordered_findings}) != len(ordered_findings):
        raise ValueError("external provider produced duplicate finding identities")
    ordered_metrics = tuple(sorted(metrics, key=lambda item: item.portable_metric_id))
    if len({item.portable_metric_id for item in ordered_metrics}) != len(ordered_metrics):
        raise ValueError("external provider produced duplicate metric identities")
    ordered_relations = tuple(sorted(relations, key=lambda item: item.portable_relation_id))
    if len({item.portable_relation_id for item in ordered_relations}) != len(ordered_relations):
        raise ValueError("external provider produced duplicate relation identities")
    validate_external_inputs(files)
    result_digest = external_provider_result_digest(
        ordered_findings,
        ordered_metrics,
        ordered_relations,
    )
    input_signature = (
        external_input_signature(files)
        if input_signature_override is None
        else input_signature_override
    )
    if not input_signature or len(input_signature.encode("utf-8")) > 512:
        raise ValueError("external provider input signature is invalid")
    counters = _result_counters(
        files,
        ordered_findings,
        ordered_metrics,
        ordered_relations,
        baseline,
        wall_milliseconds=(time.time_ns() - started_ns) // 1_000_000,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        process_invocations=process_invocations,
        bytes_read=sum(item.size for item in files),
        bytes_staged=bytes_staged,
    )
    inner = ExternalEvidencePublication(
        descriptor.tool_name,
        tool_version,
        descriptor.configuration_signature,
        "completed",
        started_ns,
        time.time_ns(),
        _provenance(
            descriptor,
            root,
            input_signature,
            execution="full",
            result_digest=result_digest,
            findings=len(ordered_findings),
            metrics=len(ordered_metrics),
            relations=len(ordered_relations),
        ),
    )
    return ExternalProviderPublication(
        descriptor,
        inner,
        str(root),
        external_root_identity(root),
        input_signature,
        _input_records(files, covered=True),
        ordered_findings,
        counters,
        True,
        result_digest,
        _portable_publication_id(
            descriptor,
            input_signature=input_signature,
            result_digest=result_digest,
        ),
        limitations=tuple(limitations),
        metrics=ordered_metrics,
        relations=ordered_relations,
    )


def _finding(
    *,
    provider_id: str,
    owner: ExternalEvidenceFile,
    category: str,
    code: str,
    severity: str,
    message: str,
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
    metadata: Mapping[str, object] | None = None,
    url: str | None = None,
    fix_available: bool = False,
) -> ExternalProviderFinding:
    identity = external_signature(
        "external-finding-v1",
        {
            "provider_id": provider_id,
            "path": owner.relative_path,
            "category": category,
            "code": code,
            "message": message,
            "start_line": start_line,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
        },
    )
    return ExternalProviderFinding(
        identity,
        owner.version_id,
        owner.relative_path,
        category,
        code,
        severity,
        message,
        True,
        1.0,
        None,
        "advisory",
        start_line,
        start_column,
        end_line,
        end_column,
        url,
        fix_available,
        {} if metadata is None else metadata,
    )


def _normalized_severity(value: object) -> str:
    observed = str(value or "info").casefold()
    if observed == "error":
        return "error"
    if observed in {"warning", "warn"}:
        return "warning"
    return "info"


class RuffProtectedBasicProvider:
    """Compatibility-backed protected Ruff policy with normalized ownership."""

    def __init__(self, root: Path):
        version = _package_version("ruff") or "unavailable"
        environment = _environment_signature(tool_name="ruff", tool_version=version)
        self.descriptor = _provider_descriptor(
            provider_id=RUFF_PROTECTED_PROVIDER_ID,
            provider_schema="neocortex.ruff-protected-basic/v1",
            tool_name="ruff",
            tool_version=version,
            profile="protected",
            source="external:ruff-protected-basic",
            configuration_payload={
                "legacy_configuration_signature": RUFF_CONFIGURATION_SIGNATURE,
                "rules": "E4,E7,E9,F",
                "isolated": True,
                "target": "py313",
                "fixes": False,
            },
            project_configuration_digest=None,
            environment_signature=environment,
            root_identity=external_root_identity(root),
            execution_strategy="verified-staged-batches-v1",
            invalidation_strategy="project_wide",
            memory=_RUFF_MEMORY_BYTES,
            loads_project_configuration=False,
        )
        self._version = None if version == "unavailable" else version

    def tool_version(self) -> str | None:
        return self._version

    def normalize_legacy(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        publication: ExternalEvidencePublication,
        *,
        baseline: ExternalProviderBaseline | None,
    ) -> ExternalProviderPublication:
        """Normalize the already-executed legacy Ruff result without rerunning it."""

        if publication.execution == "cache_replay":
            if baseline is None:
                raise ValueError("normalized Ruff replay has no normalized source")
            return _exact_replay(self.descriptor, root, files, baseline)
        if publication.status != "completed":
            reason = "ruff_protected_failed"
            error = publication.provenance.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("reason"), str):
                reason = str(error["reason"])
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=publication.tool_version,
                status=publication.status,
                reason=reason,
                started_ns=publication.started_ns,
            )
        findings = tuple(
            ExternalProviderFinding.from_diagnostic(item, category="correctness")
            for item in publication.diagnostics
        )
        return _success(
            self.descriptor,
            root,
            files,
            findings,
            baseline,
            tool_version=publication.tool_version,
            started_ns=publication.started_ns,
            stdout_bytes=0,
            stderr_bytes=0,
            process_invocations=max(1, (len(files) + 49) // 50),
            bytes_staged=sum(item.size for item in files),
        )

    def run(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        *,
        baseline: ExternalProviderBaseline | None,
        scratch_root: Path,
    ) -> ExternalProviderPublication:
        if baseline is not None and baseline.input_signature == external_input_signature(files):
            return _exact_replay(self.descriptor, root, files, baseline)
        started_ns = time.time_ns()
        if self._version is None:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version="unavailable",
                status="unavailable",
                reason="ruff_distribution_missing",
                started_ns=started_ns,
            )
        legacy = RuffEvidenceProvider().run(
            root,
            files,
            baseline=None,
            scratch_root=scratch_root,
        )
        if legacy.status != "completed":
            reason = "ruff_protected_failed"
            error = legacy.provenance.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("reason"), str):
                reason = str(error["reason"])
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=self._version,
                status=legacy.status,
                reason=reason,
                started_ns=started_ns,
            )
        findings = tuple(
            ExternalProviderFinding.from_diagnostic(item, category="correctness")
            for item in legacy.diagnostics
        )
        return _success(
            self.descriptor,
            root,
            files,
            findings,
            baseline,
            tool_version=self._version,
            started_ns=started_ns,
            stdout_bytes=0,
            stderr_bytes=0,
            process_invocations=max(1, (len(files) + 49) // 50),
            bytes_staged=sum(item.size for item in files),
        )


class _TrustedStaticProvider:
    provider_id: str
    provider_schema: str
    tool_name: str
    source: str
    memory_bound: int
    execution_strategy: str

    def __init__(self, root: Path):
        self.root = root
        self._config_error: str | None = None
        try:
            self._config_raw, self._config, config_digest = _project_configuration(root)
            self._validate_configuration(self._config)
        except (OSError, ValueError) as exc:
            self._config_raw = b""
            self._config = {}
            config_digest = None
            self._config_error = f"{type(exc).__name__}:{exc}"
        self._version = self._resolve_version()
        version = self._version or "unavailable"
        node_path = self._node_path_for_signature()
        environment = _environment_signature(
            tool_name=self.tool_name,
            tool_version=version,
            node_path=node_path,
        )
        configuration_payload = self._configuration_payload()
        self.descriptor = _provider_descriptor(
            provider_id=self.provider_id,
            provider_schema=self.provider_schema,
            tool_name=self.tool_name,
            tool_version=version,
            profile="trusted-static",
            source=self.source,
            configuration_payload=configuration_payload,
            project_configuration_digest=config_digest,
            environment_signature=environment,
            root_identity=external_root_identity(root),
            execution_strategy=self.execution_strategy,
            invalidation_strategy="project_wide",
            memory=self.memory_bound,
            loads_project_configuration=True,
        )

    def _resolve_version(self) -> str | None:
        raise NotImplementedError

    def _node_path_for_signature(self) -> str | None:
        return None

    def _validate_configuration(self, config: Mapping[str, object]) -> None:
        raise NotImplementedError

    def _configuration_payload(self) -> Mapping[str, object]:
        raise NotImplementedError

    def _execute(
        self,
        stage_root: Path,
        staged: Mapping[str, ExternalEvidenceFile],
        config_path: Path,
        environment: Mapping[str, str],
    ) -> tuple[tuple[ExternalProviderFinding, ...], int, int, int]:
        raise NotImplementedError

    def _limitations(self) -> tuple[str, ...]:
        return ()

    def tool_version(self) -> str | None:
        return self._version

    def run(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        *,
        baseline: ExternalProviderBaseline | None,
        scratch_root: Path,
    ) -> ExternalProviderPublication:
        if baseline is not None and baseline.input_signature == external_input_signature(files):
            return _exact_replay(
                self.descriptor,
                root,
                files,
                baseline,
                limitations=self._limitations(),
            )
        started_ns = time.time_ns()
        if self._config_error is not None:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=self._version or "unavailable",
                status="failed",
                reason=f"project_configuration_invalid:{self._config_error}"[:4096],
                started_ns=started_ns,
            )
        if self._version is None:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version="unavailable",
                status="unavailable",
                reason=f"{self.tool_name}_unavailable",
                started_ns=started_ns,
            )
        try:
            staging_parent = _validated_staging_parent(root, scratch_root)
            with tempfile.TemporaryDirectory(
                prefix=f"neocortex-{self.provider_id}-", dir=staging_parent
            ) as temporary:
                stage_root = Path(temporary)
                staged = _stage_external_inputs(files, stage_root / "source")
                config_path = stage_root / "pyproject.toml"
                config_path.write_bytes(self._config_raw)
                environment = _controlled_environment()
                for name in ("TEMP", "TMP", "TMPDIR"):
                    environment[name] = str(stage_root)
                findings, stdout_bytes, stderr_bytes, invocations = self._execute(
                    stage_root, staged, config_path, environment
                )
        except subprocess.TimeoutExpired:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=self._version,
                status="timeout",
                reason="provider_timeout",
                started_ns=started_ns,
            )
        except (OSError, RuntimeError, TypeError, ValueError, SubprocessOutputLimitError) as exc:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=self._version,
                status="failed",
                reason=f"provider_failure:{type(exc).__name__}:{exc}"[:4096],
                started_ns=started_ns,
            )
        return _success(
            self.descriptor,
            root,
            files,
            findings,
            baseline,
            tool_version=self._version,
            started_ns=started_ns,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            process_invocations=invocations,
            bytes_staged=sum(item.size for item in files) + len(self._config_raw),
            limitations=self._limitations(),
        )


class RuffTrustedProjectProvider(_TrustedStaticProvider):
    provider_id = RUFF_TRUSTED_PROVIDER_ID
    provider_schema = "neocortex.ruff-trusted-project/v1"
    tool_name = "ruff"
    source = "external:ruff-trusted-project"
    memory_bound = _RUFF_MEMORY_BYTES
    execution_strategy = "trusted-config-staged-batches-v1"

    def _resolve_version(self) -> str | None:
        return _package_version("ruff")

    def _validate_configuration(self, config: Mapping[str, object]) -> None:
        tool = config.get("tool")
        ruff = tool.get("ruff") if isinstance(tool, Mapping) else None
        if not isinstance(ruff, Mapping):
            raise ValueError("tool.ruff is missing")
        if ruff.get("extend") not in (None, ""):
            raise ValueError("ruff extend is not authorized in trusted-static")

    def _configuration_payload(self) -> Mapping[str, object]:
        return {
            "adapter": self.provider_schema,
            "configuration": "project-pyproject",
            "target": "py313",
            "fixes": False,
            "cache": False,
            "output": "json",
        }

    def _execute(
        self,
        stage_root: Path,
        staged: Mapping[str, ExternalEvidenceFile],
        config_path: Path,
        environment: Mapping[str, str],
    ) -> tuple[tuple[ExternalProviderFinding, ...], int, int, int]:
        paths = tuple(staged)
        outputs: list[bytes] = []
        stderr_bytes = 0
        stdout_bytes = 0
        invocations = 0
        for offset in range(0, len(paths), 50):
            batch = paths[offset : offset + 50]
            command = (
                sys.executable,
                "-I",
                "-m",
                "ruff",
                "check",
                "--config",
                str(config_path),
                "--output-format",
                "json",
                "--no-cache",
                "--no-fix",
                "--no-fix-only",
                "--no-unsafe-fixes",
                "--no-show-fixes",
                "--color",
                "never",
                *batch,
            )
            completed = run_bounded_capture(
                command,
                timeout_seconds=_TOOL_TIMEOUT_SECONDS,
                stdout_limit_bytes=max(0, _STDOUT_LIMIT_BYTES - stdout_bytes),
                stderr_limit_bytes=max(0, _STDERR_LIMIT_BYTES - stderr_bytes),
                cwd=stage_root,
                environment=environment,
                memory_limit_bytes=_RUFF_MEMORY_BYTES if os.name == "nt" else None,
            )
            invocations += 1
            if completed.returncode not in {0, 1}:
                raise ValueError(_unexpected_exit_message("ruff", completed))
            outputs.append(completed.stdout)
            stdout_bytes += len(completed.stdout)
            stderr_bytes += len(completed.stderr)
        diagnostics = tuple(
            item for output in outputs for item in _parse_diagnostics(output, staged)
        )
        findings = tuple(
            ExternalProviderFinding.from_diagnostic(item, category="correctness")
            for item in diagnostics
        )
        return findings, stdout_bytes, stderr_bytes, invocations


class MypyTrustedProjectProvider(_TrustedStaticProvider):
    provider_id = MYPY_PROVIDER_ID
    provider_schema = "neocortex.mypy-trusted-project/v1"
    tool_name = "mypy"
    source = "external:mypy"
    memory_bound = _MYPY_MEMORY_BYTES
    execution_strategy = "trusted-config-staged-project-v1"

    def _resolve_version(self) -> str | None:
        return _package_version("mypy")

    def _limitations(self) -> tuple[str, ...]:
        return ("unresolved_third_party_imports_are_treated_as_any",)

    def _validate_configuration(self, config: Mapping[str, object]) -> None:
        tool = config.get("tool")
        mypy = tool.get("mypy") if isinstance(tool, Mapping) else None
        if not isinstance(mypy, Mapping):
            raise ValueError("tool.mypy is missing")
        if mypy.get("plugins") not in (None, (), []):
            raise ValueError("mypy plugins are not authorized in trusted-static")
        if mypy.get("mypy_path") not in (None, "", (), []):
            raise ValueError("mypy_path is not authorized in trusted-static")

    def _configuration_payload(self) -> Mapping[str, object]:
        return {
            "adapter": self.provider_schema,
            "configuration": "project-pyproject",
            "python_version": "3.13",
            "platform": "win32" if os.name == "nt" else sys.platform,
            "output": "json-lines",
            "cache": "ephemeral-owned",
            "daemon": False,
            "ignore_missing_imports": True,
            "arguments": "relative-to-staged-source-root",
        }

    def _execute(
        self,
        stage_root: Path,
        staged: Mapping[str, ExternalEvidenceFile],
        config_path: Path,
        environment: Mapping[str, str],
    ) -> tuple[tuple[ExternalProviderFinding, ...], int, int, int]:
        argfile = stage_root / "mypy-arguments.txt"
        source_root = stage_root / "source"
        argfile.write_text(
            "\n".join(os.path.relpath(path, source_root) for path in staged) + "\n",
            encoding="utf-8",
        )
        cache_dir = stage_root / "mypy-cache"
        command = (
            sys.executable,
            "-I",
            "-m",
            "mypy",
            "-O",
            "json",
            "--config-file",
            str(config_path),
            "--python-version",
            "3.13",
            "--platform",
            "win32" if os.name == "nt" else sys.platform,
            "--python-executable",
            sys.executable,
            "--cache-dir",
            str(cache_dir),
            "--no-error-summary",
            "--no-pretty",
            "--no-color-output",
            "--ignore-missing-imports",
            f"@{argfile}",
        )
        completed = run_bounded_capture(
            command,
            timeout_seconds=_TOOL_TIMEOUT_SECONDS,
            stdout_limit_bytes=_STDOUT_LIMIT_BYTES,
            stderr_limit_bytes=_STDERR_LIMIT_BYTES,
            cwd=source_root,
            environment=environment,
            memory_limit_bytes=_MYPY_MEMORY_BYTES if os.name == "nt" else None,
        )
        if completed.returncode not in {0, 1}:
            raise ValueError(_unexpected_exit_message("mypy", completed))
        findings: list[ExternalProviderFinding] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("mypy JSON Lines output is malformed") from exc
            if not isinstance(item, Mapping):
                raise ValueError("mypy diagnostic is not an object")
            filename = item.get("file")
            if not isinstance(filename, str):
                raise ValueError("mypy diagnostic path is missing")
            reported_path = Path(filename)
            if not reported_path.is_absolute():
                reported_path = stage_root / "source" / reported_path
            owner = staged.get(os.path.normcase(os.path.abspath(reported_path)))
            if owner is None:
                raise ValueError("mypy reported an unowned path")
            raw_line = int(item.get("line", -1))
            raw_column = int(item.get("column", -1))
            location_precision = "range"
            if raw_line < 1:
                line_number = end_line = 1
                column = end_column = 0
                location_precision = "file"
            else:
                line_number = raw_line
                if raw_column < 0:
                    column = 0
                    location_precision = "line"
                else:
                    column = raw_column
                raw_end_line = item.get("end_line")
                end_line = int(raw_end_line) if raw_end_line is not None else line_number
                end_line = max(line_number, end_line)
                raw_end_column = item.get("end_column")
                end_column = int(raw_end_column) if raw_end_column is not None else column
                if end_column < 0 or (end_line == line_number and end_column < column):
                    end_column = column
            message = str(item.get("message", ""))[:4096]
            code = str(item.get("code") or "mypy")[:128]
            severity = str(item.get("severity") or "error")
            findings.append(
                _finding(
                    provider_id=self.provider_id,
                    owner=owner,
                    category="typing",
                    code=code,
                    severity=_normalized_severity(severity),
                    message=message,
                    start_line=line_number,
                    start_column=column,
                    end_line=end_line,
                    end_column=end_column,
                    metadata={
                        "hint": item.get("hint"),
                        "reported_severity": severity,
                        "location_precision": location_precision,
                    },
                )
            )
        unique_findings: dict[str, ExternalProviderFinding] = {}
        observation_counts: dict[str, int] = {}
        for finding in findings:
            identity = finding.portable_finding_id
            existing = unique_findings.get(identity)
            if existing is not None and existing != finding:
                raise ValueError("mypy finding identity collision")
            if existing is None:
                unique_findings[identity] = finding
            observation_counts[identity] = observation_counts.get(identity, 0) + 1
        findings = [
            replace(
                finding,
                metadata={
                    **finding.metadata,
                    "duplicate_observations": observation_counts[finding.portable_finding_id],
                },
            )
            if observation_counts[finding.portable_finding_id] > 1
            else finding
            for finding in unique_findings.values()
        ]
        if len(findings) > RUFF_MAX_DIAGNOSTICS:
            raise ValueError("mypy diagnostics exceed their bound")
        return (
            tuple(findings),
            len(completed.stdout),
            len(completed.stderr),
            1,
        )


def _pyright_locations() -> tuple[Path | None, Path | None, str | None]:
    node = shutil.which("node")
    if node is None:
        return None, None, None
    candidates: list[Path] = []
    local_app = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    if local_app:
        candidates.append(
            Path(local_app)
            / "Programs"
            / "Neocortex"
            / "tools"
            / "pyright"
            / "node_modules"
            / "pyright"
        )
    if appdata:
        candidates.append(Path(appdata) / "npm" / "node_modules" / "pyright")
    candidates.append(Path(sys.prefix) / "tools" / "pyright" / "node_modules" / "pyright")
    for package_root in candidates:
        index = package_root / "index.js"
        manifest = package_root / "package.json"
        if not index.is_file() or not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        version = payload.get("version") if isinstance(payload, Mapping) else None
        if isinstance(version, str) and version:
            return Path(node), index, version
    return Path(node), None, None


def provider_tool_versions() -> dict[str, str | None]:
    """Probe provider runtimes without reading project configuration or content."""

    _node, _index, pyright_version = _pyright_locations()
    ruff_version = _package_version("ruff")
    return {
        RUFF_PROTECTED_PROVIDER_ID: ruff_version,
        RUFF_TRUSTED_PROVIDER_ID: ruff_version,
        MYPY_PROVIDER_ID: _package_version("mypy"),
        PYRIGHT_PROVIDER_ID: pyright_version,
        RUFF_ANALYZE_PROVIDER_ID: ruff_version,
        GRIMP_ARCHITECTURE_PROVIDER_ID: _package_version("grimp"),
        COMPLEXIPY_COGNITIVE_PROVIDER_ID: _package_version("complexipy"),
        VULTURE_UNUSED_PROVIDER_ID: _package_version("vulture"),
        PYTEST_COVERAGE_PROVIDER_ID: _deep_tool_version(),
    }


class PyrightTrustedProjectProvider(_TrustedStaticProvider):
    provider_id = PYRIGHT_PROVIDER_ID
    provider_schema = "neocortex.pyright-trusted-project/v1"
    tool_name = "pyright"
    source = "external:pyright"
    memory_bound = _PYRIGHT_MEMORY_BYTES
    execution_strategy = "trusted-config-staged-project-v1"

    def __init__(self, root: Path):
        self._node, self._index, self._pyright_version = _pyright_locations()
        super().__init__(root)

    def _resolve_version(self) -> str | None:
        return self._pyright_version

    def _node_path_for_signature(self) -> str | None:
        return None if self._node is None else str(self._node)

    def _validate_configuration(self, config: Mapping[str, object]) -> None:
        tool = config.get("tool")
        pyright = tool.get("pyright") if isinstance(tool, Mapping) else None
        if not isinstance(pyright, Mapping):
            raise ValueError("tool.pyright is missing")
        forbidden = {
            "extends",
            "typeshedPath",
            "stubPath",
            "extraPaths",
            "venvPath",
            "venv",
        }
        observed = forbidden & set(pyright)
        if observed:
            raise ValueError(
                "pyright external path settings are not authorized: " + ",".join(sorted(observed))
            )

    def _configuration_payload(self) -> Mapping[str, object]:
        return {
            "adapter": self.provider_schema,
            "configuration": "project-pyproject",
            "python_version": "3.13",
            "python_platform": "Windows" if os.name == "nt" else platform.system(),
            "output": "json",
            "cache": "ephemeral-owned",
        }

    def _execute(
        self,
        stage_root: Path,
        staged: Mapping[str, ExternalEvidenceFile],
        config_path: Path,
        environment: Mapping[str, str],
    ) -> tuple[tuple[ExternalProviderFinding, ...], int, int, int]:
        if self._node is None or self._index is None:
            raise ValueError("owned Pyright runtime is unavailable")
        tool = self._config.get("tool")
        configured = tool.get("pyright") if isinstance(tool, Mapping) else None
        if not isinstance(configured, Mapping):
            raise ValueError("tool.pyright is missing")
        payload = dict(configured)
        payload["include"] = [
            os.path.relpath(path, stage_root).replace("\\", "/") for path in staged
        ]
        payload["exclude"] = []
        payload["pythonVersion"] = "3.13"
        payload["pythonPlatform"] = "Windows" if os.name == "nt" else platform.system()
        payload["executionEnvironments"] = [
            {
                "root": "source",
                "pythonVersion": "3.13",
                "pythonPlatform": "Windows" if os.name == "nt" else platform.system(),
            }
        ]
        generated_config = stage_root / "pyrightconfig.json"
        generated_config.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        selected_environment = dict(environment)
        selected_environment["PYRIGHT_TMPDIR"] = str(stage_root)
        command = (
            str(self._node),
            str(self._index),
            "--outputjson",
            "--project",
            str(generated_config),
            "--pythonpath",
            sys.executable,
            "--pythonversion",
            "3.13",
            "--pythonplatform",
            "Windows" if os.name == "nt" else platform.system(),
        )
        completed = run_bounded_capture(
            command,
            timeout_seconds=_TOOL_TIMEOUT_SECONDS,
            stdout_limit_bytes=_STDOUT_LIMIT_BYTES,
            stderr_limit_bytes=_STDERR_LIMIT_BYTES,
            cwd=stage_root,
            environment=selected_environment,
            memory_limit_bytes=_PYRIGHT_MEMORY_BYTES if os.name == "nt" else None,
        )
        if completed.returncode not in {0, 1}:
            raise ValueError(_unexpected_exit_message("pyright", completed))
        try:
            decoded = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("pyright JSON output is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("pyright JSON output is not an object")
        diagnostics = decoded.get("generalDiagnostics")
        summary = decoded.get("summary")
        if not isinstance(diagnostics, list) or not isinstance(summary, Mapping):
            raise ValueError("pyright JSON output is incomplete")
        analyzed = summary.get("filesAnalyzed")
        if not isinstance(analyzed, int) or analyzed < len(staged):
            raise ValueError("pyright did not cover every eligible file")
        findings: list[ExternalProviderFinding] = []
        for item in diagnostics:
            if not isinstance(item, Mapping):
                raise ValueError("pyright diagnostic is not an object")
            filename = item.get("file")
            if not isinstance(filename, str):
                raise ValueError("pyright diagnostic path is missing")
            owner = staged.get(os.path.normcase(os.path.abspath(filename)))
            if owner is None:
                raise ValueError("pyright reported an unowned path")
            raw_range = item.get("range")
            location_precision = "range"
            if raw_range is None:
                start_line = end_line = 1
                start_column = end_column = 0
                location_precision = "file"
            else:
                if not isinstance(raw_range, Mapping):
                    raise ValueError("pyright diagnostic range is malformed")
                start = raw_range.get("start")
                end = raw_range.get("end")
                if not isinstance(start, Mapping) or not isinstance(end, Mapping):
                    raise ValueError("pyright diagnostic range is malformed")
                start_line = int(start.get("line", 0)) + 1
                start_column = int(start.get("character", 0))
                end_line = int(end.get("line", start_line - 1)) + 1
                end_column = int(end.get("character", start_column))
            rule = item.get("rule")
            code = "pyright" if rule is None else str(rule)[:128]
            severity = str(item.get("severity") or "warning")
            message = str(item.get("message") or "")[:4096]
            findings.append(
                _finding(
                    provider_id=self.provider_id,
                    owner=owner,
                    category="typing",
                    code=code,
                    severity=_normalized_severity(severity),
                    message=message,
                    start_line=start_line,
                    start_column=start_column,
                    end_line=end_line,
                    end_column=end_column,
                    metadata={
                        "reported_severity": severity,
                        "location_precision": location_precision,
                    },
                )
            )
        if len(findings) > RUFF_MAX_DIAGNOSTICS:
            raise ValueError("pyright diagnostics exceed their bound")
        return (
            tuple(findings),
            len(completed.stdout),
            len(completed.stderr),
            1,
        )


class VultureUnusedStaticProvider:
    """Run advisory Vulture evidence over the exact project-wide Python input."""

    executor: Callable[
        [Path, Mapping[str, ExternalEvidenceFile], Mapping[str, str]],
        VultureUnusedExecution,
    ] = staticmethod(execute_vulture_unused)

    def __init__(self, root: Path) -> None:
        self.root = root
        self._version = _package_version("vulture")
        version = self._version or "unavailable"
        self.descriptor = _provider_descriptor(
            provider_id=VULTURE_UNUSED_PROVIDER_ID,
            provider_schema=VULTURE_UNUSED_PROVIDER_SCHEMA,
            tool_name="vulture",
            tool_version=version,
            profile="trusted-static",
            source="external:vulture-unused-static",
            configuration_payload={
                "adapter": VULTURE_UNUSED_PROVIDER_SCHEMA,
                "input": "exact-current-inventory-python",
                "api": "Vulture.scavenge/get_unused_code",
                "min_confidence": 0,
                "sort_by_size": False,
                "ignore_names": [],
                "ignore_decorators": [],
                "project_configuration": False,
                "plugins": False,
                "content_execution": False,
                "network": False,
                "autofix": False,
                "cache": False,
            },
            project_configuration_digest=None,
            environment_signature=_environment_signature(
                tool_name="vulture",
                tool_version=version,
            ),
            root_identity=external_root_identity(root),
            execution_strategy="isolated-python-worker-vulture-project-v1",
            invalidation_strategy="project_wide",
            memory=_VULTURE_MEMORY_BYTES,
            loads_project_configuration=False,
        )

    def tool_version(self) -> str | None:
        return self._version

    def run(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        *,
        baseline: ExternalProviderBaseline | None,
        scratch_root: Path,
    ) -> ExternalProviderPublication:
        if baseline is not None and baseline.input_signature == external_input_signature(files):
            return _exact_replay(
                self.descriptor,
                root,
                files,
                baseline,
                limitations=_VULTURE_LIMITATIONS,
            )
        started_ns = time.time_ns()
        if self._version is None:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version="unavailable",
                status="unavailable",
                reason="vulture_unavailable",
                started_ns=started_ns,
            )
        try:
            staging_parent = _validated_staging_parent(root, scratch_root)
            with tempfile.TemporaryDirectory(
                prefix="neocortex-vulture-unused-", dir=staging_parent
            ) as temporary:
                stage_root = Path(temporary)
                staged = _stage_external_inputs(files, stage_root / "source")
                environment = _controlled_environment()
                for name in ("TEMP", "TMP", "TMPDIR"):
                    environment[name] = str(stage_root)
                result = self.executor(stage_root, staged, environment)
                if result.limitations != _VULTURE_LIMITATIONS:
                    raise ValueError("Vulture adapter limitations disagree with provider contract")
        except subprocess.TimeoutExpired:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=self._version,
                status="timeout",
                reason="provider_timeout",
                started_ns=started_ns,
            )
        except (OSError, RuntimeError, TypeError, ValueError, SubprocessOutputLimitError) as exc:
            return _failure(
                self.descriptor,
                root,
                files,
                tool_version=self._version,
                status="failed",
                reason=f"provider_failure:{type(exc).__name__}:{exc}"[:4096],
                started_ns=started_ns,
            )
        return _success(
            self.descriptor,
            root,
            files,
            result.findings,
            baseline,
            tool_version=self._version,
            started_ns=started_ns,
            stdout_bytes=result.stdout_bytes,
            stderr_bytes=result.stderr_bytes,
            process_invocations=result.process_invocations,
            bytes_staged=sum(item.size for item in files),
            limitations=result.limitations,
        )


def _architecture_files(
    files: Sequence[ExternalEvidenceFile],
) -> tuple[ExternalEvidenceFile, ...]:
    """Select the exact versioned production-package domain for Hito 2."""

    roots = frozenset(PRODUCTION_ROOT_PACKAGES)
    selected = []
    for item in files:
        parts = PurePosixPath(item.relative_path).parts
        if parts and parts[0] in roots:
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: item.relative_path.casefold()))


class _TrustedArchitectureProvider:
    provider_id: str
    provider_schema: str
    tool_name: str
    distribution: str
    source: str
    memory_bound: int
    execution_strategy: str
    executor: Callable[
        [Path, Mapping[str, ExternalEvidenceFile], Mapping[str, str]],
        ArchitectureProviderExecution,
    ]

    def __init__(self, root: Path):
        self.root = root
        self._version = _package_version(self.distribution)
        version = self._version or "unavailable"
        configuration = {
            "adapter": self.provider_schema,
            "architecture_contract_schema": ARCHITECTURE_CONTRACT_SCHEMA,
            "architecture_baseline_id": ARCHITECTURE_BASELINE_ID,
            "domain": list(PRODUCTION_ROOT_PACKAGES),
            "static_only": True,
            "autofix": False,
            "network": False,
            "cache": False,
        }
        self.descriptor = _provider_descriptor(
            provider_id=self.provider_id,
            provider_schema=self.provider_schema,
            tool_name=self.tool_name,
            tool_version=version,
            profile="trusted-static",
            source=self.source,
            configuration_payload=configuration,
            project_configuration_digest=None,
            environment_signature=_environment_signature(
                tool_name=self.tool_name,
                tool_version=version,
            ),
            root_identity=external_root_identity(root),
            execution_strategy=self.execution_strategy,
            invalidation_strategy="project_wide",
            memory=self.memory_bound,
            loads_project_configuration=False,
            scope="production-packages-python-v1",
        )

    def tool_version(self) -> str | None:
        return self._version

    def run(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        *,
        baseline: ExternalProviderBaseline | None,
        scratch_root: Path,
    ) -> ExternalProviderPublication:
        selected = _architecture_files(files)
        if baseline is not None and baseline.input_signature == external_input_signature(selected):
            return _exact_replay(self.descriptor, root, selected, baseline)
        started_ns = time.time_ns()
        if self._version is None:
            return _failure(
                self.descriptor,
                root,
                selected,
                tool_version="unavailable",
                status="unavailable",
                reason=f"{self.tool_name}_unavailable",
                started_ns=started_ns,
            )
        if not selected:
            return _failure(
                self.descriptor,
                root,
                selected,
                tool_version=self._version,
                status="failed",
                reason="architecture_production_domain_empty",
                started_ns=started_ns,
            )
        try:
            staging_parent = _validated_staging_parent(root, scratch_root)
            with tempfile.TemporaryDirectory(
                prefix=f"neocortex-{self.provider_id}-", dir=staging_parent
            ) as temporary:
                stage_root = Path(temporary)
                staged = _stage_external_inputs(selected, stage_root / "source")
                environment = _controlled_environment()
                for name in ("TEMP", "TMP", "TMPDIR"):
                    environment[name] = str(stage_root)
                result = self.executor(stage_root, staged, environment)
        except subprocess.TimeoutExpired:
            return _failure(
                self.descriptor,
                root,
                selected,
                tool_version=self._version,
                status="timeout",
                reason="provider_timeout",
                started_ns=started_ns,
            )
        except (OSError, RuntimeError, TypeError, ValueError, SubprocessOutputLimitError) as exc:
            return _failure(
                self.descriptor,
                root,
                selected,
                tool_version=self._version,
                status="failed",
                reason=f"provider_failure:{type(exc).__name__}:{exc}"[:4096],
                started_ns=started_ns,
            )
        return _success(
            self.descriptor,
            root,
            selected,
            result.findings,
            baseline,
            metrics=result.metrics,
            relations=result.relations,
            tool_version=self._version,
            started_ns=started_ns,
            stdout_bytes=result.stdout_bytes,
            stderr_bytes=result.stderr_bytes,
            process_invocations=result.process_invocations,
            bytes_staged=sum(item.size for item in selected),
        )


class RuffAnalyzeImportsProvider(_TrustedArchitectureProvider):
    provider_id = RUFF_ANALYZE_PROVIDER_ID
    provider_schema = "neocortex.ruff-analyze-imports/v1"
    tool_name = "ruff"
    distribution = "ruff"
    source = "external:ruff-analyze-imports"
    memory_bound = _RUFF_MEMORY_BYTES
    execution_strategy = "isolated-ruff-analyze-graph-v1"
    executor = staticmethod(execute_ruff_analyze_imports)


class GrimpArchitectureProvider(_TrustedArchitectureProvider):
    provider_id = GRIMP_ARCHITECTURE_PROVIDER_ID
    provider_schema = "neocortex.grimp-architecture/v1"
    tool_name = "grimp"
    distribution = "grimp"
    source = "external:grimp-architecture"
    memory_bound = _GRIMP_MEMORY_BYTES
    execution_strategy = "isolated-python-worker-grimp-v1"
    executor = staticmethod(execute_grimp_architecture)


class ComplexipyCognitiveProvider(_TrustedArchitectureProvider):
    provider_id = COMPLEXIPY_COGNITIVE_PROVIDER_ID
    provider_schema = "neocortex.complexipy-cognitive/v1"
    tool_name = "complexipy"
    distribution = "complexipy"
    source = "external:complexipy-cognitive"
    memory_bound = _COMPLEXIPY_MEMORY_BYTES
    execution_strategy = "isolated-python-worker-complexipy-v1"
    executor = staticmethod(execute_complexipy_cognitive)


_DEEP_COVERAGE_LIMITATIONS = (
    "coverage_main_process_only",
    "subprocess_coverage_not_collected",
    "git_ignored_support_files_excluded_from_support_signature",
)


class PytestCoverageTrustedDeepProvider:
    """Execute the explicit canonical test selection with branch coverage."""

    def __init__(
        self,
        root: Path,
        deep_configuration: Mapping[str, object] | None,
        deep_configuration_signature: str | None,
    ) -> None:
        payload, config = _validated_deep_configuration(
            deep_configuration,
            deep_configuration_signature,
        )
        self.root = root
        self.deep_configuration = payload
        self.config = config
        self._version = _deep_tool_version()
        version = self._version or "unavailable"
        self._root_identity = external_root_identity(root)
        _raw_project, _parsed_project, project_digest = _project_configuration(root)
        self.descriptor = _provider_descriptor(
            provider_id=PYTEST_COVERAGE_PROVIDER_ID,
            provider_schema=DEEP_COVERAGE_PROVIDER_SCHEMA,
            tool_name="pytest+coverage",
            tool_version=version,
            profile="trusted-deep",
            source="external:pytest-coverage-trusted-deep",
            configuration_payload={
                "deep_configuration": payload,
                "deep_configuration_signature": config.configuration_signature,
                "branch_coverage": True,
                "dynamic_contexts": "pytest-nodeid-phase-v1",
                "subprocess_coverage": False,
                "autofix": False,
            },
            project_configuration_digest=project_digest,
            environment_signature=_environment_signature(
                tool_name="pytest+coverage",
                tool_version=version,
                home_directory=trusted_deep_home_directory(),
                path_value=os.environ.get("PATH"),
                pathext_value=os.environ.get("PATHEXT"),
            ),
            root_identity=self._root_identity,
            execution_strategy="canonical-root-bounded-sharded-pytest-coverage-v1",
            invalidation_strategy="dynamic_suite",
            memory=_DEEP_COVERAGE_MEMORY_BYTES,
            loads_project_configuration=True,
            scope="canonical-neocortex-pytest-selection-v1",
            limits=ProviderLimits(
                config.time_budget_seconds,
                _DEEP_COVERAGE_MEMORY_BYTES,
                RUFF_MAX_TOTAL_BYTES,
                _DEEP_COVERAGE_OUTPUT_BYTES,
                _DEEP_COVERAGE_FINDING_BOUND,
            ),
            loads_plugins=True,
            imports_content=True,
            executes_content=True,
            # Pytest content is trusted but not network-sandboxed.  Claiming false
            # here would overstate a guarantee the provider does not enforce.
            uses_network=True,
        )
        self._prepared_key: str | None = None
        self._prepared: DeepCoveragePreparedInput | None = None
        self._preparation_error_key: str | None = None
        self._preparation_error: Exception | None = None

    def tool_version(self) -> str | None:
        return self._version

    def _prepare(
        self,
        files: Sequence[ExternalEvidenceFile],
    ) -> tuple[DeepCoveragePreparedInput, bool]:
        key = external_input_signature(files)
        if self._prepared_key == key and self._prepared is not None:
            return self._prepared, True
        if self._preparation_error_key == key and self._preparation_error is not None:
            raise self._preparation_error
        try:
            prepared = prepare_deep_coverage_input(
                self.root,
                files,
                self.config,
                environment=_controlled_environment(),
            )
        except Exception as exc:
            self._preparation_error_key = key
            self._preparation_error = exc
            raise
        self._prepared_key = key
        self._prepared = prepared
        self._preparation_error_key = None
        self._preparation_error = None
        return prepared, False

    def baseline_input_signature(
        self,
        files: Sequence[ExternalEvidenceFile],
    ) -> str:
        """Return the exact replay key, caching its measured preflight."""

        prepared, _cached = self._prepare(files)
        return prepared.publication_input_signature

    def _attach_deep_contract(
        self,
        publication: ExternalProviderPublication,
        *,
        prepared: DeepCoveragePreparedInput | None,
        execution: DeepCoverageExecution | None,
    ) -> ExternalProviderPublication:
        counters = dict(publication.counters)
        if prepared is not None:
            counters.update(
                {
                    "support_files_verified": prepared.support_files_verified,
                    "support_bytes_verified": prepared.support_bytes_verified,
                    "preparation_milliseconds": prepared.preparation_milliseconds,
                    "bytes_read": max(
                        counters.get("bytes_read", 0),
                        prepared.support_bytes_verified,
                    ),
                }
            )
            if execution is None:
                counters.update(
                    {
                        "process_invocations": max(
                            counters.get("process_invocations", 0),
                            prepared.process_invocations,
                        ),
                        "stdout_bytes": max(
                            counters.get("stdout_bytes", 0),
                            prepared.stdout_bytes,
                        ),
                        "stderr_bytes": max(
                            counters.get("stderr_bytes", 0),
                            prepared.stderr_bytes,
                        ),
                        "wall_milliseconds": max(
                            counters.get("wall_milliseconds", 0),
                            prepared.preparation_milliseconds,
                        ),
                    }
                )
        if execution is not None:
            counters.update({name: int(value) for name, value in execution.counters.items()})
        provenance = dict(publication.publication.provenance)
        provenance["deep_configuration"] = {
            "payload": dict(self.deep_configuration),
            "signature": self.config.configuration_signature,
        }
        deep_execution: dict[str, object] = {
            "suite_selection": self.config.suite_selection,
            "content_executed": publication.execution != "cache_replay",
            "whole_publication_replay": publication.execution == "cache_replay",
        }
        if prepared is not None:
            deep_execution.update(
                {
                    "code_input_signature": prepared.code_input_signature,
                    "support_signature": prepared.support_signature,
                    "publication_input_signature": prepared.publication_input_signature,
                    "support_files_verified": prepared.support_files_verified,
                    "support_bytes_verified": prepared.support_bytes_verified,
                    "preparation_milliseconds": prepared.preparation_milliseconds,
                    "preparation_process_invocations": prepared.process_invocations,
                }
            )
        if execution is not None:
            deep_execution.update(
                {
                    "measurement_complete": execution.measurement_complete,
                    "suite_signature": execution.suite_signature,
                    "measurement_scope_signature": execution.measurement_scope_signature,
                }
            )
        provenance["deep_execution"] = deep_execution
        inner = replace(publication.publication, provenance=provenance)
        return replace(publication, publication=inner, counters=counters)

    def run(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        *,
        baseline: ExternalProviderBaseline | None,
        scratch_root: Path,
    ) -> ExternalProviderPublication:
        started_ns = time.time_ns()
        if self._version is None:
            return self._attach_deep_contract(
                _failure(
                    self.descriptor,
                    root,
                    files,
                    tool_version="unavailable",
                    status="unavailable",
                    reason="pytest_or_coverage_unavailable",
                    started_ns=started_ns,
                ),
                prepared=None,
                execution=None,
            )
        prepared: DeepCoveragePreparedInput | None = None
        try:
            if external_root_identity(root) != self._root_identity:
                raise ValueError("trusted-deep root changed after provider construction")
            prepared, was_cached = self._prepare(files)
            if was_cached:
                started_ns -= prepared.preparation_milliseconds * 1_000_000
        except subprocess.TimeoutExpired:
            return self._attach_deep_contract(
                _failure(
                    self.descriptor,
                    root,
                    files,
                    tool_version=self._version,
                    status="timeout",
                    reason="deep_coverage_preparation_timeout",
                    started_ns=started_ns,
                ),
                prepared=prepared,
                execution=None,
            )
        except (OSError, RuntimeError, TypeError, ValueError, SubprocessOutputLimitError) as exc:
            return self._attach_deep_contract(
                _failure(
                    self.descriptor,
                    root,
                    files,
                    tool_version=self._version,
                    status="failed",
                    reason=f"deep_coverage_preparation_failed:{type(exc).__name__}:{exc}"[:4096],
                    started_ns=started_ns,
                ),
                prepared=prepared,
                execution=None,
            )
        assert prepared is not None
        if (
            baseline is not None
            and baseline.input_signature == prepared.publication_input_signature
        ):
            replay = _exact_replay(
                self.descriptor,
                root,
                files,
                baseline,
                limitations=_DEEP_COVERAGE_LIMITATIONS,
                input_signature_override=prepared.publication_input_signature,
            )
            return self._attach_deep_contract(
                replay,
                prepared=prepared,
                execution=None,
            )
        try:
            staging_parent = _validated_staging_parent(root, scratch_root)
            durable_identity = external_signature(
                "deep-coverage-scratch-v1",
                {
                    "root_identity": external_root_identity(root),
                    "configuration_signature": self.config.configuration_signature,
                },
            ).rsplit(":", 1)[-1]
            # Keep the unpublished internal scratch layout deliberately short.  Pytest
            # adds node-id-derived directories below its basetemp and nested Git
            # fixtures must still fit the traditional Windows path budget.
            durable_scratch = staging_parent / "d" / durable_identity
            durable_scratch.mkdir(parents=True, exist_ok=True)
            staged = {os.path.normcase(os.path.abspath(item.path)): item for item in files}
            if len(staged) != len(files):
                raise ValueError("trusted-deep input paths are duplicated")
            with tempfile.TemporaryDirectory(
                prefix="neocortex-pytest-coverage-",
                dir=staging_parent,
            ) as temporary:
                execution = execute_pytest_coverage(
                    Path(temporary),
                    staged,
                    _controlled_environment(),
                    trusted_root=root,
                    scratch_root=durable_scratch,
                    config=self.config,
                    prepared_input=prepared,
                )
        except subprocess.TimeoutExpired:
            return self._attach_deep_contract(
                _failure(
                    self.descriptor,
                    root,
                    files,
                    tool_version=self._version,
                    status="timeout",
                    reason="provider_timeout",
                    started_ns=started_ns,
                ),
                prepared=prepared,
                execution=None,
            )
        except (OSError, RuntimeError, TypeError, ValueError, SubprocessOutputLimitError) as exc:
            return self._attach_deep_contract(
                _failure(
                    self.descriptor,
                    root,
                    files,
                    tool_version=self._version,
                    status="failed",
                    reason=f"provider_failure:{type(exc).__name__}:{exc}"[:4096],
                    started_ns=started_ns,
                ),
                prepared=prepared,
                execution=None,
            )
        publication = _success(
            self.descriptor,
            root,
            files,
            execution.findings,
            baseline if execution.measurement_complete else None,
            metrics=execution.metrics,
            relations=execution.relations,
            tool_version=self._version,
            started_ns=started_ns,
            stdout_bytes=execution.stdout_bytes,
            stderr_bytes=execution.stderr_bytes,
            process_invocations=execution.process_invocations,
            bytes_staged=0,
            limitations=execution.limitations,
            input_signature_override=prepared.publication_input_signature,
        )
        publication = replace(
            publication,
            coverage_complete=execution.measurement_complete,
        )
        return self._attach_deep_contract(
            publication,
            prepared=prepared,
            execution=execution,
        )


def providers_for_profile(
    profile: AnalysisProfile,
    root: Path,
    *,
    deep_configuration: Mapping[str, object] | None = None,
    deep_configuration_signature: str | None = None,
) -> tuple[ExternalEvidenceProvider, ...]:
    if profile != "trusted-deep" and (
        deep_configuration is not None or deep_configuration_signature is not None
    ):
        raise ValueError("deep provider configuration requires trusted-deep")
    if profile == "protected":
        return (RuffProtectedBasicProvider(root),)
    if profile == "trusted-static":
        return (
            RuffProtectedBasicProvider(root),
            RuffTrustedProjectProvider(root),
            MypyTrustedProjectProvider(root),
            PyrightTrustedProjectProvider(root),
            VultureUnusedStaticProvider(root),
            RuffAnalyzeImportsProvider(root),
            GrimpArchitectureProvider(root),
            ComplexipyCognitiveProvider(root),
        )
    if profile == "trusted-deep":
        static = providers_for_profile("trusted-static", root)
        return (
            *static,
            PytestCoverageTrustedDeepProvider(
                root,
                deep_configuration,
                deep_configuration_signature,
            ),
        )
    raise ValueError("external analysis profile is unsupported")


__all__ = [
    "COMPLEXIPY_COGNITIVE_PROVIDER_ID",
    "GRIMP_ARCHITECTURE_PROVIDER_ID",
    "MYPY_PROVIDER_ID",
    "PYRIGHT_PROVIDER_ID",
    "PYTEST_COVERAGE_PROVIDER_ID",
    "RUFF_ANALYZE_PROVIDER_ID",
    "RUFF_PROTECTED_PROVIDER_ID",
    "RUFF_TRUSTED_PROVIDER_ID",
    "VULTURE_UNUSED_PROVIDER_ID",
    "ComplexipyCognitiveProvider",
    "GrimpArchitectureProvider",
    "MypyTrustedProjectProvider",
    "PyrightTrustedProjectProvider",
    "PytestCoverageTrustedDeepProvider",
    "RuffAnalyzeImportsProvider",
    "RuffProtectedBasicProvider",
    "RuffTrustedProjectProvider",
    "VultureUnusedStaticProvider",
    "provider_tool_versions",
    "providers_for_profile",
]
