"""Fail-closed injected Windows launcher transition state machine."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol
from ctypes import wintypes

from tools.release_windows_evidence import (
    StageEvidenceCommitment,
    cleanup_committed,
    load_stage_evidence,
    require_no_effect_state,
    require_promoted_commitment,
    require_result_commitment,
    require_result_stage_evidence,
    require_stage_commitment,
    stage_evidence_commitment,
    stage_state,
    write_stage_evidence,
)
from tools.release_windows_receipts import (
    INTENT_KEYS,
    MAX_RECEIPT_BYTES,
    RESULT_KEYS,
    SCHEMA_VERSION,
    FileSnapshot,
    LauncherTransitionRequest,
    PendingIntent,
    ReceiptChain,
    ReceiptValidationError,
    ReleaseLayout,
    ReleaseTransitionError,
    TransitionEffectUncertainError,
    TransitionPaths,
    TransitionResult,
    TransitionStatus,
    absolute_path,
    bounded_canonical_bytes,
    canonical_bytes,
    canonical_object,
    intent_payload,
    moved_stage_matches,
    optional_sha,
    parse_operation,
    parse_status,
    prospective_stage_snapshot,
    prospective_success_snapshot,
    request_payload,
    require_keys,
    require_string,
    result_payload,
    result_stage_link,
    same_path,
    snapshot_from_payload,
    stage_evidence_payload,
    success_snapshot_matches,
    transition_paths,
    transition_result,
    validate_previous_result,
    validate_result_evidence,
    validate_result_flags,
    validate_result_identity,
    validate_stage_snapshot,
)


REPLACEFILE_WRITE_THROUGH = 0x00000001
REPLACEFILE_IGNORE_MERGE_ERRORS = 0x00000002
REPLACEFILE_IGNORE_ACL_ERRORS = 0x00000004
Checkpoint = Callable[[str], None]


class ExternalLock(Protocol):
    """An inter-process exclusive lock held by an OS handle."""

    def acquire(self) -> None: ...

    def release(self) -> None: ...


class ReleaseFileOperations(Protocol):  # Creator snapshots; atomic identity removal.
    def open_external_lock(self, path: Path) -> ExternalLock: ...

    def snapshot_by_handle(self, path: Path) -> FileSnapshot: ...

    def copy_create_new_and_flush(
        self, source: Path, destination: Path
    ) -> FileSnapshot: ...

    def write_create_new_and_flush(self, path: Path, payload: bytes) -> None: ...

    def read_bytes(self, path: Path, *, max_bytes: int) -> bytes: ...

    def path_exists(self, path: Path) -> bool: ...

    def remove_file_if_snapshot(self, path: Path, expected: FileSnapshot) -> None: ...


class ReplaceFileNative(Protocol):
    def replace_file(
        self,
        replaced: Path,
        replacement: Path,
        backup: Path,
        *,
        flags: int,
    ) -> None: ...


if os.name == "nt":
    _kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    _replace_file_w: Any = _kernel32.ReplaceFileW
    _replace_file_w.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    _replace_file_w.restype = wintypes.BOOL
else:  # pragma: no cover - production target is Windows

    def _replace_file_w(*_args: object) -> int:
        raise OSError("ReplaceFileW is available only on Windows")


class WindowsReplaceFileNative:
    """Minimal ReplaceFileW wrapper with no unsafe fallback or ignore flags."""

    def replace_file(
        self,
        replaced: Path,
        replacement: Path,
        backup: Path,
        *,
        flags: int,
    ) -> None:
        replaced = absolute_path(replaced)
        replacement = absolute_path(replacement)
        backup = absolute_path(backup)
        if flags != REPLACEFILE_WRITE_THROUGH:
            raise ValueError("ReplaceFileW flags must be exactly WRITE_THROUGH")
        if same_path(replaced, replacement):
            raise ValueError(
                "replacement staging file must differ from stable launcher"
            )
        if os.path.lexists(backup):
            raise FileExistsError(f"native backup path is not empty: {backup}")
        result = _replace_file_w(
            str(replaced),
            str(replacement),
            str(backup),
            REPLACEFILE_WRITE_THROUGH,
            None,
            None,
        )
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())


def _read_canonical(
    ops: ReleaseFileOperations, path: Path, *, role: str
) -> tuple[dict[str, object], bytes]:
    try:
        raw = ops.read_bytes(path, max_bytes=MAX_RECEIPT_BYTES)
    except OSError as exc:
        raise ReceiptValidationError(
            f"{role} cannot be read as canonical JSON"
        ) from exc
    return canonical_object(raw, role), raw


def _write_exact_receipt(
    ops: ReleaseFileOperations,
    path: Path,
    raw: bytes,
    role: str,
) -> None:
    ops.write_create_new_and_flush(path, raw)
    try:
        actual = ops.read_bytes(path, max_bytes=MAX_RECEIPT_BYTES)
    except (OSError, ReleaseTransitionError) as exc:
        raise ReceiptValidationError(f"{role} write verification failed") from exc
    if actual != raw:
        raise ReceiptValidationError(f"{role} write verification failed")


def _verify_previous_receipt(
    chain: ReceiptChain, ops: ReleaseFileOperations
) -> str | None:
    if chain.previous_receipt_path is None:
        return None
    assert chain.previous_receipt_sha256 is not None
    try:
        raw = ops.read_bytes(
            chain.previous_receipt_path,
            max_bytes=MAX_RECEIPT_BYTES,
        )
    except OSError as exc:
        raise ReceiptValidationError("previous receipt cannot be read") from exc
    validate_previous_result(raw, chain.previous_receipt_sha256)
    return chain.previous_receipt_sha256


def _recorded_paths(
    payload: dict[str, object], expected: TransitionPaths
) -> tuple[Path, Path, Path, Path]:
    recorded = (
        absolute_path(Path(require_string(payload, "stage_path", "intent"))),
        absolute_path(Path(require_string(payload, "stage_evidence_path", "intent"))),
        absolute_path(Path(require_string(payload, "native_backup_path", "intent"))),
        absolute_path(Path(require_string(payload, "external_backup_path", "intent"))),
    )
    expected_paths = (
        expected.stage,
        expected.stage_evidence,
        expected.native_backup,
        expected.external_backup,
    )
    if recorded != expected_paths:
        raise ReceiptValidationError("intent paths are inconsistent")
    return recorded


def _load_intent(
    layout: ReleaseLayout, intent_path: Path, ops: ReleaseFileOperations
) -> PendingIntent:
    intent_path = absolute_path(intent_path)
    if intent_path.parent != layout.receipts_directory:
        raise ReceiptValidationError("intent path is outside the receipt directory")
    payload, raw = _read_canonical(ops, intent_path, role="intent receipt")
    require_keys(payload, INTENT_KEYS, "intent receipt")
    schema = payload["schema_version"]
    if (
        isinstance(schema, bool)
        or schema != SCHEMA_VERSION
        or payload["receipt_type"] != "launcher_transition_intent"
    ):
        raise ReceiptValidationError("intent receipt schema is unsupported")
    before = snapshot_from_payload(payload["before"], "before")
    desired = snapshot_from_payload(payload["desired"], "desired")
    operation = parse_operation(payload["operation"])
    previous = optional_sha(payload["previous_receipt_sha256"], "previous receipt")
    desired_launcher = absolute_path(
        Path(require_string(payload, "desired_launcher", "intent"))
    )
    launcher = absolute_path(Path(require_string(payload, "launcher_path", "intent")))
    if not same_path(launcher, layout.launcher_path):
        raise ReceiptValidationError("intent stable launcher is inconsistent")
    lock = absolute_path(Path(require_string(payload, "lock_path", "intent")))
    if not same_path(lock, layout.lock_path):
        raise ReceiptValidationError("intent lock path is inconsistent")
    request = LauncherTransitionRequest(
        layout,
        desired_launcher,
        before,
        desired,
        operation,
        ReceiptChain(),
    )
    transition_id = hashlib.sha256(
        canonical_bytes(request_payload(request, previous))
    ).hexdigest()
    expected = transition_paths(layout, transition_id, before)
    _recorded_paths(payload, expected)
    if payload["transition_id"] != transition_id or intent_path != expected.intent:
        raise ReceiptValidationError("intent identity is inconsistent")
    return PendingIntent(
        layout=layout,
        operation=operation,
        desired_launcher=desired_launcher,
        before=before,
        desired=desired,
        previous_receipt_sha256=previous,
        paths=expected,
        intent_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _exact_snapshot(actual: FileSnapshot, expected: FileSnapshot, role: str) -> None:
    if actual != expected:
        raise ReleaseTransitionError(f"{role} failed exact CAS validation")


def _safe_source(snapshot: FileSnapshot, role: str) -> None:
    if snapshot.file_system.casefold() != "ntfs":
        raise ReleaseTransitionError(f"{role} must reside on NTFS")
    if snapshot.link_count != 1 or snapshot.is_reparse_point:
        raise ReleaseTransitionError(f"{role} must be one non-reparse file")


def _same_content(actual: FileSnapshot, expected: FileSnapshot, role: str) -> None:
    if actual.size != expected.size or actual.sha256 != expected.sha256:
        raise ReleaseTransitionError(f"{role} content is inconsistent")


def _same_content_and_acl(
    actual: FileSnapshot, expected: FileSnapshot, role: str
) -> None:
    _same_content(actual, expected, role)
    if (
        actual.security_descriptor != expected.security_descriptor
        or actual.security_descriptor_sha256 != expected.security_descriptor_sha256
    ):
        raise ReleaseTransitionError(f"{role} security descriptor is inconsistent")


def _same_moved_file(actual: FileSnapshot, expected: FileSnapshot, role: str) -> None:
    _same_content_and_acl(actual, expected, role)
    if (
        actual.volume_id != expected.volume_id
        or actual.file_id != expected.file_id
        or actual.file_system.casefold() != expected.file_system.casefold()
    ):
        raise ReleaseTransitionError(f"{role} physical identity is inconsistent")


@contextmanager
def _external_lock(ops: ReleaseFileOperations, path: Path) -> Iterator[None]:
    lock = ops.open_external_lock(path)
    lock.acquire()
    try:
        yield
    except BaseException as primary:
        try:
            lock.release()
        except BaseException as cleanup:
            primary.add_note(
                f"external lock cleanup failed: {type(cleanup).__name__}: {cleanup}"
            )
        raise
    else:
        lock.release()


def _cleanup(
    ops: ReleaseFileOperations,
    path: Path,
    expected: FileSnapshot,
    primary: BaseException,
) -> None:
    try:
        ops.remove_file_if_snapshot(path, expected)
    except BaseException as cleanup:
        primary.add_note(
            f"cleanup failed for {path}: {type(cleanup).__name__}: {cleanup}"
        )


def _ensure_external_backup(
    pending: PendingIntent, ops: ReleaseFileOperations
) -> FileSnapshot:
    path = pending.paths.external_backup
    created: FileSnapshot | None = None
    if not ops.path_exists(path):
        created = ops.copy_create_new_and_flush(pending.layout.launcher_path, path)
    try:
        snapshot = ops.snapshot_by_handle(path)
        if created is not None:
            _exact_snapshot(snapshot, created, "content-addressed backup ownership")
        _same_content_and_acl(snapshot, pending.before, "content-addressed backup")
        return snapshot
    except BaseException as primary:
        if created is not None:
            _cleanup(ops, path, created, primary)
        raise


def _create_stage(pending: PendingIntent, ops: ReleaseFileOperations) -> FileSnapshot:
    stage = pending.paths.stage
    if ops.path_exists(stage):
        raise ReleaseTransitionError("unique staging path is already occupied")
    created = ops.copy_create_new_and_flush(pending.desired_launcher, stage)
    try:
        snapshot = ops.snapshot_by_handle(stage)
        _exact_snapshot(snapshot, created, "staging ownership")
        validate_stage_snapshot(
            snapshot,
            pending.before,
            pending.desired,
            pending.paths.stage,
        )
        return snapshot
    except BaseException as primary:
        _cleanup(ops, stage, created, primary)
        raise


def _write_result(
    pending: PendingIntent,
    status: TransitionStatus,
    current: FileSnapshot | None,
    stage_evidence_sha256: str | None,
    ops: ReleaseFileOperations,
    *,
    performed: bool,
    recovered: bool,
) -> tuple[TransitionResult, bytes]:
    validate_result_evidence(
        status,
        current,
        pending.before,
        pending.desired,
        pending.layout.launcher_path,
    )
    raw = bounded_canonical_bytes(
        result_payload(
            pending,
            status,
            current,
            stage_evidence_sha256,
            performed=performed,
            recovered=recovered,
        ),
        "result receipt",
    )
    _write_exact_receipt(ops, pending.paths.result, raw, "result receipt")
    result = transition_result(
        pending,
        status,
        current,
        stage_evidence_sha256,
        raw,
        performed=performed,
        recovered=recovered,
    )
    return result, raw


def _load_result(
    pending: PendingIntent, ops: ReleaseFileOperations
) -> tuple[TransitionResult, bytes]:
    payload, raw = _read_canonical(ops, pending.paths.result, role="result receipt")
    role = "result receipt"
    require_keys(payload, RESULT_KEYS, role)
    schema = payload["schema_version"]
    if (
        isinstance(schema, bool)
        or schema != SCHEMA_VERSION
        or payload["receipt_type"] != "launcher_transition_result"
    ):
        raise ReceiptValidationError(f"{role} schema is unsupported")
    status = parse_status(payload["status"], role)
    before = snapshot_from_payload(payload["before"], "result before")
    desired = snapshot_from_payload(payload["desired"], "result desired")
    validate_result_identity(payload, pending, before, desired)
    performed, recovered = validate_result_flags(payload, status, role)
    current_value = payload["current"]
    current = (
        None
        if current_value is None
        else snapshot_from_payload(current_value, "result current")
    )
    validate_result_evidence(
        status,
        current,
        pending.before,
        pending.desired,
        pending.layout.launcher_path,
    )
    stage_sha = result_stage_link(payload, status, role)
    return transition_result(
        pending,
        status,
        current,
        stage_sha,
        raw,
        performed=performed,
        recovered=recovered,
    ), raw


def _current_or_none(ops: ReleaseFileOperations, path: Path) -> FileSnapshot | None:
    try:
        return ops.snapshot_by_handle(path)
    except FileNotFoundError:
        return None


def _confirmed_native_backup(
    pending: PendingIntent, ops: ReleaseFileOperations
) -> tuple[FileSnapshot, FileSnapshot] | None:
    try:
        external = ops.snapshot_by_handle(pending.paths.external_backup)
        native = ops.snapshot_by_handle(pending.paths.native_backup)
        _same_content_and_acl(external, pending.before, "content-addressed backup")
        _same_moved_file(native, pending.before, "native backup")
    except (FileNotFoundError, ReleaseTransitionError):
        return None
    return native, external


def _verify_existing_result_state(
    result: TransitionResult,
    result_raw: bytes,
    pending: PendingIntent,
    ops: ReleaseFileOperations,
) -> None:
    current = _current_or_none(ops, pending.layout.launcher_path)
    if result.status == "success":
        success_commitment = require_result_stage_evidence(
            pending, result.stage_evidence_sha256, ops
        )
        if result.current is None or current != result.current:
            raise TransitionEffectUncertainError(
                "successful result receipt contradicts current physical identity"
            )
        require_promoted_commitment(pending, success_commitment, result.current, ops)
        try:
            external = ops.snapshot_by_handle(pending.paths.external_backup)
            _same_content_and_acl(
                external,
                pending.before,
                "content-addressed backup",
            )
        except (FileNotFoundError, ReleaseTransitionError) as exc:
            raise TransitionEffectUncertainError(
                "successful result receipt has no exact external backup"
            ) from exc
    elif result.status == "no_effect":
        if current != pending.before:
            raise TransitionEffectUncertainError(
                "no-effect result receipt contradicts current launcher state"
            )
        trusted, stage, commitment = stage_state(pending, ops)
        evidence_sha = None if commitment is None else commitment.sha256
        if not trusted or evidence_sha != result.stage_evidence_sha256:
            raise TransitionEffectUncertainError(
                "no-effect result receipt has untrusted stage evidence"
            )
        if stage is not None:
            assert commitment is not None
            cleanup_committed(
                pending,
                commitment,
                pending.paths.stage,
                stage,
                ops,
                None,
                require_before=True,
                result_raw=result_raw,
            )
        else:
            require_no_effect_state(pending, commitment, ops)
    require_result_commitment(pending, result_raw, ops)


def _recovered_success_evidence(
    pending: PendingIntent,
    current: FileSnapshot | None,
    ops: ReleaseFileOperations,
) -> tuple[
    tuple[FileSnapshot, FileSnapshot] | None,
    StageEvidenceCommitment | None,
]:
    try:
        commitment = load_stage_evidence(pending, ops)
    except (OSError, ReleaseTransitionError):
        return None, None
    if ops.path_exists(pending.paths.stage) or not moved_stage_matches(
        current, commitment.stage, pending
    ):
        return None, commitment
    return _confirmed_native_backup(pending, ops), commitment


def _recover_locked(
    pending: PendingIntent, ops: ReleaseFileOperations
) -> TransitionResult:
    if ops.path_exists(pending.paths.result):
        result, result_raw = _load_result(pending, ops)
        _verify_existing_result_state(result, result_raw, pending, ops)
        return result
    current = _current_or_none(ops, pending.layout.launcher_path)
    backups: tuple[FileSnapshot, FileSnapshot] | None = None
    stage: FileSnapshot | None = None
    commitment: StageEvidenceCommitment | None = None
    if current == pending.before:
        trusted, stage, commitment = stage_state(pending, ops)
        status: TransitionStatus = "no_effect" if trusted else "uncertain"
    elif success_snapshot_matches(
        current, pending.before, pending.desired, pending.layout.launcher_path
    ):
        backups, commitment = _recovered_success_evidence(pending, current, ops)
        status = "success" if backups is not None else "uncertain"
    else:
        status = "uncertain"
    evidence_sha = None if commitment is None else commitment.sha256
    result, result_raw = _write_result(
        pending,
        status,
        current,
        evidence_sha,
        ops,
        performed=False,
        recovered=True,
    )
    if status == "success":
        assert backups is not None and commitment is not None and current is not None
        native_backup, external_backup = backups
        cleanup_committed(
            pending,
            commitment,
            pending.paths.native_backup,
            native_backup,
            ops,
            None,
            promoted=current,
            result_raw=result_raw,
            external_backup=external_backup,
        )
    elif status == "no_effect" and stage is not None:
        assert commitment is not None
        cleanup_committed(
            pending,
            commitment,
            pending.paths.stage,
            stage,
            ops,
            None,
            require_before=True,
            result_raw=result_raw,
        )
    elif status == "no_effect":
        require_no_effect_state(pending, commitment, ops)
    require_result_commitment(pending, result_raw, ops)
    return result


def recover_pending_transition(
    layout: ReleaseLayout,
    intent_path: Path,
    *,
    ops: ReleaseFileOperations,
) -> TransitionResult:
    """Classify a pending intent from physical evidence without replacing a file."""

    preview = _load_intent(layout, intent_path, ops)
    with _external_lock(ops, preview.layout.lock_path):
        pending = _load_intent(layout, intent_path, ops)
        if pending.intent_sha256 != preview.intent_sha256:
            raise ReceiptValidationError("intent changed while acquiring its lock")
        return _recover_locked(pending, ops)


def _new_pending(
    request: LauncherTransitionRequest,
    previous: str | None,
    ops: ReleaseFileOperations,
) -> PendingIntent:
    before = ops.snapshot_by_handle(request.layout.launcher_path)
    desired = ops.snapshot_by_handle(request.desired_launcher)
    _exact_snapshot(before, request.expected_current, "current launcher")
    _exact_snapshot(desired, request.expected_desired, "desired launcher")
    _safe_source(before, "current launcher")
    _safe_source(desired, "desired launcher")
    transition_id = hashlib.sha256(
        canonical_bytes(request_payload(request, previous))
    ).hexdigest()
    paths = transition_paths(request.layout, transition_id, before)
    intent_raw = bounded_canonical_bytes(
        intent_payload(request, previous, paths), "intent receipt"
    )
    pending = PendingIntent(
        layout=request.layout,
        operation=request.operation,
        desired_launcher=request.desired_launcher,
        before=before,
        desired=desired,
        previous_receipt_sha256=previous,
        paths=paths,
        intent_sha256=hashlib.sha256(intent_raw).hexdigest(),
    )
    prospective_stage = prospective_stage_snapshot(pending)
    bounded_canonical_bytes(
        stage_evidence_payload(pending, prospective_stage),
        "stage evidence receipt",
    )
    prospective = prospective_success_snapshot(before, desired)
    bounded_canonical_bytes(
        result_payload(
            pending,
            "success",
            prospective,
            "f" * 64,
            performed=True,
            recovered=False,
        ),
        "result receipt",
    )
    _write_exact_receipt(ops, paths.intent, intent_raw, "intent receipt")
    return pending


def _final_pre_native_cas(
    request: LauncherTransitionRequest,
    pending: PendingIntent,
    stage: FileSnapshot,
    ops: ReleaseFileOperations,
) -> None:
    _exact_snapshot(
        ops.snapshot_by_handle(request.layout.launcher_path),
        request.expected_current,
        "current launcher",
    )
    _exact_snapshot(
        ops.snapshot_by_handle(request.desired_launcher),
        request.expected_desired,
        "desired launcher",
    )
    if ops.path_exists(pending.paths.native_backup):
        raise ReleaseTransitionError("unique native backup path is already occupied")
    _exact_snapshot(
        ops.snapshot_by_handle(pending.paths.stage),
        stage,
        "staged launcher",
    )


def _verify_native_result(
    request: LauncherTransitionRequest,
    pending: PendingIntent,
    stage: FileSnapshot,
    ops: ReleaseFileOperations,
) -> tuple[FileSnapshot, FileSnapshot]:
    final = ops.snapshot_by_handle(request.layout.launcher_path)
    native_backup = ops.snapshot_by_handle(pending.paths.native_backup)
    desired_after = ops.snapshot_by_handle(request.desired_launcher)
    _same_content(final, pending.desired, "final launcher")
    if (
        final.security_descriptor != pending.before.security_descriptor
        or final.security_descriptor_sha256 != pending.before.security_descriptor_sha256
    ):
        raise ReleaseTransitionError(
            "final launcher security descriptor was not preserved"
        )
    if final.volume_id != stage.volume_id or final.file_id != stage.file_id:
        raise ReleaseTransitionError("final launcher is not the staged file")
    _same_moved_file(native_backup, pending.before, "native backup")
    _exact_snapshot(desired_after, pending.desired, "desired launcher source")
    _ensure_external_backup(pending, ops)
    return final, native_backup


def _transition_locked(
    request: LauncherTransitionRequest,
    ops: ReleaseFileOperations,
    native: ReplaceFileNative,
    checkpoint: Checkpoint,
) -> TransitionResult:
    previous = _verify_previous_receipt(request.receipt_chain, ops)
    transition_id = hashlib.sha256(
        canonical_bytes(request_payload(request, previous))
    ).hexdigest()
    expected_paths = transition_paths(
        request.layout, transition_id, request.expected_current
    )
    if ops.path_exists(expected_paths.intent):
        pending = _load_intent(request.layout, expected_paths.intent, ops)
        return _recover_locked(pending, ops)
    if ops.path_exists(expected_paths.result):
        raise ReceiptValidationError("result exists without its intent receipt")
    if ops.path_exists(expected_paths.stage_evidence):
        raise ReceiptValidationError("stage evidence exists without its intent receipt")

    pending = _new_pending(request, previous, ops)
    checkpoint("after_intent")
    native_started = False
    result_written = False
    stage_snapshot: FileSnapshot | None = None
    stage_commitment: StageEvidenceCommitment | None = None
    final_snapshot: FileSnapshot | None = None
    native_backup_snapshot: FileSnapshot | None = None
    external_backup_snapshot: FileSnapshot | None = None
    result_raw: bytes | None = None
    primary: BaseException | None = None
    try:
        external_backup_snapshot = _ensure_external_backup(pending, ops)
        checkpoint("after_external_backup")
        stage_snapshot = _create_stage(pending, ops)
        stage_commitment = stage_evidence_commitment(pending, stage_snapshot)
        write_stage_evidence(pending, stage_commitment, ops)
        checkpoint("after_stage")
        _final_pre_native_cas(request, pending, stage_snapshot, ops)
        checkpoint("before_replace")
        require_stage_commitment(pending, stage_commitment, ops)
        _final_pre_native_cas(request, pending, stage_snapshot, ops)
        native_started = True
        try:
            native.replace_file(
                request.layout.launcher_path,
                pending.paths.stage,
                pending.paths.native_backup,
                flags=REPLACEFILE_WRITE_THROUGH,
            )
        except BaseException as exc:
            raise TransitionEffectUncertainError(
                "ReplaceFileW may have committed; recover the pending intent"
            ) from exc
        checkpoint("after_replace")
        require_stage_commitment(pending, stage_commitment, ops)
        final_snapshot, native_backup_snapshot = _verify_native_result(
            request,
            pending,
            stage_snapshot,
            ops,
        )
        checkpoint("after_verification")
        require_promoted_commitment(pending, stage_commitment, final_snapshot, ops)
        result, result_raw = _write_result(
            pending,
            "success",
            final_snapshot,
            stage_commitment.sha256,
            ops,
            performed=True,
            recovered=False,
        )
        result_written = True
        checkpoint("after_result")
        return result
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if (
            not native_started
            and stage_snapshot is not None
            and stage_commitment is not None
        ):
            cleanup_committed(
                pending,
                stage_commitment,
                pending.paths.stage,
                stage_snapshot,
                ops,
                primary,
                require_before=True,
            )
        if (
            result_written
            and native_backup_snapshot is not None
            and stage_commitment is not None
            and final_snapshot is not None
            and external_backup_snapshot is not None
            and result_raw is not None
        ):
            cleanup_committed(
                pending,
                stage_commitment,
                pending.paths.native_backup,
                native_backup_snapshot,
                ops,
                primary,
                promoted=final_snapshot,
                result_raw=result_raw,
                external_backup=external_backup_snapshot,
            )


def transition_launcher(
    request: LauncherTransitionRequest,
    *,
    ops: ReleaseFileOperations,
    native: ReplaceFileNative,
    checkpoint: Checkpoint | None = None,
) -> TransitionResult:
    """Execute promote, rollback, or re-promote through one state machine."""

    callback = checkpoint if checkpoint is not None else lambda _name: None
    with _external_lock(ops, request.layout.lock_path):
        return _transition_locked(request, ops, native, callback)


__all__ = [
    "FileSnapshot",
    "LauncherTransitionRequest",
    "ReceiptChain",
    "ReceiptValidationError",
    "REPLACEFILE_IGNORE_ACL_ERRORS",
    "REPLACEFILE_IGNORE_MERGE_ERRORS",
    "REPLACEFILE_WRITE_THROUGH",
    "ReleaseFileOperations",
    "ReleaseLayout",
    "ReleaseTransitionError",
    "ReplaceFileNative",
    "TransitionEffectUncertainError",
    "TransitionResult",
    "WindowsReplaceFileNative",
    "recover_pending_transition",
    "transition_launcher",
]
