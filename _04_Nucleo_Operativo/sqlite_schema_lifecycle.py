"""Compatibility facade for the shared versioned SQLite lifecycle."""
# region [00] Contexto del módulo
# Módulo: _04_Nucleo_Operativo/sqlite_schema_lifecycle.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from neocortex.sqlite_schema_lifecycle import (
    ConnectionFactory as ConnectionFactory,
    initialize_versioned_sqlite_schema as initialize_versioned_sqlite_schema,
    readonly_sqlite_uri as readonly_sqlite_uri,
)
# endregion [01]

# region [02] Implementación


__all__ = [
    "ConnectionFactory",
    "initialize_versioned_sqlite_schema",
    "readonly_sqlite_uri",
]
# endregion [02]
