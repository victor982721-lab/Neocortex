"""Optional semantic-code search integration without downloading a model."""
# region [00] Contexto del módulo
# Módulo: tests/test_code_semantic_search.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Mapping, Sequence

import pytest

from _02_Deduplicacion import FileSnapshot
from _04_Nucleo_Operativo import semantic_service
from _04_Nucleo_Operativo import (
    semantic_search_service as semantic_search_implementation,
)
from _04_Nucleo_Operativo.code_contracts import CodeRouteConfig, CodeSearchQuery
from _04_Nucleo_Operativo.code_route import CodeRoute
from _04_Nucleo_Operativo.code_search import search_code
from _04_Nucleo_Operativo.semantic_models import (
    BackendEmbedding,
    EmbeddingModelSpec,
    EmbeddingRequest,
)
# endregion [01]

# region [02] Implementación


def _snapshot(path: Path) -> FileSnapshot:
    observed = path.stat()
    return FileSnapshot(
        str(path),
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        getattr(observed, "st_birthtime_ns", observed.st_ctime_ns),
    )


class _Inventory:
    def __init__(self, paths: Iterable[Path]):
        self.paths = tuple(paths)

    def snapshots(self, _scan_id: int):
        return iter(_snapshot(path) for path in self.paths)


class _FrameworkState:
    def begin_route_phase(
        self,
        _run_id: int,
        _route: str,
        _phase: str,
        *,
        source_run_id: int | None = None,
    ) -> None:
        del source_run_id

    def complete_route_phase(
        self,
        _run_id: int,
        _route: str,
        _phase: str,
        summary: Mapping[str, object] | None = None,
    ) -> None:
        assert summary is not None

    def fail_route_phase(
        self,
        _run_id: int,
        _route: str,
        _phase: str,
        _exc: BaseException,
    ) -> None:
        raise AssertionError("semantic search fixture route must not fail")


class _ConstantBackend:
    def __init__(self, model: EmbeddingModelSpec) -> None:
        self.model = model

    @property
    def max_batch_size(self) -> int:
        return 16

    def embed(
        self,
        requests: Sequence[EmbeddingRequest],
    ) -> Sequence[BackendEmbedding]:
        vector = (1.0,) + (0.0,) * (self.model.dimensions - 1)
        return tuple(
            BackendEmbedding(
                request_id=request.request_id,
                vector=vector,
                provenance={"backend": "code-cancellation-fixture"},
            )
            for request in requests
        )


