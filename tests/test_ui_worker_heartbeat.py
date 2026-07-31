from __future__ import annotations

from _03_Progreso import ProgressEvent
from _05_Interfaz.worker import (
    _active_progress_snapshot,
    _reset_active_progress,
    _track_progress,
)


def test_worker_heartbeat_tracks_only_unfinished_progress() -> None:
    _reset_active_progress()
    _track_progress(ProgressEvent("pdf", "profile", "Perfilando PDF", 0, 26, "PDF"))

    active = _active_progress_snapshot()
    assert len(active) == 1
    assert active[0]["description"] == "Perfilando PDF"

    _track_progress(
        ProgressEvent(
            "pdf", "profile", "Perfiles PDF actualizados", 26, 26, "PDF", True
        )
    )
    assert _active_progress_snapshot() == []
