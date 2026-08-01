"""Explicit durable recording for read-only file-action reconciliations.
# region [00] Contexto del módulo
# Módulo: _04_Nucleo_Operativo/file_action_reconciliation_store.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


This repository stores observation evidence only.  It never performs, retries,
or authorizes a filesystem mutation.
"""

# region [01] Dependencias del módulo
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

import xxhash

from .file_action_recovery import FileActionReconciliation
# endregion [01]

# region [02] Implementación


FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION = 1
_RECOMMENDATIONS = {
    "confirmed": frozenset({"confirm_action_record"}),
    "not_performed": frozenset({"review_before_new_authorized_attempt"}),
    "ambiguous": frozenset({"preserve_evidence_and_review_manually"}),
    "impossible_to_check": frozenset(
        {"preserve_evidence_and_review_manually"}
    ),
}


class FileActionReconciliationConflict(RuntimeError):
    """The action or reconciliation event frontier changed before recording."""


@dataclass(frozen=True, slots=True)
class RecordedFileActionReconciliation:
    """One immutable reconciliation event committed to framework state."""

    event_id: int
    action_id: int
    sequence: int
    previous_event_id: int | None
    reconciliation_key: str
    observed_ns: int
    recorded_ns: int
    action_status: str
    reconciler_signature: str
    event_schema_version: int
    actor: str
    provenance_json: str
    classification: str
    recommendation: str
    detail: str
    evidence_json: str


def _validate_reconciliation(reconciliation: FileActionReconciliation) -> None:
    if any(
        isinstance(identifier, bool)
        or not isinstance(identifier, int)
        or identifier < 1
        for identifier in (reconciliation.action_id, reconciliation.run_id)
    ):
        raise ValueError("file-action and run identifiers must be positive")
    if not reconciliation.action_type:
        raise ValueError("file-action type cannot be empty")
    if not reconciliation.source_path:
        raise ValueError("file-action source path cannot be empty")
    if reconciliation.recorded_status not in {"applying", "recovery_required"}:
        raise ValueError(
            "reconciliation status must be applying or recovery_required"
        )
    if not reconciliation.reconciler_signature:
        raise ValueError("reconciler signature cannot be empty")
    recommendations = _RECOMMENDATIONS.get(reconciliation.classification)
    if recommendations is None:
        raise ValueError(
            f"invalid file-action classification: {reconciliation.classification}"
        )
    if reconciliation.recommendation not in recommendations:
        raise ValueError(
            "recommendation is incompatible with the reconciliation classification"
        )
    if not reconciliation.detail:
        raise ValueError("reconciliation detail cannot be empty")


