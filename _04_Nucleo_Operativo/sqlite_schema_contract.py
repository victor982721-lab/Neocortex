"""Compatibility facade for the shared SQLite structural contract."""

from neocortex.sqlite_schema_contract import (
    SQLiteSchemaContract as SQLiteSchemaContract,
    SQLiteSchemaContractError as SQLiteSchemaContractError,
    capture_sqlite_schema_contract as capture_sqlite_schema_contract,
    read_application_schema_version as read_application_schema_version,
    read_metadata_schema_version as read_metadata_schema_version,
    schema_contract_from_builder as schema_contract_from_builder,
    validate_sqlite_schema_contract as validate_sqlite_schema_contract,
)


__all__ = [
    "SQLiteSchemaContract",
    "SQLiteSchemaContractError",
    "capture_sqlite_schema_contract",
    "read_application_schema_version",
    "read_metadata_schema_version",
    "schema_contract_from_builder",
    "validate_sqlite_schema_contract",
]
