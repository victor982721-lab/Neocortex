from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from _04_Nucleo_Operativo.zip_safety import (
    ZipStructureError,
    inspect_zip_structure,
)


class ZipSafetyTests(unittest.TestCase):
    def test_reads_normal_archive_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("one.txt", "one")
                archive.writestr("two.txt", "two")

            structure = inspect_zip_structure(path, max_members=4)

            self.assertEqual(structure.members, 2)
            self.assertGreater(structure.central_directory_bytes, 0)
            self.assertFalse(structure.zip64)

    def test_rejects_member_count_before_loading_central_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "many.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("one.txt", "one")
                archive.writestr("two.txt", "two")

            with self.assertRaisesRegex(ZipStructureError, "members"):
                inspect_zip_structure(path, max_members=1)

    def test_rejects_declared_oversized_central_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "oversized.zip"
            eocd = struct.pack(
                "<4s4H2IH",
                b"PK\x05\x06",
                0,
                0,
                0,
                0,
                1024,
                0,
                0,
            )
            path.write_bytes(eocd)

            with self.assertRaisesRegex(ZipStructureError, "safety limit"):
                inspect_zip_structure(
                    path,
                    max_members=10,
                    max_central_directory_bytes=128,
                )


if __name__ == "__main__":
    unittest.main()
