"""Generic contracts for bounded, advisory external code evidence."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .code_external_evidence import (
    ExternalDiagnostic,
    ExternalEvidenceFile,
    ExternalEvidencePublication,
)
from .semantic_models import canonical_json, fingerprint_text

AnalysisProfile = Literal["protected", "trusted-static", "trusted-deep"]
ProviderTrust = Literal["untrusted-safe", "trusted-static", "trusted-execution"]
InvalidationStrategy = Literal[
    "file_local",
    "module_closure",
    "dependency_closure",
    "project_wide",
    "dynamic_suite",
]
ProviderGateStatus = Literal["passed", "failed", "baseline", "not_evaluated", "abstained"]
TypeConsensusKind = Literal[
    "both_report", "mypy_only", "pyright_only", "contradictory", "not_comparable"
]

EXTERNAL_PROVIDER_SCHEMA = "neocortex.external-provider/v1"
EXTERNAL_SUITE_SCHEMA = "neocortex.external-evidence-suite/v1"


@dataclass(frozen=True, slots=True)
class ProviderLimits:
    timeout_seconds: float
    memory_bound_bytes: int
    input_bytes_bound: int
    output_bytes_bound: int
    diagnostic_bound: int

    def as_payload(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "memory_bound_bytes": self.memory_bound_bytes,
            "input_bytes_bound": self.input_bytes_bound,
            "output_bytes_bound": self.output_bytes_bound,
            "diagnostic_bound": self.diagnostic_bound,
        }


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    provider_schema: str
    tool_name: str
    profile: AnalysisProfile
    trust_requirement: ProviderTrust
    scope: str
    source: str
    configuration_signature: str
    project_configuration_digest: str | None
    environment_signature: str
    comparability_signature: str
    execution_strategy: str
    invalidation_strategy: InvalidationStrategy
    cache_policy: str
    limits: ProviderLimits
    loads_project_configuration: bool = False
    loads_plugins: bool = False
    imports_content: bool = False
    executes_content: bool = False
    uses_network: bool = False
    authority: Literal["advisory"] = "advisory"
    mutation_authority: bool = False

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_schema or not self.tool_name:
            raise ValueError("external provider identity cannot be empty")
        if not self.source.startswith("external:"):
            raise ValueError("external provider source must use the external namespace")
        expected_trust = {
            "protected": "untrusted-safe",
            "trusted-static": "trusted-static",
            "trusted-deep": "trusted-execution",
        }[self.profile]
        if self.trust_requirement != expected_trust:
            raise ValueError("external provider profile and trust requirement disagree")
        if self.mutation_authority:
            raise ValueError("external evidence providers cannot mutate content")

    def as_payload(self) -> dict[str, object]:
        return {
            "schema": EXTERNAL_PROVIDER_SCHEMA,
            "provider_id": self.provider_id,
            "provider_schema": self.provider_schema,
            "tool_name": self.tool_name,
            "profile": self.profile,
            "trust_requirement": self.trust_requirement,
            "scope": self.scope,
            "source": self.source,
            "configuration_signature": self.configuration_signature,
            "project_configuration_digest": self.project_configuration_digest,
            "environment_signature": self.environment_signature,
            "comparability_signature": self.comparability_signature,
            "execution_strategy": self.execution_strategy,
            "invalidation_strategy": self.invalidation_strategy,
            "cache_policy": self.cache_policy,
            "limits": self.limits.as_payload(),
            "loads_project_configuration": self.loads_project_configuration,
            "loads_plugins": self.loads_plugins,
            "imports_content": self.imports_content,
            "executes_content": self.executes_content,
            "uses_network": self.uses_network,
            "authority": self.authority,
            "mutation_authority": self.mutation_authority,
        }


@dataclass(frozen=True, slots=True)
class ExternalRunInput:
    version_id: int
    portable_input_id: str
    relative_path: str
    eligible: bool
    covered: bool
    coverage_reason: str | None
    size: int
    content_digest: str

    @classmethod
    def from_file(
        cls,
        item: ExternalEvidenceFile,
        *,
        covered: bool,
        coverage_reason: str | None = None,
    ) -> ExternalRunInput:
        digest = f"xxh3_128:{item.raw_xxh3_128}:xxh3_64:{item.raw_xxh3_64_guard}"
        identity_payload = canonical_json(
            {
                "path": item.relative_path,
                "size": item.size,
                "content_digest": digest,
            }
        )
        portable_id = "external-input-v1:xxh3_128:" + fingerprint_text(identity_payload).xxh3_128
        return cls(
            item.version_id,
            portable_id,
            item.relative_path,
            True,
            covered,
            coverage_reason,
            item.size,
            digest,
        )


@dataclass(frozen=True, slots=True)
class ExternalProviderFinding:
    portable_finding_id: str
    version_id: int
    relative_path: str
    category: str
    code: str
    severity: str
    message: str
    observation_confirmed: bool
    tool_confidence: float | None
    calibrated_confidence: float | None
    gate_authority: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    url: str | None = None
    fix_available: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)
    mutation_authority: bool = False

    def __post_init__(self) -> None:
        if self.mutation_authority:
            raise ValueError("external findings cannot authorize mutation")
        for confidence in (self.tool_confidence, self.calibrated_confidence):
            if confidence is not None and not 0.0 <= confidence <= 1.0:
                raise ValueError("external finding confidence must be within 0..1")
        if self.start_line < 1 or self.start_column < 0:
            raise ValueError("external finding start location is invalid")
        if self.end_line < self.start_line or self.end_column < 0:
            raise ValueError("external finding end location is invalid")

    @classmethod
    def from_diagnostic(
        cls,
        diagnostic: ExternalDiagnostic,
        *,
        category: str,
        severity: str = "warning",
        gate_authority: str = "advisory",
        metadata: Mapping[str, object] | None = None,
    ) -> ExternalProviderFinding:
        return cls(
            diagnostic.identity,
            diagnostic.version_id,
            diagnostic.relative_path,
            category,
            diagnostic.code,
            severity,
            diagnostic.message,
            True,
            1.0,
            None,
            gate_authority,
            diagnostic.start_line,
            diagnostic.start_column,
            diagnostic.end_line,
            diagnostic.end_column,
            diagnostic.url,
            diagnostic.fix_available,
            {} if metadata is None else metadata,
        )

    def digest_payload(self) -> dict[str, object]:
        return {
            "portable_finding_id": self.portable_finding_id,
            "path": self.relative_path,
            "category": self.category,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "observation_confirmed": self.observation_confirmed,
            "tool_confidence": self.tool_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "gate_authority": self.gate_authority,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "url": self.url,
            "fix_available": self.fix_available,
            "metadata": dict(self.metadata),
            "mutation_authority": self.mutation_authority,
        }


def external_findings_digest(
    findings: Sequence[ExternalProviderFinding],
) -> str:
    ordered = sorted(
        (item.digest_payload() for item in findings),
        key=lambda item: str(item["portable_finding_id"]),
    )
    payload = canonical_json({"findings": ordered})
    return "external-findings-v1:xxh3_128:" + fingerprint_text(payload).xxh3_128


def external_root_identity(root: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(root))
    payload = canonical_json({"root": normalized.replace("\\", "/")})
    return "external-root-v1:xxh3_128:" + fingerprint_text(payload).xxh3_128


def external_signature(prefix: str, payload: Mapping[str, object]) -> str:
    if not prefix or any(character.isspace() for character in prefix):
        raise ValueError("external signature prefix is invalid")
    return f"{prefix}:xxh3_128:" + fingerprint_text(canonical_json(payload)).xxh3_128


@dataclass(frozen=True, slots=True)
class ExternalProviderPublication:
    descriptor: ProviderDescriptor
    publication: ExternalEvidencePublication
    observed_root: str
    root_identity: str
    input_signature: str
    inputs: tuple[ExternalRunInput, ...]
    findings: tuple[ExternalProviderFinding, ...]
    counters: Mapping[str, int]
    coverage_complete: bool
    result_digest: str | None
    portable_publication_id: str
    replay_source_tool_run_id: int | None = None
    verification_signature: str | None = None
    limitations: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return self.publication.status

    @property
    def execution(self) -> str:
        return self.publication.execution


@dataclass(frozen=True, slots=True)
class ExternalEvidenceBundle:
    """One legacy projection plus normalized providers, published atomically."""

    legacy: ExternalEvidencePublication | None
    providers: tuple[ExternalProviderPublication, ...]


@dataclass(frozen=True, slots=True)
class ExternalProviderBaseline:
    tool_run_id: int
    provider_id: str
    tool_version: str
    input_signature: str
    comparability_signature: str
    result_digest: str
    portable_finding_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExternalProviderStatus:
    provider_id: str
    provider_schema: str
    profile: AnalysisProfile
    tool_name: str
    tool_version: str | None
    status: Literal["ready", "abstained", "not_recorded"]
    reason: str | None
    execution: str | None
    eligible_files: int
    covered_files: int
    findings: int
    added: int | None
    resolved: int | None
    comparable: bool
    result_digest: str | None
    comparability_signature: str | None
    gate: Literal["passed", "failed", "baseline", "not_evaluated"]
    limitations: tuple[str, ...] = ()
    authority: Literal["advisory"] = "advisory"
    mutation_authority: bool = False
    content_executed: bool = False
    counters: Mapping[str, int] = field(default_factory=dict)

    def as_payload(self) -> dict[str, object]:
        return {
            "schema": EXTERNAL_PROVIDER_SCHEMA,
            "provider_id": self.provider_id,
            "provider_schema": self.provider_schema,
            "profile": self.profile,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "status": self.status,
            "reason": self.reason,
            "execution": self.execution,
            "eligible_files": self.eligible_files,
            "covered_files": self.covered_files,
            "findings": self.findings,
            "added": self.added,
            "resolved": self.resolved,
            "comparable": self.comparable,
            "result_digest": self.result_digest,
            "comparability_signature": self.comparability_signature,
            "gate": self.gate,
            "limitations": list(self.limitations),
            "authority": self.authority,
            "mutation_authority": self.mutation_authority,
            "content_executed": self.content_executed,
            "counters": dict(sorted(self.counters.items())),
        }


@dataclass(frozen=True, slots=True)
class ProviderGateEvaluation:
    gate: str
    provider_id: str
    status: ProviderGateStatus
    reason: str

    def as_payload(self) -> dict[str, str]:
        return {
            "gate": self.gate,
            "provider_id": self.provider_id,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TypeConsensusSummary:
    status: TypeConsensusKind
    both_report: int = 0
    mypy_only: int = 0
    pyright_only: int = 0
    contradictory: int = 0
    not_comparable: int = 0

    def as_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "both_report": self.both_report,
            "mypy_only": self.mypy_only,
            "pyright_only": self.pyright_only,
            "contradictory": self.contradictory,
            "not_comparable": self.not_comparable,
        }


@dataclass(frozen=True, slots=True)
class ExternalEvidenceSuiteStatus:
    profile: AnalysisProfile
    status: Literal["ready", "partial", "abstained", "not_recorded"]
    providers: tuple[ExternalProviderStatus, ...]
    type_consensus: TypeConsensusSummary
    gates: tuple[ProviderGateEvaluation, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "schema": EXTERNAL_SUITE_SCHEMA,
            "profile": self.profile,
            "status": self.status,
            "providers": [item.as_payload() for item in self.providers],
            "type_consensus": self.type_consensus.as_payload(),
            "gates": [item.as_payload() for item in self.gates],
        }


class ExternalEvidenceProvider(Protocol):
    descriptor: ProviderDescriptor

    def tool_version(self) -> str | None:
        """Return the exact executable version or ``None`` when unavailable."""

    def run(
        self,
        root: Path,
        files: Sequence[ExternalEvidenceFile],
        *,
        baseline: ExternalProviderBaseline | None,
        scratch_root: Path,
    ) -> ExternalProviderPublication:
        """Produce one terminal, bounded publication without mutating content."""


__all__ = [
    "EXTERNAL_PROVIDER_SCHEMA",
    "EXTERNAL_SUITE_SCHEMA",
    "AnalysisProfile",
    "ExternalEvidenceBundle",
    "ExternalEvidenceProvider",
    "ExternalEvidenceSuiteStatus",
    "ExternalProviderBaseline",
    "ExternalProviderFinding",
    "ExternalProviderPublication",
    "ExternalProviderStatus",
    "ExternalRunInput",
    "ProviderDescriptor",
    "ProviderGateEvaluation",
    "ProviderLimits",
    "TypeConsensusSummary",
    "external_findings_digest",
    "external_root_identity",
    "external_signature",
]
