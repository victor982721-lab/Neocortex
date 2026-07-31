"""Fail-closed containment for tests that may invoke native file mutation."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, TypeVar


_T = TypeVar("_T")


class MutationContainmentError(RuntimeError):
    """A test path or post-condition escaped its dedicated mutation root."""


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    """Identity-bound evidence emitted after one contained native syscall."""

    operation: str
    source_path: Path
    destination_path: Path | None
    volume_id: int
    file_id: int
    source_parent_identity: tuple[int, int]
    destination_parent_identity: tuple[int, int] | None
    source_absent: bool
    destination_identity_confirmed: bool


@dataclass(frozen=True, slots=True)
class _PreparedRename:
    source: Path
    destination: Path
    source_identity: tuple[int, int]
    source_parent: Path
    destination_parent: Path
    source_parent_identity: tuple[int, int]
    destination_parent_identity: tuple[int, int]


def _reject_ambiguous_path(path: str | Path, *, label: str) -> Path:
    raw = os.fspath(path)
    candidate = Path(raw)
    if not raw or "\x00" in raw:
        raise MutationContainmentError(f"{label} path is blank or contains NUL")
    normalized_separators = raw.replace("/", "\\")
    if normalized_separators.startswith("\\\\"):
        raise MutationContainmentError(f"{label} path cannot be UNC")
    if not candidate.is_absolute():
        raise MutationContainmentError(f"{label} path must be absolute")
    if any(part in {".", ".."} for part in candidate.parts):
        raise MutationContainmentError(
            f"{label} path cannot contain relative traversal components"
        )
    return candidate


def _is_reparse(path: Path) -> bool:
    attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse_flag)


def _reject_reparse_chain(path: Path, *, label: str) -> None:
    anchor = Path(path.anchor)
    current = anchor
    if _is_reparse(anchor):
        raise MutationContainmentError(f"{label} path crosses a reparse point: {anchor}")
    for part in path.parts[1:]:
        current /= part
        if _is_reparse(current):
            raise MutationContainmentError(
                f"{label} path crosses a reparse point: {current}"
            )


def _canonical_existing_directory(path: str | Path, *, label: str) -> Path:
    candidate = _reject_ambiguous_path(path, label=label)
    try:
        canonical = candidate.resolve(strict=True)
    except OSError as exc:
        raise MutationContainmentError(f"{label} directory is unavailable: {exc}") from exc
    if not canonical.is_dir():
        raise MutationContainmentError(f"{label} path is not a directory")
    _reject_reparse_chain(candidate, label=label)
    _reject_reparse_chain(canonical, label=label)
    return canonical


def _directory_names(path: Path) -> frozenset[str]:
    try:
        return frozenset(entry.name for entry in os.scandir(path))
    except OSError as exc:
        raise MutationContainmentError(
            f"cannot inspect containment watch directory {path}: {exc}"
        ) from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


@dataclass(slots=True)
class ContainedMutationRoot:
    """Unique canonical root plus pre/post leak inspection for native tests."""

    root: Path
    trusted_parent: Path
    _root_identity: tuple[int, int]
    _watch_baselines: dict[Path, frozenset[str]]
    _validated_paths: set[Path] = field(default_factory=set)
    _mutation_receipts: list[MutationReceipt] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        base_directory: str | Path,
        *,
        watch_directories: Iterable[str | Path] | None = None,
    ) -> "ContainedMutationRoot":
        trusted_parent = _canonical_existing_directory(
            base_directory,
            label="mutation base",
        )
        watches = tuple(watch_directories or (trusted_parent, Path.cwd()))
        canonical_watches = tuple(
            dict.fromkeys(
                _canonical_existing_directory(path, label="leak watch")
                for path in watches
            )
        )
        baselines = {path: _directory_names(path) for path in canonical_watches}
        created = Path(
            tempfile.mkdtemp(prefix="neocortex-native-mutation-", dir=trusted_parent)
        )
        root = _canonical_existing_directory(created, label="mutation root")
        if root.parent != trusted_parent:
            raise MutationContainmentError("unique mutation root escaped its parent")
        root_stat = root.stat()
        return cls(
            root=root,
            trusted_parent=trusted_parent,
            _root_identity=(int(root_stat.st_dev), int(root_stat.st_ino)),
            _watch_baselines=baselines,
        )

    def _contained_existing(
        self,
        path: str | Path,
        *,
        label: str,
        allow_root: bool = False,
    ) -> Path:
        candidate = _reject_ambiguous_path(path, label=label)
        try:
            canonical = candidate.resolve(strict=True)
        except OSError as exc:
            raise MutationContainmentError(f"{label} path is unavailable: {exc}") from exc
        _reject_reparse_chain(candidate, label=label)
        _reject_reparse_chain(canonical, label=label)
        if canonical == self.root:
            if allow_root:
                return canonical
            raise MutationContainmentError(
                f"{label} must be an entry below the mutation root"
            )
        if not _is_within(canonical, self.root):
            raise MutationContainmentError(f"{label} path is outside the mutation root")
        return canonical

    def _contained_destination(self, path: str | Path) -> Path:
        candidate = _reject_ambiguous_path(path, label="destination")
        parent = self._contained_existing(
            candidate.parent,
            label="destination parent",
            allow_root=True,
        )
        canonical = parent / candidate.name
        if not _is_within(canonical, self.root):
            raise MutationContainmentError("destination path is outside the mutation root")
        if candidate.exists() or candidate.is_symlink():
            _reject_reparse_chain(candidate, label="destination")
            canonical = candidate.resolve(strict=True)
            if not _is_within(canonical, self.root):
                raise MutationContainmentError(
                    "existing destination is outside the mutation root"
                )
        return canonical

    def validate_mutation(
        self,
        source: str | Path,
        destination: str | Path | None = None,
    ) -> tuple[Path, Path | None]:
        """Validate every path before a native test boundary is reachable."""

        self.assert_no_leaks()
        canonical_source = self._contained_existing(source, label="source")
        canonical_destination = (
            None if destination is None else self._contained_destination(destination)
        )
        self._validated_paths.add(canonical_source)
        if canonical_destination is not None:
            self._validated_paths.add(canonical_destination)
        return canonical_source, canonical_destination

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        metadata = path.stat(follow_symlinks=False)
        return int(metadata.st_dev), int(metadata.st_ino)

    def _require_file_identity(self, path: Path, *, label: str) -> tuple[int, int]:
        if not path.is_file():
            raise MutationContainmentError(f"{label} is not a regular file")
        return self._require_entry_identity(path, label=label)

    def _require_entry_identity(self, path: Path, *, label: str) -> tuple[int, int]:
        if not os.path.lexists(path):
            raise MutationContainmentError(f"{label} is absent")
        identity = self._identity(path)
        if identity[0] != self._root_identity[0]:
            raise MutationContainmentError(
                f"{label} volume differs from the mutation root"
            )
        return identity

    def _prepare_rename(
        self,
        source: str | Path,
        destination: str | Path,
    ) -> _PreparedRename:
        canonical_source, canonical_destination = self.validate_mutation(
            source,
            destination,
        )
        if canonical_destination is None:  # pragma: no cover - signature invariant
            raise AssertionError("rename destination was not returned")
        if os.path.lexists(canonical_destination):
            raise MutationContainmentError("rename destination already exists")
        source_identity = self._require_entry_identity(
            canonical_source,
            label="rename source",
        )
        source_parent = self._contained_existing(
            canonical_source.parent,
            label="source parent",
            allow_root=True,
        )
        destination_parent = self._contained_existing(
            canonical_destination.parent,
            label="destination parent",
            allow_root=True,
        )
        source_parent_identity = self._identity(source_parent)
        destination_parent_identity = self._identity(destination_parent)
        if any(
            identity[0] != self._root_identity[0]
            for identity in (source_parent_identity, destination_parent_identity)
        ):
            raise MutationContainmentError(
                "rename parent volume differs from the mutation root"
            )
        return _PreparedRename(
            source=canonical_source,
            destination=canonical_destination,
            source_identity=source_identity,
            source_parent=source_parent,
            destination_parent=destination_parent,
            source_parent_identity=source_parent_identity,
            destination_parent_identity=destination_parent_identity,
        )

    def _confirm_rename(
        self,
        prepared: _PreparedRename,
        *,
        operation: str,
    ) -> MutationReceipt:
        if os.path.lexists(prepared.source):
            raise MutationContainmentError("rename source remains present")
        confirmed_destination = self._contained_existing(
            prepared.destination,
            label="renamed destination",
        )
        if self._require_entry_identity(
            confirmed_destination,
            label="renamed destination",
        ) != prepared.source_identity:
            raise MutationContainmentError("rename changed the observed file identity")
        if self._identity(prepared.source_parent) != prepared.source_parent_identity:
            raise MutationContainmentError("rename source-parent identity changed")
        if (
            self._identity(prepared.destination_parent)
            != prepared.destination_parent_identity
        ):
            raise MutationContainmentError("rename destination-parent identity changed")
        receipt = MutationReceipt(
            operation=operation,
            source_path=prepared.source,
            destination_path=confirmed_destination,
            volume_id=prepared.source_identity[0],
            file_id=prepared.source_identity[1],
            source_parent_identity=prepared.source_parent_identity,
            destination_parent_identity=prepared.destination_parent_identity,
            source_absent=True,
            destination_identity_confirmed=True,
        )
        self._mutation_receipts.append(receipt)
        self.assert_no_leaks()
        return receipt

    def call_rename(
        self,
        operation: Callable[..., _T],
        source: str | Path,
        destination: str | Path,
        *args: object,
        **kwargs: object,
    ) -> _T:
        """Run a production rename boundary inside pre/post identity checks."""

        prepared = self._prepare_rename(source, destination)
        try:
            result = operation(prepared.source, prepared.destination, *args, **kwargs)
        except BaseException as exc:
            if not os.path.lexists(prepared.source) and os.path.lexists(
                prepared.destination
            ):
                self._confirm_rename(prepared, operation="rename_effect_uncertain")
            elif os.path.lexists(prepared.source):
                if self._require_entry_identity(
                    prepared.source,
                    label="rename source after failure",
                ) != prepared.source_identity:
                    raise MutationContainmentError(
                        "rename failure left a different source identity"
                    ) from exc
                self.assert_no_leaks()
            else:
                raise MutationContainmentError(
                    "rename failure left an ambiguous filesystem effect"
                ) from exc
            raise
        self._confirm_rename(prepared, operation="rename")
        return result

    def rename(self, source: str | Path, destination: str | Path) -> MutationReceipt:
        """Revalidate, rename exactly once, and confirm the retained identity."""

        self.call_rename(os.rename, source, destination)
        return self._mutation_receipts[-1]

    def unlink(self, source: str | Path) -> MutationReceipt:
        """Revalidate, unlink exactly once, and confirm absence inside the root."""

        canonical_source, _destination = self.validate_mutation(source)
        source_identity = self._require_file_identity(
            canonical_source,
            label="unlink source",
        )
        source_parent = self._contained_existing(
            canonical_source.parent,
            label="source parent",
            allow_root=True,
        )
        source_parent_identity = self._identity(source_parent)
        if source_parent_identity[0] != self._root_identity[0]:
            raise MutationContainmentError(
                "unlink parent volume differs from the mutation root"
            )
        try:
            os.unlink(canonical_source)
        except BaseException:
            self.assert_no_leaks()
            raise
        if os.path.lexists(canonical_source):
            raise MutationContainmentError("unlink source remains present")
        if self._identity(source_parent) != source_parent_identity:
            raise MutationContainmentError("unlink source-parent identity changed")
        receipt = MutationReceipt(
            operation="unlink",
            source_path=canonical_source,
            destination_path=None,
            volume_id=source_identity[0],
            file_id=source_identity[1],
            source_parent_identity=source_parent_identity,
            destination_parent_identity=None,
            source_absent=True,
            destination_identity_confirmed=False,
        )
        self._mutation_receipts.append(receipt)
        self.assert_no_leaks()
        return receipt

    @property
    def mutation_receipts(self) -> tuple[MutationReceipt, ...]:
        """Return immutable evidence for confirmed contained syscalls."""

        return tuple(self._mutation_receipts)

    def assert_no_leaks(self) -> None:
        """Verify root identity and reject new entries outside the owned root."""

        current_root = _canonical_existing_directory(self.root, label="mutation root")
        root_stat = current_root.stat()
        if (int(root_stat.st_dev), int(root_stat.st_ino)) != self._root_identity:
            raise MutationContainmentError("mutation root identity changed")
        for watch, baseline in self._watch_baselines.items():
            current = _directory_names(watch)
            allowed = {self.root.name} if watch == self.trusted_parent else set()
            leaked = sorted(current.difference(baseline).difference(allowed))
            if leaked:
                raise MutationContainmentError(
                    f"new entries escaped the mutation root via {watch}: {leaked}"
                )
        if any(not _is_within(path, self.root) for path in self._validated_paths):
            raise MutationContainmentError("a validated path escaped the mutation root")

    def __enter__(self) -> "ContainedMutationRoot":
        self.assert_no_leaks()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.assert_no_leaks()


__all__ = ["ContainedMutationRoot", "MutationContainmentError", "MutationReceipt"]
