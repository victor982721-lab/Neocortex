"""Private Win32 handle bindings for the release NTFS adapter."""
# region [00] Contexto del módulo
# Módulo: tools/release_windows_ntfs_native.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, Protocol
from ctypes import wintypes

from tools.release_windows_receipts import ReleaseTransitionError, absolute_path
# endregion [01]

# region [02] Implementación


_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_WRITE_THROUGH = 0x80000000
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_FILE_ID_INFO_CLASS = 18
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_GROUP_SECURITY_INFORMATION = 0x00000002
_DACL_SECURITY_INFORMATION = 0x00000004
_SECURITY_INFORMATION = (
    _OWNER_SECURITY_INFORMATION
    | _GROUP_SECURITY_INFORMATION
    | _DACL_SECURITY_INFORMATION
)
_CHUNK_BYTES = 64 * 1024
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_DISPOSITION_INFO_EX_CLASS = 21
_FILE_DISPOSITION_FLAG_DELETE = 0x00000001


class _FileId128(ctypes.Structure):
    _fields_ = (("identifier", ctypes.c_ubyte * 16),)


class _FileIdInfo(ctypes.Structure):
    _fields_ = (
        ("volume_serial_number", ctypes.c_ulonglong),
        ("file_id", _FileId128),
    )


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = (
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    )


class _FileDispositionInfoEx(ctypes.Structure):
    _fields_ = (("flags", wintypes.DWORD),)


class _Overlapped(ctypes.Structure):
    _fields_ = (
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", wintypes.DWORD),
        ("offset_high", wintypes.DWORD),
        ("event", wintypes.HANDLE),
    )


@dataclass(frozen=True, slots=True)
class _OpenSpec:
    desired_access: int
    share_mode: int
    creation_disposition: int
    flags_and_attributes: int


@dataclass(frozen=True, slots=True)
class _HandleFacts:
    path: str
    size: int
    volume_id: int
    file_id: int
    file_system: str
    security_descriptor: bytes
    link_count: int
    is_reparse_point: bool
    is_directory: bool


class _NtfsApiProtocol(Protocol):
    def open_file(self, path: Path, spec: _OpenSpec) -> int: ...

    def open_parent_guard(self, path: Path) -> int: ...

    def close_handle(self, handle: int) -> None: ...

    def read_file(self, handle: int, max_bytes: int) -> bytes: ...

    def write_file(self, handle: int, payload: bytes) -> int: ...

    def flush_file_buffers(self, handle: int) -> None: ...

    def inspect_handle(
        self, handle: int, *, security_information: int
    ) -> _HandleFacts: ...

    def inspect_parent_guard(self, handle: int) -> _HandleFacts: ...

    def set_security_descriptor(
        self,
        handle: int,
        descriptor: bytes,
        *,
        security_information: int,
    ) -> None: ...

    def set_delete_disposition(
        self, handle: int, *, information_class: int, flags: int
    ) -> None: ...

    def lock_file(self, handle: int) -> None: ...

    def unlock_file(self, handle: int) -> None: ...

    def path_exists(self, path: Path) -> bool: ...


_OBSERVATION_SPEC = _OpenSpec(
    _GENERIC_READ | _READ_CONTROL,
    _FILE_SHARE_READ,
    _OPEN_EXISTING,
    _FILE_FLAG_OPEN_REPARSE_POINT,
)
_DIRECTORY_SPEC = _OpenSpec(
    _FILE_READ_ATTRIBUTES | _READ_CONTROL,
    _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
    _OPEN_EXISTING,
    _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
)
_PARENT_GUARD_SPEC = _OpenSpec(
    _GENERIC_READ | _READ_CONTROL,
    _FILE_SHARE_READ | _FILE_SHARE_WRITE,
    _OPEN_EXISTING,
    _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
)
_CREATOR_SPEC = _OpenSpec(
    _GENERIC_READ
    | _GENERIC_WRITE
    | _DELETE
    | _READ_CONTROL
    | _WRITE_DAC
    | _WRITE_OWNER,
    0,
    _CREATE_NEW,
    _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_WRITE_THROUGH,
)
_REMOVE_SPEC = _OpenSpec(
    _GENERIC_READ | _DELETE | _READ_CONTROL,
    0,
    _OPEN_EXISTING,
    _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_WRITE_THROUGH,
)
_LOCK_SPEC = _OpenSpec(
    _GENERIC_READ | _GENERIC_WRITE | _READ_CONTROL,
    _FILE_SHARE_READ | _FILE_SHARE_WRITE,
    _OPEN_ALWAYS,
    _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_WRITE_THROUGH,
)


