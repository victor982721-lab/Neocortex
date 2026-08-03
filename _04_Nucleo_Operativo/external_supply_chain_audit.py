"""Bounded, advisory supply-chain evidence for the installed runtime.

The networked producer is intentionally limited to ``pip-audit``'s PyPI
service.  The local producer uses only installed distribution metadata and a
verified staged ``pyproject.toml``.  Neither producer fixes packages, evaluates
legal compatibility, imports project content, or grants mutation authority.
"""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import re
import stat
import sys
import tomllib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .bounded_subprocess import run_bounded_capture
from .external_evidence_models import (
    ExternalProviderMetric,
    ExternalProviderRelation,
    external_metric_identity,
    external_relation_identity,
    external_signature,
)

PIP_AUDIT_PROVIDER_ID = "pip-audit-known-vulnerabilities"
PIP_AUDIT_PROVIDER_SCHEMA = "neocortex.pip-audit-known-vulnerabilities/v1"
INSTALLED_PACKAGE_PROVIDER_ID = "installed-package-inventory"
INSTALLED_PACKAGE_PROVIDER_SCHEMA = "neocortex.installed-package-inventory/v1"

PIP_AUDIT_USES_NETWORK = True
INSTALLED_PACKAGE_USES_NETWORK = False
PIP_AUDIT_SERVICE = "pypi"

_PIP_AUDIT_TIMEOUT_SECONDS = 180.0
_PIP_AUDIT_SOCKET_TIMEOUT_SECONDS = 15
_PIP_AUDIT_STDOUT_LIMIT_BYTES = 8 * 1024 * 1024
_PIP_AUDIT_STDERR_LIMIT_BYTES = 128 * 1024
_PIP_AUDIT_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
_PIP_AUDIT_FRESHNESS_SECONDS = 24 * 60 * 60
_MAX_AUDIT_PACKAGES = 2_000
_MAX_VULNERABILITIES = 10_000
_MAX_ALIASES_PER_VULNERABILITY = 128
_MAX_FIX_VERSIONS_PER_VULNERABILITY = 128

_MAX_PYPROJECT_BYTES = 1024 * 1024
_MAX_DISTRIBUTIONS = 2_000
_MAX_REQUIREMENTS = 20_000
_MAX_REQUIREMENTS_PER_EDGE = 128
_MAX_LICENSE_DECLARATIONS = 10_000
_MAX_LICENSE_DECLARATIONS_PER_PACKAGE = 64
_MAX_RECORD_BYTES = 8 * 1024 * 1024
_MAX_RECORD_ENTRIES = 20_000
_MAX_RECORD_HASH_BYTES = 1024 * 1024 * 1024
_MAX_TEXT_BYTES = 4_096
_MAX_LICENSE_VALUE_BYTES = 64 * 1024
_LICENSE_EXCERPT_BYTES = 1_024
_HASH_READ_BYTES = 1024 * 1024

_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_REQUIREMENT_NAME_PATTERN = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_PIP_AUDIT_VERSION_PATTERN = re.compile(r"^2\.10\.\d+(?:[A-Za-z0-9.+-]*)?$")

_PIP_AUDIT_LIMITATIONS = (
    "known_vulnerability_feed_is_a_point_in_time_snapshot",
    "absence_of_a_report_is_not_proof_of_security",
    "package_reachability_and_runtime_exposure_are_not_assessed",
    "advisory_only_no_fix_or_mutation_authority",
)
_INVENTORY_LIMITATIONS = (
    "requirement_markers_and_version_constraints_are_recorded_not_evaluated",
    "license_metadata_is_inventory_not_legal_compatibility_analysis",
    "multiple_license_declarations_remain_explicitly_ambiguous",
    "record_verification_cannot_detect_files_omitted_from_record_without_enumeration",
    "inventory_is_current_only_at_its_observation_time",
    "advisory_only_no_mutation_authority",
)


@dataclass(frozen=True, slots=True)
class PipAuditCounters:
    packages_observed: int
    packages_audited: int
    packages_skipped: int
    vulnerable_packages: int
    vulnerabilities: int
    aliases: int


@dataclass(frozen=True, slots=True)
class PipAuditExecution:
    metrics: tuple[ExternalProviderMetric, ...]
    relations: tuple[ExternalProviderRelation, ...]
    counters: PipAuditCounters
    tool_version: str
    source: str
    observed_at_utc: str
    observed_date_utc: str
    snapshot_id: str
    freshness_status: str
    fresh_until_utc: str
    stdout_bytes: int
    stderr_bytes: int
    process_invocations: int
    uses_network: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InstalledPackageCounters:
    distributions: int
    requirement_relations: int
    pyproject_required_dependencies: int
    pyproject_required_dependencies_installed: int
    pyproject_required_dependencies_missing: int
    pyproject_optional_dependencies: int
    packages_with_license_metadata: int
    packages_with_ambiguous_license_metadata: int
    packages_without_license_metadata: int
    record_entries: int
    record_hash_verified: int
    record_size_verified: int
    record_missing_files: int
    record_hash_mismatches: int
    record_size_mismatches: int
    record_unverifiable_entries: int
    record_unsafe_entries: int


