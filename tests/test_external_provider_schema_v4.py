"""Regression contracts for generic external metrics and relations in Code v4."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from _04_Nucleo_Operativo import code_schema
from _04_Nucleo_Operativo.code_external_evidence import ExternalEvidencePublication
from _04_Nucleo_Operativo.external_evidence_models import (
    ExternalProviderMetric,
    ExternalProviderPublication,
    ExternalProviderRelation,
    ExternalRunInput,
    ProviderDescriptor,
    ProviderLimits,
    external_findings_digest,
    external_metric_identity,
    external_provider_result_digest,
    external_relation_identity,
)
from _04_Nucleo_Operativo.external_evidence_store import (
    publish_external_provider,
    read_external_evidence_suite,
    read_external_provider_baselines,
    read_external_provider_evidence,
)


def _create_current_owner(database: Path, *analysis_run_ids: int) -> None:
    code_schema.initialize_code_state(database)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executemany(
            """INSERT INTO analysis_runs(
            analysis_run_id,framework_run_id,scan_id,processing_signature,status,
            started_ns,candidates,processed,cache_hits,errors)
            VALUES(?,?,?,'fixture','running',1,1,1,0,0)""",
            ((run_id, run_id, run_id) for run_id in analysis_run_ids),
        )
        connection.execute(
            """INSERT INTO files(
            file_id,volume_id,physical_file_id,current_path,current_version_id,
            status,first_seen_run_id,last_seen_run_id)
            VALUES(1,'volume','physical','C:/fixture/a.py',NULL,'current',1,1)"""
        )
        connection.execute(
            """INSERT INTO file_versions(
            version_id,file_id,path_observed,size,mtime_ns,birthtime_ns,
            raw_xxh3_128,raw_xxh3_64_guard,text_xxh3_128,text_xxh3_64_guard,
            normalized_xxh3_128,token_xxh3_128,structure_xxh3_128,encoding,
            language,artifact_kind,generated,vendored,classification_confidence,
            classification_evidence_json,analysis_status,processing_signature,
            analyzer_id,analyzer_version,parser_kind,text_zlib,text_chars,
            text_truncated,provenance_json,first_observed_run_id,
            last_observed_run_id,valid_from_ns,invalidated_ns,invalidation_reason)
            VALUES(1,1,'C:/fixture/a.py',4,1,1,
            'raw-128','raw-64','text-128','text-64','normalized','token','structure',
            'utf-8','python','source',0,0,1.0,'["fixture"]','complete','fixture',
            'fixture','1','python-ast',NULL,4,0,'{"fixture":true}',1,1,1,NULL,NULL)"""
        )
        connection.execute("UPDATE files SET current_version_id=1 WHERE file_id=1")
        connection.commit()
    finally:
        connection.close()


def _descriptor(provider_id: str) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id,
        f"neocortex.{provider_id}/v1",
        "fixture-tool",
        "protected",
        "untrusted-safe",
        "project",
        f"external:{provider_id}",
        "fixture-configuration",
        None,
        "fixture-environment",
        f"fixture-comparability:{provider_id}",
        "bounded-fixture",
        "project_wide",
        "exact-input",
        ProviderLimits(1.0, 1_000_000, 1_000_000, 1_000_000, 100),
    )


def _metric(provider_id: str, *, value: float = 3.0) -> ExternalProviderMetric:
    return ExternalProviderMetric(
        external_metric_identity(
            provider_id,
            subject_kind="file",
            subject_key="a.py",
            category="architecture",
            metric_name="fan_out",
            unit="edges",
        ),
        "file",
        "a.py",
        "architecture",
        "fan_out",
        value,
        "edges",
        version_id=1,
        metadata={"definition": "direct module imports"},
    )


def _relation(provider_id: str) -> ExternalProviderRelation:
    return ExternalProviderRelation(
        external_relation_identity(
            provider_id,
            relation_kind="module_import",
            source_kind="file",
            source_key="a.py",
            target_kind="module",
            target_key="pkg.b",
        ),
        "module_import",
        "file",
        "a.py",
        "module",
        "pkg.b",
        source_version_id=1,
        metadata={"line": 1, "oracle": "fixture"},
    )


def _full_publication(provider_id: str) -> ExternalProviderPublication:
    metric = _metric(provider_id)
    relation = _relation(provider_id)
    digest = external_provider_result_digest((), (metric,), (relation,))
    return ExternalProviderPublication(
        _descriptor(provider_id),
        ExternalEvidencePublication(
            "fixture-tool",
            "1.0",
            "fixture-configuration",
            "completed",
            1,
            2,
            {"execution": "full"},
        ),
        "C:/fixture",
        "fixture-root",
        "fixture-input",
        (ExternalRunInput(1, "input:a.py", "a.py", True, True, None, 4, "digest"),),
        (),
        {
            "eligible_files": 1,
            "covered_files": 1,
            "findings": 0,
            "metrics": 1,
            "relations": 1,
            "comparable": 0,
        },
        True,
        digest,
        f"publication:{provider_id}:full",
        metrics=(metric,),
        relations=(relation,),
    )


def _replay_publication(
    source: ExternalProviderPublication,
    source_tool_run_id: int,
) -> ExternalProviderPublication:
    return ExternalProviderPublication(
        source.descriptor,
        replace(
            source.publication,
            status="skipped",
            started_ns=3,
            completed_ns=4,
            provenance={"execution": "cache_replay"},
        ),
        source.observed_root,
        source.root_identity,
        source.input_signature,
        source.inputs,
        (),
        {
            "eligible_files": 1,
            "covered_files": 1,
            "files_verified": 1,
            "bytes_verified": 4,
            "findings": 0,
            "metrics": 1,
            "relations": 1,
            "comparable": 1,
            "added": 0,
            "resolved": 0,
        },
        True,
        source.result_digest,
        f"publication:{source.descriptor.provider_id}:replay",
        source_tool_run_id,
        "verification:fixture",
    )


def _downgrade_fixture_to_populated_v3(database: Path) -> tuple[tuple[object, ...], ...]:
    _create_current_owner(database, 1)
    connection = code_schema.connect_code_state(database, create=False)
    try:
        publication = replace(
            _full_publication("legacy-provider"),
            metrics=(),
            relations=(),
            result_digest=external_findings_digest(()),
        )
        publish_external_provider(connection, 1, publication)
        connection.execute("DROP TABLE external_relations")
        connection.execute("DROP TABLE external_metrics")
        connection.execute("DELETE FROM schema_migrations WHERE version=4")
        connection.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")
        connection.execute("PRAGMA user_version=3")
        rows = tuple(
            tuple(row)
            for row in connection.execute(
                """SELECT r.tool_run_id,r.analysis_run_id,c.provider_id,c.result_digest
                FROM external_tool_runs r JOIN external_run_contracts c
                ON c.tool_run_id=r.tool_run_id ORDER BY r.tool_run_id"""
            )
        )
        connection.commit()
        return rows
    finally:
        connection.close()


def test_v1_digest_and_hito1_publication_constructor_remain_exact() -> None:
    digest = external_findings_digest(())
    publication = ExternalProviderPublication(
        _descriptor("hito1-provider"),
        ExternalEvidencePublication(
            "fixture-tool",
            "1.0",
            "fixture-configuration",
            "completed",
            1,
            2,
            {"execution": "full"},
        ),
        "C:/fixture",
        "fixture-root",
        "fixture-input",
        (),
        (),
        {},
        True,
        digest,
        "publication:hito1",
    )

    assert publication.metrics == ()
    assert publication.relations == ()
    assert external_provider_result_digest(publication.findings) == digest


def test_v2_digest_and_portable_identities_are_order_stable() -> None:
    first_metric = _metric("architecture-provider", value=1)
    changed_value = _metric("architecture-provider", value=9)
    relation = _relation("architecture-provider")

    assert first_metric.portable_metric_id == changed_value.portable_metric_id
    digest = external_provider_result_digest((), (first_metric,), (relation,))
    assert digest.startswith("external-provider-result-v2:xxh3_128:")
    assert digest != external_provider_result_digest((), (changed_value,), (relation,))
    assert digest == external_provider_result_digest((), (first_metric,), (relation,))


def test_fresh_schema_records_exact_history_one_through_four(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite3"
    code_schema.initialize_code_state(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone() == ("4",)
        code_schema.validate_code_schema(connection)


def test_populated_v3_migrates_transactionally_without_changing_prior_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "populated-v3.sqlite3"
    before = _downgrade_fixture_to_populated_v3(database)

    code_schema.initialize_code_state(database)

    with sqlite3.connect(database) as connection:
        after = tuple(
            tuple(row)
            for row in connection.execute(
                """SELECT r.tool_run_id,r.analysis_run_id,c.provider_id,c.result_digest
                FROM external_tool_runs r JOIN external_run_contracts c
                ON c.tool_run_id=r.tool_run_id ORDER BY r.tool_run_id"""
            )
        )
        assert after == before
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute("SELECT COUNT(*) FROM external_metrics").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM external_relations").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v3_to_v4_migration_failure_rolls_back_every_new_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "rollback-v3.sqlite3"
    before = _downgrade_fixture_to_populated_v3(database)
    monkeypatch.setattr(
        code_schema,
        "_V4_DDL",
        (code_schema._V4_DDL[0], "CREATE TABLE deliberately_incomplete("),
    )

    with pytest.raises(sqlite3.OperationalError):
        code_schema.initialize_code_state(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone() == ("3",)
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='external_metrics'"
            ).fetchone()
            is None
        )
        assert (
            tuple(
                tuple(row)
                for row in connection.execute(
                    """SELECT r.tool_run_id,r.analysis_run_id,c.provider_id,c.result_digest
                FROM external_tool_runs r JOIN external_run_contracts c
                ON c.tool_run_id=r.tool_run_id ORDER BY r.tool_run_id"""
                )
            )
            == before
        )


def test_publication_replay_and_baseline_resolve_all_evidence(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite3"
    _create_current_owner(database, 1, 2)
    connection = code_schema.connect_code_state(database, create=False)
    try:
        source = _full_publication("architecture-provider")
        source_run_id = publish_external_provider(connection, 1, source)
        replay_run_id = publish_external_provider(
            connection,
            2,
            _replay_publication(source, source_run_id),
        )
        connection.commit()

        evidence = read_external_provider_evidence(connection, 2)["architecture-provider"]
        suite = read_external_evidence_suite(connection, 2, enforce_current_runtime=False)
        exact, comparable = read_external_provider_baselines(
            connection,
            provider_id="architecture-provider",
            profile="protected",
            tool_version="1.0",
            configuration_signature="fixture-configuration",
            environment_signature="fixture-environment",
            root_identity="fixture-root",
            input_signature="fixture-input",
            comparability_signature="fixture-comparability:architecture-provider",
        )
    finally:
        connection.close()

    assert evidence.status == "ready"
    assert evidence.tool_run_id == replay_run_id
    assert evidence.effective_tool_run_id == source_run_id
    assert len(evidence.metrics) == len(evidence.relations) == 1
    assert evidence.relations[0].relation_kind == "module_import"
    assert suite.providers[0].metrics == suite.providers[0].relations == 1
    assert suite.providers[0].result_digest == source.result_digest
    assert exact is not None and comparable is not None
    assert exact.portable_metric_ids == (source.metrics[0].portable_metric_id,)
    assert exact.portable_relation_ids == (source.relations[0].portable_relation_id,)


def test_one_corrupt_provider_abstains_without_hiding_a_valid_provider(
    tmp_path: Path,
) -> None:
    database = tmp_path / "coexistence.sqlite3"
    _create_current_owner(database, 1)
    connection = code_schema.connect_code_state(database, create=False)
    try:
        first_id = publish_external_provider(connection, 1, _full_publication("first"))
        publish_external_provider(connection, 1, _full_publication("second"))
        connection.execute(
            "UPDATE external_metrics SET metadata_json='not-json' WHERE tool_run_id=?",
            (first_id,),
        )
        connection.commit()
        suite = read_external_evidence_suite(connection, 1, enforce_current_runtime=False)
        evidence = read_external_provider_evidence(connection, 1)
    finally:
        connection.close()

    statuses = {item.provider_id: item for item in suite.providers}
    assert statuses["first"].status == "abstained"
    assert statuses["first"].reason == "external_provider_projection_invalid"
    assert statuses["second"].status == "ready"
    assert evidence["first"].status == "abstained"
    assert evidence["second"].status == "ready"
    assert len(evidence["second"].metrics) == len(evidence["second"].relations) == 1


def test_invalid_provider_publication_rolls_back_its_whole_savepoint(
    tmp_path: Path,
) -> None:
    database = tmp_path / "atomic.sqlite3"
    _create_current_owner(database, 1)
    valid = _full_publication("duplicate-provider")
    duplicate = replace(
        valid,
        metrics=(valid.metrics[0], valid.metrics[0]),
        counters={**valid.counters, "metrics": 2},
        result_digest=external_provider_result_digest(
            (),
            (valid.metrics[0], valid.metrics[0]),
            valid.relations,
        ),
    )
    connection = code_schema.connect_code_state(database, create=False)
    try:
        with pytest.raises(ValueError, match="duplicate metric identities"):
            publish_external_provider(connection, 1, duplicate)
        for table in (
            "external_tool_runs",
            "external_run_contracts",
            "external_run_inputs",
            "external_metrics",
            "external_relations",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()
