"""Stable facade for safe technical-document organization.

Planning only persists deterministic proposals. Filesystem mutation remains isolated
behind the explicitly invoked apply functions.
"""

from __future__ import annotations

from .document_organization_application import (
    _create_destination_parent as _create_destination_parent,
)
from .document_organization_application import (
    apply_all_document_organization,
    apply_document_organization,
)
from .document_organization_models import (
    DEFAULT_ORGANIZATION_DIRECTORY_NAME,
    ORGANIZATION_APPLY_BATCH_SIZE,
    ORGANIZATION_FILENAME_LIMIT,
    ORGANIZATION_PROGRESS_INTERVAL,
    OrganizationApplyProgress,
    OrganizationApplyProgressCallback,
    OrganizationApplySummary,
    OrganizationPlanSummary,
    OrganizationPlanView,
    default_organization_root,
    list_organization_plans,
)
from .document_organization_planning import plan_document_organization

__all__ = (
    "DEFAULT_ORGANIZATION_DIRECTORY_NAME",
    "ORGANIZATION_APPLY_BATCH_SIZE",
    "ORGANIZATION_FILENAME_LIMIT",
    "ORGANIZATION_PROGRESS_INTERVAL",
    "OrganizationApplyProgress",
    "OrganizationApplyProgressCallback",
    "OrganizationApplySummary",
    "OrganizationPlanSummary",
    "OrganizationPlanView",
    "apply_all_document_organization",
    "apply_document_organization",
    "default_organization_root",
    "list_organization_plans",
    "plan_document_organization",
)
