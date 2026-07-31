"""Handle-bound Windows NTFS operations for launcher transitions."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tools.release_windows_ntfs_native import (
    WindowsNtfsApi,
    _CHUNK_BYTES,
    _CREATOR_SPEC,
    _DIRECTORY_SPEC,
    _FILE_DISPOSITION_FLAG_DELETE,
    _FILE_DISPOSITION_INFO_EX_CLASS,
    _HandleFacts,
    _LOCK_SPEC,
    _NtfsApiProtocol,
    _OBSERVATION_SPEC,
    _OpenSpec,
    _PARENT_GUARD_SPEC,
    _REMOVE_SPEC,
    _SECURITY_INFORMATION,
)
from tools.release_windows_receipts import (
    FileSnapshot,
    ReceiptValidationError,
    ReleaseLayout,
    ReleaseTransitionError,
    absolute_path,
    same_path,
)


__all__ = ["WindowsNtfsApi", "WindowsNtfsReleaseFileOperations"]

_HEX64 = r"[0-9a-f]{64}"
_BACKUP_NAME = re.compile(rf"{_HEX64}\.launcher\Z")
_RECEIPT_NAME = re.compile(rf"{_HEX64}\.(?:intent|result|stage)\.json\Z")
_STAGE_NAME = re.compile(rf"\.neocortex-{_HEX64}\.stage\Z")
_PREVIOUS_NAME = re.compile(rf"\.neocortex-{_HEX64}\.previous\Z")


@dataclass(frozen=True, slots=True)
class _DirectoryBinding:
    path: Path
    volume_id: int
    file_id: int


def _add_cleanup_note(
    primary: BaseException, operation: str, cleanup: BaseException
) -> None:
    primary.add_note(f"{operation} failed: {type(cleanup).__name__}: {cleanup}")


@contextmanager
def _owned_handle(api: _NtfsApiProtocol, path: Path, spec: _OpenSpec) -> Iterator[int]:
    handle = api.open_file(path, spec)
    try:
        yield handle
    except BaseException as primary:
        try:
            api.close_handle(handle)
        except BaseException as cleanup:
            _add_cleanup_note(primary, "handle close", cleanup)
        raise
    else:
        api.close_handle(handle)


def _delete_creator_preserving(
    api: _NtfsApiProtocol, handle: int, primary: BaseException
) -> None:
    try:
        api.set_delete_disposition(
            handle,
            information_class=_FILE_DISPOSITION_INFO_EX_CLASS,
            flags=_FILE_DISPOSITION_FLAG_DELETE,
        )
    except BaseException as cleanup:
        _add_cleanup_note(primary, "creator-handle cleanup", cleanup)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(absolute_path(path)))


def _same_identity(left: _HandleFacts, right: _HandleFacts) -> bool:
    return (left.volume_id, left.file_id) == (right.volume_id, right.file_id)


def _require_ntfs_file(
    facts: _HandleFacts,
    path: Path,
    volume_id: int,
    *,
    role: str,
) -> None:
    if not same_path(Path(facts.path), path):
        raise ReleaseTransitionError(f"{role} canonical path changed")
    if facts.file_system.casefold() != "ntfs" or facts.volume_id != volume_id:
        raise ReleaseTransitionError(
            f"{role} must remain on the authorized NTFS volume"
        )
    if facts.is_reparse_point or facts.is_directory:
        raise ReleaseTransitionError(f"{role} must be one non-reparse file")


def _snapshot_from_facts(facts: _HandleFacts, size: int, sha256: str) -> FileSnapshot:
    descriptor_sha256 = hashlib.sha256(facts.security_descriptor).hexdigest()
    return FileSnapshot(
        path=facts.path,
        size=size,
        sha256=sha256,
        volume_id=facts.volume_id,
        file_id=facts.file_id,
        file_system=facts.file_system,
        security_descriptor_sha256=descriptor_sha256,
        security_descriptor=facts.security_descriptor,
        link_count=facts.link_count,
        is_reparse_point=facts.is_reparse_point,
    )


def _read_hash(api: _NtfsApiProtocol, handle: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = api.read_file(handle, _CHUNK_BYTES)
        if not chunk:
            return size, digest.hexdigest()
        digest.update(chunk)
        size += len(chunk)


def _snapshot_retained_handle(
    api: _NtfsApiProtocol,
    handle: int,
    path: Path,
    volume_id: int,
) -> FileSnapshot:
    before = api.inspect_handle(handle, security_information=_SECURITY_INFORMATION)
    _require_ntfs_file(before, path, volume_id, role="snapshot")
    size, sha256 = _read_hash(api, handle)
    after = api.inspect_handle(handle, security_information=_SECURITY_INFORMATION)
    if before != after or size != before.size:
        raise ReleaseTransitionError(
            "file changed or drifted during handle observation"
        )
    return _snapshot_from_facts(after, size, sha256)


def _write_all(api: _NtfsApiProtocol, handle: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = api.write_file(handle, payload[offset : offset + _CHUNK_BYTES])
        if written < 1:
            raise OSError("Win32 write made no progress")
        offset += written


def _copy_stream(
    api: _NtfsApiProtocol, source: int, destination: int
) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = api.read_file(source, _CHUNK_BYTES)
        if not chunk:
            return total, digest.hexdigest()
        _write_all(api, destination, chunk)
        digest.update(chunk)
        total += len(chunk)


def _read_sized(api: _NtfsApiProtocol, handle: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = api.read_file(handle, min(_CHUNK_BYTES, remaining))
        if not chunk:
            raise ReleaseTransitionError("file ended during bounded handle read")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _require_parent_guard(
    facts: _HandleFacts,
    binding: _DirectoryBinding,
) -> None:
    if not same_path(Path(facts.path), binding.path):
        raise ReleaseTransitionError("layout parent guard is not canonical")
    if facts.file_system.casefold() != "ntfs" or facts.volume_id != binding.volume_id:
        raise ReleaseTransitionError("layout parent guard left its NTFS volume")
    if facts.is_reparse_point or not facts.is_directory:
        raise ReleaseTransitionError("layout parent guard is not a plain directory")
    if facts.file_id != binding.file_id:
        raise ReleaseTransitionError("layout parent guard identity changed")


def _open_parent_guard(
    api: _NtfsApiProtocol,
    binding: _DirectoryBinding,
) -> int:
    handle = api.open_file(binding.path, _PARENT_GUARD_SPEC)
    try:
        facts = api.inspect_handle(handle, security_information=_SECURITY_INFORMATION)
        _require_parent_guard(facts, binding)
    except BaseException as primary:
        try:
            api.close_handle(handle)
        except BaseException as cleanup:
            _add_cleanup_note(primary, "parent guard close", cleanup)
        raise
    return handle


@contextmanager
def _retained_parent_guard(
    api: _NtfsApiProtocol,
    binding: _DirectoryBinding,
    *,
    preserve_observation_hooks: bool,
) -> Iterator[None]:
    handle = (
        api.open_parent_guard(binding.path)
        if preserve_observation_hooks
        else api.open_file(binding.path, _PARENT_GUARD_SPEC)
    )
    with _preopened_handle(api, handle):
        facts = (
            api.inspect_parent_guard(handle)
            if preserve_observation_hooks
            else api.inspect_handle(
                handle,
                security_information=_SECURITY_INFORMATION,
            )
        )
        _require_parent_guard(facts, binding)
        yield


def _record_external_cleanup(
    primary: BaseException | None,
    cleanup: BaseException,
) -> BaseException:
    if primary is None:
        return cleanup
    _add_cleanup_note(primary, "external lock cleanup", cleanup)
    return primary


def _close_external_handles(
    api: _NtfsApiProtocol,
    handles: Iterator[int],
    primary: BaseException | None,
) -> BaseException | None:
    for handle in handles:
        try:
            api.close_handle(handle)
        except BaseException as cleanup:
            primary = _record_external_cleanup(primary, cleanup)
    return primary


class _WindowsExternalLock:
    def __init__(
        self,
        api: _NtfsApiProtocol,
        path: Path,
        volume_id: int,
        parent_bindings: tuple[_DirectoryBinding, ...],
    ) -> None:
        self._api = api
        self._path = path
        self._volume_id = volume_id
        self._parent_bindings = parent_bindings
        self._handle: int | None = None
        self._guard_handles: tuple[int, ...] = ()

    def acquire(self) -> None:
        if self._handle is not None or self._guard_handles:
            raise ReleaseTransitionError("external lock is already acquired")
        guards: list[int] = []
        handle: int | None = None
        try:
            for binding in self._parent_bindings:
                guards.append(_open_parent_guard(self._api, binding))
            handle = self._api.open_file(self._path, _LOCK_SPEC)
            facts = self._api.inspect_handle(
                handle,
                security_information=_SECURITY_INFORMATION,
            )
            _require_ntfs_file(
                facts,
                self._path,
                self._volume_id,
                role="canonical lock",
            )
            if facts.link_count != 1:
                raise ReleaseTransitionError("canonical lock has physical aliases")
            self._api.lock_file(handle)
        except BaseException as primary:
            handles = iter(
                (() if handle is None else (handle,)) + tuple(reversed(guards))
            )
            _close_external_handles(self._api, handles, primary)
            raise
        assert handle is not None
        self._guard_handles = tuple(guards)
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        guards = self._guard_handles
        if handle is None and not guards:
            return
        self._handle = None
        self._guard_handles = ()
        primary: BaseException | None = None
        if handle is not None:
            try:
                self._api.unlock_file(handle)
            except BaseException as exc:
                primary = exc
            primary = _close_external_handles(self._api, iter((handle,)), primary)
        primary = _close_external_handles(
            self._api,
            iter(reversed(guards)),
            primary,
        )
        if primary is not None:
            raise primary


class WindowsNtfsReleaseFileOperations:
    """Concrete NTFS implementation of the structural release file protocol."""

    def __init__(
        self,
        layout: ReleaseLayout,
        *,
        api: _NtfsApiProtocol | None = None,
    ) -> None:
        self._layout = layout
        self._api: _NtfsApiProtocol = WindowsNtfsApi() if api is None else api
        self._bindings, self._volume_id = self._bind_layout()

    def _inspect_path(self, path: Path, *, directory: bool) -> _HandleFacts:
        spec = _DIRECTORY_SPEC if directory else _OBSERVATION_SPEC
        with _owned_handle(self._api, path, spec) as handle:
            return self._api.inspect_handle(
                handle,
                security_information=_SECURITY_INFORMATION,
            )

    def _check_layout_fact(
        self,
        role: str,
        path: Path,
        facts: _HandleFacts,
        *,
        directory: bool,
        volume_id: int | None,
    ) -> None:
        if not same_path(Path(facts.path), path):
            raise ReleaseTransitionError(f"layout {role} is not canonical")
        if facts.file_system.casefold() != "ntfs":
            raise ReleaseTransitionError(f"layout {role} must reside on NTFS")
        if facts.is_reparse_point:
            raise ReleaseTransitionError(f"layout {role} is a reparse point")
        if facts.is_directory != directory:
            raise ReleaseTransitionError(f"layout {role} has the wrong entry type")
        if volume_id is not None and facts.volume_id != volume_id:
            raise ReleaseTransitionError("layout components cross a physical volume")

    def _bind_layout(self) -> tuple[dict[str, _DirectoryBinding], int]:
        directories = (
            ("launcher parent", self._layout.launcher_path.parent),
            ("receipts", self._layout.receipts_directory),
            ("backup", self._layout.backup_directory),
            ("lock parent", self._layout.lock_path.parent),
        )
        bindings: dict[str, _DirectoryBinding] = {}
        identities: set[tuple[int, int]] = set()
        volume_id: int | None = None
        for role, path in directories:
            facts = self._inspect_path(path, directory=True)
            self._check_layout_fact(
                role,
                path,
                facts,
                directory=True,
                volume_id=volume_id,
            )
            volume_id = facts.volume_id if volume_id is None else volume_id
            identity = (facts.volume_id, facts.file_id)
            if identity in identities:
                raise ReleaseTransitionError("layout component identity collision")
            identities.add(identity)
            bindings[_path_key(path)] = _DirectoryBinding(
                path,
                facts.volume_id,
                facts.file_id,
            )
        assert volume_id is not None
        self._bind_optional_launcher(volume_id, identities)
        return bindings, volume_id

    def _bind_optional_launcher(
        self, volume_id: int, identities: set[tuple[int, int]]
    ) -> None:
        try:
            facts = self._inspect_path(self._layout.launcher_path, directory=False)
        except FileNotFoundError:
            return
        self._check_layout_fact(
            "launcher",
            self._layout.launcher_path,
            facts,
            directory=False,
            volume_id=volume_id,
        )
        if (facts.volume_id, facts.file_id) in identities:
            raise ReleaseTransitionError("layout launcher identity aliases a directory")

    def _binding_for_parent(self, parent: Path) -> _DirectoryBinding:
        binding = self._bindings.get(_path_key(parent))
        if binding is None:
            raise ReleaseTransitionError(
                "mutation parent is outside the release layout"
            )
        return binding

    @contextmanager
    def _retain_parent(
        self,
        parent: Path,
        *,
        preserve_observation_hooks: bool = False,
    ) -> Iterator[None]:
        binding = self._binding_for_parent(parent)
        with _retained_parent_guard(
            self._api,
            binding,
            preserve_observation_hooks=preserve_observation_hooks,
        ):
            yield

    def _copy_destination(self, destination: Path) -> Path:
        path = absolute_path(destination)
        parent = _path_key(path.parent)
        backup = _path_key(self._layout.backup_directory)
        launcher = _path_key(self._layout.launcher_path.parent)
        allowed = (parent == backup and _BACKUP_NAME.fullmatch(path.name)) or (
            parent == launcher and _STAGE_NAME.fullmatch(path.name)
        )
        if not allowed:
            raise ReleaseTransitionError("copy destination is outside its allowlist")
        self._binding_for_parent(path.parent)
        return path

    def _receipt_destination(self, destination: Path) -> Path:
        path = absolute_path(destination)
        if _path_key(path.parent) != _path_key(
            self._layout.receipts_directory
        ) or not _RECEIPT_NAME.fullmatch(path.name):
            raise ReleaseTransitionError("receipt destination is outside its allowlist")
        self._binding_for_parent(path.parent)
        return path

    def _cleanup_destination(self, destination: Path) -> Path:
        path = absolute_path(destination)
        launcher = _path_key(self._layout.launcher_path.parent)
        valid_name = _STAGE_NAME.fullmatch(path.name) or _PREVIOUS_NAME.fullmatch(
            path.name
        )
        if _path_key(path.parent) != launcher or not valid_name:
            raise ReleaseTransitionError("cleanup target is outside its allowlist")
        self._binding_for_parent(path.parent)
        return path

    def open_external_lock(self, path: Path) -> _WindowsExternalLock:
        candidate = absolute_path(path)
        if not same_path(candidate, self._layout.lock_path):
            raise ReleaseTransitionError("only the canonical lock path is authorized")
        return _WindowsExternalLock(
            self._api,
            candidate,
            self._volume_id,
            tuple(self._bindings.values()),
        )

    def snapshot_by_handle(self, path: Path) -> FileSnapshot:
        candidate = absolute_path(path)
        with self._retain_parent(
            candidate.parent,
            preserve_observation_hooks=True,
        ):
            with _owned_handle(self._api, candidate, _OBSERVATION_SPEC) as handle:
                return _snapshot_retained_handle(
                    self._api,
                    handle,
                    candidate,
                    self._volume_id,
                )

    def copy_create_new_and_flush(
        self, source: Path, destination: Path
    ) -> FileSnapshot:
        source = absolute_path(source)
        destination = self._copy_destination(destination)
        with self._retain_parent(source.parent):
            with _owned_handle(self._api, source, _OBSERVATION_SPEC) as source_handle:
                source_before = self._api.inspect_handle(
                    source_handle,
                    security_information=_SECURITY_INFORMATION,
                )
                _require_ntfs_file(
                    source_before,
                    source,
                    self._volume_id,
                    role="copy source",
                )
                with self._retain_parent(destination.parent):
                    return self._copy_to_creator(
                        source_handle,
                        source_before,
                        destination,
                    )

    def _copy_to_creator(
        self,
        source_handle: int,
        source_before: _HandleFacts,
        destination: Path,
    ) -> FileSnapshot:
        with _owned_handle(self._api, destination, _CREATOR_SPEC) as creator:
            try:
                created = self._api.inspect_handle(
                    creator,
                    security_information=_SECURITY_INFORMATION,
                )
                _require_ntfs_file(
                    created,
                    destination,
                    self._volume_id,
                    role="copy destination",
                )
                total, sha256 = _copy_stream(self._api, source_handle, creator)
                self._api.set_security_descriptor(
                    creator,
                    source_before.security_descriptor,
                    security_information=_SECURITY_INFORMATION,
                )
                self._api.flush_file_buffers(creator)
                return self._finish_copy(
                    source_handle,
                    source_before,
                    creator,
                    destination,
                    total,
                    sha256,
                )
            except BaseException as primary:
                _delete_creator_preserving(self._api, creator, primary)
                raise

    def _finish_copy(
        self,
        source_handle: int,
        source_before: _HandleFacts,
        creator: int,
        destination: Path,
        total: int,
        sha256: str,
    ) -> FileSnapshot:
        source_after = self._api.inspect_handle(
            source_handle,
            security_information=_SECURITY_INFORMATION,
        )
        created = self._api.inspect_handle(
            creator,
            security_information=_SECURITY_INFORMATION,
        )
        if source_before != source_after or total != source_before.size:
            raise ReleaseTransitionError("copy source changed or drifted")
        _require_ntfs_file(
            created,
            destination,
            self._volume_id,
            role="copy destination",
        )
        if created.size != total or created.security_descriptor != (
            source_before.security_descriptor
        ):
            raise ReleaseTransitionError("copied file content or ACL is inconsistent")
        if created.link_count != 1 or _same_identity(created, source_before):
            raise ReleaseTransitionError("copied file identity is not unique")
        return _snapshot_from_facts(created, total, sha256)

    def write_create_new_and_flush(self, path: Path, payload: bytes) -> None:
        destination = self._receipt_destination(path)
        with self._retain_parent(destination.parent):
            with _owned_handle(self._api, destination, _CREATOR_SPEC) as creator:
                try:
                    created = self._api.inspect_handle(
                        creator,
                        security_information=_SECURITY_INFORMATION,
                    )
                    _require_ntfs_file(
                        created,
                        destination,
                        self._volume_id,
                        role="receipt",
                    )
                    _write_all(self._api, creator, payload)
                    self._api.flush_file_buffers(creator)
                    final = self._api.inspect_handle(
                        creator,
                        security_information=_SECURITY_INFORMATION,
                    )
                    _require_ntfs_file(
                        final,
                        destination,
                        self._volume_id,
                        role="receipt",
                    )
                    if final.size != len(payload) or final.link_count != 1:
                        raise ReleaseTransitionError("receipt write is not exact")
                except BaseException as primary:
                    _delete_creator_preserving(self._api, creator, primary)
                    raise

    def read_bytes(self, path: Path, *, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise ValueError("receipt size limit must be non-negative")
        candidate = absolute_path(path)
        with self._retain_parent(
            candidate.parent,
            preserve_observation_hooks=True,
        ):
            with _owned_handle(self._api, candidate, _OBSERVATION_SPEC) as handle:
                before = self._api.inspect_handle(
                    handle,
                    security_information=_SECURITY_INFORMATION,
                )
                _require_ntfs_file(
                    before,
                    candidate,
                    self._volume_id,
                    role="receipt",
                )
                if before.size > max_bytes:
                    raise ReceiptValidationError("receipt exceeds its size limit")
                payload = _read_sized(self._api, handle, before.size)
                after = self._api.inspect_handle(
                    handle,
                    security_information=_SECURITY_INFORMATION,
                )
                if before != after or len(payload) != before.size:
                    raise ReceiptValidationError(
                        "receipt changed during its handle read"
                    )
                return payload

    def path_exists(self, path: Path) -> bool:
        candidate = absolute_path(path)
        with self._retain_parent(candidate.parent):
            return self._api.path_exists(candidate)

    def remove_file_if_snapshot(self, path: Path, expected: FileSnapshot) -> None:
        candidate = self._cleanup_destination(path)
        if not same_path(Path(expected.path), candidate):
            raise ReleaseTransitionError("cleanup snapshot path mismatch")
        with self._retain_parent(candidate.parent):
            try:
                handle = self._api.open_file(candidate, _REMOVE_SPEC)
            except FileNotFoundError:
                return
            with _preopened_handle(self._api, handle):
                actual = _snapshot_retained_handle(
                    self._api,
                    handle,
                    candidate,
                    self._volume_id,
                )
                if (actual.volume_id, actual.file_id) != (
                    expected.volume_id,
                    expected.file_id,
                ):
                    raise ReleaseTransitionError("cleanup identity mismatch")
                if actual != expected:
                    raise ReleaseTransitionError("cleanup exact snapshot mismatch")
                self._api.set_delete_disposition(
                    handle,
                    information_class=_FILE_DISPOSITION_INFO_EX_CLASS,
                    flags=_FILE_DISPOSITION_FLAG_DELETE,
                )


@contextmanager
def _preopened_handle(api: _NtfsApiProtocol, handle: int) -> Iterator[None]:
    try:
        yield
    except BaseException as primary:
        try:
            api.close_handle(handle)
        except BaseException as cleanup:
            _add_cleanup_note(primary, "handle close", cleanup)
        raise
    else:
        api.close_handle(handle)
