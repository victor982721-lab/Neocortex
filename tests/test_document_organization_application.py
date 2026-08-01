# region [00] Contexto del módulo
# Módulo: tests/test_document_organization_application.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]
# region [01] Dependencias del módulo
from __future__ import annotations

import os
import sqlite3
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest

import _04_Nucleo_Operativo.document_organization_application as organization_application
from _02_Deduplicacion import FileSnapshot, snapshot_path
from _04_Nucleo_Operativo.corpus_access import (
    CorpusAccessPolicy,
    CorpusMutationGuard,
)
from _04_Nucleo_Operativo.document_catalog import (
    document_catalog_database,
    update_document_catalog,
)
from _04_Nucleo_Operativo.document_organization import (
    apply_document_organization,
    plan_document_organization,
)
from _04_Nucleo_Operativo.docx_state import initialize_docx_state
from _04_Nucleo_Operativo.protected_content import (
    ProtectedContentPolicy,
    ProtectedPathSpec,
)
from tests.internal_paths_test_support import disjoint_internal_paths_policy
# endregion [01]

# region [02] Implementación


def _normal_mutation_guard(root: Path) -> CorpusMutationGuard:
    return CorpusMutationGuard(
        CorpusAccessPolicy.capture("normal", root),
        disjoint_internal_paths_policy(root),
    )


def _protected_mutation_guard(
    root: Path,
    protected_root: Path,
) -> CorpusMutationGuard:
    return CorpusMutationGuard(
        CorpusAccessPolicy.capture("normal", root),
        disjoint_internal_paths_policy(root),
        ProtectedContentPolicy.capture(
            (
                ProtectedPathSpec(
                    "fixture_read_only",
                    "tree",
                    "analyze_read_only",
                    protected_root,
                ),
            )
        ),
    )


