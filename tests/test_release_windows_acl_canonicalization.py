"""Pure regressions for canonical Windows security-descriptor comparison."""

from __future__ import annotations

import pytest

from tools.release_windows_ntfs_native import (
    _SE_DACL_AUTO_INHERITED,
    _canonical_security_descriptor,
)
from tools.release_windows_receipts import ReleaseTransitionError


def _descriptor(control: int, *, size: int = 64) -> bytes:
    payload = bytearray(range(size))
    payload[2:4] = control.to_bytes(2, "little")
    return bytes(payload)


def test_security_descriptor_canonicalization_clears_only_auto_inherited() -> None:
    control = 0x9504
    descriptor = _descriptor(control)

    canonical = _canonical_security_descriptor(descriptor)
    expected = bytearray(descriptor)
    expected[2:4] = (control & ~_SE_DACL_AUTO_INHERITED).to_bytes(2, "little")
    byte_pairs = zip(descriptor, canonical, strict=True)
    changed_indices = [
        index
        for index, (before, after) in enumerate(byte_pairs)
        if before != after
    ]

    assert canonical == bytes(expected)
    assert canonical[:2] == descriptor[:2]
    assert canonical[4:] == descriptor[4:]
    assert changed_indices == [3]
    assert _canonical_security_descriptor(canonical) == canonical


def test_security_descriptor_canonicalization_rejects_short_header() -> None:
    with pytest.raises(ReleaseTransitionError, match="shorter than"):
        _canonical_security_descriptor(bytes(19))
