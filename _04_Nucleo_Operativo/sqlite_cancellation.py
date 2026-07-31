"""Identity-preserving compatibility facade for SQLite cancellation."""

from neocortex.sqlite_cancellation import (
    CancellationCheck as CancellationCheck,
    DEFAULT_PROGRESS_INSTRUCTIONS as DEFAULT_PROGRESS_INSTRUCTIONS,
    SQLiteCancellationBridge as SQLiteCancellationBridge,
    sqlite_cancellation_scope as sqlite_cancellation_scope,
)


__all__ = [
    "CancellationCheck",
    "DEFAULT_PROGRESS_INSTRUCTIONS",
    "SQLiteCancellationBridge",
    "sqlite_cancellation_scope",
]