def _seed_docx(state: Path, source: Path) -> None:
    text = "Formato SERINTRA formulario de control"
    initialize_docx_state(state)
    snapshot = snapshot_path(source)
    with sqlite3.connect(state) as connection:
        connection.execute(
            """INSERT INTO documents(
            file_key,path,size,mtime_ns,birthtime_ns,processing_signature,status,
            integrity_status,text_zlib,text_chars,text_xxh3_128,last_seen_run_id,
            updated_ns,title,author)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"{snapshot.volume_id}:{snapshot.file_id}",
                snapshot.path,
                snapshot.size,
                snapshot.mtime_ns,
                snapshot.birthtime_ns,
                "docx-test-v1",
                "complete",
                "valid",
                zlib.compress(text.encode("utf-8")),
                len(text),
                "docx-text-xxh3-test",
                1,
                1,
                "Formato SERINTRA",
                "SERINTRA",
            ),
        )


@pytest.mark.parametrize("protected_field", ("source_path", "destination_path"))
def test_apply_blocks_stale_protected_row_without_path_syscalls_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_field: str,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    protected_root = tmp_path / "read-only"
    protected_root.mkdir()
    protected_source = protected_root / "protected stale row.docx"
    protected_source.write_bytes(b"protected")
    sources = tuple(tmp_path / f"Formato SERINTRA {index}.docx" for index in range(2))
    for source in sources:
        source.write_bytes(f"safe fixture {source.stem}".encode())
        _seed_docx(state_directory / "docx.sqlite3", source)
    update_document_catalog(state_directory)
    catalog_path = state_directory / "document_catalog.sqlite3"
    destination_root = tmp_path / "organizados"
    plan_document_organization(catalog_path, destination_root)

    with document_catalog_database(catalog_path) as catalog:
        rows = catalog.execute(
            """SELECT plan_id,source_path,destination_path
            FROM organization_plans ORDER BY plan_id"""
        ).fetchall()
        assert len(rows) == 2
        blocked_plan_id = str(rows[0]["plan_id"])
        safe_plan_id = str(rows[1]["plan_id"])
        blocked_original_source = Path(str(rows[0]["source_path"]))
        safe_source = Path(str(rows[1]["source_path"]))
        safe_destination = Path(str(rows[1]["destination_path"]))
        if protected_field == "source_path":
            protected_candidate = protected_source
            catalog.execute(
                "UPDATE organization_plans SET source_path=? WHERE plan_id=?",
                (str(protected_candidate), blocked_plan_id),
            )
        else:
            protected_candidate = protected_root / "protected destination.docx"
            catalog.execute(
                "UPDATE organization_plans SET destination_path=? WHERE plan_id=?",
                (str(protected_candidate), blocked_plan_id),
            )
        catalog.commit()
        blocked_paths = (
            Path(
                str(
                    protected_candidate
                    if protected_field == "source_path"
                    else rows[0]["source_path"]
                )
            ),
            Path(
                str(
                    protected_candidate
                    if protected_field == "destination_path"
                    else rows[0]["destination_path"]
                )
            ),
        )

    guard = _protected_mutation_guard(tmp_path, protected_root)
    forbidden_keys = {os.path.normcase(os.path.abspath(path)) for path in blocked_paths}
    forbidden_calls: list[tuple[str, str]] = []

    def is_forbidden(value: object) -> bool:
        try:
            candidate = os.path.normcase(os.path.abspath(os.fspath(value)))
        except (TypeError, ValueError):
            return False
        return candidate in forbidden_keys

    def guarded_os_call(
        name: str,
        original: Callable[..., object],
    ) -> Callable[..., object]:
        def guarded(*args: object, **kwargs: object) -> object:
            for value in args[:2]:
                if is_forbidden(value):
                    forbidden_calls.append((name, os.fspath(value)))
                    raise AssertionError(
                        f"{name} must remain unreachable for protected plan paths"
                    )
            return original(*args, **kwargs)

        return guarded

    for syscall_name in (
        "lstat",
        "mkdir",
        "open",
        "remove",
        "rename",
        "replace",
        "scandir",
        "stat",
        "unlink",
    ):
        monkeypatch.setattr(
            os,
            syscall_name,
            guarded_os_call(syscall_name, getattr(os, syscall_name)),
        )

    original_snapshot = organization_application.snapshot_path

    def guarded_snapshot(path: Path) -> FileSnapshot:
        assert not is_forbidden(path), "snapshot must not inspect protected plan paths"
        return original_snapshot(path)

    monkeypatch.setattr(organization_application, "snapshot_path", guarded_snapshot)
    original_native_move = organization_application.rename_no_replace_by_identity

    def guarded_native_move(*args: object, **kwargs: object) -> object:
        assert not any(is_forbidden(value) for value in args[:2])
        return original_native_move(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        organization_application,
        "rename_no_replace_by_identity",
        guarded_native_move,
    )
    selected_plan_ids: list[str] = []
    original_apply_selected = organization_application._apply_selected_organization_plan

    def guarded_apply_selected(*args: object, **kwargs: object) -> object:
        row = args[2]
        assert isinstance(row, sqlite3.Row)
        plan_id = str(row["plan_id"])
        selected_plan_ids.append(plan_id)
        assert plan_id != blocked_plan_id
        return original_apply_selected(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        organization_application,
        "_apply_selected_organization_plan",
        guarded_apply_selected,
    )

    summary = apply_document_organization(
        catalog_path,
        destination_root,
        mutation_guard=guard,
        max_actions=2,
    )
    monkeypatch.undo()

    assert summary.selected == 2
    assert summary.applied == 1
    assert summary.blocked == 1
    assert summary.stale == 0
    assert summary.failed == 0
    assert summary.cache_synced == 1
    assert summary.remaining == 0
    assert selected_plan_ids == [safe_plan_id]
    assert forbidden_calls == []
    assert protected_source.read_bytes() == b"protected"
    assert blocked_original_source.is_file()
    assert not safe_source.exists()
    assert safe_destination.is_file()
    with document_catalog_database(catalog_path, readonly=True) as catalog:
        statuses = {
            str(row["plan_id"]): row
            for row in catalog.execute(
                """SELECT plan_id,status,detail,completed_ns,cache_sync_status
                FROM organization_plans"""
            ).fetchall()
        }
    blocked_row = statuses[blocked_plan_id]
    assert blocked_row["status"] == "blocked"
    assert "protected content" in str(blocked_row["detail"])
    assert blocked_row["completed_ns"] is not None
    assert blocked_row["cache_sync_status"] == "not_required"
    assert statuses[safe_plan_id]["status"] == "applied"


def test_apply_propagates_systemic_preadmission_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    source = tmp_path / "Formato SERINTRA.docx"
    source.write_bytes(b"safe fixture")
    _seed_docx(state_directory / "docx.sqlite3", source)
    update_document_catalog(state_directory)
    catalog_path = state_directory / "document_catalog.sqlite3"
    destination_root = tmp_path / "organizados"
    plan_document_organization(catalog_path, destination_root)

    def fail_preadmission(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise OSError("systemic preadmission failure")

    monkeypatch.setattr(
        organization_application,
        "_protected_organization_plan_denials",
        fail_preadmission,
    )

    with pytest.raises(OSError, match="systemic preadmission failure"):
        apply_document_organization(
            catalog_path,
            destination_root,
            mutation_guard=_normal_mutation_guard(tmp_path),
        )

    assert source.is_file()
    assert not destination_root.exists()
    with document_catalog_database(catalog_path, readonly=True) as catalog:
        row = catalog.execute(
            "SELECT status,completed_ns FROM organization_plans"
        ).fetchone()
        run = catalog.execute(
            """SELECT status,error_type,error_message FROM catalog_runs
            WHERE mode='apply' ORDER BY catalog_run_id DESC LIMIT 1"""
        ).fetchone()
    assert row is not None
    assert row["status"] == "planned"
    assert row["completed_ns"] is None
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_type"] == "OSError"
    assert run["error_message"] == "systemic preadmission failure"
# endregion [02]
