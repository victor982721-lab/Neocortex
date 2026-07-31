"""Streaming MFT enumeration and resumable USN consumption on Windows."""

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