if os.name == "nt":
    _kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32: Any = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = _kernel32.ReadFile.argtypes
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.GetVolumeInformationByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    _kernel32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    _kernel32.UnlockFileEx.restype = wintypes.BOOL
    _kernel32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _advapi32.GetSecurityInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetSecurityInfo.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorLength.argtypes = (wintypes.LPVOID,)
    _advapi32.GetSecurityDescriptorLength.restype = wintypes.DWORD
    _advapi32.SetKernelObjectSecurity.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    _advapi32.SetKernelObjectSecurity.restype = wintypes.BOOL
else:  # pragma: no cover - import remains safe for static tooling
    _kernel32 = None
    _advapi32 = None


def _require_windows() -> None:
    if _kernel32 is None or _advapi32 is None:
        raise OSError("Windows NTFS operations require Windows")


def _winerror(code: int | None = None) -> OSError:
    number = ctypes.get_last_error() if code is None else code
    return ctypes.WinError(number)


def _raise_last_error() -> NoReturn:
    raise _winerror()


def _final_path(handle: int) -> str:
    _require_windows()
    capacity = 512
    buffer = ctypes.create_unicode_buffer(capacity)
    length = int(_kernel32.GetFinalPathNameByHandleW(handle, buffer, capacity, 0))
    if length == 0:
        _raise_last_error()
    if length >= capacity:
        capacity = length + 1
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(_kernel32.GetFinalPathNameByHandleW(handle, buffer, capacity, 0))
        if length == 0 or length >= capacity:
            _raise_last_error()
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _file_system(handle: int) -> str:
    _require_windows()
    buffer = ctypes.create_unicode_buffer(64)
    if not _kernel32.GetVolumeInformationByHandleW(
        handle, None, 0, None, None, None, buffer, len(buffer)
    ):
        _raise_last_error()
    return buffer.value


def _security_descriptor(handle: int, security_information: int) -> bytes:
    _require_windows()
    pointer = ctypes.c_void_p()
    status = int(
        _advapi32.GetSecurityInfo(
            handle,
            _SE_FILE_OBJECT,
            security_information,
            None,
            None,
            None,
            None,
            ctypes.byref(pointer),
        )
    )
    if status:
        raise _winerror(status)
    if pointer.value is None:
        raise ReleaseTransitionError("security descriptor pointer is absent")
    try:
        length = int(_advapi32.GetSecurityDescriptorLength(pointer))
        if length < 1:
            _raise_last_error()
        return bytes(ctypes.string_at(pointer, length))
    finally:
        _kernel32.LocalFree(pointer)


