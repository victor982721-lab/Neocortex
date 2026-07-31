"""Focused compatibility tests for the modular NeoCortex CLI."""


# region [01] Imports

from __future__ import annotations

import io
import runpy
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import Orquestador
from _04_Nucleo_Operativo.cli_app import main, run_framework
from _04_Nucleo_Operativo.cli_config import framework_config_from_args
from _04_Nucleo_Operativo.cli_parser import (
    ExplicitArgumentParser,
    build_parser,
    decimal_megabytes,
)
from _04_Nucleo_Operativo.cli_reporting import has_strict_route_errors

# endregion [01]


# region [02] Parser and configuration tests


class ModularParserTests(unittest.TestCase):
    def test_long_option_abbreviations_are_rejected(self) -> None:
        cases = (
            ["--rou", "pdf"],
            ["--all", "--global-cpu-s", "8"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    build_parser().parse_args(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_configuration_translation_preserves_cli_units(self) -> None:
        args = build_parser().parse_args(
            [
                "--route",
                "image",
                "--image-memory-budget-mb",
                "384",
                "--image-ocr-lang",
                "spa",
                "--MaxMB",
                "1.5",
            ]
        )
        Orquestador._validate_arguments(args)
        config = framework_config_from_args(args)

        self.assertEqual(config.route, "image")
        self.assertEqual(config.image_memory_budget_bytes, 384 * 1024 * 1024)
        self.assertEqual(config.image_document_ocr_lang, "spa")
        self.assertEqual(config.pdf_max_file_bytes, 1_500_000)


# endregion [02]


# region [03] Stable shim tests


class OrchestratorShimTests(unittest.TestCase):
    def test_historical_symbols_reexport_modular_implementations(self) -> None:
        self.assertIs(Orquestador._ExplicitArgumentParser, ExplicitArgumentParser)
        self.assertIs(Orquestador._decimal_megabytes, decimal_megabytes)
        self.assertIs(Orquestador._parser, build_parser)
        self.assertIs(Orquestador._run_framework, run_framework)
        self.assertIs(Orquestador._has_strict_route_errors, has_strict_route_errors)
        self.assertIs(Orquestador.main, main)
        self.assertEqual(Orquestador._parser().prog, "Neocortex")

    def test_process_shim_preserves_keyboard_interrupt_exit(self) -> None:
        stderr = io.StringIO()
        shim_path = Path(Orquestador.__file__)
        with (
            patch(
                "_04_Nucleo_Operativo.cli_app.main",
                side_effect=KeyboardInterrupt,
            ),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(shim_path), run_name="__main__")

        self.assertEqual(raised.exception.code, 130)
        self.assertIn("Ejecución cancelada por el usuario.", stderr.getvalue())


# endregion [03]


if __name__ == "__main__":
    unittest.main()
