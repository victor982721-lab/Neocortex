"""Durable transcript, segment and full-text state for the audio route."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from neocortex.sqlite_connection import (
    READONLY_EXISTING,
    READWRITE_CREATE,
    READWRITE_EXISTING,
    SQLiteConnectionPolicy,
    SQLiteWriterPragmas,
    connect_sqlite,
)

from .sqlite_schema_contract import (
    SQLiteSchemaContract,
    read_metadata_schema_version,
    schema_contract_from_builder,
    validate_sqlite_schema_contract,
)


# region [01] Connections and additive schema


AUDIO_SCHEMA_VERSION = 1

_AUDIO_SQLITE_POLICY = SQLiteConnectionPolicy(
    label="audio state",
    timeout_seconds=60.0,
    row_factory=sqlite3.Row,
    writer_pragmas=SQLiteWriterPragmas(
        journal_mode="WAL",
        synchronous="NORMAL",
        cache_size_kib=65_536,
        wal_autocheckpoint_pages=2_048,
        journal_size_limit_bytes=268_435_456,
    ),
)


_AUDIO_SCHEMA_DDL = (
    """CREATE TABLE IF NOT EXISTS metadata(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS documents(
        file_key TEXT PRIMARY KEY,
        path TEXT NOT NULL COLLATE NOCASE,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        birthtime_ns INTEGER NOT NULL,
        processing_signature TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT,
        duration_seconds REAL,
        speech_duration_seconds REAL,
        language TEXT,
        language_probability REAL,
        model_name TEXT,
        backend_version TEXT,
        device TEXT,
        compute_type TEXT,
        media_metadata_json TEXT NOT NULL DEFAULT '{}',
        text_zlib BLOB,
        text_chars INTEGER NOT NULL DEFAULT 0,
        text_xxh3_128 TEXT,
        segment_count INTEGER NOT NULL DEFAULT 0,
        error_type TEXT,
        error_message TEXT,
        retryable INTEGER NOT NULL DEFAULT 0,
        review_disposition TEXT NOT NULL DEFAULT 'none',
        last_seen_run_id INTEGER NOT NULL,
        updated_ns INTEGER NOT NULL
    ) WITHOUT ROWID""",
    """CREATE UNIQUE INDEX IF NOT EXISTS audio_documents_path_idx
        ON documents(path)""",
    """CREATE INDEX IF NOT EXISTS audio_documents_status_idx
        ON documents(status,review_disposition,path)""",
    """CREATE TABLE IF NOT EXISTS audio_inventory(
        file_key TEXT PRIMARY KEY,
        path TEXT NOT NULL COLLATE NOCASE,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        birthtime_ns INTEGER NOT NULL,
        last_seen_run_id INTEGER NOT NULL
    ) WITHOUT ROWID""",
    """CREATE UNIQUE INDEX IF NOT EXISTS audio_inventory_path_idx
        ON audio_inventory(path)""",
    """CREATE INDEX IF NOT EXISTS audio_inventory_run_idx
        ON audio_inventory(last_seen_run_id,file_key)""",
    """CREATE TABLE IF NOT EXISTS segments(
        file_key TEXT NOT NULL,
        segment_index INTEGER NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        text TEXT NOT NULL,
        avg_logprob REAL,
        no_speech_probability REAL,
        PRIMARY KEY(file_key,segment_index),
        FOREIGN KEY(file_key) REFERENCES documents(file_key) ON DELETE CASCADE
    ) WITHOUT ROWID""",
    """CREATE INDEX IF NOT EXISTS audio_segments_time_idx
        ON segments(file_key,start_ms,end_ms)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
        file_key UNINDEXED,
        path UNINDEXED,
        title,
        body,
        tokenize='unicode61 remove_diacritics 2'
    )""",
)


def _create_audio_schema(connection: sqlite3.Connection) -> None:
    for statement in _AUDIO_SCHEMA_DDL:
        connection.execute(statement)


@lru_cache(maxsize=1)
def _audio_schema_contract() -> SQLiteSchemaContract:
    return schema_contract_from_builder(_create_audio_schema)


@contextmanager
def audio_database(
    path: Path,
    *,
    readonly: bool = False,
    create: bool = True,
):
    """Open audio state, optionally refusing creation after initialization."""

    mode = (
        READONLY_EXISTING
        if readonly
        else READWRITE_CREATE
        if create
        else READWRITE_EXISTING
    )
    connection = connect_sqlite(
        path,
        mode=mode,
        policy=_AUDIO_SQLITE_POLICY,
    )
    try:
        yield connection
    finally:
        connection.close()


def initialize_audio_state(path: Path) -> None:
    """Create the audio cache without replacing prior transcripts."""

    prior: int | None = None
    if path.is_file():
        with audio_database(path, readonly=True) as connection:
            prior = read_metadata_schema_version(connection, label="audio")
            if prior is not None and prior > AUDIO_SCHEMA_VERSION:
                raise RuntimeError(
                    f"audio schema {prior} is newer than supported "
                    f"schema {AUDIO_SCHEMA_VERSION}"
                )
            if prior == AUDIO_SCHEMA_VERSION:
                validate_sqlite_schema_contract(
                    connection,
                    _audio_schema_contract(),
                    label="audio",
                    exact=True,
                )
                return

    with audio_database(path, create=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _create_audio_schema(connection)
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(AUDIO_SCHEMA_VERSION),),
            )
            validate_sqlite_schema_contract(
                connection,
                _audio_schema_contract(),
                label="audio",
                exact=True,
            )
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


# endregion [01]
