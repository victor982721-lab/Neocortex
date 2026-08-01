"""Fail-closed validation for explicitly activated audit test laboratories."""
# region [00] Contexto del módulo
# Módulo: tests/audit_lab_guard.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]

# region [01] Dependencias del módulo
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
# endregion [01]

# region [02] Implementación


AUDIT_LAB_ENVIRONMENT = "NEOCORTEX_AUDIT_LAB_ROOT"
_TEMP_KEYS = ("TEMP", "TMP", "TMPDIR")
_REPARSE_POINT = 0x400


class AuditLabContractError(RuntimeError):
    """The active test process could escape its declared audit laboratory."""


@dataclass(frozen=True, slots=True)
class AuditLabDirectoryIdentity:
    """Physical identity retained across one contained audit process."""

    path: Path
    device: int
    inode: int
    birthtime_ns: int | None


def _canonical_directory(raw: str, *, label: str) -> Path:
    if not raw.strip():
        raise AuditLabContractError(f"{label} is empty")
    path = Path(raw)
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise AuditLabContractError(f"{label} must be an absolute local path")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise AuditLabContractError(f"{label} does not exist: {path}") from exc
    if not canonical.is_dir():
        raise AuditLabContractError(f"{label} is not a directory: {canonical}")
    current = canonical
    while current != current.parent:
        attributes = int(getattr(os.lstat(current), "st_file_attributes", 0))
        if attributes & _REPARSE_POINT:
            raise AuditLabContractError(f"{label} traverses a reparse point: {current}")
        current = current.parent
    return canonical


def capture_audit_lab_directory_identity(
    path: str | Path,
    *,
    label: str,
) -> AuditLabDirectoryIdentity:
    """Capture one existing local directory without following a reparse."""

    canonical = _canonical_directory(str(path), label=label)
    observed = os.stat(canonical, follow_symlinks=False)
    device = int(observed.st_dev)
    inode = int(observed.st_ino)
    if device <= 0 or inode <= 0:
        raise AuditLabContractError(f"{label} has no stable physical identity")
    birthtime = getattr(observed, "st_birthtime_ns", None)
    return AuditLabDirectoryIdentity(
        canonical,
        device,
        inode,
        None if birthtime is None else int(birthtime),
    )


def require_unchanged_audit_lab_directory(
    expected: AuditLabDirectoryIdentity,
    *,
    label: str,
) -> None:
    """Fail if a guarded directory disappeared or was replaced in place."""

    observed = capture_audit_lab_directory_identity(expected.path, label=label)
    if observed != expected:
        raise AuditLabContractError(
            f"{label} changed physical identity: {expected.path}"
        )


def _require_within(path: Path, root: Path, *, label: str) -> None:
    if path != root and not path.is_relative_to(root):
        raise AuditLabContractError(f"{label} escapes audit root: {path}")
    if path.drive.casefold() != root.drive.casefold():
        raise AuditLabContractError(f"{label} uses another volume: {path}")


def validate_audit_lab_environment(
    root_raw: str,
    *,
    environment: Mapping[str, str] | None = None,
    selected_temp_directory: str | None = None,
) -> Path:
    """Validate process-wide temp and bytecode destinations before test import.

    The guard is opt-in through :data:`AUDIT_LAB_ENVIRONMENT`; normal developer
    test runs keep their existing behavior.  An activated audit must pre-create
    every destination.  Silent ``tempfile`` fallback is treated as an error.
    """

    values = os.environ if environment is None else environment
    root = _canonical_directory(root_raw, label="audit laboratory")
    for key in _TEMP_KEYS:
        raw = values.get(key, "")
        directory = _canonical_directory(raw, label=key)
        _require_within(directory, root, label=key)
    pycache = _canonical_directory(
        values.get("PYTHONPYCACHEPREFIX", ""),
        label="PYTHONPYCACHEPREFIX",
    )
    _require_within(pycache, root, label="PYTHONPYCACHEPREFIX")
    if selected_temp_directory is None:
        tempfile.tempdir = None
        selected_temp_directory = tempfile.gettempdir()
    selected = _canonical_directory(
        selected_temp_directory,
        label="tempfile.gettempdir()",
    )
    _require_within(selected, root, label="tempfile.gettempdir()")
    coverage_file = values.get("COVERAGE_FILE")
    if coverage_file:
        coverage_parent = Path(coverage_file).resolve(strict=False).parent
        _require_within(coverage_parent, root, label="COVERAGE_FILE parent")
    return root


def validate_pytest_artifact_paths(
    root: Path,
    *,
    base_temp: str | Path | None,
    cache_directory: str | Path | None,
) -> None:
    """Require pytest basetemp/cache paths below the activated audit root."""

    for label, raw in (
        ("pytest basetemp", base_temp),
        ("pytest cache", cache_directory),
    ):
        if raw is None or not str(raw).strip():
            raise AuditLabContractError(f"{label} must be explicit during an audit")
        path = Path(raw)
        if not path.is_absolute():
            raise AuditLabContractError(f"{label} must be absolute: {path}")
        resolved = path.resolve(strict=False)
        _require_within(resolved, root, label=label)
        if resolved == root:
            raise AuditLabContractError(
                f"{label} must be a strict descendant of audit root: {resolved}"
            )


__all__ = [
    "AUDIT_LAB_ENVIRONMENT",
    "AuditLabContractError",
    "AuditLabDirectoryIdentity",
    "capture_audit_lab_directory_identity",
    "require_unchanged_audit_lab_directory",
    "validate_audit_lab_environment",
    "validate_pytest_artifact_paths",
]
# endregion [02]
