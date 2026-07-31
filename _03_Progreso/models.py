"""Backend-neutral progress event schema."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


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