def test_semantic_hits_resolve_to_current_rows_and_apply_filters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "access.py"
    source_text = (
        "import sqlite3\n\n"
        + "\n".join(f"PREFIX_{index} = {index}" for index in range(160))
        + "\n\ndef validate_access(path):\n"
        + "    return sqlite3.connect(path).execute('PRAGMA quick_check').fetchone()\n"
    )
    source.write_text(source_text, encoding="utf-8")
    state_path = tmp_path / "state" / "code.sqlite3"
    config = CodeRouteConfig(
        state_path=state_path,
        dedup_path=tmp_path / "state" / "dedup.sqlite3",
        chunk_chars=1024,
    )
    CodeRoute(
        config,
        _Inventory((source,)),
        _FrameworkState(),
        1,
        1,
    ).run()
    with sqlite3.connect(state_path) as connection:
        target = connection.execute(
            """SELECT f.volume_id || ':' || f.physical_file_id,v.version_id,
            c.chunk_index,c.kind,c.start_line,c.end_line,s.qualified_name
            FROM files f JOIN file_versions v ON v.version_id=f.current_version_id
            JOIN code_chunks c ON c.version_id=v.version_id
            LEFT JOIN symbols s ON s.symbol_id=c.symbol_id
            WHERE c.chunk_index>0 ORDER BY c.chunk_index DESC LIMIT 1"""
        ).fetchone()
    assert target is not None
    source_identity = str(target[0])
    target_version_id = int(target[1])
    target_chunk_index = int(target[2])
    target_kind = str(target[3])
    target_start_line = int(target[4])
    target_end_line = int(target[5])
    target_symbol = None if target[6] is None else str(target[6])

    (state_path.parent / "semantic.sqlite3").touch()
    resolved = SimpleNamespace(
        source_kind="code",
        source_identity=source_identity,
        section_kind=f"code_{target_kind}",
        section_id=str(target_chunk_index),
        source_revision={"version_id": target_version_id},
        snippet="Validate SQLite access with PRAGMA quick_check",
        hit=SimpleNamespace(
            indexed_model_signature="fixture-model-v1",
            score=0.875,
            generation_id=4,
        ),
    )
    result = SimpleNamespace(
        rankings=(
            SimpleNamespace(
                name="semantic_text",
                available=True,
                resolved=(resolved,),
            ),
        )
    )
    monkeypatch.setattr(
        semantic_service,
        "search_semantic_index",
        lambda *_args, **_kwargs: result,
    )

    hits = search_code(
        state_path,
        CodeSearchQuery(
            text="where is SQLite access validated",
            modes=("semantic",),
            path="access.py",
            language="python",
        ),
    )
    rejected = search_code(
        state_path,
        CodeSearchQuery(
            text="where is SQLite access validated",
            modes=("semantic",),
            language="rust",
        ),
    )

    semantic_hits = tuple(hit for hit in hits if "semantic" in hit.match_types)
    assert len(semantic_hits) == 1
    assert semantic_hits[0].path == str(source)
    assert semantic_hits[0].language == "python"
    assert semantic_hits[0].version_id == target_version_id
    assert semantic_hits[0].start_line == target_start_line
    assert semantic_hits[0].end_line == target_end_line
    assert semantic_hits[0].symbol == target_symbol
    assert any(
        evidence.startswith("semantic:fixture-model-v1:0.87500000")
        for evidence in semantic_hits[0].evidence
    )
    assert rejected == ()

    base_resolved = vars(resolved)
    invalid_overrides: tuple[dict[str, object], ...] = (
        {"section_id": f"0{target_chunk_index}"},
        {"section_id": "-1"},
        {"section_id": "not-a-chunk"},
        {"section_id": "9223372036854775808"},
        {"section_id": str(target_chunk_index + 10_000)},
        {"section_kind": "text"},
        {"section_kind": f"code_{target_kind}_missing"},
        {"source_revision": {}},
        {"source_revision": {"version_id": True}},
        {"source_revision": {"version_id": target_version_id + 10_000}},
        {"source_identity": f"{source_identity}:missing"},
    )
    for overrides in invalid_overrides:
        invalid = SimpleNamespace(**(base_resolved | overrides))
        invalid_result = SimpleNamespace(
            rankings=(
                SimpleNamespace(
                    name="semantic_text",
                    available=True,
                    resolved=(invalid,),
                ),
            )
        )
        monkeypatch.setattr(
            semantic_service,
            "search_semantic_index",
            lambda *_args, _result=invalid_result, **_kwargs: _result,
        )
        assert search_code(
            state_path,
            CodeSearchQuery(text="SQLite access", modes=("semantic",)),
        ) == ()

    source.write_text(source_text + "\n# changed revision\n", encoding="utf-8")
    CodeRoute(
        config,
        _Inventory((source,)),
        _FrameworkState(),
        2,
        2,
    ).run()
    monkeypatch.setattr(
        semantic_service,
        "search_semantic_index",
        lambda *_args, **_kwargs: result,
    )
    assert search_code(
        state_path,
        CodeSearchQuery(text="SQLite access", modes=("semantic",)),
    ) == ()


def test_code_semantic_search_cancels_inside_exact_vector_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "breaker.py"
    source.write_text(
        "def inspect_breaker():\n    return 'protection breaker'\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "state" / "code.sqlite3"
    config = CodeRouteConfig(
        state_path=state_path,
        dedup_path=tmp_path / "state" / "dedup.sqlite3",
    )
    CodeRoute(config, _Inventory((source,)), _FrameworkState(), 1, 1).run()
    monkeypatch.setattr(
        semantic_service,
        "_backend",
        lambda model, **_kwargs: _ConstantBackend(model),
    )
    semantic_service.index_text_embeddings(
        state_path.parent,
        source_kinds=("code",),
    )

    original_search = semantic_search_implementation.search_exact_page
    entered_vector_scan = False

    def observing_search(*args, **kwargs):
        nonlocal entered_vector_scan
        entered_vector_scan = True
        return original_search(*args, **kwargs)

    monkeypatch.setattr(
        semantic_search_implementation,
        "search_exact_page",
        observing_search,
    )

    class SearchCancelled(Exception):
        pass

    cancellation = SearchCancelled("cancel code semantic vector scan")
    repository_checkpoints = 0

    def cancellation_check() -> None:
        nonlocal repository_checkpoints
        if not entered_vector_scan:
            return
        repository_checkpoints += 1
        if repository_checkpoints == 2:
            raise cancellation

    with pytest.raises(SearchCancelled) as raised:
        search_code(
            state_path,
            CodeSearchQuery(text="protection breaker", modes=("semantic",)),
            cancellation_check=cancellation_check,
        )

    assert raised.value is cancellation
    assert entered_vector_scan
    assert repository_checkpoints == 2
# endregion [02]
