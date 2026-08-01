"""Streaming MFT enumeration and resumable USN consumption on Windows."""
# region [00] Contexto del módulo
# Módulo: _01_Enumeracion/__init__.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from .consumer import ALL_REASONS, UsnJournalReader, consume_changes
from .enumeration import VolumeEnumeration, enumerate_volume, query_journal_cursor
from .errors import (
    CorruptBufferError,
    InvalidVolumeError,
    JournalDiscontinuityError,
    NtfsUsnError,
    UnsupportedPlatformError,
    UnsupportedRecordVersionError,
    VolumeAccessError,
)
from .models import (
    EnumerationCheckpoint,
    JournalCursor,
    NtfsEntry,
    UsnChangeBatch,
    UsnJournalInfo,
)
from .path_index import SqlitePathIndex
# endregion [01]

# region [02] Implementación

__all__ = [
    "CorruptBufferError",
    "ALL_REASONS",
    "EnumerationCheckpoint",
    "InvalidVolumeError",
    "JournalDiscontinuityError",
    "JournalCursor",
    "NtfsEntry",
    "NtfsUsnError",
    "SqlitePathIndex",
    "UnsupportedPlatformError",
    "UnsupportedRecordVersionError",
    "UsnJournalInfo",
    "UsnChangeBatch",
    "UsnJournalReader",
    "VolumeAccessError",
    "VolumeEnumeration",
    "enumerate_volume",
    "query_journal_cursor",
    "consume_changes",
]
# endregion [02]
