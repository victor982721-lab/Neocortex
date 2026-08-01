"""Compatibility facade for the shared SQLite structural contract."""
# region [00] Contexto del módulo
# Módulo: _04_Nucleo_Operativo/sqlite_schema_contract.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from neocortex.sqlite_schema_contract import (
    SQLiteSchemaContract as SQLiteSchemaContract,
    SQLiteSchemaContractError as SQLiteSchemaContractError,
    capture_sqlite_schema_contract as capture_sqlite_schema_contract,
    read_application_schema_version as read_application_schema_version,
    read_metadata_schema_version as read_metadata_schema_version,
    schema_contract_from_builder as schema_contract_from_builder,
    validate_sqlite_schema_contract as validate_sqlite_schema_contract,
)
# endregion [01]

# region [02] Implementación


__all__ = [
    "SQLiteSchemaContract",
    "SQLiteSchemaContractError",
    "capture_sqlite_schema_contract",
    "read_application_schema_version",
    "read_metadata_schema_version",
    "schema_contract_from_builder",
    "validate_sqlite_schema_contract",
]
# endregion [02]
