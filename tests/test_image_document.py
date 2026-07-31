from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from _04_Nucleo_Operativo.bounded_subprocess import SubprocessOutputLimitError
from _04_Nucleo_Operativo.image_document import (
    DOCUMENT_OCR_TEXT_MAX_UTF8_BYTES,
    DocumentVerifierRuntime,
    verify_document_text,
)


# region [01] Bounded OCR fixtures


RUNTIME = DocumentVerifierRuntime(
    enabled=True,
    lang="spa+eng",
    timeout_seconds=12.0,
    tesseract_cmd="tesseract-test",
    tessdata_dir=None,
    signature="test-document-verifier",
    provenance="test-tesseract",
)


def _tsv_result(words: list[str]) -> subprocess.CompletedProcess[bytes]:
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    rows = [header]
    for index, word in enumerate(words, start=1):
        rows.append(
            f"5\t1\t1\t1\t{((index - 1) // 8) + 1}\t{index}\t1\t1\t20\t10\t92.5\t{word}"
        )
    return subprocess.CompletedProcess(
        args=["tesseract-test"],
        returncode=0,
        stdout=("\n".join(rows) + "\n").encode("utf-8"),
        stderr=b"",
    )


def _image(root: Path) -> Path:
    path = root / "subestación_ñ.png"
    with Image.new("RGB", (640, 480), "white") as image:
        image.save(path)
    return path


# endregion [01]


# region [02] Unicode and retention limit


class ImageDocumentTextTests(unittest.TestCase):
    def test_preserves_unicode_words_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = _image(Path(temporary))
            words = ["Subestación", "eléctrica", "número", "tres"]

            with patch(
                "_04_Nucleo_Operativo.image_document.run_bounded_capture",
                return_value=_tsv_result(words),
            ):
                evidence = verify_document_text(path, RUNTIME)

            self.assertTrue(evidence.available)
            self.assertEqual(evidence.recognized_text, " ".join(words))
            self.assertFalse(evidence.recognized_text_truncated)
            self.assertEqual(evidence.word_count, len(words))
            self.assertEqual(
                evidence.character_count,
                sum(len(word) for word in words),
            )

    def test_retained_text_is_a_whole_word_bounded_utf8_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = _image(Path(temporary))
            word = "á" * 32
            words = [word] * 400

            with patch(
                "_04_Nucleo_Operativo.image_document.run_bounded_capture",
                return_value=_tsv_result(words),
            ):
                evidence = verify_document_text(path, RUNTIME)

            retained = evidence.recognized_text.encode("utf-8")
            self.assertLessEqual(
                len(retained),
                DOCUMENT_OCR_TEXT_MAX_UTF8_BYTES,
            )
            self.assertTrue(evidence.recognized_text_truncated)
            self.assertEqual(evidence.word_count, len(words))
            self.assertTrue(evidence.recognized_text)
            self.assertTrue(
                all(value == word for value in evidence.recognized_text.split(" "))
            )

    def test_output_overflow_becomes_bounded_unavailable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = _image(Path(temporary))
            with patch(
                "_04_Nucleo_Operativo.image_document.run_bounded_capture",
                side_effect=SubprocessOutputLimitError("stdout", 1024),
            ):
                evidence = verify_document_text(path, RUNTIME)

            self.assertTrue(evidence.attempted)
            self.assertFalse(evidence.available)
            self.assertEqual(evidence.error_type, "SubprocessOutputLimitError")
            self.assertIn("1024 bytes", evidence.error_message or "")


# endregion [02]


if __name__ == "__main__":
    unittest.main()
