"""Reusable, non-destructive deduplication for all content routes."""
# region [00] Contexto del módulo
# Módulo: _02_Deduplicacion/__init__.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from .errors import DedupError, FileChangedError, InventoryError, MissingDependencyError
from .hashing import (
    FULL_ALGORITHM,
    PARTIAL_ALGORITHM,
    files_equal_exact,
    full_fingerprint,
    partial_fingerprint,
    snapshot_path,
    stat_matches_snapshot,
)
from .inventory import (
    DEFAULT_INVENTORY_EXCLUSION_POLICY,
    DedupIndex,
    InventoryExclusionPolicy,
)
from .models import (
    DedupPlan,
    DuplicateGroup,
    FileSnapshot,
    InventoryCheckpoint,
    PlanStatistics,
    ScanSummary,
)
from .planner import DedupPlanner
# endregion [01]

# region [02] Implementación

__all__ = [
    "DedupError",
    "DedupIndex",
    "DedupPlan",
    "DedupPlanner",
    "DuplicateGroup",
    "DEFAULT_INVENTORY_EXCLUSION_POLICY",
    "FULL_ALGORITHM",
    "FileChangedError",
    "FileSnapshot",
    "InventoryCheckpoint",
    "InventoryError",
    "InventoryExclusionPolicy",
    "MissingDependencyError",
    "PARTIAL_ALGORITHM",
    "PlanStatistics",
    "ScanSummary",
    "files_equal_exact",
    "full_fingerprint",
    "partial_fingerprint",
    "snapshot_path",
    "stat_matches_snapshot",
]
# endregion [02]
