"""Exact stage-evidence commitments for Windows launcher transitions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tools.release_windows_receipts import (
    MAX_RECEIPT_BYTES,
    FileSnapshot,
    PendingIntent,
    ReceiptValidationError,
    ReleaseTransitionError,
    TransitionEffectUncertainError,
    bounded_canonical_bytes,
    canonical_object,
    moved_stage_matches,
    stage_evidence_from_payload,
    stage_evidence_payload,
)


class StageEvidenceOperations(Protocol):
    """Minimal injected filesystem surface needed by commitment checks."""

    def snapshot_by_handle(self, path: Path) -> FileSnapshot: ...

    def write_create_new_and_flush(self, path: Path, payload: bytes) -> None: ...

    def read_bytes(self, path: Path, *, max_bytes: int) -> bytes: ...

    def path_exists(self, path: Path) -> bool: ...

    def remove_file_if_snapshot(self, path: Path, expected: FileSnapshot) -> None:
        """Atomically remove only the object identified by ``expected``."""


@dataclass(frozen=True, slots=True)
class StageEvidenceCommitment:
    """Canonical sidecar bytes and the exact staged file they commit to."""

    stage: FileSnapshot
    raw: bytes
    sha256: str


def stage_evidence_commitment(
    pending: PendingIntent, stage: FileSnapshot
) -> StageEvidenceCommitment:
    raw = bounded_canonical_bytes(
        stage_evidence_payload(pending, stage), "stage evidence receipt"
    )
    return StageEvidenceCommitment(stage, raw, hashlib.sha256(raw).hexdigest())


def write_stage_evidence(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment,
    ops: StageEvidenceOperations,
) -> None:
    ops.write_create_new_and_flush(pending.paths.stage_evidence, commitment.raw)


def load_stage_evidence(
    pending: PendingIntent, ops: StageEvidenceOperations
) -> StageEvidenceCommitment:
    role = "stage evidence receipt"
    try:
        raw = ops.read_bytes(pending.paths.stage_evidence, max_bytes=MAX_RECEIPT_BYTES)
    except OSError as exc:
        raise ReceiptValidationError(
            f"{role} cannot be read as canonical JSON"
        ) from exc
    payload = canonical_object(raw, role)
    stage = stage_evidence_from_payload(pending, payload)
    return StageEvidenceCommitment(stage, raw, hashlib.sha256(raw).hexdigest())


def _require_intent_commitment(
    pending: PendingIntent, ops: StageEvidenceOperations
) -> None:
    try:
        raw = ops.read_bytes(pending.paths.intent, max_bytes=MAX_RECEIPT_BYTES)
    except (OSError, ReleaseTransitionError) as exc:
        raise TransitionEffectUncertainError(
            "stage evidence intent commitment cannot be revalidated"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != pending.intent_sha256:
        raise TransitionEffectUncertainError("stage evidence intent commitment changed")


def require_stage_commitment(
    pending: PendingIntent,
    expected: StageEvidenceCommitment,
    ops: StageEvidenceOperations,
) -> None:
    try:
        actual = load_stage_evidence(pending, ops)
    except (OSError, ReleaseTransitionError) as exc:
        raise TransitionEffectUncertainError(
            "stage evidence commitment cannot be revalidated"
        ) from exc
    if actual != expected:
        raise TransitionEffectUncertainError("stage evidence commitment changed")
    _require_intent_commitment(pending, ops)


def require_result_stage_evidence(
    pending: PendingIntent,
    expected_sha256: str | None,
    ops: StageEvidenceOperations,
) -> StageEvidenceCommitment:
    try:
        commitment = load_stage_evidence(pending, ops)
    except (OSError, ReleaseTransitionError) as exc:
        raise TransitionEffectUncertainError(
            "successful result receipt has no exact stage evidence"
        ) from exc
    if expected_sha256 is None or commitment.sha256 != expected_sha256:
        raise TransitionEffectUncertainError(
            "successful result receipt stage evidence hash is inconsistent"
        )
    return commitment


def _require_promoted_identity(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment,
    final: FileSnapshot,
    ops: StageEvidenceOperations,
) -> None:
    try:
        current = ops.snapshot_by_handle(pending.layout.launcher_path)
    except OSError as exc:
        raise TransitionEffectUncertainError(
            "stage evidence contradicts promoted launcher identity"
        ) from exc
    if (
        current != final
        or not moved_stage_matches(current, commitment.stage, pending)
        or ops.path_exists(pending.paths.stage)
    ):
        raise TransitionEffectUncertainError(
            "stage evidence contradicts promoted launcher identity"
        )


def require_promoted_commitment(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment,
    final: FileSnapshot,
    ops: StageEvidenceOperations,
) -> None:
    require_stage_commitment(pending, commitment, ops)
    _require_promoted_identity(pending, commitment, final, ops)


def _require_before_identity(
    pending: PendingIntent, ops: StageEvidenceOperations
) -> None:
    try:
        current = ops.snapshot_by_handle(pending.layout.launcher_path)
    except OSError as exc:
        raise TransitionEffectUncertainError(
            "stage evidence contradicts pre-transition launcher identity"
        ) from exc
    if current != pending.before:
        raise TransitionEffectUncertainError(
            "stage evidence contradicts pre-transition launcher identity"
        )


def require_before_commitment(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment,
    ops: StageEvidenceOperations,
) -> None:
    require_stage_commitment(pending, commitment, ops)
    _require_before_identity(pending, ops)


def _require_no_stage_state(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment | None,
    ops: StageEvidenceOperations,
) -> None:
    if commitment is None:
        changed = ops.path_exists(pending.paths.stage_evidence) or ops.path_exists(
            pending.paths.stage
        )
    else:
        require_stage_commitment(pending, commitment, ops)
        changed = ops.path_exists(pending.paths.stage)
    if changed:
        raise TransitionEffectUncertainError("stage evidence canonical absence changed")


def require_no_effect_state(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment | None,
    ops: StageEvidenceOperations,
) -> None:
    _require_intent_commitment(pending, ops)
    _require_no_stage_state(pending, commitment, ops)
    _require_before_identity(pending, ops)
    _require_no_stage_state(pending, commitment, ops)
    _require_intent_commitment(pending, ops)


def stage_state(
    pending: PendingIntent, ops: StageEvidenceOperations
) -> tuple[bool, FileSnapshot | None, StageEvidenceCommitment | None]:
    stage_exists = ops.path_exists(pending.paths.stage)
    evidence_exists = ops.path_exists(pending.paths.stage_evidence)
    if not stage_exists and not evidence_exists:
        return True, None, None
    if not evidence_exists:
        return False, None, None
    try:
        commitment = load_stage_evidence(pending, ops)
        if not stage_exists:
            return True, None, commitment
        stage = ops.snapshot_by_handle(pending.paths.stage)
    except (OSError, ReleaseTransitionError):
        return False, None, None
    if stage != commitment.stage:
        return False, None, commitment
    return True, stage, commitment


def require_result_commitment(
    pending: PendingIntent,
    result_raw: bytes,
    ops: StageEvidenceOperations,
) -> None:
    try:
        actual = ops.read_bytes(pending.paths.result, max_bytes=MAX_RECEIPT_BYTES)
    except (OSError, ReleaseTransitionError) as exc:
        raise TransitionEffectUncertainError(
            "result receipt cleanup evidence cannot be revalidated"
        ) from exc
    if actual != result_raw:
        raise TransitionEffectUncertainError("result receipt cleanup evidence changed")


def _require_external_backup_commitment(
    pending: PendingIntent,
    external_backup: FileSnapshot,
    ops: StageEvidenceOperations,
) -> None:
    try:
        actual = ops.snapshot_by_handle(pending.paths.external_backup)
    except (OSError, ReleaseTransitionError) as exc:
        raise TransitionEffectUncertainError(
            "successful cleanup evidence cannot be revalidated"
        ) from exc
    if actual != external_backup:
        raise TransitionEffectUncertainError("successful cleanup evidence changed")


def cleanup_committed(
    pending: PendingIntent,
    commitment: StageEvidenceCommitment,
    path: Path,
    expected: FileSnapshot,
    ops: StageEvidenceOperations,
    primary: BaseException | None,
    *,
    promoted: FileSnapshot | None = None,
    require_before: bool = False,
    result_raw: bytes | None = None,
    external_backup: FileSnapshot | None = None,
) -> None:
    """Remove only the atomically owned target after double evidence checks."""

    if external_backup is not None and result_raw is None:
        raise ValueError("external backup cleanup evidence requires a result")
    try:
        if promoted is not None:
            require_promoted_commitment(pending, commitment, promoted, ops)
        elif require_before:
            require_before_commitment(pending, commitment, ops)
        else:
            require_stage_commitment(pending, commitment, ops)
        if external_backup is not None:
            _require_external_backup_commitment(pending, external_backup, ops)
        if result_raw is not None:
            require_result_commitment(pending, result_raw, ops)
        if ops.snapshot_by_handle(path) != expected:
            raise ReleaseTransitionError(
                f"cleanup ownership changed for transaction path: {path}"
            )
        if promoted is not None:
            _require_promoted_identity(pending, commitment, promoted, ops)
        elif require_before:
            _require_before_identity(pending, ops)
        require_stage_commitment(pending, commitment, ops)
        if external_backup is not None:
            _require_external_backup_commitment(pending, external_backup, ops)
        if result_raw is not None:
            require_result_commitment(pending, result_raw, ops)
        ops.remove_file_if_snapshot(path, expected)
    except BaseException as cleanup:
        if primary is None:
            if isinstance(cleanup, TransitionEffectUncertainError):
                raise
            raise TransitionEffectUncertainError(
                "stage evidence cleanup target identity is uncertain"
            ) from cleanup
        primary.add_note(
            f"cleanup failed for {path}: {type(cleanup).__name__}: {cleanup}"
        )


__all__ = [
    "StageEvidenceCommitment",
    "cleanup_committed",
    "load_stage_evidence",
    "require_before_commitment",
    "require_no_effect_state",
    "require_promoted_commitment",
    "require_result_commitment",
    "require_result_stage_evidence",
    "require_stage_commitment",
    "stage_evidence_commitment",
    "stage_state",
    "write_stage_evidence",
]
