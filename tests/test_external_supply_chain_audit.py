"""Focused contracts for bounded supply-chain producers."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

import pytest

import _04_Nucleo_Operativo.external_supply_chain_audit as audit

_FIXTURES = Path(__file__).with_name("fixtures")
_OBSERVED = datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)


class _FakeDistribution:
    def __init__(
        self,
        root: Path,
        *,
        name: str,
        version: str,
        requires: list[str] | None = None,
        license_expression: str | None = None,
        license_text: str | None = None,
        license_classifiers: tuple[str, ...] = (),
        record_path: Path | None = None,
    ) -> None:
        metadata = Message()
        metadata["Metadata-Version"] = "2.4"
        metadata["Name"] = name
        metadata["Version"] = version
        if license_expression is not None:
            metadata["License-Expression"] = license_expression
        if license_text is not None:
            metadata["License"] = license_text
        for classifier in license_classifiers:
            metadata["Classifier"] = classifier
        self.metadata = metadata
        self.version = version
        self.requires = requires
        self._root = root
        self._record_path = record_path

    def read_text(self, filename: str) -> str | None:
        if filename != "RECORD" or self._record_path is None:
            return None
        return self._record_path.read_text("utf-8")

    def locate_file(self, path: str) -> Path:
        return self._root / Path(path)


def _metric(
    result: audit.PipAuditExecution | audit.InstalledPackageInventoryExecution,
    subject_key: str,
    name: str,
) -> audit.ExternalProviderMetric:
    return next(
        item
        for item in result.metrics
        if item.subject_key == subject_key and item.metric_name == name
    )


def _pip_payload() -> bytes:
    return (_FIXTURES / "pip_audit_vulnerabilities_v1.json").read_bytes()


def _install_fixture(tmp_path: Path) -> tuple[Path, Path, list[_FakeDistribution]]:
    install_root = tmp_path / "runtime"
    site_packages = install_root / "Lib" / "site-packages"
    package_file = site_packages / "neocortex" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_bytes = b"healthy"
    package_file.write_bytes(package_bytes)
    dist_info = site_packages / "neocortex_framework-0.7.2.dist-info"
    dist_info.mkdir(parents=True)
    record_path = dist_info / "RECORD"
    digest = base64.urlsafe_b64encode(hashlib.sha256(package_bytes).digest()).decode().rstrip("=")
    record_path.write_text(
        "neocortex/__init__.py," + f"sha256={digest},{len(package_bytes)}\n"
        "neocortex_framework-0.7.2.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    framework = _FakeDistribution(
        site_packages,
        name="neocortex-framework",
        version="0.7.2",
        requires=["demo-dep>=1", "optional-extra>=3; extra == 'full'"],
        license_expression="MIT",
        license_text="MIT License",
        license_classifiers=("License :: OSI Approved :: MIT License",),
        record_path=record_path,
    )
    dependency = _FakeDistribution(
        site_packages,
        name="Demo.Dep",
        version="1.5",
        license_text="BSD-3-Clause",
    )
    mismatch = _FakeDistribution(
        site_packages,
        name="mismatch-dep",
        version="1.5",
        license_expression="Apache-2.0",
    )
    return install_root, package_file, [framework, dependency, mismatch]


def test_pip_audit_is_bounded_no_fix_and_normalizes_advisories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_commands: list[tuple[str, ...]] = []

    def run(arguments, **kwargs):
        command = tuple(arguments)
        observed_commands.append(command)
        assert kwargs["timeout_seconds"] == 180.0
        assert kwargs["stdout_limit_bytes"] == 8 * 1024 * 1024
        assert kwargs["stderr_limit_bytes"] == 128 * 1024
        assert kwargs["environment"] == {"SAFE_SENTINEL": "1"}
        return subprocess.CompletedProcess(arguments, 1, _pip_payload(), b"2 vulnerabilities")

    monkeypatch.setattr(audit.importlib.metadata, "version", lambda _name: "2.10.0")
    monkeypatch.setattr(audit, "run_bounded_capture", run)
    result = audit.execute_pip_audit_known_vulnerabilities(
        {"SAFE_SENTINEL": "1", "PIP_AUDIT_FORMAT": "cyclonedx-json"},
        observed_at=_OBSERVED,
    )

    command = observed_commands[0]
    assert command[1:3] == ("-m", "pip_audit")
    assert command[command.index("--vulnerability-service") + 1] == "pypi"
    assert command[command.index("--aliases") + 1] == "on"
    assert command[command.index("--desc") + 1] == "off"
    assert command[command.index("--progress-spinner") + 1] == "off"
    assert command[command.index("--timeout") + 1] == "15"
    assert "--fix" not in command
    assert result.counters == audit.PipAuditCounters(3, 2, 1, 1, 2, 2)
    assert result.uses_network is True
    assert result.source.startswith("PyPI JSON API")
    assert result.observed_at_utc == "2026-08-03T12:30:00Z"
    assert result.observed_date_utc == "2026-08-03"
    assert result.fresh_until_utc == "2026-08-04T12:30:00Z"
    assert result.freshness_status == "fresh_at_observation"
    assert _metric(result, "package:demo-pkg", "known_vulnerability_count").value == 2
    advisory = _metric(result, "package:demo-pkg", "known_vulnerability:PYSEC-2026-1")
    assert advisory.category == "known_vulnerability"
    assert advisory.metadata["aliases"] == ["CVE-2026-0001", "GHSA-aaaa-bbbb-cccc"]
    assert advisory.metadata["descriptions_collected"] is False
    assert "description" not in advisory.metadata
    assert (
        _metric(result, "project:installed-environment", "audit_current_at_observation").value == 1
    )
    assert {item.relation_kind for item in result.relations} == {"package_has_known_vulnerability"}
    assert all(item.metadata["mutation_authority"] is False for item in result.relations)


def test_pip_audit_accepts_clean_exit_and_rejects_failure_and_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit.importlib.metadata, "version", lambda _name: "2.10.7")
    monkeypatch.setattr(
        audit,
        "run_bounded_capture",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(arguments, 0, b"[]", b""),
    )
    clean = audit.execute_pip_audit_known_vulnerabilities({}, observed_at=_OBSERVED)
    assert clean.counters.vulnerabilities == 0
    assert clean.process_invocations == 1

    monkeypatch.setattr(
        audit,
        "run_bounded_capture",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(arguments, 2, b"[]", b"bad"),
    )
    with pytest.raises(ValueError, match="unexpected_exit:2"):
        audit.execute_pip_audit_known_vulnerabilities({}, observed_at=_OBSERVED)

    payload = json.dumps(
        [
            {"name": "first", "version": "1", "vulns": []},
            {"name": "second", "version": "1", "vulns": []},
        ]
    ).encode()
    monkeypatch.setattr(audit, "_MAX_AUDIT_PACKAGES", 1)
    monkeypatch.setattr(
        audit,
        "run_bounded_capture",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(arguments, 0, payload, b""),
    )
    with pytest.raises(ValueError, match="package count"):
        audit.execute_pip_audit_known_vulnerabilities({}, observed_at=_OBSERVED)


def test_installed_inventory_correlates_pyproject_licenses_requirements_and_record(
    tmp_path: Path,
) -> None:
    install_root, _package_file, distributions = _install_fixture(tmp_path)
    result = audit.execute_installed_package_inventory(
        _FIXTURES / "pyproject_inventory_v1.toml",
        distributions=distributions,
        installation_root=install_root,
        observed_at=_OBSERVED,
    )

    assert result.counters.distributions == 3
    assert result.counters.pyproject_required_dependencies == 4
    assert result.counters.pyproject_required_dependencies_applicable == 3
    assert result.counters.pyproject_required_dependencies_installed == 2
    assert result.counters.pyproject_required_dependencies_missing == 1
    assert result.counters.pyproject_required_dependencies_version_compatible == 1
    assert result.counters.pyproject_required_dependencies_version_mismatch == 1
    assert result.counters.pyproject_optional_dependencies == 1
    assert result.counters.record_hash_verified == 1
    assert result.counters.record_size_verified == 1
    assert result.files_hashed == 1
    assert result.bytes_hashed == len(b"healthy")
    assert result.process_invocations == 0
    assert result.uses_network is False
    assert result.freshness_status == "current_at_observation_only"
    assert (
        _metric(result, "package:neocortex-framework", "wheel_record_integrity_current").value == 1
    )
    assert _metric(result, "package:neocortex-framework", "license_metadata_ambiguous").value == 1
    assert _metric(result, "package:demo-dep", "license_metadata_ambiguous").value == 0
    assert (
        _metric(result, "project:installed-environment", "inventory_current_at_observation").value
        == 1
    )
    relation_kinds = {item.relation_kind for item in result.relations}
    assert relation_kinds == {
        "package_declares_license",
        "package_requires_distribution",
        "project_declares_dependency",
    }
    license_relations = [
        item for item in result.relations if item.relation_kind == "package_declares_license"
    ]
    assert license_relations
    assert all(item.metadata["category"] == "license_inventory" for item in license_relations)
    assert all(item.metadata["legal_compatibility_assessed"] is False for item in license_relations)
    dependency = next(
        item
        for item in result.relations
        if item.relation_kind == "project_declares_dependency"
        and item.target_key == "package:demo-dep"
    )
    assert dependency.metadata["target_installed"] is True
    demo_evaluation = dependency.metadata["base_dependency_evaluations"][0]
    assert demo_evaluation["marker_evaluated"] is True
    assert demo_evaluation["marker_applies"] is True
    assert demo_evaluation["presence_gate_evaluated"] is True
    assert demo_evaluation["version_constraint_evaluated"] is True
    assert demo_evaluation["version_compatible"] is True
    assert demo_evaluation["installed_version"] == "1.5"
    assert demo_evaluation["marker_environment"]["python_version"] == "3.13"


def test_base_dependency_gates_exclude_false_markers_and_optional_extras(
    tmp_path: Path,
) -> None:
    install_root, _package_file, distributions = _install_fixture(tmp_path)
    result = audit.execute_installed_package_inventory(
        _FIXTURES / "pyproject_inventory_v1.toml",
        distributions=distributions,
        installation_root=install_root,
        observed_at=_OBSERVED,
    )

    assert (
        _metric(
            result,
            "project:installed-environment",
            "pyproject_required_applicable_dependency_count",
        ).value
        == 3
    )
    assert (
        _metric(
            result,
            "project:installed-environment",
            "pyproject_required_missing_dependency_count",
        ).value
        == 1
    )
    assert (
        _metric(
            result,
            "project:installed-environment",
            "pyproject_required_version_mismatch_count",
        ).value
        == 1
    )
    ignored = next(
        item
        for item in result.relations
        if item.relation_kind == "project_declares_dependency"
        and item.target_key == "package:ignored-dep"
    )
    ignored_evaluation = ignored.metadata["base_dependency_evaluations"][0]
    assert ignored_evaluation["marker_applies"] is False
    assert ignored_evaluation["presence_gate_evaluated"] is False
    assert ignored_evaluation["version_constraint_evaluated"] is False
    assert ignored_evaluation["version_compatible"] is None
    mismatch = next(
        item
        for item in result.relations
        if item.relation_kind == "project_declares_dependency"
        and item.target_key == "package:mismatch-dep"
    )
    assert mismatch.metadata["base_dependency_evaluations"][0]["version_compatible"] is False
    optional = next(
        item
        for item in result.relations
        if item.relation_kind == "project_declares_dependency"
        and item.target_key == "package:optional-extra"
    )
    optional_metadata = optional.metadata["optional_declarations"][0]
    assert optional_metadata["extra_group_selected"] is False
    assert optional_metadata["presence_gate_evaluated"] is False
    assert optional_metadata["version_constraint_evaluated"] is False
    assert all("recorded_not_evaluated" not in item for item in result.limitations)


def test_installed_inventory_detects_altered_record_member_and_changes_snapshot(
    tmp_path: Path,
) -> None:
    install_root, package_file, distributions = _install_fixture(tmp_path)
    first = audit.execute_installed_package_inventory(
        _FIXTURES / "pyproject_inventory_v1.toml",
        distributions=distributions,
        installation_root=install_root,
        observed_at=_OBSERVED,
    )
    package_file.write_bytes(b"tampered!")
    second = audit.execute_installed_package_inventory(
        _FIXTURES / "pyproject_inventory_v1.toml",
        distributions=distributions,
        installation_root=install_root,
        observed_at=_OBSERVED,
    )

    assert second.counters.record_hash_mismatches == 1
    assert second.counters.record_size_mismatches == 1
    assert (
        _metric(second, "package:neocortex-framework", "wheel_record_integrity_current").value == 0
    )
    assert second.snapshot_id != first.snapshot_id


def test_installed_inventory_has_hard_distribution_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root, _package_file, distributions = _install_fixture(tmp_path)
    monkeypatch.setattr(audit, "_MAX_DISTRIBUTIONS", 1)
    with pytest.raises(ValueError, match="distribution count"):
        audit.execute_installed_package_inventory(
            _FIXTURES / "pyproject_inventory_v1.toml",
            distributions=distributions,
            installation_root=install_root,
            observed_at=_OBSERVED,
        )