@dataclass(frozen=True, slots=True)
class InstalledPackageInventoryExecution:
    metrics: tuple[ExternalProviderMetric, ...]
    relations: tuple[ExternalProviderRelation, ...]
    counters: InstalledPackageCounters
    source: str
    observed_at_utc: str
    observed_date_utc: str
    snapshot_id: str
    freshness_status: str
    pyproject_sha256: str
    installed_project_version: str
    files_hashed: int
    bytes_hashed: int
    process_invocations: int
    uses_network: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LicenseDeclaration:
    field: str
    value_sha256: str
    excerpt: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class _DistributionRow:
    distribution: importlib.metadata.Distribution
    name: str
    normalized_name: str
    version: str
    requirements: tuple[str, ...]
    licenses: tuple[_LicenseDeclaration, ...]
    license_expression_count: int
    license_legacy_count: int
    license_classifier_count: int


@dataclass(frozen=True, slots=True)
class _RecordVerification:
    present: bool
    digest: str | None
    entries: int
    hash_verified: int
    size_verified: int
    missing_files: int
    hash_mismatches: int
    size_mismatches: int
    unverifiable_entries: int
    unsafe_entries: int
    malformed_entries: int
    files_hashed: int
    bytes_hashed: int

    @property
    def current(self) -> bool:
        return bool(
            self.present
            and self.entries > 1
            and self.hash_verified > 0
            and self.size_verified > 0
            and not (
                self.missing_files
                or self.hash_mismatches
                or self.size_mismatches
                or self.unverifiable_entries
                or self.unsafe_entries
                or self.malformed_entries
            )
        )


def _normalized_package_name(value: object, *, label: str) -> str:
    name = _required_text(value, label=label, maximum=256)
    if _PACKAGE_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(f"{label} is not a valid distribution name")
    return re.sub(r"[-_.]+", "-", name).lower()


def _required_text(value: object, *, label: str, maximum: int = _MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} is invalid")
    return value


def _required_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} is not a list")
    return value


def _required_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not an object")
    return value


def _observation_time(value: datetime | None) -> datetime:
    observed = datetime.now(timezone.utc) if value is None else value
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("supply-chain observation time must be timezone-aware")
    return observed.astimezone(timezone.utc).replace(microsecond=0)


def _iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _metric(
    provider_id: str,
    *,
    subject_key: str,
    category: str,
    name: str,
    value: int | float,
    unit: str = "count",
    metadata: Mapping[str, object] | None = None,
) -> ExternalProviderMetric:
    return ExternalProviderMetric(
        external_metric_identity(
            provider_id,
            subject_kind="project",
            subject_key=subject_key,
            category=category,
            metric_name=name,
            unit=unit,
        ),
        "project",
        subject_key,
        category,
        name,
        value,
        unit,
        metadata={} if metadata is None else metadata,
    )


def _relation(
    provider_id: str,
    *,
    relation_kind: str,
    source_key: str,
    target_kind: Literal["project", "contract"],
    target_key: str,
    metadata: Mapping[str, object],
) -> ExternalProviderRelation:
    return ExternalProviderRelation(
        external_relation_identity(
            provider_id,
            relation_kind=relation_kind,
            source_kind="project",
            source_key=source_key,
            target_kind=target_kind,
            target_key=target_key,
        ),
        relation_kind,
        "project",
        source_key,
        target_kind,
        target_key,
        confidence=1.0,
        metadata=metadata,
    )


def _pip_audit_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in environment.items()
        if not str(key).upper().startswith("PIP_AUDIT_")
    }


def _pip_audit_version() -> str:
    try:
        version = importlib.metadata.version("pip-audit")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("pip-audit runtime dependency is unavailable") from exc
    if _PIP_AUDIT_VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("pip-audit runtime must use the supported 2.10.x line")
    return version


