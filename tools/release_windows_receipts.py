"""Pure contracts and canonical receipts for Windows launcher transitions."""
# region [00] Contexto del módulo
# Módulo: tools/release_windows_receipts.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

import base64
import hashlib
import json
import os
import string
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast
# endregion [01]

# region [02] Implementación


SCHEMA_VERSION = 1
MAX_RECEIPT_BYTES = 1024 * 1024
_HEX = frozenset(string.hexdigits.casefold())

Operation = Literal["promote", "rollback", "repromote"]
TransitionStatus = Literal["success", "no_effect", "uncertain"]


class ReleaseTransitionError(RuntimeError):
    """A launcher transition failed a required safety condition."""


class ReceiptValidationError(ReleaseTransitionError):
    """A receipt is absent, non-canonical, inconsistent, or untrusted."""


class TransitionEffectUncertainError(ReleaseTransitionError):
    """The native replacement may have committed and requires recovery."""


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.casefold()
        and set(value) <= _HEX
    )


def absolute_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("release paths must be absolute")
    return Path(os.path.abspath(candidate))


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _contains(parent: Path, child: Path) -> bool:
    try:
        common = os.path.commonpath((str(parent), str(child)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(parent))


@dataclass(frozen=True, slots=True)
class ReleaseLayout:
    """Stable launcher plus external evidence, backup, and lock locations."""

    launcher_path: Path
    receipts_directory: Path
    backup_directory: Path
    lock_path: Path

    def __post_init__(self) -> None:
        launcher = absolute_path(self.launcher_path)
        receipts = absolute_path(self.receipts_directory)
        backup = absolute_path(self.backup_directory)
        lock = absolute_path(self.lock_path)
        object.__setattr__(self, "launcher_path", launcher)
        object.__setattr__(self, "receipts_directory", receipts)
        object.__setattr__(self, "backup_directory", backup)
        object.__setattr__(self, "lock_path", lock)
        if launcher.name.casefold() != "neocortex.exe" or (
            launcher.parent.name.casefold() != "bin"
        ):
            raise ValueError("stable launcher must be bin/Neocortex.exe")
        for role, path in (
            ("receipts", receipts),
            ("backup", backup),
            ("lock", lock),
        ):
            if _contains(launcher.parent, path):
                raise ValueError(f"{role} path must be external to launcher directory")
        if same_path(receipts, backup):
            raise ValueError("receipt and backup directories must differ")


def _positive_integer(value: object, role: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"snapshot {role} identifier is invalid")


def _descriptor_contract(descriptor: object, descriptor_sha256: object) -> None:
    if not isinstance(descriptor, bytes):
        raise ValueError("security descriptor must be bytes")
    if not isinstance(descriptor_sha256, str) or not is_sha256(descriptor_sha256):
        raise ValueError("security descriptor hash is invalid")
    if hashlib.sha256(descriptor).hexdigest() != descriptor_sha256:
        raise ValueError("security descriptor hash does not match its bytes")


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One handle-bound byte, identity, filesystem, and ACL observation."""

    path: str
    size: int
    sha256: str
    volume_id: int
    file_id: int
    file_system: str
    security_descriptor_sha256: str
    security_descriptor: bytes
    link_count: int = 1
    is_reparse_point: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not Path(self.path).is_absolute():
            raise ValueError("snapshot path must be an absolute string")
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
        ):
            raise ValueError("snapshot size must be a non-negative integer")
        if not isinstance(self.sha256, str) or not is_sha256(self.sha256):
            raise ValueError("snapshot SHA-256 is invalid")
        _positive_integer(self.volume_id, "volume")
        _positive_integer(self.file_id, "file")
        _positive_integer(self.link_count, "link count")
        if not isinstance(self.file_system, str) or not self.file_system.strip():
            raise ValueError("snapshot filesystem is invalid")
        _descriptor_contract(
            self.security_descriptor,
            self.security_descriptor_sha256,
        )
        if not isinstance(self.is_reparse_point, bool):
            raise ValueError("snapshot reparse marker must be boolean")


@dataclass(frozen=True, slots=True)
class ReceiptChain:
    """Hash-bound link to the preceding canonical result receipt."""

    previous_receipt_path: Path | None = None
    previous_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        if (self.previous_receipt_path is None) != (
            self.previous_receipt_sha256 is None
        ):
            raise ValueError("previous receipt path and hash must be supplied together")
        if self.previous_receipt_path is not None:
            object.__setattr__(
                self,
                "previous_receipt_path",
                absolute_path(self.previous_receipt_path),
            )
        if self.previous_receipt_sha256 is not None and not is_sha256(
            self.previous_receipt_sha256
        ):
            raise ValueError("previous receipt SHA-256 is invalid")

    def advance(self, result: TransitionResult) -> ReceiptChain:
        return ReceiptChain(result.result_path, result.receipt_sha256)


@dataclass(frozen=True, slots=True)
class LauncherTransitionRequest:
    """Exact authorization input; observations never create authorization."""

    layout: ReleaseLayout
    desired_launcher: Path
    expected_current: FileSnapshot
    expected_desired: FileSnapshot
    operation: Operation
    receipt_chain: ReceiptChain

    def __post_init__(self) -> None:
        desired = absolute_path(self.desired_launcher)
        object.__setattr__(self, "desired_launcher", desired)
        if not isinstance(self.operation, str) or self.operation not in {
            "promote",
            "rollback",
            "repromote",
        }:
            raise ValueError("launcher transition operation is invalid")
        if same_path(desired, self.layout.launcher_path):
            raise ValueError("desired launcher and stable launcher must differ")
        if not same_path(Path(self.expected_current.path), self.layout.launcher_path):
            raise ValueError("current snapshot path does not bind the stable launcher")
        if not same_path(Path(self.expected_desired.path), desired):
            raise ValueError("desired snapshot path does not bind the desired launcher")
        if self.expected_current.sha256 == self.expected_desired.sha256:
            raise ValueError("current and desired launcher hashes must differ")


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Canonical classification and evidence receipt for one transition."""

    status: TransitionStatus
    operation: Operation
    transition_id: str
    intent_path: Path
    result_path: Path
    intent_sha256: str
    receipt_sha256: str
    before: FileSnapshot
    desired: FileSnapshot
    current: FileSnapshot | None
    external_backup_path: Path
    native_backup_path: Path
    stage_evidence_path: Path
    stage_evidence_sha256: str | None
    performed: bool
    recovered: bool


def snapshot_payload(snapshot: FileSnapshot) -> dict[str, object]:
    return {
        "file_id": f"{snapshot.file_id:x}",
        "file_system": snapshot.file_system,
        "is_reparse_point": snapshot.is_reparse_point,
        "link_count": snapshot.link_count,
        "path": snapshot.path,
        "security_descriptor_b64": base64.b64encode(
            snapshot.security_descriptor
        ).decode("ascii"),
        "security_descriptor_sha256": snapshot.security_descriptor_sha256,
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "volume_id": f"{snapshot.volume_id:x}",
    }


def canonical_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def bounded_canonical_bytes(payload: dict[str, object], role: str) -> bytes:
    raw = canonical_bytes(payload)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ReceiptValidationError(f"{role} exceeds its size limit")
    return raw


@dataclass(frozen=True, slots=True)
class TransitionPaths:
    transition_id: str
    intent: Path
    result: Path
    stage: Path
    stage_evidence: Path
    native_backup: Path
    external_backup: Path


def transition_paths(
    layout: ReleaseLayout, transition_id: str, before: FileSnapshot
) -> TransitionPaths:
    return TransitionPaths(
        transition_id=transition_id,
        intent=layout.receipts_directory / f"{transition_id}.intent.json",
        result=layout.receipts_directory / f"{transition_id}.result.json",
        stage=layout.launcher_path.parent / f".neocortex-{transition_id}.stage",
        stage_evidence=layout.receipts_directory / f"{transition_id}.stage.json",
        native_backup=(
            layout.launcher_path.parent / f".neocortex-{transition_id}.previous"
        ),
        external_backup=layout.backup_directory / f"{before.sha256}.launcher",
    )


@dataclass(frozen=True, slots=True)
class PendingIntent:
    layout: ReleaseLayout
    operation: Operation
    desired_launcher: Path
    before: FileSnapshot
    desired: FileSnapshot
    previous_receipt_sha256: str | None
    paths: TransitionPaths
    intent_sha256: str


def intent_payload(
    request: LauncherTransitionRequest,
    previous: str | None,
    paths: TransitionPaths,
) -> dict[str, object]:
    return {
        "before": snapshot_payload(request.expected_current),
        "desired": snapshot_payload(request.expected_desired),
        "desired_launcher": str(request.desired_launcher),
        "external_backup_path": str(paths.external_backup),
        "launcher_path": str(request.layout.launcher_path),
        "lock_path": str(request.layout.lock_path),
        "native_backup_path": str(paths.native_backup),
        "operation": request.operation,
        "previous_receipt_sha256": previous,
        "receipt_type": "launcher_transition_intent",
        "schema_version": SCHEMA_VERSION,
        "stage_evidence_path": str(paths.stage_evidence),
        "stage_path": str(paths.stage),
        "transition_id": paths.transition_id,
    }


def request_payload(
    request: LauncherTransitionRequest, previous: str | None
) -> dict[str, object]:
    return {
        "before": snapshot_payload(request.expected_current),
        "desired": snapshot_payload(request.expected_desired),
        "desired_launcher": str(request.desired_launcher),
        "launcher_path": str(request.layout.launcher_path),
        "lock_path": str(request.layout.lock_path),
        "operation": request.operation,
        "previous_receipt_sha256": previous,
        "schema_version": SCHEMA_VERSION,
    }


def canonical_object(raw: bytes, role: str) -> dict[str, object]:
    try:
        decoded = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptValidationError(
            f"{role} cannot be read as canonical JSON"
        ) from exc
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) for key in decoded
    ):
        raise ReceiptValidationError(f"{role} must be a JSON object")
    payload = cast(dict[str, object], decoded)
    if canonical_bytes(payload) != raw:
        raise ReceiptValidationError(f"{role} JSON is not canonical")
    return payload


