"""Compatibility facade for the shared versioned SQLite lifecycle."""

from neocortex.sqlite_schema_lifecycle import (
    ConnectionFactory as ConnectionFactory,
    initialize_versioned_sqlite_schema as initialize_versioned_sqlite_schema,
    readonly_sqlite_uri as readonly_sqlite_uri,
)


__all__ = [
    "ConnectionFactory",
    "initialize_versioned_sqlite_schema",
    "readonly_sqlite_uri",
]