def _pip_audit_payload(raw: bytes) -> list[object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("pip-audit JSON output is malformed") from exc
    return _required_list(payload, label="pip-audit output")


def _bounded_text_list(value: object, *, label: str, maximum_items: int) -> tuple[str, ...]:
    raw_items = _required_list(value, label=label)
    if len(raw_items) > maximum_items:
        raise ValueError(f"{label} exceeds its bound")
    items = {_required_text(item, label=label, maximum=512) for item in raw_items}
    return tuple(sorted(items, key=str.casefold))


def execute_pip_audit_known_vulnerabilities(
    environment: Mapping[str, str],
    *,
    observed_at: datetime | None = None,
    freshness_seconds: int = _PIP_AUDIT_FRESHNESS_SECONDS,
) -> PipAuditExecution:
    """Audit the installed environment through the bounded PyPI service only."""

    if not 60 <= freshness_seconds <= 7 * 24 * 60 * 60:
        raise ValueError("pip-audit freshness window is outside its bound")
    tool_version = _pip_audit_version()
    command = (
        sys.executable,
        "-m",
        "pip_audit",
        "--format",
        "json",
        "--vulnerability-service",
        PIP_AUDIT_SERVICE,
        "--aliases",
        "on",
        "--desc",
        "off",
        "--progress-spinner",
        "off",
        "--timeout",
        str(_PIP_AUDIT_SOCKET_TIMEOUT_SECONDS),
    )
    completed = run_bounded_capture(
        command,
        timeout_seconds=_PIP_AUDIT_TIMEOUT_SECONDS,
        stdout_limit_bytes=_PIP_AUDIT_STDOUT_LIMIT_BYTES,
        stderr_limit_bytes=_PIP_AUDIT_STDERR_LIMIT_BYTES,
        environment=_pip_audit_environment(environment),
        memory_limit_bytes=_PIP_AUDIT_MEMORY_LIMIT_BYTES if os.name == "nt" else None,
    )
    if completed.returncode not in {0, 1}:
        detail = " ".join(
            (completed.stderr or completed.stdout).decode("utf-8", errors="replace").split()
        )[:2048]
        message = f"pip_audit_unexpected_exit:{completed.returncode}"
        raise ValueError(message if not detail else f"{message}:{detail}")

    payload = _pip_audit_payload(completed.stdout)
    if len(payload) > _MAX_AUDIT_PACKAGES:
        raise ValueError("pip-audit package count exceeds its bound")
    observed = _observation_time(observed_at)
    fresh_until = observed + timedelta(seconds=freshness_seconds)
    observed_text = _iso_utc(observed)
    fresh_until_text = _iso_utc(fresh_until)
    snapshot_id = external_signature(
        "pip-audit-snapshot-v1",
        {
            "tool_version": tool_version,
            "service": PIP_AUDIT_SERVICE,
            "observed_at_utc": observed_text,
            "payload_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        },
    )
    common_metadata = {
        "provider_schema": PIP_AUDIT_PROVIDER_SCHEMA,
        "tool_version": tool_version,
        "source": "PyPI JSON API via pip-audit pypi service",
        "service": PIP_AUDIT_SERVICE,
        "observed_at_utc": observed_text,
        "observed_date_utc": observed.date().isoformat(),
        "snapshot_id": snapshot_id,
        "freshness_status": "fresh_at_observation",
        "fresh_until_utc": fresh_until_text,
        "authority": "advisory",
        "mutation_authority": False,
    }
    metrics: list[ExternalProviderMetric] = []
    relations: list[ExternalProviderRelation] = []
    seen_packages: set[str] = set()
    packages_audited = packages_skipped = vulnerable_packages = 0
    vulnerability_count = alias_count = 0
    for raw_package in payload:
        package = _required_mapping(raw_package, label="pip-audit package")
        raw_name = _required_text(package.get("name"), label="pip-audit package name", maximum=256)
        normalized = _normalized_package_name(raw_name, label="pip-audit package name")
        if normalized in seen_packages:
            raise ValueError("pip-audit output contains a duplicate package")
        seen_packages.add(normalized)
        subject_key = f"package:{normalized}"
        skip_reason = package.get("skip_reason")
        if skip_reason is not None:
            reason = _required_text(skip_reason, label="pip-audit skip reason")
            packages_skipped += 1
            metrics.append(
                _metric(
                    PIP_AUDIT_PROVIDER_ID,
                    subject_key=subject_key,
                    category="known_vulnerability",
                    name="package_audit_skipped",
                    value=1,
                    metadata={**common_metadata, "package_name": raw_name, "skip_reason": reason},
                )
            )
            continue
        version = _required_text(
            package.get("version"), label="pip-audit installed version", maximum=512
        )
        raw_vulnerabilities = _required_list(
            package.get("vulns"), label="pip-audit vulnerabilities"
        )
        if vulnerability_count + len(raw_vulnerabilities) > _MAX_VULNERABILITIES:
            raise ValueError("pip-audit vulnerability count exceeds its bound")
        packages_audited += 1
        if raw_vulnerabilities:
            vulnerable_packages += 1
        package_metadata = {
            **common_metadata,
            "package_name": raw_name,
            "normalized_name": normalized,
            "installed_version": version,
        }
        metrics.extend(
            (
                _metric(
                    PIP_AUDIT_PROVIDER_ID,
                    subject_key=subject_key,
                    category="known_vulnerability",
                    name="package_audited",
                    value=1,
                    metadata=package_metadata,
                ),
                _metric(
                    PIP_AUDIT_PROVIDER_ID,
                    subject_key=subject_key,
                    category="known_vulnerability",
                    name="known_vulnerability_count",
                    value=len(raw_vulnerabilities),
                    metadata=package_metadata,
                ),
            )
        )
        seen_vulnerabilities: set[str] = set()
        for raw_vulnerability in raw_vulnerabilities:
            vulnerability = _required_mapping(raw_vulnerability, label="pip-audit vulnerability")
            vulnerability_id = _required_text(
                vulnerability.get("id"), label="pip-audit vulnerability id", maximum=256
            )
            vulnerability_key = vulnerability_id.casefold()
            if vulnerability_key in seen_vulnerabilities:
                raise ValueError("pip-audit output contains a duplicate package vulnerability")
            seen_vulnerabilities.add(vulnerability_key)
            aliases = _bounded_text_list(
                vulnerability.get("aliases", []),
                label="pip-audit aliases",
                maximum_items=_MAX_ALIASES_PER_VULNERABILITY,
            )
            fix_versions = _bounded_text_list(
                vulnerability.get("fix_versions", []),
                label="pip-audit fix versions",
                maximum_items=_MAX_FIX_VERSIONS_PER_VULNERABILITY,
            )
            vulnerability_count += 1
            alias_count += len(aliases)
            advisory_key = f"advisory:{vulnerability_id}"
            evidence_metadata = {
                **package_metadata,
                "vulnerability_id": vulnerability_id,
                "aliases": list(aliases),
                "fix_versions": list(fix_versions),
                "fix_available": bool(fix_versions),
                "descriptions_collected": False,
            }
            metrics.append(
                _metric(
                    PIP_AUDIT_PROVIDER_ID,
                    subject_key=subject_key,
                    category="known_vulnerability",
                    name=f"known_vulnerability:{vulnerability_id}",
                    value=1,
                    metadata=evidence_metadata,
                )
            )
            relations.append(
                _relation(
                    PIP_AUDIT_PROVIDER_ID,
                    relation_kind="package_has_known_vulnerability",
                    source_key=subject_key,
                    target_kind="contract",
                    target_key=advisory_key,
                    metadata={**evidence_metadata, "category": "known_vulnerability"},
                )
            )

    summary_key = "project:installed-environment"
    summary_values = (
        ("audit_observed_at_unix_seconds", int(observed.timestamp()), "unix_seconds"),
        ("audit_fresh_until_unix_seconds", int(fresh_until.timestamp()), "unix_seconds"),
        ("audit_freshness_window_seconds", freshness_seconds, "seconds"),
        ("audit_current_at_observation", 1, "boolean"),
        ("audited_package_count", packages_audited, "count"),
        ("skipped_package_count", packages_skipped, "count"),
        ("vulnerable_package_count", vulnerable_packages, "count"),
        ("known_vulnerability_count", vulnerability_count, "count"),
    )
    metrics.extend(
        _metric(
            PIP_AUDIT_PROVIDER_ID,
            subject_key=summary_key,
            category="known_vulnerability",
            name=name,
            value=value,
            unit=unit,
            metadata=common_metadata,
        )
        for name, value, unit in summary_values
    )
    return PipAuditExecution(
        tuple(sorted(metrics, key=lambda item: item.portable_metric_id)),
        tuple(sorted(relations, key=lambda item: item.portable_relation_id)),
        PipAuditCounters(
            len(payload),
            packages_audited,
            packages_skipped,
            vulnerable_packages,
            vulnerability_count,
            alias_count,
        ),
        tool_version,
        "PyPI JSON API via pip-audit pypi service",
        observed_text,
        observed.date().isoformat(),
        snapshot_id,
        "fresh_at_observation",
        fresh_until_text,
        len(completed.stdout),
        len(completed.stderr),
        1,
        PIP_AUDIT_USES_NETWORK,
        _PIP_AUDIT_LIMITATIONS,
    )


def _is_reparse_point(path: Path) -> bool:
    metadata = os.lstat(path)
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return path.is_symlink() or bool(attributes & reparse)


def _read_verified_file(path: Path, *, maximum: int, label: str) -> bytes:
    before = os.lstat(path)
    if _is_reparse_point(path) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} is not a regular file")
    if before.st_size > maximum:
        raise ValueError(f"{label} exceeds its byte bound")
    raw = path.read_bytes()
    after = os.lstat(path)
    if (
        len(raw) != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ValueError(f"{label} changed while it was read")
    return raw


def _requirement_name(requirement: str) -> str:
    _required_text(requirement, label="requirement")
    match = _REQUIREMENT_NAME_PATTERN.match(requirement)
    if match is None:
        raise ValueError("requirement does not start with a valid distribution name")
    return _normalized_package_name(match.group(1), label="requirement name")


def _project_metadata(
    pyproject_path: Path,
) -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    raw = _read_verified_file(
        pyproject_path.absolute(), maximum=_MAX_PYPROJECT_BYTES, label="staged pyproject"
    )
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("staged pyproject is malformed") from exc
    project = _required_mapping(document.get("project"), label="pyproject project")
    raw_name = _required_text(project.get("name"), label="pyproject project name", maximum=256)
    normalized_name = _normalized_package_name(raw_name, label="pyproject project name")
    if normalized_name != "neocortex-framework":
        raise ValueError("staged pyproject does not describe neocortex-framework")
    declarations: list[tuple[str, str, str]] = []
    for raw_requirement in _required_list(
        project.get("dependencies", []), label="pyproject dependencies"
    ):
        requirement = _required_text(raw_requirement, label="pyproject dependency")
        declarations.append(("required", _requirement_name(requirement), requirement))
    optional = _required_mapping(
        project.get("optional-dependencies", {}), label="pyproject optional dependencies"
    )
    for raw_group, raw_requirements in sorted(optional.items(), key=lambda item: str(item[0])):
        group = _required_text(raw_group, label="pyproject optional group", maximum=128)
        for raw_requirement in _required_list(
            raw_requirements, label="pyproject optional dependency group"
        ):
            requirement = _required_text(raw_requirement, label="pyproject optional dependency")
            declarations.append((group, _requirement_name(requirement), requirement))
    if len(declarations) > _MAX_REQUIREMENTS:
        raise ValueError("pyproject dependency declarations exceed their bound")
    return normalized_name, hashlib.sha256(raw).hexdigest(), tuple(declarations)


def _metadata_values(distribution: importlib.metadata.Distribution, field: str) -> tuple[str, ...]:
    metadata = distribution.metadata
    getter = getattr(metadata, "get_all", None)
    raw_values: object
    if callable(getter):
        raw_values = getter(field, [])
    else:
        raw_values = metadata.get(field)
    if raw_values is None:
        return ()
    if isinstance(raw_values, str):
        values: Sequence[object] = (raw_values,)
    elif isinstance(raw_values, Sequence):
        values = raw_values
    else:
        raise ValueError(f"installed metadata field {field} is invalid")
    result = []
    for value in values:
        text = _required_text(
            value, label=f"installed metadata field {field}", maximum=_MAX_LICENSE_VALUE_BYTES
        ).strip()
        if text:
            result.append(text)
    return tuple(result)


def _license_declaration(field: str, value: str) -> _LicenseDeclaration:
    raw = value.encode("utf-8")
    excerpt_bytes = raw[:_LICENSE_EXCERPT_BYTES]
    while True:
        try:
            excerpt = excerpt_bytes.decode("utf-8")
            break
        except UnicodeDecodeError:
            excerpt_bytes = excerpt_bytes[:-1]
    return _LicenseDeclaration(
        field,
        hashlib.sha256(raw).hexdigest(),
        excerpt,
        len(raw) > len(excerpt_bytes),
    )


def _distribution_rows(
    distributions: Iterable[importlib.metadata.Distribution],
) -> tuple[_DistributionRow, ...]:
    rows: list[_DistributionRow] = []
    seen: set[str] = set()
    total_requirements = total_licenses = 0
    for distribution in distributions:
        if len(rows) >= _MAX_DISTRIBUTIONS:
            raise ValueError("installed distribution count exceeds its bound")
        raw_name = distribution.metadata.get("Name")
        normalized = _normalized_package_name(raw_name, label="installed distribution name")
        if normalized in seen:
            raise ValueError("installed distribution names are ambiguous after normalization")
        seen.add(normalized)
        version = _required_text(
            distribution.version, label="installed distribution version", maximum=512
        )
        raw_requirements = distribution.requires or []
        requirements = tuple(
            _required_text(value, label="installed distribution requirement")
            for value in raw_requirements
        )
        total_requirements += len(requirements)
        if total_requirements > _MAX_REQUIREMENTS:
            raise ValueError("installed requirement declarations exceed their bound")
        expressions = _metadata_values(distribution, "License-Expression")
        legacy = _metadata_values(distribution, "License")
        classifiers = tuple(
            value
            for value in _metadata_values(distribution, "Classifier")
            if value.startswith("License ::")
        )
        declarations = tuple(
            _license_declaration(field, value)
            for field, values in (
                ("License-Expression", expressions),
                ("License", legacy),
                ("Classifier", classifiers),
            )
            for value in values
        )
        if len(declarations) > _MAX_LICENSE_DECLARATIONS_PER_PACKAGE:
            raise ValueError("installed package license declarations exceed their bound")
        total_licenses += len(declarations)
        if total_licenses > _MAX_LICENSE_DECLARATIONS:
            raise ValueError("installed license declarations exceed their bound")
        rows.append(
            _DistributionRow(
                distribution,
                _required_text(raw_name, label="installed distribution name", maximum=256),
                normalized,
                version,
                requirements,
                declarations,
                len(expressions),
                len(legacy),
                len(classifiers),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.normalized_name))


def _is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(path), os.fspath(root)))
        return os.path.normcase(common) == os.path.normcase(os.fspath(root))
    except ValueError:
        return False


