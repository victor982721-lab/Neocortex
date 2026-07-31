from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _05_Interfaz.elevation import elevation_launch_spec


class UiElevationTests(unittest.TestCase):
    def test_source_launch_reopens_the_stable_orchestrator_in_ui_mode(self) -> None:
        root = Path(r"C:\Users\Fixture Root")
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.object(sys, "executable", r"C:\Python\pythonw.exe"),
        ):
            spec = elevation_launch_spec(root)

        self.assertEqual(spec.executable, Path(r"C:\Python\pythonw.exe"))
        self.assertIn("-m neocortex", spec.parameters)
        self.assertIn("--ui", spec.parameters)
        self.assertIn(str(root), spec.parameters)


if __name__ == "__main__":
    unittest.main()
