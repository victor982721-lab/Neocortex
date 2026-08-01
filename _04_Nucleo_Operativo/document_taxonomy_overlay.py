"""Bounded loading and validation for user-controlled taxonomy overlays."""
# region [00] Contexto del módulo
# Módulo: _04_Nucleo_Operativo/document_taxonomy_overlay.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping

import xxhash

from .document_taxonomy_models import (
    AuthoritySpec,
    ClientSpec,
    OrganizationSpec,
    ProjectSpec,
    TechnicalTaxonomy,
)
from .document_taxonomy_vocabulary import (
    BUILTIN_TAXONOMY_VERSION,
    builtin_taxonomy,
)
# endregion [01]

# region [02] Implementación

MAX_TAXONOMY_BYTES = 1_048_576
MAX_TAXONOMY_TABLES_PER_SECTION = 256
MAX_TAXONOMY_TEXT_CHARS = 512
MAX_TAXONOMY_SEQUENCE_ITEMS = 128
MAX_TAXONOMY_PATTERNS = 64
MAX_TAXONOMY_PATTERN_CHARS = 512


def load_taxonomy(path: Path | None = None) -> TechnicalTaxonomy:
    """Load optional TOML additions without replacing the sector defaults."""

    taxonomy = builtin_taxonomy()
    if path is None:
        return taxonomy
    raw = _read_taxonomy_bytes(path)
    data = tomllib.loads(raw.decode("utf-8"))
    authorities = list(taxonomy.authorities)
    organizations = list(taxonomy.organizations)
    clients = list(taxonomy.clients)
    projects = list(taxonomy.projects)
    for item in _table_sequence(data.get("authorities"), "authorities"):
        code = _required_text(item, "code").upper()
        aliases = _text_sequence(item.get("aliases", ()), "aliases")
        patterns = _text_sequence(
            item.get("identifier_patterns", ()),
            "identifier_patterns",
            max_items=MAX_TAXONOMY_PATTERNS,
            max_chars=MAX_TAXONOMY_PATTERN_CHARS,
        )
        for pattern in patterns:
            _validate_identifier_pattern(pattern, code)
        authorities.append(AuthoritySpec(code, aliases or (code,), patterns))
    for item in _table_sequence(data.get("organizations"), "organizations"):
        name = _required_text(item, "name")
        aliases = _text_sequence(item.get("aliases", ()), "aliases")
        organizations.append(OrganizationSpec(name, aliases or (name,)))
    for item in _table_sequence(data.get("clients"), "clients"):
        name = _required_text(item, "name")
        aliases = _text_sequence(item.get("aliases", ()), "aliases")
        clients.append(ClientSpec(name, aliases or (name,)))
    for item in _table_sequence(data.get("projects"), "projects"):
        name = _required_text(item, "name")
        client = _required_text(item, "client")
        aliases = _text_sequence(item.get("aliases", ()), "aliases")
        projects.append(ProjectSpec(name, client, aliases or (name,)))
    digest = xxhash.xxh3_64_hexdigest(raw)
    return TechnicalTaxonomy(
        signature=f"{BUILTIN_TAXONOMY_VERSION}|custom-xxh3-64={digest}",
        authorities=_deduplicate_authorities(authorities),
        organizations=_deduplicate_organizations(organizations),
        clients=_deduplicate_clients(clients),
        projects=_deduplicate_projects(projects),
    )


def _read_taxonomy_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_TAXONOMY_BYTES + 1)
    if len(raw) > MAX_TAXONOMY_BYTES:
        raise ValueError(f"taxonomy file exceeds the {MAX_TAXONOMY_BYTES}-byte limit")
    return raw


def _table_sequence(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"taxonomy {name} must be an array of tables")
    if len(value) > MAX_TAXONOMY_TABLES_PER_SECTION:
        raise ValueError(
            f"taxonomy {name} exceeds the {MAX_TAXONOMY_TABLES_PER_SECTION}-table limit"
        )
    return tuple(value)


def _required_text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"taxonomy field {name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > MAX_TAXONOMY_TEXT_CHARS:
        raise ValueError(
            f"taxonomy field {name} exceeds the {MAX_TAXONOMY_TEXT_CHARS}-character limit"
        )
    return normalized


def _text_sequence(
    value: Any,
    name: str,
    *,
    max_items: int = MAX_TAXONOMY_SEQUENCE_ITEMS,
    max_chars: int = MAX_TAXONOMY_TEXT_CHARS,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"taxonomy field {name} must be a text array")
    if len(value) > max_items:
        raise ValueError(f"taxonomy field {name} exceeds the {max_items}-item limit")
    normalized = tuple(dict.fromkeys(item.strip() for item in value))
    if any(len(item) > max_chars for item in normalized):
        raise ValueError(
            f"taxonomy field {name} contains text longer than {max_chars} characters"
        )
    return normalized


