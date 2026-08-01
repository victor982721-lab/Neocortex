"""Exceptions for :mod:`_02_Deduplicacion`."""
# region [00] Contexto del módulo
# Módulo: _02_Deduplicacion/errors.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]



# region [01] Implementación
class DedupError(Exception):
    """Base package exception."""


class MissingDependencyError(DedupError):
    """A required optimized native dependency is unavailable."""


class FileChangedError(DedupError):
    """A file changed while it was being fingerprinted or compared."""


class InventoryError(DedupError):
    """An inventory operation could not be completed safely."""
# endregion [02]
