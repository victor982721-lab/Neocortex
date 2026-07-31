"""Compatible application configuration and domain projections.

The flat configuration remains the public construction boundary while runtime
owners migrate to explicit domain contracts.  Projections are deliberately
computed from the current instance so canonical path replacements cannot leave
stale nested state behind.
"""

from __future__ import annotations

from typing import TypeAlias

from .application_config_projections import (
    audio_route_config_from_application,
    code_route_config_from_application,
    docx_route_config_from_application,
    global_resource_limits_from_application,
    image_route_config_from_application,
    office_route_config_from_application,
    pdf_route_config_from_application,
)
from .models import FrameworkConfig

__all__ = [
    "ApplicationConfig",
    "FrameworkConfig",
    "audio_route_config_from_application",
    "code_route_config_from_application",
    "docx_route_config_from_application",
    "global_resource_limits_from_application",
    "image_route_config_from_application",
    "office_route_config_from_application",
    "pdf_route_config_from_application",
]


# ``FrameworkConfig`` is a durable legacy import and a frozen slotted dataclass.
# Keeping object identity during this migration preserves its constructor,
# dataclasses.replace behavior, equality, hashing and derived database paths.
ApplicationConfig: TypeAlias = FrameworkConfig
