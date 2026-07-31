from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from _04_Nucleo_Operativo import action_policy
from _04_Nucleo_Operativo.action_policy import validate_mutation_path
from _04_Nucleo_Operativo.corpus_access import (
    CorpusAccessPolicy,
    CorpusMutationGuard,
)
from _04_Nucleo_Operativo.document_organization import _create_destination_parent
from _04_Nucleo_Operativo.document_taxonomy import (
    MAX_TAXONOMY_BYTES,
    load_taxonomy,
)
from _04_Nucleo_Operativo.pdf_isolation import _read_file_tail
from _05_Interfaz.controller import MAX_PROCESS_LINE_BYTES, WorkerController
from tests.internal_paths_test_support import disjoint_internal_paths_policy


def test_qpdf_diagnostic_tail_read_is_bounded(tmp_path: Path) -> None:
    diagnostics = tmp_path / "qpdf.stderr"
    diagnostics.write_bytes(b"prefix" * 20_000 + b"expected-tail")

    sample = _read_file_tail(diagnostics, 64)

    assert len(sample) == 64
    assert sample.endswith(b"expected-tail")


def test_taxonomy_file_size_is_bounded(tmp_path: Path) -> None:
    taxonomy_path = tmp_path / "taxonomy.toml"
    taxonomy_path.write_bytes(b" " * (MAX_TAXONOMY_BYTES + 1))

    with pytest.raises(ValueError, match="exceeds"):
        load_taxonomy(taxonomy_path)


def test_taxonomy_rejects_nested_repetition(tmp_path: Path) -> None:
    taxonomy_path = tmp_path / "taxonomy.toml"
    taxonomy_path.write_text(
        """
[[authorities]]
code = "UNSAFE"
aliases = ["UNSAFE"]
identifier_patterns = ["(a+)+$"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsafe identifier pattern"):
        load_taxonomy(taxonomy_path)


def test_controller_discards_oversized_unterminated_line_and_resynchronizes() -> None:
    controller = WorkerController()
    emitted: list[str] = []
    controller.output_received.connect(emitted.append)

    controller._ingest_output(  # noqa: SLF001 - direct bounded-buffer regression
        controller._stdout_buffer,  # noqa: SLF001
        b"x" * (MAX_PROCESS_LINE_BYTES + 1),
        protocol=True,
    )

    assert not controller._stdout_buffer  # noqa: SLF001
    assert controller._stdout_discarding_oversized_line  # noqa: SLF001
    controller._ingest_output(  # noqa: SLF001
        controller._stdout_buffer,  # noqa: SLF001
        b"discarded suffix\nvisible output\n",
        protocol=True,
    )
    assert not controller._stdout_discarding_oversized_line  # noqa: SLF001
    assert any("exceder el límite" in message for message in emitted)
    assert emitted[-1] == "visible output"


def test_mutation_policy_allows_only_a_missing_destination_suffix(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organized"
    root.mkdir()
    destination = root / "one" / "two" / "document.pdf"

    result = validate_mutation_path(
        root,
        destination,
        role="organization destination",
        allow_missing_tail=True,
    )

    assert result is None


def test_destination_parent_creation_rejects_existing_reparse_component(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    root = tmp_path / "organized"
    unsafe_parent = root / "unsafe"
    unsafe_parent.mkdir(parents=True)
    destination = unsafe_parent / "nested" / "document.pdf"
    unsafe_key = os.path.normcase(os.path.abspath(unsafe_parent))
    real_check = action_policy._is_reparse_entry

    def simulated_reparse(path: Path, entry_stat: os.stat_result) -> bool:
        return os.path.normcase(os.path.abspath(path)) == unsafe_key or real_check(
            path, entry_stat
        )

    with patch.object(
        action_policy,
        "_is_reparse_entry",
        side_effect=simulated_reparse,
    ):
        with pytest.raises(ValueError, match="reparse point"):
            _create_destination_parent(
                state_directory,
                source,
                root,
                destination,
                os.stat(root, follow_symlinks=False),
                CorpusMutationGuard(
                    CorpusAccessPolicy.capture("normal", root),
                    disjoint_internal_paths_policy(tmp_path),
                ),
            )

    assert not (unsafe_parent / "nested").exists()
