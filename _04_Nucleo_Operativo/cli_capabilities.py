"""Canonical read-only runtime-capabilities doctor handler."""


# region [01] Imports and exit contract

from __future__ import annotations

import argparse
import json
import sys
from enum import IntEnum

from neocortex.capabilities import (
    RUNTIME_CAPABILITY_PROBE_POLICY,
    RUNTIME_CAPABILITY_SCHEMA_VERSION,
    CapabilityState,
    RuntimeCapabilityStatus,
    inspect_runtime_capabilities,
)

__all__ = ["CapabilitiesExitCode", "run_doctor_capabilities"]


class CapabilitiesExitCode(IntEnum):
    """Stable process outcomes for a valid or failed lightweight probe."""

    SUCCESS = 0
    FATAL = 1
    NOT_FULLY_AVAILABLE = 2


# endregion [01]


# region [02] Canonical report and presentation


def _all_available(statuses: tuple[RuntimeCapabilityStatus, ...]) -> bool:
    return all(status.state is CapabilityState.AVAILABLE for status in statuses)


def _report_payload(
    statuses: tuple[RuntimeCapabilityStatus, ...],
) -> dict[str, object]:
    return {
        "schema_version": RUNTIME_CAPABILITY_SCHEMA_VERSION,
        "kind": "runtime_capabilities_report",
        "probe_policy": RUNTIME_CAPABILITY_PROBE_POLICY,
        "all_available": _all_available(statuses),
        "capabilities": [status.to_dict() for status in statuses],
    }


def _quoted(value: str | None) -> str:
    if value is None:
        return "-"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _print_human(statuses: tuple[RuntimeCapabilityStatus, ...]) -> None:
    print(
        "CAPABILITIES "
        f"schema={RUNTIME_CAPABILITY_SCHEMA_VERSION} "
        f"probe_policy={RUNTIME_CAPABILITY_PROBE_POLICY} "
        f"count={len(statuses)} all_available={int(_all_available(statuses))}"
    )
    for status in statuses:
        reasons = ",".join(status.degradation_reasons) or "-"
        print(
            f"CAPABILITY name={status.capability} state={status.state.value} "
            f"extra={status.extra or '-'} reasons={reasons}"
        )
        for component in status.components:
            requirement = component.requirement
            reason = "-" if component.available else requirement.missing_reason
            print(
                f"CAPABILITY_COMPONENT capability={status.capability} "
                f"component={requirement.component} kind={requirement.kind.value} "
                f"required={int(requirement.required)} "
                f"available={int(component.available)} "
                f"version={_quoted(component.version)} path={_quoted(component.path)} "
                f"reason={reason} extra={requirement.extra or '-'}"
            )


# endregion [02]


# region [03] Direct handler


def run_doctor_capabilities(args: argparse.Namespace) -> int:
    """Inspect declared runtime prerequisites without loading optional engines."""

    try:
        statuses = inspect_runtime_capabilities()
        serialized = (
            json.dumps(
                _report_payload(statuses),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if args.doctor_capabilities_json
            else None
        )
    except Exception as exc:
        print(
            f"ERROR doctor-capabilities {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return int(CapabilitiesExitCode.FATAL)

    if serialized is not None:
        print(serialized)
    else:
        _print_human(statuses)
    return int(
        CapabilitiesExitCode.SUCCESS
        if _all_available(statuses)
        else CapabilitiesExitCode.NOT_FULLY_AVAILABLE
    )


# endregion [03]
