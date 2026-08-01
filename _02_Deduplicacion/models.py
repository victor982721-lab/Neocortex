"""Immutable public models used by the deduplication pipeline."""
# region [00] Contexto del módulo
# Módulo: _02_Deduplicacion/models.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

from dataclasses import dataclass
# endregion [01]

# region [02] Implementación


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    volume_id: int
    file_id: int
    size: int
    mtime_ns: int
    birthtime_ns: int

    @property
    def identity(self) -> tuple[int, int]:
        return self.volume_id, self.file_id


@dataclass(frozen=True, slots=True)
class ScanSummary:
    scan_id: int
    root: str
    files_seen: int
    directories_seen: int
    bytes_seen: int
    skipped_links: int
    excluded_directories: int
    errors: int


@dataclass(frozen=True, slots=True)
class InventoryCheckpoint:
    """Durable USN boundary associated with one policy-bound inventory."""

    root: str
    scan_id: int
    volume: str
    journal_id: int
    next_usn: int
    valid: bool = True
    inventory_policy_signature: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """One byte-for-byte equivalent set; no action has been executed."""

    size: int
    keep: FileSnapshot
    redundant: tuple[FileSnapshot, ...]
    full_fingerprint: str

    @property
    def reclaimable_bytes(self) -> int:
        return self.size * len(self.redundant)


@dataclass(frozen=True, slots=True)
class PlanStatistics:
    inventory_files: int
    size_candidate_files: int
    partial_hash_files: int
    full_hash_files: int
    exact_compare_files: int
    changed_or_unreadable_files: int


@dataclass(frozen=True, slots=True)
class DedupPlan:
    """Non-destructive plan of exact duplicate groups."""

    scan_id: int
    groups: tuple[DuplicateGroup, ...]
    statistics: PlanStatistics
    total_groups: int | None = None
    total_redundant_files: int | None = None
    total_reclaimable_bytes: int | None = None

    @property
    def group_count(self) -> int:
        return len(self.groups) if self.total_groups is None else self.total_groups

    @property
    def redundant_files(self) -> int:
        if self.total_redundant_files is not None:
            return self.total_redundant_files
        return sum(len(group.redundant) for group in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        if self.total_reclaimable_bytes is not None:
            return self.total_reclaimable_bytes
        return sum(group.reclaimable_bytes for group in self.groups)
# endregion [02]