def _record_hash(path: Path, algorithm: str) -> tuple[bytes, int]:
    digest = hashlib.new(algorithm)
    observed = 0
    before = os.lstat(path)
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_READ_BYTES):
            observed += len(chunk)
            digest.update(chunk)
    after = os.lstat(path)
    if (
        observed != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ValueError("installed package file changed during RECORD verification")
    return digest.digest(), observed


def _record_verification(
    distribution: importlib.metadata.Distribution,
    *,
    installation_root: Path,
) -> _RecordVerification:
    record = distribution.read_text("RECORD")
    if record is None:
        return _RecordVerification(False, None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    raw_record = record.encode("utf-8")
    if len(raw_record) > _MAX_RECORD_BYTES:
        raise ValueError("installed RECORD exceeds its byte bound")
    rows = list(csv.reader(io.StringIO(record)))
    if len(rows) > _MAX_RECORD_ENTRIES:
        raise ValueError("installed RECORD entry count exceeds its bound")
    root = installation_root.resolve(strict=True)
    hash_verified = size_verified = missing = hash_mismatches = 0
    size_mismatches = unverifiable = unsafe = malformed = 0
    files_hashed = bytes_hashed = 0
    for row in rows:
        if len(row) != 3 or not row[0] or len(row[0].encode("utf-8")) > _MAX_TEXT_BYTES:
            malformed += 1
            continue
        record_path, hash_field, size_field = row
        candidate = Path(str(distribution.locate_file(record_path))).resolve(strict=False)
        if not _is_within(candidate, root):
            unsafe += 1
            continue
        try:
            file_metadata = os.lstat(candidate)
        except FileNotFoundError:
            missing += 1
            continue
        if _is_reparse_point(candidate) or not stat.S_ISREG(file_metadata.st_mode):
            unsafe += 1
            continue
        expected_size: int | None = None
        if size_field:
            if not size_field.isdecimal():
                malformed += 1
            else:
                expected_size = int(size_field)
                if expected_size == file_metadata.st_size:
                    size_verified += 1
                else:
                    size_mismatches += 1
        expected_digest: bytes | None = None
        algorithm = ""
        if hash_field:
            algorithm, separator, encoded_digest = hash_field.partition("=")
            try:
                if (
                    not separator
                    or algorithm not in hashlib.algorithms_guaranteed
                    or not encoded_digest
                ):
                    raise ValueError
                padding = "=" * (-len(encoded_digest) % 4)
                expected_digest = base64.urlsafe_b64decode(encoded_digest + padding)
            except (ValueError, binascii.Error):
                malformed += 1
                expected_digest = None
        normalized_record_path = record_path.replace("\\", "/").casefold()
        exempt_blank = normalized_record_path.endswith((".pyc", ".dist-info/record"))
        if expected_digest is None:
            if not exempt_blank:
                unverifiable += 1
            continue
        if bytes_hashed + file_metadata.st_size > _MAX_RECORD_HASH_BYTES:
            raise ValueError("installed RECORD hash bytes exceed their bound")
        observed_digest, observed_bytes = _record_hash(candidate, algorithm)
        files_hashed += 1
        bytes_hashed += observed_bytes
        if observed_digest == expected_digest:
            hash_verified += 1
        else:
            hash_mismatches += 1
    return _RecordVerification(
        True,
        hashlib.sha256(raw_record).hexdigest(),
        len(rows),
        hash_verified,
        size_verified,
        missing,
        hash_mismatches,
        size_mismatches,
        unverifiable,
        unsafe,
        malformed,
        files_hashed,
        bytes_hashed,
    )


def _license_ambiguity(row: _DistributionRow) -> tuple[bool, tuple[str, ...]]:
    reasons = []
    populated_fields = sum(
        count > 0
        for count in (
            row.license_expression_count,
            row.license_legacy_count,
            row.license_classifier_count,
        )
    )
    if populated_fields > 1:
        reasons.append("multiple_metadata_fields")
    if any(
        count > 1
        for count in (
            row.license_expression_count,
            row.license_legacy_count,
            row.license_classifier_count,
        )
    ):
        reasons.append("multiple_values_in_field")
    return bool(reasons), tuple(reasons)


def execute_installed_package_inventory(
    pyproject_path: Path,
    *,
    distributions: Iterable[importlib.metadata.Distribution] | None = None,
    installation_root: Path | None = None,
    observed_at: datetime | None = None,
) -> InstalledPackageInventoryExecution:
    """Inventory installed metadata and verify the framework wheel's RECORD."""

    project_name, pyproject_digest, declarations = _project_metadata(pyproject_path)
    rows = _distribution_rows(
        importlib.metadata.distributions() if distributions is None else distributions
    )
    rows_by_name = {row.normalized_name: row for row in rows}
    project_row = rows_by_name.get(project_name)
    if project_row is None:
        raise ValueError("installed neocortex-framework distribution is unavailable")
    root = Path(sys.prefix) if installation_root is None else installation_root
    record = _record_verification(project_row.distribution, installation_root=root)
    observed = _observation_time(observed_at)
    observed_text = _iso_utc(observed)
    snapshot_payload = {
        "pyproject_sha256": pyproject_digest,
        "distributions": [
            {
                "name": row.normalized_name,
                "version": row.version,
                "requirements": list(row.requirements),
                "licenses": [
                    {"field": item.field, "value_sha256": item.value_sha256}
                    for item in row.licenses
                ],
            }
            for row in rows
        ],
        "record": {
            "sha256": record.digest,
            "current": record.current,
            "entries": record.entries,
            "hash_verified": record.hash_verified,
            "size_verified": record.size_verified,
            "missing_files": record.missing_files,
            "hash_mismatches": record.hash_mismatches,
            "size_mismatches": record.size_mismatches,
            "unverifiable_entries": record.unverifiable_entries,
            "unsafe_entries": record.unsafe_entries,
            "malformed_entries": record.malformed_entries,
        },
        "observed_at_utc": observed_text,
    }
    snapshot_id = (
        "installed-package-snapshot-v1:sha256:"
        + hashlib.sha256(
            json.dumps(
                snapshot_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )
    common_metadata = {
        "provider_schema": INSTALLED_PACKAGE_PROVIDER_SCHEMA,
        "source": "python importlib.metadata and installed wheel RECORD",
        "observed_at_utc": observed_text,
        "observed_date_utc": observed.date().isoformat(),
        "snapshot_id": snapshot_id,
        "freshness_status": "current_at_observation_only",
        "pyproject_sha256": pyproject_digest,
        "authority": "advisory",
        "mutation_authority": False,
    }
    metrics: list[ExternalProviderMetric] = []
    relations: list[ExternalProviderRelation] = []
    requirement_edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    license_available = license_ambiguous = license_missing = 0
    for row in rows:
        subject_key = f"package:{row.normalized_name}"
        package_metadata = {
            **common_metadata,
            "package_name": row.name,
            "normalized_name": row.normalized_name,
            "installed_version": row.version,
        }
        for requirement in row.requirements:
            target = _requirement_name(requirement)
            edge = requirement_edges[(subject_key, f"package:{target}")]
            if len(edge) >= _MAX_REQUIREMENTS_PER_EDGE:
                raise ValueError("installed requirements per package edge exceed their bound")
            edge.append(requirement)
        ambiguous, ambiguity_reasons = _license_ambiguity(row)
        if row.licenses:
            license_available += 1
            license_ambiguous += int(ambiguous)
        else:
            license_missing += 1
        metrics.extend(
            (
                _metric(
                    INSTALLED_PACKAGE_PROVIDER_ID,
                    subject_key=subject_key,
                    category="package_integrity",
                    name="distribution_present",
                    value=1,
                    metadata=package_metadata,
                ),
                _metric(
                    INSTALLED_PACKAGE_PROVIDER_ID,
                    subject_key=subject_key,
                    category="package_integrity",
                    name="declared_requirement_count",
                    value=len(row.requirements),
                    metadata={
                        **package_metadata,
                        "requirement_markers_evaluated": False,
                        "version_constraints_evaluated": False,
                    },
                ),
                _metric(
                    INSTALLED_PACKAGE_PROVIDER_ID,
                    subject_key=subject_key,
                    category="license_inventory",
                    name="license_metadata_available",
                    value=int(bool(row.licenses)),
                    unit="boolean",
                    metadata={
                        **package_metadata,
                        "metadata_status": "present" if row.licenses else "missing",
                        "legal_compatibility_assessed": False,
                    },
                ),
                _metric(
                    INSTALLED_PACKAGE_PROVIDER_ID,
                    subject_key=subject_key,
                    category="license_inventory",
                    name="license_metadata_ambiguous",
                    value=int(ambiguous),
                    unit="boolean",
                    metadata={
                        **package_metadata,
                        "ambiguity_reasons": list(ambiguity_reasons),
                        "legal_compatibility_assessed": False,
                    },
                ),
            )
        )
        for name, value in (
            ("license_expression_count", row.license_expression_count),
            ("license_legacy_field_count", row.license_legacy_count),
            ("license_classifier_count", row.license_classifier_count),
        ):
            metrics.append(
                _metric(
                    INSTALLED_PACKAGE_PROVIDER_ID,
                    subject_key=subject_key,
                    category="license_inventory",
                    name=name,
                    value=value,
                    metadata={**package_metadata, "legal_compatibility_assessed": False},
                )
            )
        for declaration in row.licenses:
            field_key = re.sub(r"[^a-z0-9]+", "-", declaration.field.casefold()).strip("-")
            relations.append(
                _relation(
                    INSTALLED_PACKAGE_PROVIDER_ID,
                    relation_kind="package_declares_license",
                    source_key=subject_key,
                    target_kind="contract",
                    target_key=(
                        f"license-declaration:{field_key}:sha256:{declaration.value_sha256}"
                    ),
                    metadata={
                        **package_metadata,
                        "category": "license_inventory",
                        "source_field": declaration.field,
                        "declaration_excerpt": declaration.excerpt,
                        "declaration_sha256": declaration.value_sha256,
                        "declaration_truncated": declaration.truncated,
                        "metadata_ambiguous": ambiguous,
                        "ambiguity_reasons": list(ambiguity_reasons),
                        "legal_compatibility_assessed": False,
                    },
                )
            )
    for (source_key, target_key), requirements in sorted(requirement_edges.items()):
        target_name = target_key.removeprefix("package:")
        relations.append(
            _relation(
                INSTALLED_PACKAGE_PROVIDER_ID,
                relation_kind="package_requires_distribution",
                source_key=source_key,
                target_kind="project",
                target_key=target_key,
                metadata={
                    **common_metadata,
                    "category": "package_integrity",
                    "requirements": sorted(set(requirements)),
                    "target_installed": target_name in rows_by_name,
                    "requirement_markers_evaluated": False,
                    "version_constraints_evaluated": False,
                },
            )
        )

    direct_edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for group, target, requirement in declarations:
        direct_edges[target].append((group, requirement))
    required_targets = {
        target for group, target, _requirement in declarations if group == "required"
    }
    optional_count = sum(group != "required" for group, _target, _requirement in declarations)
    required_installed = sum(target in rows_by_name for target in required_targets)
    for target, edge_declarations in sorted(direct_edges.items()):
        relations.append(
            _relation(
                INSTALLED_PACKAGE_PROVIDER_ID,
                relation_kind="project_declares_dependency",
                source_key=f"package:{project_name}",
                target_kind="project",
                target_key=f"package:{target}",
                metadata={
                    **common_metadata,
                    "category": "package_integrity",
                    "groups": sorted({group for group, _requirement in edge_declarations}),
                    "requirements": sorted(
                        {requirement for _group, requirement in edge_declarations}
                    ),
                    "target_installed": target in rows_by_name,
                    "version_constraint_evaluated": False,
                },
            )
        )

    project_key = f"package:{project_name}"
    record_values = (
        ("record_present", int(record.present), "boolean"),
        ("record_entry_count", record.entries, "count"),
        ("record_hash_verified_count", record.hash_verified, "count"),
        ("record_size_verified_count", record.size_verified, "count"),
        ("record_missing_file_count", record.missing_files, "count"),
        ("record_hash_mismatch_count", record.hash_mismatches, "count"),
        ("record_size_mismatch_count", record.size_mismatches, "count"),
        ("record_unverifiable_entry_count", record.unverifiable_entries, "count"),
        ("record_unsafe_entry_count", record.unsafe_entries, "count"),
        ("record_malformed_entry_count", record.malformed_entries, "count"),
        ("wheel_record_integrity_current", int(record.current), "boolean"),
    )
    metrics.extend(
        _metric(
            INSTALLED_PACKAGE_PROVIDER_ID,
            subject_key=project_key,
            category="package_integrity",
            name=name,
            value=value,
            unit=unit,
            metadata={
                **common_metadata,
                "installed_version": project_row.version,
                "record_sha256": record.digest,
            },
        )
        for name, value, unit in record_values
    )
    summary_key = "project:installed-environment"
    summary_values = (
        ("inventory_observed_at_unix_seconds", int(observed.timestamp()), "unix_seconds"),
        ("inventory_current_at_observation", 1, "boolean"),
        ("installed_distribution_count", len(rows), "count"),
        ("declared_requirement_relation_count", len(requirement_edges), "count"),
        ("pyproject_direct_requirement_count", len(required_targets), "count"),
        ("pyproject_direct_requirement_installed_count", required_installed, "count"),
        (
            "pyproject_direct_requirement_missing_count",
            len(required_targets) - required_installed,
            "count",
        ),
        ("pyproject_optional_dependency_count", optional_count, "count"),
    )
    metrics.extend(
        _metric(
            INSTALLED_PACKAGE_PROVIDER_ID,
            subject_key=summary_key,
            category="package_integrity",
            name=name,
            value=value,
            unit=unit,
            metadata=common_metadata,
        )
        for name, value, unit in summary_values
    )
    for name, value in (
        ("packages_with_license_metadata", license_available),
        ("packages_with_ambiguous_license_metadata", license_ambiguous),
        ("packages_without_license_metadata", license_missing),
    ):
        metrics.append(
            _metric(
                INSTALLED_PACKAGE_PROVIDER_ID,
                subject_key=summary_key,
                category="license_inventory",
                name=name,
                value=value,
                metadata={**common_metadata, "legal_compatibility_assessed": False},
            )
        )
    counters = InstalledPackageCounters(
        len(rows),
        len(requirement_edges),
        len(required_targets),
        required_installed,
        len(required_targets) - required_installed,
        optional_count,
        license_available,
        license_ambiguous,
        license_missing,
        record.entries,
        record.hash_verified,
        record.size_verified,
        record.missing_files,
        record.hash_mismatches,
        record.size_mismatches,
        record.unverifiable_entries,
        record.unsafe_entries,
    )
    return InstalledPackageInventoryExecution(
        tuple(sorted(metrics, key=lambda item: item.portable_metric_id)),
        tuple(sorted(relations, key=lambda item: item.portable_relation_id)),
        counters,
        "python importlib.metadata and installed wheel RECORD",
        observed_text,
        observed.date().isoformat(),
        snapshot_id,
        "current_at_observation_only",
        pyproject_digest,
        project_row.version,
        record.files_hashed,
        record.bytes_hashed,
        0,
        INSTALLED_PACKAGE_USES_NETWORK,
        _INVENTORY_LIMITATIONS,
    )


__all__ = [
    "INSTALLED_PACKAGE_PROVIDER_ID",
    "INSTALLED_PACKAGE_PROVIDER_SCHEMA",
    "INSTALLED_PACKAGE_USES_NETWORK",
    "PIP_AUDIT_PROVIDER_ID",
    "PIP_AUDIT_PROVIDER_SCHEMA",
    "PIP_AUDIT_SERVICE",
    "PIP_AUDIT_USES_NETWORK",
    "InstalledPackageCounters",
    "InstalledPackageInventoryExecution",
    "PipAuditCounters",
    "PipAuditExecution",
    "execute_installed_package_inventory",
    "execute_pip_audit_known_vulnerabilities",
]
