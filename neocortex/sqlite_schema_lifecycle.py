"""Atomic lifecycle primitives for versioned SQLite application databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .sqlite_schema_contract import read_application_schema_version


class ConnectionFactory(Protocol):
    def __call__(
        self,
        path: Path,
        *,
        readonly: bool = False,
    ) -> sqlite3.Connection: ...


def readonly_sqlite_uri(path: str | Path) -> str:
    """Return a correctly escaped SQLite URI that cannot create or write a file."""

    return f"{Path(path).resolve(strict=False).as_uri()}?mode=ro"


def existing_sqlite_uri(path: str | Path) -> str:
    """Return an escaped read-write URI that refuses to create a missing database."""

    return f"{Path(path).resolve(strict=False).as_uri()}?mode=rw"


def _require_supported_version(
    version: int | None,
    *,
    label: str,
    current_version: int,
) -> None:
    if version is None:
        return
    if version < 1 or version > current_version:
        raise RuntimeError(
            f"{label} state schema {version} is unsupported; "
            f"expected 1..{current_version}"
        )


def _probe_existing_schema(
    path: Path,
    *,
    label: str,
    current_version: int,
    connect: ConnectionFactory,
    validate_metadata: Callable[[sqlite3.Connection], None],
    validate_current: Callable[[sqlite3.Connection], None],
) -> int | None:
    if not path.is_file():
        return None
    connection = connect(path, readonly=True)
    try:
        version = read_application_schema_version(connection, label=label)
        if version is not None:
            validate_metadata(connection)
        _require_supported_version(
            version,
            label=label,
            current_version=current_version,
        )
        if version == current_version:
            validate_current(connection)
        return version
    finally:
        connection.close()


def initialize_versioned_sqlite_schema(
    path: Path,
    *,
    label: str,
    current_version: int,
    connect: ConnectionFactory,
    validate_metadata: Callable[[sqlite3.Connection], None],
    validate_current: Callable[[sqlite3.Connection], None],
    create_fresh: Callable[[sqlite3.Connection], None],
    migrate: Callable[[sqlite3.Connection, int], None],
) -> None:
    """Validate read-only first, then create or migrate in one transaction."""

    initial_version = _probe_existing_schema(
        path,
        label=label,
        current_version=current_version,
        connect=connect,
        validate_metadata=validate_metadata,
        validate_current=validate_current,
    )
    if initial_version == current_version:
        return

    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        locked_version = read_application_schema_version(connection, label=label)
        if locked_version != initial_version:
            raise RuntimeError(f"{label} state schema changed during initialization")
        if locked_version is not None:
            validate_metadata(connection)
        _require_supported_version(
            locked_version,
            label=label,
            current_version=current_version,
        )
        if locked_version is None:
            create_fresh(connection)
        else:
            migrate(connection, locked_version)
        validate_current(connection)
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        source = "new" if initial_version is None else str(initial_version)
        raise RuntimeError(
            f"{label} state schema initialization from version {source} failed"
        ) from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = [
    "ConnectionFactory",
    "existing_sqlite_uri",
    "initialize_versioned_sqlite_schema",
    "readonly_sqlite_uri",
]