def _validate_identifier_pattern(pattern: str, authority_code: str) -> None:
    """Accept bounded regular expressions without high-risk repeated subexpressions."""

    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(
            f"invalid identifier pattern for authority {authority_code}: {exc}"
        ) from exc
    unsafe_reason = _unsafe_custom_regex_reason(pattern)
    if unsafe_reason is not None:
        raise ValueError(
            f"unsafe identifier pattern for authority {authority_code}: {unsafe_reason}"
        )


def _unsafe_custom_regex_reason(pattern: str) -> str | None:
    """Conservatively reject constructs commonly responsible for regex backtracking."""

    # Each frame records whether its group contains repetition or alternation.
    frames: list[list[bool]] = [[False, False]]
    in_character_class = False
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            if index + 1 < len(pattern) and pattern[index + 1] in "123456789":
                return "backreferences are not allowed"
            index += 2
            continue
        if character == "[" and not in_character_class:
            in_character_class = True
            index += 1
            continue
        if character == "]" and in_character_class:
            in_character_class = False
            index += 1
            continue
        if in_character_class:
            index += 1
            continue
        if character == "(":
            if pattern.startswith("(?:", index):
                index += 3
            elif pattern.startswith("(?", index):
                return (
                    "lookarounds, named groups, and inline extensions are not allowed"
                )
            else:
                index += 1
            frames.append([False, False])
            continue
        if character == "|":
            frames[-1][1] = True
            index += 1
            continue
        if character == ")" and len(frames) > 1:
            has_repetition, has_alternation = frames.pop()
            repeated, next_index = _regex_quantifier_end(pattern, index + 1)
            if repeated and (has_repetition or has_alternation):
                return (
                    "a repeated group cannot itself contain repetition or alternation"
                )
            frames[-1][0] = frames[-1][0] or has_repetition or repeated
            frames[-1][1] = frames[-1][1] or has_alternation
            index = next_index if repeated else index + 1
            continue
        repeated, next_index = _regex_quantifier_end(pattern, index)
        if repeated:
            frames[-1][0] = True
            index = next_index
            continue
        index += 1
    return None


def _regex_quantifier_end(pattern: str, index: int) -> tuple[bool, int]:
    if index >= len(pattern):
        return False, index
    if pattern[index] in "*+?":
        end = index + 1
        if end < len(pattern) and pattern[end] in "+?":
            end += 1
        return True, end
    if pattern[index] != "{":
        return False, index
    match = re.match(r"\{\d+(?:,\d*)?\}[+?]?", pattern[index:])
    if match is None:
        return False, index
    return True, index + len(match.group(0))


def _deduplicate_authorities(
    values: Iterable[AuthoritySpec],
) -> tuple[AuthoritySpec, ...]:
    merged: dict[str, AuthoritySpec] = {}
    for value in values:
        prior = merged.get(value.code.casefold())
        if prior is None:
            merged[value.code.casefold()] = value
            continue
        merged[value.code.casefold()] = AuthoritySpec(
            prior.code,
            tuple(dict.fromkeys((*prior.aliases, *value.aliases))),
            tuple(
                dict.fromkeys((*prior.identifier_patterns, *value.identifier_patterns))
            ),
        )
    return tuple(merged.values())


def _deduplicate_organizations(
    values: Iterable[OrganizationSpec],
) -> tuple[OrganizationSpec, ...]:
    merged: dict[str, OrganizationSpec] = {}
    for value in values:
        prior = merged.get(value.name.casefold())
        if prior is None:
            merged[value.name.casefold()] = value
            continue
        merged[value.name.casefold()] = OrganizationSpec(
            prior.name,
            tuple(dict.fromkeys((*prior.aliases, *value.aliases))),
        )
    return tuple(merged.values())


def _deduplicate_clients(values: Iterable[ClientSpec]) -> tuple[ClientSpec, ...]:
    merged: dict[str, ClientSpec] = {}
    for value in values:
        prior = merged.get(value.name.casefold())
        if prior is None:
            merged[value.name.casefold()] = value
            continue
        merged[value.name.casefold()] = ClientSpec(
            prior.name,
            tuple(dict.fromkeys((*prior.aliases, *value.aliases))),
        )
    return tuple(merged.values())


def _deduplicate_projects(values: Iterable[ProjectSpec]) -> tuple[ProjectSpec, ...]:
    merged: dict[str, ProjectSpec] = {}
    for value in values:
        key = value.name.casefold()
        prior = merged.get(key)
        if prior is None:
            merged[key] = value
            continue
        if prior.client.casefold() != value.client.casefold():
            raise ValueError(
                f"taxonomy project {value.name} has conflicting clients: "
                f"{prior.client} and {value.client}"
            )
        merged[key] = ProjectSpec(
            prior.name,
            prior.client,
            tuple(dict.fromkeys((*prior.aliases, *value.aliases))),
        )
    return tuple(merged.values())
# endregion [02]
