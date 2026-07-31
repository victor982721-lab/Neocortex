"""Stable facade for durable framework state repositories.

The single-writer lifecycle/cache repository and the short-lived concurrent
route/review repository intentionally live in separate modules.  Existing
imports continue to use this module as the public compatibility surface.
"""

from __future__ import annotations

from .framework_route_state import (
    REVIEW_RECONCILIATION_BATCH_SIZE,
    FrameworkRouteState,
    ReviewCandidateReconciliation,
)
from .framework_schema import SCHEMA_VERSION
from .framework_state_common import CACHE_PRUNE_BATCH_SIZE, FileActionSpec
from .framework_state_writer import FrameworkState


__all__ = (
    "CACHE_PRUNE_BATCH_SIZE",
    "REVIEW_RECONCILIATION_BATCH_SIZE",
    "SCHEMA_VERSION",
    "FileActionSpec",
    "FrameworkRouteState",
    "FrameworkState",
    "ReviewCandidateReconciliation",
)