class WindowsNtfsApi:
    """Injectable, exact wrappers around the required Win32 handle APIs."""

    def open_file(self, path: Path, spec: _OpenSpec) -> int:
        _require_windows()
        handle = _kernel32.CreateFileW(
            str(absolute_path(path)),
            spec.desired_access,
            spec.share_mode,
            None,
            spec.creation_disposition,
            spec.flags_and_attributes,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            _raise_last_error()
        return int(handle)

    def open_parent_guard(self, path: Path) -> int:
        return self.open_file(path, _PARENT_GUARD_SPEC)

    def close_handle(self, handle: int) -> None:
        _require_windows()
        if not _kernel32.CloseHandle(handle):
            _raise_last_error()

    def read_file(self, handle: int, max_bytes: int) -> bytes:
        _require_windows()
        if max_bytes < 0 or max_bytes > _CHUNK_BYTES:
            raise ValueError("read size is outside the bounded chunk contract")
        if max_bytes == 0:
            return b""
        buffer = (ctypes.c_ubyte * max_bytes)()
        transferred = wintypes.DWORD()
        if not _kernel32.ReadFile(
            handle,
            buffer,
            max_bytes,
            ctypes.byref(transferred),
            None,
        ):
            _raise_last_error()
        return bytes(buffer[: transferred.value])

    def write_file(self, handle: int, payload: bytes) -> int:
        _require_windows()
        if len(payload) > _CHUNK_BYTES:
            raise ValueError("write exceeds the bounded chunk contract")
        transferred = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(payload, len(payload))
        if not _kernel32.WriteFile(
            handle,
            buffer,
            len(payload),
            ctypes.byref(transferred),
            None,
        ):
            _raise_last_error()
        return int(transferred.value)

    def flush_file_buffers(self, handle: int) -> None:
        _require_windows()
        if not _kernel32.FlushFileBuffers(handle):
            _raise_last_error()

    def inspect_handle(self, handle: int, *, security_information: int) -> _HandleFacts:
        _require_windows()
        legacy = _ByHandleFileInformation()
        identity = _FileIdInfo()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(legacy)):
            _raise_last_error()
        if not _kernel32.GetFileInformationByHandleEx(
            handle,
            _FILE_ID_INFO_CLASS,
            ctypes.byref(identity),
            ctypes.sizeof(identity),
        ):
            _raise_last_error()
        attributes = int(legacy.file_attributes)
        return _HandleFacts(
            path=_final_path(handle),
            size=(int(legacy.file_size_high) << 32) | int(legacy.file_size_low),
            volume_id=int(identity.volume_serial_number),
            file_id=int.from_bytes(bytes(identity.file_id.identifier), "little"),
            file_system=_file_system(handle),
            security_descriptor=_security_descriptor(handle, security_information),
            link_count=int(legacy.number_of_links),
            is_reparse_point=bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT),
            is_directory=bool(attributes & _FILE_ATTRIBUTE_DIRECTORY),
        )

    def inspect_parent_guard(self, handle: int) -> _HandleFacts:
        return self.inspect_handle(handle, security_information=_SECURITY_INFORMATION)

    def set_security_descriptor(
        self,
        handle: int,
        descriptor: bytes,
        *,
        security_information: int,
    ) -> None:
        _require_windows()
        buffer = ctypes.create_string_buffer(descriptor, len(descriptor))
        pointer = ctypes.cast(buffer, wintypes.LPVOID)
        if not _advapi32.SetKernelObjectSecurity(handle, security_information, pointer):
            _raise_last_error()

    def set_delete_disposition(
        self, handle: int, *, information_class: int, flags: int
    ) -> None:
        _require_windows()
        information = _FileDispositionInfoEx(flags)
        if not _kernel32.SetFileInformationByHandle(
            handle,
            information_class,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            _raise_last_error()

    def lock_file(self, handle: int) -> None:
        _require_windows()
        overlapped = _Overlapped()
        if not _kernel32.LockFileEx(
            handle,
            _LOCKFILE_EXCLUSIVE_LOCK,
            0,
            0xFFFFFFFF,
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        ):
            _raise_last_error()

    def unlock_file(self, handle: int) -> None:
        _require_windows()
        overlapped = _Overlapped()
        if not _kernel32.UnlockFileEx(
            handle,
            0,
            0xFFFFFFFF,
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        ):
            _raise_last_error()

    def path_exists(self, path: Path) -> bool:
        _require_windows()
        attributes = int(_kernel32.GetFileAttributesW(str(absolute_path(path))))
        if attributes != _INVALID_FILE_ATTRIBUTES:
            return True
        error = ctypes.get_last_error()
        if error in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            return False
        raise _winerror(error)
# endregion [02]
