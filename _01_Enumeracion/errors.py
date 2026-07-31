"""Exceptions raised by :mod:`_01_Enumeracion`."""

from __future__ import annotations


class NtfsUsnError(Exception):
    """Base exception for this package."""


class UnsupportedPlatformError(NtfsUsnError):
    """The package was used on a platform other than Windows."""


class InvalidVolumeError(NtfsUsnError, ValueError):
    """A volume designator is invalid or does not refer to NTFS."""


class UnsupportedRecordVersionError(NtfsUsnError):
    """Windows returned an unsupported USN record version."""


class CorruptBufferError(NtfsUsnError):
    """A buffer returned by Windows is structurally invalid."""


class JournalDiscontinuityError(NtfsUsnError):
    """The USN journal changed or wrapped while an initial scan was running."""


class VolumeAccessError(NtfsUsnError):
    """A Win32 operation on the volume failed."""

    def __init__(self, operation: str, volume: str, winerror: int, message: str):
        self.operation = operation
        self.volume = volume
        self.winerror = winerror
        super().__init__(
            f"{operation} failed for {volume!r}: [WinError {winerror}] {message}"
        )
