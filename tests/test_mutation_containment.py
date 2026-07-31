"""No native mutation test may run before these containment contracts pass."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.mutation_containment as containment_module
from tests.mutation_containment import (
    ContainedMutationRoot,
    MutationContainmentError,
)


def _base(tmp_path: Path) -> Path:
    base = tmp_path / "containment-base"
    base.mkdir()
    return base


def test_unique_canonical_root_accepts_only_contained_absolute_paths(
    tmp_path: Path,
) -> None:
    base = _base(tmp_path)
    first = ContainedMutationRoot.create(base, watch_directories=(base,))
    assert first.root.parent == base.resolve(strict=True)

    source = first.root / "source.bin"
    source.write_bytes(b"fixture")
    destination_parent = first.root / "destination"
    destination_parent.mkdir()
    source_result, destination_result = first.validate_mutation(
        source,
        destination_parent / "target.bin",
    )
    assert source_result == source.resolve(strict=True)
    assert destination_result == destination_parent.resolve(strict=True) / "target.bin"
    _, direct_destination = first.validate_mutation(
        source,
        first.root / "direct-target.bin",
    )
    assert direct_destination == first.root / "direct-target.bin"
    first.assert_no_leaks()

    second = ContainedMutationRoot.create(base, watch_directories=(base,))
    assert first.root != second.root


@pytest.mark.parametrize(
    "unsafe",
    (
        "relative/path",
        "C:drive-relative",
        r"\\server\share\fixture",
    ),
)
def test_root_rejects_relative_ambiguous_and_unc_paths(
    unsafe: str,
) -> None:
    with pytest.raises(MutationContainmentError):
        ContainedMutationRoot.create(unsafe)


def test_mutation_rejects_traversal_and_every_outside_path(tmp_path: Path) -> None:
    base = _base(tmp_path)
    sandbox = ContainedMutationRoot.create(base, watch_directories=(base,))
    source = sandbox.root / "source.bin"
    source.write_bytes(b"fixture")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    outside_parent = tmp_path / "outside-parent"
    outside_parent.mkdir()

    with pytest.raises(MutationContainmentError, match="traversal"):
        sandbox.validate_mutation(source, sandbox.root / "nested" / ".." / "target")
    with pytest.raises(MutationContainmentError, match="outside"):
        sandbox.validate_mutation(outside)
    with pytest.raises(MutationContainmentError, match="outside"):
        sandbox.validate_mutation(source, outside_parent / "target.bin")
    with pytest.raises(MutationContainmentError, match="below the mutation root"):
        sandbox.validate_mutation(sandbox.root)


def test_mutation_rejects_reparse_or_symlink_chain_without_native_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base(tmp_path)
    sandbox = ContainedMutationRoot.create(base, watch_directories=(base,))
    source = sandbox.root / "source.bin"
    source.write_bytes(b"fixture")
    reparse_parent = sandbox.root / "simulated-reparse"
    reparse_parent.mkdir()
    real_is_reparse = containment_module._is_reparse

    def simulated_reparse(path: Path) -> bool:
        return path == reparse_parent or real_is_reparse(path)

    monkeypatch.setattr(containment_module, "_is_reparse", simulated_reparse)
    with pytest.raises(MutationContainmentError, match="reparse point"):
        sandbox.validate_mutation(source, reparse_parent / "target.bin")


def test_post_inspection_detects_entry_outside_unique_root(tmp_path: Path) -> None:
    base = _base(tmp_path)
    sandbox = ContainedMutationRoot.create(base, watch_directories=(base,))
    (base / "escaped.bin").write_bytes(b"simulated leak")

    with pytest.raises(MutationContainmentError, match="escaped"):
        sandbox.assert_no_leaks()


def test_native_wrappers_execute_and_record_only_identity_bound_effects(
    tmp_path: Path,
) -> None:
    base = _base(tmp_path)
    sandbox = ContainedMutationRoot.create(base, watch_directories=(base,))
    source = sandbox.root / "source.bin"
    destination = sandbox.root / "destination.bin"
    source.write_bytes(b"controlled fixture")
    before = source.stat(follow_symlinks=False)

    rename_receipt = sandbox.rename(source, destination)
    assert not source.exists()
    assert destination.read_bytes() == b"controlled fixture"
    assert rename_receipt.operation == "rename"
    assert rename_receipt.volume_id == int(before.st_dev)
    assert rename_receipt.file_id == int(before.st_ino)
    assert rename_receipt.destination_identity_confirmed is True

    unlink_receipt = sandbox.unlink(destination)
    assert not destination.exists()
    assert unlink_receipt.operation == "unlink"
    assert unlink_receipt.volume_id == rename_receipt.volume_id
    assert unlink_receipt.file_id == rename_receipt.file_id
    assert unlink_receipt.source_absent is True
    assert sandbox.mutation_receipts == (rename_receipt, unlink_receipt)

    conflicting_source = sandbox.root / "conflicting-source.bin"
    conflicting_target = sandbox.root / "conflicting-target.bin"
    conflicting_source.write_bytes(b"source")
    conflicting_target.write_bytes(b"target")
    with pytest.raises(MutationContainmentError, match="already exists"):
        sandbox.rename(conflicting_source, conflicting_target)
    assert conflicting_source.read_bytes() == b"source"
    assert conflicting_target.read_bytes() == b"target"
    sandbox.assert_no_leaks()
