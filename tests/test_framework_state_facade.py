from __future__ import annotations

import importlib


def test_state_facade_preserves_public_import_contract() -> None:
    facade = importlib.import_module("_04_Nucleo_Operativo.state")
    route_repository = importlib.import_module(
        "_04_Nucleo_Operativo.framework_route_state"
    )
    schema = importlib.import_module("_04_Nucleo_Operativo.framework_schema")
    shared = importlib.import_module("_04_Nucleo_Operativo.framework_state_common")
    writer = importlib.import_module("_04_Nucleo_Operativo.framework_state_writer")

    assert facade.FrameworkState is writer.FrameworkState
    assert facade.FrameworkRouteState is route_repository.FrameworkRouteState
    assert (
        facade.ReviewCandidateReconciliation
        is route_repository.ReviewCandidateReconciliation
    )
    assert facade.FileActionSpec is shared.FileActionSpec
    assert facade.CACHE_PRUNE_BATCH_SIZE == shared.CACHE_PRUNE_BATCH_SIZE
    assert (
        facade.REVIEW_RECONCILIATION_BATCH_SIZE
        == route_repository.REVIEW_RECONCILIATION_BATCH_SIZE
    )
    assert facade.SCHEMA_VERSION == schema.SCHEMA_VERSION
    assert set(facade.__all__) == {
        "CACHE_PRUNE_BATCH_SIZE",
        "REVIEW_RECONCILIATION_BATCH_SIZE",
        "SCHEMA_VERSION",
        "FileActionSpec",
        "FrameworkRouteState",
        "FrameworkState",
        "ReviewCandidateReconciliation",
    }


def test_state_facade_classes_are_physically_separated() -> None:
    facade = importlib.import_module("_04_Nucleo_Operativo.state")

    assert facade.FrameworkState.__module__.endswith("framework_state_writer")
    assert facade.FrameworkRouteState.__module__.endswith("framework_route_state")
    assert facade.FrameworkState.__module__ != facade.FrameworkRouteState.__module__