def require_keys(
    payload: dict[str, object], expected: frozenset[str], role: str
) -> None:
    if frozenset(payload) != expected:
        raise ReceiptValidationError(f"{role} fields are inconsistent")


def require_string(payload: dict[str, object], key: str, role: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ReceiptValidationError(f"{role} {key} is invalid")
    return value


def _hex_integer(value: object, role: str) -> int:
    if not isinstance(value, str) or not value or set(value) - _HEX:
        raise ReceiptValidationError(f"{role} is not canonical hexadecimal")
    parsed = int(value, 16)
    if parsed < 1 or value != f"{parsed:x}":
        raise ReceiptValidationError(f"{role} is not canonical hexadecimal")
    return parsed


SNAPSHOT_KEYS = frozenset(
    {
        "file_id",
        "file_system",
        "is_reparse_point",
        "link_count",
        "path",
        "security_descriptor_b64",
        "security_descriptor_sha256",
        "sha256",
        "size",
        "volume_id",
    }
)


def snapshot_from_payload(value: object, role: str) -> FileSnapshot:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReceiptValidationError(f"{role} snapshot is invalid")
    payload = cast(dict[str, object], value)
    require_keys(payload, SNAPSHOT_KEYS, f"{role} snapshot")
    encoded = require_string(payload, "security_descriptor_b64", role)
    try:
        descriptor = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ReceiptValidationError(f"{role} descriptor encoding is invalid") from exc
    try:
        return FileSnapshot(
            path=require_string(payload, "path", role),
            size=cast(int, payload["size"]),
            sha256=require_string(payload, "sha256", role),
            volume_id=_hex_integer(payload["volume_id"], f"{role} volume"),
            file_id=_hex_integer(payload["file_id"], f"{role} file"),
            file_system=require_string(payload, "file_system", role),
            security_descriptor_sha256=require_string(
                payload, "security_descriptor_sha256", role
            ),
            security_descriptor=descriptor,
            link_count=cast(int, payload["link_count"]),
            is_reparse_point=cast(bool, payload["is_reparse_point"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReceiptValidationError(f"{role} snapshot is invalid") from exc


def parse_operation(value: object, role: str = "intent") -> Operation:
    if not isinstance(value, str) or value not in {
        "promote",
        "rollback",
        "repromote",
    }:
        raise ReceiptValidationError(f"{role} operation is invalid")
    return cast(Operation, value)


def parse_status(value: object, role: str) -> TransitionStatus:
    if not isinstance(value, str) or value not in {
        "success",
        "no_effect",
        "uncertain",
    }:
        raise ReceiptValidationError(f"{role} status is invalid")
    return cast(TransitionStatus, value)


def validate_result_flags(
    payload: dict[str, object], status: TransitionStatus, role: str
) -> tuple[bool, bool]:
    performed = payload["performed"]
    recovered = payload["recovered"]
    if not isinstance(performed, bool) or not isinstance(recovered, bool):
        raise ReceiptValidationError(f"{role} boolean flags are invalid")
    flags = (performed, recovered)
    valid = (
        flags in {(True, False), (False, True)}
        if status == "success"
        else flags == (False, True)
    )
    if not valid:
        raise ReceiptValidationError(f"{role} status flags are inconsistent")
    return performed, recovered


def optional_sha(value: object, role: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not is_sha256(value):
        raise ReceiptValidationError(f"{role} SHA-256 is invalid")
    return value


INTENT_KEYS = frozenset(
    {
        "before",
        "desired",
        "desired_launcher",
        "external_backup_path",
        "launcher_path",
        "lock_path",
        "native_backup_path",
        "operation",
        "previous_receipt_sha256",
        "receipt_type",
        "schema_version",
        "stage_evidence_path",
        "stage_path",
        "transition_id",
    }
)
RESULT_KEYS = frozenset(
    {
        "before",
        "current",
        "desired",
        "external_backup_path",
        "intent_sha256",
        "native_backup_path",
        "operation",
        "performed",
        "receipt_type",
        "recovered",
        "schema_version",
        "stage_evidence_sha256",
        "status",
        "transition_id",
    }
)
STAGE_EVIDENCE_KEYS = frozenset(
    {
        "intent_sha256",
        "receipt_type",
        "schema_version",
        "stage",
        "transition_id",
    }
)


def validate_stage_snapshot(
    snapshot: FileSnapshot,
    before: FileSnapshot,
    desired: FileSnapshot,
    stage_path: Path,
) -> None:
    if not same_path(Path(snapshot.path), stage_path):
        raise ReleaseTransitionError("staged launcher path is inconsistent")
    content = (snapshot.size, snapshot.sha256)
    if content != (desired.size, desired.sha256):
        raise ReleaseTransitionError("staged launcher content is inconsistent")
    descriptor = (
        snapshot.security_descriptor,
        snapshot.security_descriptor_sha256,
    )
    if descriptor != (
        desired.security_descriptor,
        desired.security_descriptor_sha256,
    ):
        raise ReleaseTransitionError(
            "staged launcher security descriptor is inconsistent"
        )
    placement = (
        snapshot.file_system.casefold(),
        snapshot.volume_id,
        snapshot.link_count,
        snapshot.is_reparse_point,
    )
    if placement != ("ntfs", before.volume_id, 1, False):
        raise ReleaseTransitionError("staging must use the same NTFS volume")
    if snapshot.file_id in {before.file_id, desired.file_id}:
        raise ReleaseTransitionError("staging must be a distinct copied file")


def prospective_stage_snapshot(pending: PendingIntent) -> FileSnapshot:
    return replace(
        pending.desired,
        path=str(pending.paths.stage),
        volume_id=pending.before.volume_id,
        file_id=(1 << 128) - 1,
        file_system="NTFS",
        link_count=1,
        is_reparse_point=False,
    )


def stage_evidence_payload(
    pending: PendingIntent, stage: FileSnapshot
) -> dict[str, object]:
    return {
        "intent_sha256": pending.intent_sha256,
        "receipt_type": "launcher_transition_stage_evidence",
        "schema_version": SCHEMA_VERSION,
        "stage": snapshot_payload(stage),
        "transition_id": pending.paths.transition_id,
    }


def stage_evidence_from_payload(
    pending: PendingIntent, payload: dict[str, object]
) -> FileSnapshot:
    role = "stage evidence receipt"
    require_keys(payload, STAGE_EVIDENCE_KEYS, role)
    schema = payload["schema_version"]
    if (
        isinstance(schema, bool)
        or schema != SCHEMA_VERSION
        or payload["receipt_type"] != "launcher_transition_stage_evidence"
    ):
        raise ReceiptValidationError(f"{role} schema is unsupported")
    if (
        payload["intent_sha256"] != pending.intent_sha256
        or payload["transition_id"] != pending.paths.transition_id
    ):
        raise ReceiptValidationError(f"{role} contradicts its intent")
    stage = snapshot_from_payload(payload["stage"], role)
    validate_stage_snapshot(stage, pending.before, pending.desired, pending.paths.stage)
    return stage


def success_snapshot_matches(
    current: FileSnapshot | None,
    before: FileSnapshot,
    desired: FileSnapshot,
    launcher_path: Path,
) -> bool:
    if current is None or not same_path(Path(current.path), launcher_path):
        return False
    actual = (
        current.size,
        current.sha256,
        current.volume_id,
        current.file_system.casefold(),
        current.security_descriptor,
        current.security_descriptor_sha256,
        current.link_count,
        current.is_reparse_point,
    )
    expected = (
        desired.size,
        desired.sha256,
        before.volume_id,
        "ntfs",
        before.security_descriptor,
        before.security_descriptor_sha256,
        1,
        False,
    )
    return actual == expected


def moved_stage_matches(
    current: FileSnapshot | None,
    stage: FileSnapshot,
    pending: PendingIntent,
) -> bool:
    return (
        success_snapshot_matches(
            current,
            pending.before,
            pending.desired,
            pending.layout.launcher_path,
        )
        and current is not None
        and current.file_id == stage.file_id
    )


def validate_result_evidence(
    status: TransitionStatus,
    current: FileSnapshot | None,
    before: FileSnapshot,
    desired: FileSnapshot,
    launcher_path: Path,
) -> None:
    if status == "success" and not success_snapshot_matches(
        current, before, desired, launcher_path
    ):
        raise ReceiptValidationError("result current evidence contradicts its intent")
    if status == "no_effect" and current != before:
        raise ReceiptValidationError("result current evidence contradicts its intent")


def prospective_success_snapshot(
    before: FileSnapshot, desired: FileSnapshot
) -> FileSnapshot:
    return replace(
        before,
        size=desired.size,
        sha256=desired.sha256,
        file_id=(1 << 128) - 1,
    )


def result_payload(
    pending: PendingIntent,
    status: TransitionStatus,
    current: FileSnapshot | None,
    stage_evidence_sha256: str | None,
    *,
    performed: bool,
    recovered: bool,
) -> dict[str, object]:
    return {
        "before": snapshot_payload(pending.before),
        "current": None if current is None else snapshot_payload(current),
        "desired": snapshot_payload(pending.desired),
        "external_backup_path": str(pending.paths.external_backup),
        "intent_sha256": pending.intent_sha256,
        "native_backup_path": str(pending.paths.native_backup),
        "operation": pending.operation,
        "performed": performed,
        "receipt_type": "launcher_transition_result",
        "recovered": recovered,
        "schema_version": SCHEMA_VERSION,
        "stage_evidence_sha256": stage_evidence_sha256,
        "status": status,
        "transition_id": pending.paths.transition_id,
    }


def transition_result(
    pending: PendingIntent,
    status: TransitionStatus,
    current: FileSnapshot | None,
    stage_evidence_sha256: str | None,
    raw: bytes,
    *,
    performed: bool,
    recovered: bool,
) -> TransitionResult:
    return TransitionResult(
        status=status,
        operation=pending.operation,
        transition_id=pending.paths.transition_id,
        intent_path=pending.paths.intent,
        result_path=pending.paths.result,
        intent_sha256=pending.intent_sha256,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        before=pending.before,
        desired=pending.desired,
        current=current,
        external_backup_path=pending.paths.external_backup,
        native_backup_path=pending.paths.native_backup,
        stage_evidence_path=pending.paths.stage_evidence,
        stage_evidence_sha256=stage_evidence_sha256,
        performed=performed,
        recovered=recovered,
    )


def result_stage_link(
    payload: dict[str, object], status: TransitionStatus, role: str
) -> str | None:
    stage_sha = optional_sha(payload["stage_evidence_sha256"], f"{role} stage evidence")
    if status == "success" and stage_sha is None:
        raise ReceiptValidationError(f"{role} success has no stage evidence")
    return stage_sha


def validate_result_identity(
    payload: dict[str, object],
    pending: PendingIntent,
    before: FileSnapshot,
    desired: FileSnapshot,
) -> None:
    if (
        payload["intent_sha256"] != pending.intent_sha256
        or payload["transition_id"] != pending.paths.transition_id
        or payload["operation"] != pending.operation
        or payload["external_backup_path"] != str(pending.paths.external_backup)
        or payload["native_backup_path"] != str(pending.paths.native_backup)
        or before != pending.before
        or desired != pending.desired
    ):
        raise ReceiptValidationError("result receipt contradicts its intent")


def _previous_identity(payload: dict[str, object]) -> TransitionStatus:
    schema = payload["schema_version"]
    if (
        isinstance(schema, bool)
        or schema != SCHEMA_VERSION
        or payload["receipt_type"] != "launcher_transition_result"
    ):
        raise ReceiptValidationError("previous receipt schema is unsupported")
    parse_operation(payload["operation"], "previous receipt")
    status = parse_status(payload["status"], "previous receipt")
    for key in ("intent_sha256", "transition_id"):
        if not is_sha256(require_string(payload, key, "previous receipt")):
            raise ReceiptValidationError(f"previous receipt {key} is invalid")
    paths = []
    for key in ("external_backup_path", "native_backup_path"):
        value = require_string(payload, key, "previous receipt")
        try:
            paths.append(absolute_path(Path(value)))
        except ValueError as exc:
            raise ReceiptValidationError(f"previous receipt {key} is invalid") from exc
    if same_path(paths[0], paths[1]):
        raise ReceiptValidationError("previous receipt backup paths conflict")
    return status


def _safe_previous_source(snapshot: FileSnapshot, role: str) -> None:
    if (
        snapshot.file_system.casefold() != "ntfs"
        or snapshot.link_count != 1
        or snapshot.is_reparse_point
    ):
        raise ReceiptValidationError(f"previous receipt {role} is unsafe")


def _previous_success_contract(
    before: FileSnapshot,
    desired: FileSnapshot,
    current: FileSnapshot | None,
) -> None:
    if not success_snapshot_matches(current, before, desired, Path(before.path)):
        raise ReceiptValidationError(
            "previous receipt success evidence is inconsistent"
        )
    assert current is not None
    if current.file_id in {before.file_id, desired.file_id}:
        raise ReceiptValidationError(
            "previous receipt success identity is inconsistent"
        )


def _previous_snapshot_contract(
    payload: dict[str, object], status: TransitionStatus
) -> None:
    before = snapshot_from_payload(payload["before"], "previous receipt before")
    desired = snapshot_from_payload(payload["desired"], "previous receipt desired")
    current_value = payload["current"]
    current = (
        None
        if current_value is None
        else snapshot_from_payload(current_value, "previous receipt current")
    )
    if (
        same_path(Path(before.path), Path(desired.path))
        or before.sha256 == desired.sha256
    ):
        raise ReceiptValidationError("previous receipt source snapshots conflict")
    _safe_previous_source(before, "before snapshot")
    _safe_previous_source(desired, "desired snapshot")
    if current is not None and not same_path(Path(current.path), Path(before.path)):
        raise ReceiptValidationError("previous receipt current path is inconsistent")
    if status == "success":
        _previous_success_contract(before, desired, current)
    elif status == "no_effect" and current != before:
        raise ReceiptValidationError(
            "previous receipt no-effect evidence is inconsistent"
        )


def validate_previous_result(raw: bytes, expected_sha256: str) -> None:
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ReceiptValidationError("previous receipt exceeds its size limit")
    payload = canonical_object(raw, "previous receipt")
    if not is_sha256(expected_sha256) or (
        hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise ReceiptValidationError("previous receipt SHA-256 is inconsistent")
    require_keys(payload, RESULT_KEYS, "previous receipt")
    status = _previous_identity(payload)
    validate_result_flags(payload, status, "previous receipt")
    result_stage_link(payload, status, "previous receipt")
    _previous_snapshot_contract(payload, status)


__all__ = [
    "FileSnapshot",
    "INTENT_KEYS",
    "LauncherTransitionRequest",
    "MAX_RECEIPT_BYTES",
    "Operation",
    "PendingIntent",
    "ReceiptChain",
    "ReceiptValidationError",
    "ReleaseLayout",
    "ReleaseTransitionError",
    "RESULT_KEYS",
    "SCHEMA_VERSION",
    "TransitionEffectUncertainError",
    "TransitionPaths",
    "TransitionResult",
    "TransitionStatus",
    "absolute_path",
    "bounded_canonical_bytes",
    "canonical_bytes",
    "canonical_object",
    "intent_payload",
    "optional_sha",
    "moved_stage_matches",
    "parse_operation",
    "parse_status",
    "prospective_stage_snapshot",
    "prospective_success_snapshot",
    "request_payload",
    "require_keys",
    "result_payload",
    "result_stage_link",
    "require_string",
    "same_path",
    "snapshot_from_payload",
    "snapshot_payload",
    "stage_evidence_from_payload",
    "stage_evidence_payload",
    "success_snapshot_matches",
    "transition_paths",
    "transition_result",
    "validate_previous_result",
    "validate_result_flags",
    "validate_result_identity",
    "validate_result_evidence",
    "validate_stage_snapshot",
]
# endregion [02]
