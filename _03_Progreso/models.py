"""Backend-neutral progress event schema."""
# region [00] Contexto del módulo
# Módulo: _03_Progreso/models.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
# endregion [01]

# region [02] Implementación


@dataclass(frozen=True, slots=True)
class ProgressMetric:
    """One machine-readable live counter rendered by the selected frontend."""

    name: str
    value: int | str


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Absolute progress update for one stable operation/phase pair."""

    operation: str
    phase: str
    description: str
    completed: int
    total: int | None = None
    unit: str = "elementos"
    finished: bool = False
    metrics: tuple[ProgressMetric, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return self.operation, self.phase


ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)
# endregion [02]