def _classification_payload(reconciliation: FileActionReconciliation) -> str:
    return json.dumps(
        {
            "action_id": reconciliation.action_id,
            "action_type": reconciliation.action_type,
            "classification": reconciliation.classification,
            "detail": reconciliation.detail,
            "idempotency_key": reconciliation.idempotency_key,
            "recommendation": reconciliation.recommendation,
            "reconciler_signature": reconciliation.reconciler_signature,
            "recorded_status": reconciliation.recorded_status,
            "run_id": reconciliation.run_id,
            "source_path": reconciliation.source_path,
            "target_path": reconciliation.target_path,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _reconciliation_key(
    classification_payload: str,
    expected_previous_event_id: int | None,
    actor: str,
    provenance_json: str,
) -> str:
    payload = json.dumps(
        [
            "file-action-reconciliation-v1",
            FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION,
            expected_previous_event_id,
            actor,
            provenance_json,
            classification_payload,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return xxhash.xxh3_128_hexdigest(payload)


def _canonical_provenance_json(raw: str) -> str:
    try:
        document = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("reconciliation provenance must be valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("reconciliation provenance must be a JSON object")
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _required_sqlite_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise sqlite3.DatabaseError(f"{label} is not a SQLite integer")
    return value


def _record_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> RecordedFileActionReconciliation:
    return RecordedFileActionReconciliation(
        event_id=_required_sqlite_integer(row[0], label="reconciliation event id"),
        action_id=_required_sqlite_integer(row[1], label="file-action id"),
        sequence=_required_sqlite_integer(row[2], label="reconciliation sequence"),
        previous_event_id=(
            None
            if row[3] is None
            else _required_sqlite_integer(row[3], label="previous event id")
        ),
        reconciliation_key=str(row[4]),
        observed_ns=_required_sqlite_integer(row[5], label="observation time"),
        recorded_ns=_required_sqlite_integer(row[6], label="recording time"),
        action_status=str(row[7]),
        reconciler_signature=str(row[8]),
        event_schema_version=_required_sqlite_integer(
            row[9], label="reconciliation event schema version"
        ),
        actor=str(row[10]),
        provenance_json=str(row[11]),
        classification=str(row[12]),
        recommendation=str(row[13]),
        detail=str(row[14]),
        evidence_json=str(row[15]),
    )


def _after_reconciliation_insert(
    _connection: sqlite3.Connection,
    _event_id: int,
) -> None:
    """Fault-injection seam executed before the transaction commits."""


def record_file_action_reconciliation(
    connection: sqlite3.Connection,
    reconciliation: FileActionReconciliation,
    *,
    actor: str,
    provenance_json: str,
    expected_previous_event_id: int | None,
    observed_ns: int | None = None,
) -> RecordedFileActionReconciliation:
    """Append evidence through action-status and prior-event compare-and-swap.

    An identical request returns its existing immutable event.  A stale action
    status or prior event raises :class:`FileActionReconciliationConflict`.
    The function only writes SQLite evidence and never touches either path.
    """

    _validate_reconciliation(reconciliation)
    canonical_actor = actor.strip()
    if not canonical_actor:
        raise ValueError("reconciliation actor cannot be empty")
    canonical_provenance = _canonical_provenance_json(provenance_json)
    if expected_previous_event_id is not None and (
        isinstance(expected_previous_event_id, bool)
        or not isinstance(expected_previous_event_id, int)
        or expected_previous_event_id < 1
    ):
        raise ValueError("expected previous reconciliation event must be positive")
    if observed_ns is None:
        observed_ns = time.time_ns()
    if (
        isinstance(observed_ns, bool)
        or not isinstance(observed_ns, int)
        or observed_ns < 0
    ):
        raise ValueError("reconciliation observation time must be a nonnegative integer")
    if connection.in_transaction:
        raise RuntimeError("reconciliation recording requires no active transaction")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError("reconciliation recording requires foreign_keys=ON")

    classification_payload = _classification_payload(reconciliation)
    reconciliation_key = _reconciliation_key(
        classification_payload,
        expected_previous_event_id,
        canonical_actor,
        canonical_provenance,
    )
    columns = """reconciliation_event_id,action_id,sequence,previous_event_id,
    reconciliation_key,observed_ns,recorded_ns,action_status,reconciler_signature,
    event_schema_version,actor,provenance_json,classification,recommendation,
    detail,evidence_json"""

    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            f"SELECT {columns} FROM file_action_reconciliation_events "
            "WHERE reconciliation_key=?",
            (reconciliation_key,),
        ).fetchone()
        if existing is not None:
            recorded = _record_from_row(existing)
            try:
                stored_evidence = json.loads(recorded.evidence_json)
            except (TypeError, ValueError) as exc:
                raise sqlite3.DatabaseError(
                    "stored reconciliation evidence is not valid JSON"
                ) from exc
            expected = (
                reconciliation.action_id,
                expected_previous_event_id,
                reconciliation.recorded_status,
                reconciliation.reconciler_signature,
                FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION,
                canonical_actor,
                canonical_provenance,
                reconciliation.classification,
                reconciliation.recommendation,
                reconciliation.detail,
            )
            actual = (
                recorded.action_id,
                recorded.previous_event_id,
                recorded.action_status,
                recorded.reconciler_signature,
                recorded.event_schema_version,
                recorded.actor,
                recorded.provenance_json,
                recorded.classification,
                recorded.recommendation,
                recorded.detail,
            )
            if (
                actual != expected
                or not isinstance(stored_evidence, dict)
                or stored_evidence.get("reconciliation")
                != json.loads(classification_payload)
            ):
                raise RuntimeError("file-action reconciliation-key collision")
            connection.commit()
            return recorded

        action = connection.execute(
            """SELECT run_id,idempotency_key,action_type,source_path,target_path,
            status,expected_identity_json,effect_receipt_json
            FROM file_actions WHERE action_id=?""",
            (reconciliation.action_id,),
        ).fetchone()
        if action is None:
            raise FileActionReconciliationConflict(
                f"file action disappeared: {reconciliation.action_id}"
            )
        current_status = str(action[5])
        if current_status != reconciliation.recorded_status:
            raise FileActionReconciliationConflict(
                "file action status changed before reconciliation recording: "
                f"{reconciliation.recorded_status} -> {current_status}"
            )
        actual_identity = (
            _required_sqlite_integer(action[0], label="file-action run id"),
            None if action[1] is None else str(action[1]),
            str(action[2]),
            str(action[3]),
            None if action[4] is None else str(action[4]),
        )
        expected_identity = (
            reconciliation.run_id,
            reconciliation.idempotency_key,
            reconciliation.action_type,
            reconciliation.source_path,
            reconciliation.target_path,
        )
        if actual_identity != expected_identity:
            raise FileActionReconciliationConflict(
                "file action identity changed before reconciliation recording"
            )

        latest = connection.execute(
            """SELECT reconciliation_event_id,sequence
            FROM file_action_reconciliation_events WHERE action_id=?
            ORDER BY reconciliation_event_id DESC LIMIT 1""",
            (reconciliation.action_id,),
        ).fetchone()
        latest_event_id = (
            None
            if latest is None
            else _required_sqlite_integer(latest[0], label="latest event id")
        )
        if latest_event_id != expected_previous_event_id:
            raise FileActionReconciliationConflict(
                "latest event changed before reconciliation recording: "
                f"expected {expected_previous_event_id!r}, found {latest_event_id!r}"
            )
        sequence = (
            1
            if latest is None
            else _required_sqlite_integer(
                latest[1], label="latest reconciliation sequence"
            )
            + 1
        )
        recorded_ns = time.time_ns()
        evidence_json = json.dumps(
            {
                "action": {
                    "action_id": reconciliation.action_id,
                    "action_type": reconciliation.action_type,
                    "effect_receipt_json": (
                        None if action[7] is None else str(action[7])
                    ),
                    "expected_identity_json": (
                        None if action[6] is None else str(action[6])
                    ),
                    "idempotency_key": reconciliation.idempotency_key,
                    "run_id": reconciliation.run_id,
                    "source_path": reconciliation.source_path,
                    "status": reconciliation.recorded_status,
                    "target_path": reconciliation.target_path,
                },
                "authorizes_filesystem_mutation": False,
                "actor": canonical_actor,
                "event_schema_version": (
                    FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION
                ),
                "provenance": json.loads(canonical_provenance),
                "reconciliation": json.loads(classification_payload),
                "schema_version": FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cursor = connection.execute(
            """INSERT INTO file_action_reconciliation_events(
            action_id,sequence,previous_event_id,reconciliation_key,observed_ns,
            recorded_ns,action_status,reconciler_signature,event_schema_version,
            actor,provenance_json,classification,recommendation,detail,evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                reconciliation.action_id,
                sequence,
                expected_previous_event_id,
                reconciliation_key,
                observed_ns,
                recorded_ns,
                reconciliation.recorded_status,
                reconciliation.reconciler_signature,
                FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION,
                canonical_actor,
                canonical_provenance,
                reconciliation.classification,
                reconciliation.recommendation,
                reconciliation.detail,
                evidence_json,
            ),
        )
        if cursor.lastrowid is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("SQLite did not return a reconciliation event identifier")
        event_id = int(cursor.lastrowid)
        _after_reconciliation_insert(connection, event_id)
        row = connection.execute(
            f"SELECT {columns} FROM file_action_reconciliation_events "
            "WHERE reconciliation_event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("reconciliation event disappeared before commit")
        recorded = _record_from_row(row)
        connection.commit()
        return recorded
    except BaseException:
        connection.rollback()
        raise


__all__ = [
    "FILE_ACTION_RECONCILIATION_EVENT_SCHEMA_VERSION",
    "FileActionReconciliationConflict",
    "RecordedFileActionReconciliation",
    "record_file_action_reconciliation",
]
# endregion [02]
