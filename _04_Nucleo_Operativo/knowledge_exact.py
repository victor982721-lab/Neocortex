"""Bounded, read-only exact lookup over published Knowledge owner state.

The adapter is deliberately a leaf: it returns neutral evidence records and
does not import the search/fusion layer.  Catalog generations are truly pinned
to :class:`KnowledgeSnapshot` publication heads.  Inventory and code are not
generational owners; their reads are constrained to snapshot observations and
reported as partial rather than being presented as an as-of snapshot.

No function in this module initializes, migrates, attaches or writes a
database.  Missing and incompatible state is reported without creating files.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from .code_detection import LANGUAGE_EXTENSIONS
from .code_schema import connect_code_state
from .document_catalog import connect_document_catalog
from .file_identity import FileIdentity, FileIdentityError
from .knowledge_contracts import (
    EvidenceMethod,
    EvidenceRef,
    KnowledgeSnapshot,
    OwnerAvailability,
    OwnerSnapshot,
    PhysicalIdentityRef,
    ResourceRef,
    RevisionRef,
    RevisionState,
    SnapshotConsistency,
)
from .knowledge_planner import KnowledgePlan
from .knowledge_snapshot import KnowledgeStatePaths
from .semantic_models import canonical_json, fingerprint_text
from .sqlite_paths import readonly_sqlite_uri

# region [01] Public immutable contracts and bounds


MAX_EXACT_TERMS = 64
MAX_EXACT_VALUE_CHARS = 4_096
MAX_EXACT_RESULTS = 1_000
MAX_EXACT_OBSERVED_ROWS = 10_000
MAX_EXACT_SQLITE_STEPS = 100_000_000
DEFAULT_EXACT_SQLITE_STEPS = 5_000_000
SQLITE_PROGRESS_INTERVAL = 1_000
HEAD_BATCH_SIZE = 200
EXACT_OWNER_NAMES = ("inventory", "code", "catalog")


def _duration_ns(clock_ns: Callable[[], int], started_ns: int) -> int:
    finished_ns = clock_ns()
    if (
        isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
        or finished_ns < started_ns
    ):
        raise RuntimeError("exact monotonic clock moved backwards or was invalid")
    return finished_ns - started_ns


class ExactLookupKind(StrEnum):
    PATH = "path"
    NAME = "name"
    IDENTIFIER = "identifier"
    SERIAL = "serial"
    HASH = "hash"
    SYMBOL = "symbol"


class ExactLookupStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ExactLookupTerm:
    kind: ExactLookupKind
    value: str
    algorithm: str | None = None
    surface: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExactLookupKind):
            raise ValueError("exact lookup kind must be an ExactLookupKind")
        value = self.value.strip()
        if not value:
            raise ValueError("exact lookup value cannot be blank")
        if len(value) > MAX_EXACT_VALUE_CHARS:
            raise ValueError(
                f"exact lookup value cannot exceed {MAX_EXACT_VALUE_CHARS} characters"
            )
        algorithm = None if self.algorithm is None else self.algorithm.strip()
        if self.algorithm is not None and not algorithm:
            raise ValueError("exact lookup algorithm cannot be blank")
        if algorithm is not None and len(algorithm) > 128:
            raise ValueError("exact lookup algorithm cannot exceed 128 characters")
        if self.kind is not ExactLookupKind.HASH and algorithm is not None:
            raise ValueError("only hash lookup terms may name an algorithm")
        if self.kind is ExactLookupKind.HASH:
            if not 16 <= len(value) <= 64 or len(value) % 2:
                raise ValueError(
                    "exact hash values must contain 16..64 even hex digits"
                )
            if any(character not in "0123456789abcdefABCDEF" for character in value):
                raise ValueError("exact hash values must be hexadecimal")
            value = value.casefold()
            if algorithm is not None:
                algorithm = algorithm.casefold()
        if self.kind is ExactLookupKind.NAME and any(
            separator in value for separator in ("/", "\\")
        ):
            raise ValueError("exact file names cannot contain path separators")
        surface = None if self.surface is None else self.surface.strip()
        if self.surface is not None and not surface:
            raise ValueError("exact lookup surface cannot be blank")
        if surface is not None and len(surface) > MAX_EXACT_VALUE_CHARS:
            raise ValueError(
                f"exact lookup surface cannot exceed {MAX_EXACT_VALUE_CHARS} characters"
            )
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "algorithm", algorithm)
        object.__setattr__(self, "surface", surface)

    @property
    def term_id(self) -> str:
        payload: dict[str, object] = {"kind": self.kind.value, "value": self.value}
        if self.algorithm is not None:
            payload["algorithm"] = self.algorithm
        identity = fingerprint_text(canonical_json(payload))
        return f"exact-term-v1:{self.kind.value}:{identity.xxh3_128}"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "term_id": self.term_id,
            "kind": self.kind.value,
            "value": self.value,
        }
        if self.algorithm is not None:
            payload["algorithm"] = self.algorithm
        if self.surface is not None and self.surface != self.value:
            payload["surface"] = self.surface
        return payload


@dataclass(frozen=True, slots=True)
class ExactLookupRequest:
    terms: tuple[ExactLookupTerm, ...]
    limit: int = 100
    max_observed_rows: int = 4_096
    max_sqlite_steps: int = DEFAULT_EXACT_SQLITE_STEPS
    owner_scope: tuple[str, ...] = EXACT_OWNER_NAMES
    source_kinds: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("exact lookup requires at least one term")
        if len(self.terms) > MAX_EXACT_TERMS:
            raise ValueError(f"exact lookup accepts at most {MAX_EXACT_TERMS} terms")
        if len({term.term_id for term in self.terms}) != len(self.terms):
            raise ValueError("exact lookup terms must be unique")
        if not isinstance(self.owner_scope, tuple) or not self.owner_scope:
            raise ValueError("exact lookup owner_scope must be a non-empty tuple")
        if len(set(self.owner_scope)) != len(self.owner_scope):
            raise ValueError("exact lookup owner_scope must be unique")
        if any(owner not in EXACT_OWNER_NAMES for owner in self.owner_scope):
            raise ValueError("exact lookup owner_scope contains an unknown owner")
        for label, values in (
            ("source_kinds", self.source_kinds),
            ("formats", self.formats),
        ):
            if not isinstance(values, tuple):
                raise ValueError(f"exact lookup {label} must be a tuple")
            if len(values) > MAX_EXACT_TERMS:
                raise ValueError(f"exact lookup {label} accepts at most 64 values")
            normalized: list[str] = []
            for raw_value in values:
                if not isinstance(raw_value, str):
                    raise ValueError(f"exact lookup {label} values must be strings")
                value = raw_value.strip().casefold()
                if label == "formats":
                    value = value.removeprefix(".")
                if not value or len(value) > 128:
                    raise ValueError(
                        f"exact lookup {label} values must contain 1..128 characters"
                    )
                if value not in normalized:
                    normalized.append(value)
            object.__setattr__(self, label, tuple(normalized))
        if isinstance(self.limit, bool) or not 1 <= self.limit <= MAX_EXACT_RESULTS:
            raise ValueError("exact lookup limit must be between 1 and 1000")
        if (
            isinstance(self.max_observed_rows, bool)
            or not self.limit <= self.max_observed_rows <= MAX_EXACT_OBSERVED_ROWS
        ):
            raise ValueError("exact max_observed_rows must be between limit and 10000")
        if (
            isinstance(self.max_sqlite_steps, bool)
            or not SQLITE_PROGRESS_INTERVAL
            <= self.max_sqlite_steps
            <= MAX_EXACT_SQLITE_STEPS
        ):
            raise ValueError(
                "exact max_sqlite_steps must be between 1000 and 100000000"
            )


@dataclass(frozen=True, slots=True)
class ExactEvidenceMatch:
    ranking_name: str
    term: ExactLookupTerm
    resource: ResourceRef
    revision: RevisionRef
    evidence: EvidenceRef
    source_rank: int
    reason: str
    confidence: float | None = None
    model_signature: str | None = None
    generation: int | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.ranking_name.strip() or not self.reason.strip():
            raise ValueError("exact match ranking and reason cannot be blank")
        if isinstance(self.source_rank, bool) or self.source_rank < 1:
            raise ValueError("exact match source_rank must be positive")
        if self.revision.resource_id != self.resource.resource_id:
            raise ValueError("exact match revision belongs to another resource")
        if (
            self.evidence.resource_id != self.resource.resource_id
            or self.evidence.revision_id != self.revision.revision_id
        ):
            raise ValueError("exact match evidence belongs to another revision")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("exact match confidence must be between zero and one")
        if self.generation is not None and self.generation < 0:
            raise ValueError("exact match generation cannot be negative")
        for warning in self.warnings:
            if not warning.strip():
                raise ValueError("exact match warnings cannot be blank")

    @property
    def match_id(self) -> str:
        """Return a query-independent identity for this evidence match."""

        payload = {
            "resource_id": self.resource.resource_id,
            "revision_id": self.revision.revision_id,
            "evidence_id": self.evidence.evidence_id,
        }
        identity = fingerprint_text(canonical_json(payload))
        return f"exact-match-v1:{self.resource.owner}:{identity.xxh3_128}"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "match_id": self.match_id,
            "ranking_name": self.ranking_name,
            "term": self.term.to_dict(),
            "resource": self.resource.to_dict(),
            "revision": self.revision.to_dict(),
            "evidence": self.evidence.to_dict(),
            "source_rank": self.source_rank,
            "reason": self.reason,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.model_signature is not None:
            payload["model_signature"] = self.model_signature
        if self.generation is not None:
            payload["generation"] = self.generation
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload


@dataclass(frozen=True, slots=True)
class ExactOwnerReport:
    name: str
    owner: str
    term: ExactLookupTerm
    status: ExactLookupStatus
    executed: bool
    available: bool
    returned: int
    rows_observed: int = 0
    sqlite_steps: int = 0
    truncated: bool = False
    omitted_matches: int = 0
    reason: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.owner.strip():
            raise ValueError("exact report name and owner cannot be blank")
        if (
            self.returned < 0
            or self.rows_observed < 0
            or self.sqlite_steps < 0
            or self.omitted_matches < 0
        ):
            raise ValueError("exact report counters cannot be negative")
        if self.returned > self.rows_observed:
            raise ValueError("exact report cannot return more rows than it observed")
        if self.status is ExactLookupStatus.COMPLETE and (
            not self.executed or not self.available or self.truncated
        ):
            raise ValueError("complete exact report must be executed and available")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("exact report reason cannot be blank")

    @property
    def complete(self) -> bool:
        return self.status is ExactLookupStatus.COMPLETE

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "owner": self.owner,
            "term": self.term.to_dict(),
            "status": self.status.value,
            "executed": self.executed,
            "available": self.available,
            "complete": self.complete,
            "returned": self.returned,
            "rows_observed": self.rows_observed,
            "sqlite_steps": self.sqlite_steps,
            "truncated": self.truncated,
            "omitted_matches": self.omitted_matches,
            "omitted_match_count_semantics": "materialized_valid_lower_bound",
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload


@dataclass(frozen=True, slots=True)
class ExactOwnerTiming:
    """One owner-batch duration covering one or more exact term rankings."""

    owner: str
    ranking_names: tuple[str, ...]
    duration_ns: int
    executed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.owner, str) or not self.owner.strip():
            raise ValueError("exact owner timing owner cannot be blank")
        if not isinstance(self.ranking_names, tuple) or not self.ranking_names:
            raise ValueError("exact owner timing requires ranking names")
        if any(
            not isinstance(ranking_name, str) or not ranking_name.strip()
            for ranking_name in self.ranking_names
        ):
            raise ValueError("exact owner timing requires ranking names")
        if len(set(self.ranking_names)) != len(self.ranking_names):
            raise ValueError("exact owner timing ranking names must be unique")
        if (
            isinstance(self.duration_ns, bool)
            or not isinstance(self.duration_ns, int)
            or self.duration_ns < 0
        ):
            raise ValueError("exact owner timing duration_ns cannot be negative")
        if not isinstance(self.executed, bool):
            raise ValueError("exact owner timing executed must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "ranking_names": list(self.ranking_names),
            "duration_ns": self.duration_ns,
            "executed": self.executed,
            "measurement_scope": "owner_batch",
        }


@dataclass(frozen=True, slots=True)
class ExactLookupResult:
    snapshot_id: str
    matches: tuple[ExactEvidenceMatch, ...]
    reports: tuple[ExactOwnerReport, ...]
    complete: bool
    truncated: bool
    omitted_matches: int
    rows_observed: int
    sqlite_steps: int
    warnings: tuple[str, ...] = ()
    owner_timings: tuple[ExactOwnerTiming, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "kind": "knowledge_exact_lookup",
            "snapshot_id": self.snapshot_id,
            "matches": [match.to_dict() for match in self.matches],
            "reports": [report.to_dict() for report in self.reports],
            "complete": self.complete,
            "truncated": self.truncated,
            "omitted_matches": self.omitted_matches,
            "omitted_match_count_semantics": "materialized_valid_lower_bound",
            "rows_observed": self.rows_observed,
            "sqlite_steps": self.sqlite_steps,
        }
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.owner_timings:
            payload["owner_timings"] = [
                timing.to_dict() for timing in self.owner_timings
            ]
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


# endregion [01]


# region [02] Plan term typing and query controls


_SERIAL_TERM = re.compile(
    r"^(?:"
    r"SN(?:[\s:#-]+(?P<sn_separated>[A-Z0-9][A-Z0-9._/-]*)|"
    r"(?P<sn_compact>[0-9][A-Z0-9._/-]*))|"
    r"S/N(?:[\s:#-]+(?P<slash_separated>[A-Z0-9][A-Z0-9._/-]*)|"
    r"(?P<slash_compact>[0-9][A-Z0-9._/-]*))|"
    r"SERIAL[\s:#-]+(?P<serial>[A-Z0-9][A-Z0-9._/-]*)"
    r")$",
    re.IGNORECASE,
)
_HEX_TERM = re.compile(r"^[0-9a-fA-F]{16,64}$")
_QUALIFIED_SYMBOL = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)+$"
)
_BARE_CODE_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_NUMBERED_UNDERSCORE_IDENTIFIER = re.compile(
    r"^[A-Za-z][A-Za-z0-9.]*_[0-9][A-Za-z0-9_.-]*$"
)
_CODE_EXTENSIONS = frozenset(
    extension.removeprefix(".").casefold() for extension in LANGUAGE_EXTENSIONS
)
_CODE_LANGUAGES = frozenset(
    language.casefold() for language in LANGUAGE_EXTENSIONS.values()
)
_CODE_EXACT_FORMATS = frozenset(_CODE_EXTENSIONS | _CODE_LANGUAGES)
_CATALOG_EXACT_SOURCE_KINDS = frozenset(
    {"audio", "docx", "office", "pdf", "pptx", "xlsx"}
)
_CATALOG_EXACT_FORMATS = frozenset(
    {
        "aac",
        "doc",
        "docx",
        "flac",
        "m4a",
        "mp3",
        "odp",
        "ods",
        "odt",
        "ogg",
        "opus",
        "pdf",
        "ppt",
        "pptx",
        "wav",
        "wma",
        "xls",
        "xlsm",
        "xlsx",
    }
)
_IMAGE_EXACT_FORMATS = frozenset(
    {"avif", "bmp", "gif", "heic", "heif", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
)
_AUDIO_EXACT_FORMATS = frozenset(
    {"aac", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"}
)
_OFFICE_EXACT_FORMATS = frozenset(
    {"doc", "odt", "ods", "odp", "xls", "xlsx", "xlsm", "ppt", "pptx"}
)
_SOURCE_EXTENSION_ALIASES: Mapping[str, frozenset[str]] = {
    "audio": _AUDIO_EXACT_FORMATS,
    "code": _CODE_EXTENSIONS,
    "docx": frozenset({"docx"}),
    "image": _IMAGE_EXACT_FORMATS,
    "image_ocr": _IMAGE_EXACT_FORMATS,
    "office": _OFFICE_EXACT_FORMATS,
    "odt": frozenset({"doc", "odt"}),
    "pdf": frozenset({"pdf"}),
    "pptx": frozenset({"odp", "ppt", "pptx"}),
    "xlsx": frozenset({"ods", "xls", "xlsm", "xlsx"}),
}
_CATALOG_SOURCE_ALIASES: Mapping[str, frozenset[str]] = {
    "audio": frozenset({"audio"}),
    "docx": frozenset({"docx"}),
    "office": frozenset({"odt", "pptx", "xlsx"}),
    "odt": frozenset({"odt"}),
    "pdf": frozenset({"pdf"}),
    "pptx": frozenset({"pptx"}),
    "xlsx": frozenset({"xlsx"}),
}
_FILE_EXTENSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_-]{0,15}$")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_EXPLICIT_CODE_WORDS = frozenset(
    {
        "class",
        "clase",
        "code",
        "codigo",
        "código",
        "definicion",
        "definición",
        "definition",
        "function",
        "funcion",
        "función",
        "method",
        "metodo",
        "método",
        "module",
        "modulo",
        "módulo",
        "symbol",
        "symbols",
        "simbolo",
        "símbolo",
        "simbolos",
        "símbolos",
    }
)


def _serial_body(value: str) -> str | None:
    candidate = value.strip()
    for _ in range(2):
        match = _SERIAL_TERM.fullmatch(candidate)
        if match is None:
            return None
        body = next(group for group in match.groups() if group is not None)
        nested = _SERIAL_TERM.fullmatch(body)
        if nested is None:
            return body
        candidate = body
    return None


def _canonical_serial(value: str) -> str:
    body = _serial_body(value)
    return f"SN-{body}" if body is not None else value.strip()


def _looks_like_path(value: str) -> bool:
    if _URI_SCHEME.match(value) or _serial_body(value) is not None:
        return False
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", value)
        or value.startswith(("/", "\\", "./", ".\\", "../", "..\\"))
        or "/" in value
        or "\\" in value
    )


def _looks_like_file_name(value: str) -> bool:
    if (
        not value
        or len(value) > 255
        or any(separator in value for separator in ("/", "\\", "\r", "\n"))
    ):
        return False
    head, separator, extension = value.rpartition(".")
    return bool(
        head
        and not head.endswith((" ", "."))
        and separator
        and _FILE_EXTENSION.fullmatch(extension)
    )


def _is_bare_code_symbol(value: str) -> bool:
    if _BARE_CODE_TOKEN.fullmatch(value) is None:
        return False
    if _NUMBERED_UNDERSCORE_IDENTIFIER.fullmatch(value) is not None:
        return False
    core = value.strip("_")
    if not core:
        return False
    snake_case = "_" in value and any(character.isalpha() for character in core)
    camel_case = any(character.isupper() for character in value[1:]) and any(
        character.islower() for character in value
    )
    return snake_case or camel_case


def _masked_exact_term_text(plan: KnowledgePlan) -> str:
    characters = list(plan.normalized_query)
    for term in sorted(plan.exact_terms, key=len, reverse=True):
        start = 0
        while True:
            index = plan.normalized_query.find(term, start)
            if index < 0:
                break
            characters[index : index + len(term)] = " " * len(term)
            start = index + len(term)
    return "".join(characters)


def _plan_demonstrates_code(plan: KnowledgePlan) -> bool:
    return "structural" in plan.intents or any(
        step.channel == "structural_code" for step in plan.steps
    )


def _plan_has_explicit_code_evidence(plan: KnowledgePlan) -> bool:
    sources = {value.casefold() for value in plan.source_kinds}
    formats = {value.casefold().removeprefix(".") for value in plan.formats}
    if "code" in sources or formats.intersection(_CODE_EXACT_FORMATS):
        return True
    words = {
        match.group(0).casefold()
        for match in re.finditer(r"[^\W\d_]+", plan.normalized_query, re.UNICODE)
    }
    return bool(words.intersection(_EXPLICIT_CODE_WORDS))


def _expanded_format_extensions(formats: Sequence[str]) -> frozenset[str]:
    extensions: set[str] = set()
    for value in formats:
        key = value.casefold().removeprefix(".")
        language_extensions = {
            extension.removeprefix(".").casefold()
            for extension, language in LANGUAGE_EXTENSIONS.items()
            if language.casefold() == key
        }
        if language_extensions:
            extensions.update(language_extensions)
        elif key in _SOURCE_EXTENSION_ALIASES:
            extensions.update(_SOURCE_EXTENSION_ALIASES[key])
        else:
            extensions.add(key)
    return frozenset(extensions)


def _combine_extension_scopes(
    left: frozenset[str] | None,
    right: frozenset[str] | None,
) -> tuple[str, ...] | None:
    if left is None and right is None:
        return None
    if left is None:
        assert right is not None
        return tuple(sorted(right))
    if right is None:
        return tuple(sorted(left))
    return tuple(sorted(left.intersection(right)))


def _inventory_path_scope(
    source_kinds: Sequence[str],
    formats: Sequence[str],
) -> tuple[str, ...] | None:
    source_scope: frozenset[str] | None
    if not source_kinds or any(
        value in {"file", "inventory"} for value in source_kinds
    ):
        source_scope = None
    else:
        source_scope = frozenset(
            extension
            for value in source_kinds
            for extension in _SOURCE_EXTENSION_ALIASES.get(value, ())
        )
    format_scope = _expanded_format_extensions(formats) if formats else None
    return _combine_extension_scopes(source_scope, format_scope)


def _code_path_scope(
    source_kinds: Sequence[str],
    formats: Sequence[str],
) -> tuple[str, ...] | None:
    if source_kinds and "code" not in source_kinds:
        return ()
    if not source_kinds and not formats:
        return None
    format_scope = _expanded_format_extensions(formats) if formats else None
    return _combine_extension_scopes(_CODE_EXTENSIONS, format_scope)


def _catalog_row_scopes(
    source_kinds: Sequence[str],
    formats: Sequence[str],
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    if not source_kinds or "catalog" in source_kinds:
        source_scope: tuple[str, ...] | None = None
    else:
        source_scope = tuple(
            sorted(
                {
                    source_kind
                    for value in source_kinds
                    for source_kind in _CATALOG_SOURCE_ALIASES.get(value, ())
                }
            )
        )
    if formats:
        path_scope: tuple[str, ...] | None = tuple(
            sorted(
                _expanded_format_extensions(formats).intersection(
                    _CATALOG_EXACT_FORMATS
                )
            )
        )
    else:
        path_scope = None
    return source_scope, path_scope


def _catalog_head_sources_for_path_scope(
    path_scope: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if path_scope is None:
        return None
    sources: set[str] = set()
    for extension in path_scope:
        if extension == "pdf":
            sources.add("pdf")
        elif extension == "docx":
            sources.add("docx")
        elif extension in _AUDIO_EXACT_FORMATS:
            sources.add("audio")
        elif extension in {"doc", "odt"}:
            sources.add("odt")
        elif extension in {"ods", "xls", "xlsm", "xlsx"}:
            sources.add("xlsx")
        elif extension in {"odp", "ppt", "pptx"}:
            sources.add("pptx")
    return tuple(sorted(sources))


def _path_scope_clause(
    column: str,
    extensions: tuple[str, ...] | None,
) -> tuple[str, tuple[object, ...]]:
    if extensions is None:
        return "", ()
    if not extensions:
        return " AND 0", ()
    clauses: list[str] = []
    parameters: list[object] = []
    for extension in extensions:
        suffix = f".{extension}"
        clauses.append(f"lower(substr({column},-?))=?")
        parameters.extend((len(suffix), suffix))
    return f" AND ({' OR '.join(clauses)})", tuple(parameters)


def _value_scope_clause(
    column: str,
    values: tuple[str, ...] | None,
) -> tuple[str, tuple[object, ...]]:
    if values is None:
        return "", ()
    if not values:
        return " AND 0", ()
    placeholders = ",".join("?" for _ in values)
    return f" AND lower({column}) IN ({placeholders})", tuple(values)


def _plan_exact_owner_scope(plan: KnowledgePlan) -> tuple[str, ...]:
    """Return owner scope for source/format filters without hiding inventory."""

    if not plan.source_kinds and not plan.formats:
        return EXACT_OWNER_NAMES
    selected = ["inventory"]
    if _code_path_scope(plan.source_kinds, plan.formats) != ():
        selected.append("code")
    catalog_sources, catalog_formats = _catalog_row_scopes(
        plan.source_kinds,
        plan.formats,
    )
    if catalog_sources != () and catalog_formats != ():
        selected.append("catalog")
    return tuple(selected)


def classify_plan_exact_terms(plan: KnowledgePlan) -> tuple[ExactLookupTerm, ...]:
    """Type and deduplicate the planner's legacy string exact terms.

    Serial surface variants such as ``serial SN-2048`` and ``SN-2048`` are
    normalized to one unsupported serial request rather than being queried as
    unrelated catalog identifiers.
    """

    result: list[ExactLookupTerm] = []
    seen: set[tuple[ExactLookupKind, str, str | None]] = set()
    code_context = _plan_demonstrates_code(plan)
    explicit_code_evidence = _plan_has_explicit_code_evidence(plan)
    for surface in plan.exact_terms:
        value = surface.strip()
        if _looks_like_path(value):
            kind = ExactLookupKind.PATH
            canonical = value
        elif _looks_like_file_name(value) and not explicit_code_evidence:
            kind = ExactLookupKind.NAME
            canonical = value
        elif _serial_body(value) is not None:
            kind = ExactLookupKind.SERIAL
            canonical = _canonical_serial(value)
        elif _HEX_TERM.fullmatch(value):
            kind = ExactLookupKind.HASH
            canonical = value.casefold()
        elif _QUALIFIED_SYMBOL.fullmatch(value):
            kind = ExactLookupKind.SYMBOL
            canonical = value
        elif _looks_like_file_name(value):
            kind = ExactLookupKind.NAME
            canonical = value
        elif code_context and _is_bare_code_symbol(value):
            kind = ExactLookupKind.SYMBOL
            canonical = value
        else:
            kind = ExactLookupKind.IDENTIFIER
            canonical = value
        key_value = (
            canonical if kind is ExactLookupKind.SYMBOL else canonical.casefold()
        )
        key = (kind, key_value, None)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ExactLookupTerm(
                kind,
                canonical,
                surface=(surface if surface != canonical else None),
            )
        )
    if code_context:
        for match in _BARE_CODE_TOKEN.finditer(_masked_exact_term_text(plan)):
            value = match.group(0)
            if not _is_bare_code_symbol(value):
                continue
            key = (ExactLookupKind.SYMBOL, value, None)
            if key in seen:
                continue
            seen.add(key)
            result.append(ExactLookupTerm(ExactLookupKind.SYMBOL, value))
    return tuple(result)


class _WorkBudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class _QueryControl:
    remaining_rows: int
    remaining_steps: int
    cancellation_check: Callable[[], None] | None
    cancellation_failure: BaseException | None = None

    def checkpoint(self) -> None:
        if self.cancellation_check is not None:
            try:
                self.cancellation_check()
            except BaseException as exc:
                self.cancellation_failure = exc
                raise

    def query(
        self,
        connection: sqlite3.Connection,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> tuple[tuple[sqlite3.Row, ...], int]:
        self.checkpoint()
        if self.remaining_rows <= 0:
            raise _WorkBudgetExceeded("exact row observation budget exhausted")
        if self.remaining_steps < SQLITE_PROGRESS_INTERVAL:
            raise _WorkBudgetExceeded("exact SQLite work budget exhausted")
        before = self.remaining_steps
        exhausted = False
        progress_interval = SQLITE_PROGRESS_INTERVAL
        callbacks = 0

        def progress() -> int:
            nonlocal callbacks, exhausted
            if self.cancellation_check is not None:
                try:
                    self.cancellation_check()
                except BaseException as exc:  # preserve the caller's signal
                    self.cancellation_failure = exc
                    return 1
            callbacks += 1
            self.remaining_steps -= progress_interval
            if self.remaining_steps < progress_interval:
                exhausted = True
                return 1
            return 0

        connection.set_progress_handler(progress, progress_interval)
        try:
            try:
                rows = tuple(connection.execute(sql, parameters).fetchall())
            except sqlite3.OperationalError as exc:
                if self.cancellation_failure is not None:
                    raise self.cancellation_failure
                if exhausted:
                    raise _WorkBudgetExceeded(
                        "exact SQLite work budget exhausted"
                    ) from exc
                raise
        finally:
            connection.set_progress_handler(None, 0)
        # SQLite does not call the handler for a final sub-interval.  Charge
        # one conservative interval so many short statements cannot bypass
        # the aggregate VM-work budget.
        if callbacks == 0 or self.remaining_steps >= progress_interval:
            self.remaining_steps -= progress_interval
        if len(rows) > self.remaining_rows:
            raise AssertionError("exact owner query exceeded its row observation limit")
        self.remaining_rows -= len(rows)
        self.checkpoint()
        return rows, before - self.remaining_steps


def _query_limit(control: _QueryControl, requested: int) -> int:
    return max(1, min(requested, control.remaining_rows))


def _ranking_name(owner: str, term: ExactLookupTerm) -> str:
    suffix = term.term_id.rsplit(":", 1)[-1]
    return f"exact_{owner}_{term.kind.value}:{suffix}"


def _report(
    owner: str,
    term: ExactLookupTerm,
    status: ExactLookupStatus,
    *,
    executed: bool,
    available: bool,
    returned: int = 0,
    rows_observed: int = 0,
    sqlite_steps: int = 0,
    truncated: bool = False,
    omitted_matches: int = 0,
    reason: str | None = None,
    warnings: tuple[str, ...] = (),
) -> ExactOwnerReport:
    return ExactOwnerReport(
        _ranking_name(owner, term),
        owner,
        term,
        status,
        executed,
        available,
        returned,
        rows_observed,
        sqlite_steps,
        truncated,
        omitted_matches,
        reason,
        warnings,
    )


# endregion [02]


# region [03] Snapshot, connection, identity and evidence helpers


def _snapshot_owner(snapshot: KnowledgeSnapshot, name: str) -> OwnerSnapshot | None:
    return next((owner for owner in snapshot.owners if owner.owner == name), None)


def _watermarks(owner: OwnerSnapshot) -> dict[str, str]:
    return {item.name: item.value for item in owner.watermarks}


def _watermark_int(owner: OwnerSnapshot, name: str) -> int | None:
    value = _watermarks(owner).get(name)
    if value is None:
        return None
    try:
        result = int(value)
    except ValueError:
        return None
    return result if result >= 0 else None


def _head_batches(
    values: Sequence[tuple[str, int]],
) -> Iterable[tuple[tuple[str, int], ...]]:
    for offset in range(0, len(values), HEAD_BATCH_SIZE):
        yield tuple(values[offset : offset + HEAD_BATCH_SIZE])


def _expected_values(count: int) -> str:
    if not 1 <= count <= HEAD_BATCH_SIZE:
        raise ValueError("exact head batch is outside its supported bound")
    return ",".join("(?,?)" for _ in range(count))


def _flatten_heads(heads: Sequence[tuple[str, int]]) -> tuple[object, ...]:
    return tuple(value for head in heads for value in head)


@contextmanager
def _inventory_database(path: Path):
    connection = sqlite3.connect(
        readonly_sqlite_uri(path),
        uri=True,
        timeout=60,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA foreign_keys=ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise RuntimeError("exact inventory reader could not enable foreign keys")
        connection.execute("PRAGMA query_only=ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise RuntimeError("exact inventory reader could not enforce query-only")
        yield connection
    finally:
        connection.close()


def _physical_resource(
    *,
    source_kind: str,
    owner: str,
    source_identity: str,
    identity: FileIdentity,
    birthtime_ns: object,
    path: str,
) -> tuple[ResourceRef, tuple[str, ...]]:
    birthtime = (
        birthtime_ns
        if isinstance(birthtime_ns, int) and not isinstance(birthtime_ns, bool)
        else None
    )
    if birthtime is not None and birthtime >= 0:
        physical = f"{identity.volume_id}:{identity.file_id}:{birthtime}"
        return (
            ResourceRef(
                f"resource:file:{physical}",
                source_kind,
                owner,
                PhysicalIdentityRef("windows_file_id_birthtime", physical, 1),
                path,
            ),
            (),
        )
    return (
        ResourceRef(
            f"resource:{owner}:{source_identity}",
            source_kind,
            owner,
            PhysicalIdentityRef(f"{owner}_file_key", source_identity, 1),
            path,
        ),
        ("physical_identity_unresolved",),
    )


def _stable_exact_evidence_id(
    *,
    owner: str,
    resource_id: str,
    revision_id: str,
    section_kind: str,
    section_id: str,
    identifiers: Sequence[tuple[str, str]],
    extractor: str,
    generation: int | None = None,
) -> str:
    payload: dict[str, object] = {
        "owner": owner,
        "resource_id": resource_id,
        "revision_id": revision_id,
        "section_kind": section_kind,
        "section_id": section_id,
        "identifiers": list(identifiers),
        "extractor": extractor,
    }
    if generation is not None:
        payload["generation"] = generation
    identity = fingerprint_text(canonical_json(payload))
    return f"evidence:exact:{owner}:{identity.xxh3_128}"


def _inventory_identity(volume: object, file_id: object) -> FileIdentity:
    values: list[int] = []
    for value in (volume, file_id):
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("inventory identity components must be binary")
        raw = bytes(value)
        if len(raw) != 16:
            raise ValueError("inventory identity components must be 16 bytes")
        values.append(int.from_bytes(raw, "little"))
    return FileIdentity(values[0], values[1])


def _code_identity(volume: object, file_id: object) -> FileIdentity:
    return FileIdentity(int(str(volume), 16), int(str(file_id), 16))


def _canonical_decimal(value: object) -> int:
    text = str(value)
    if text != "0" and (not text or text[0] == "0" or not text.isdecimal()):
        raise ValueError("catalog identity is not canonical decimal")
    return int(text)


def _catalog_identity(row: sqlite3.Row) -> FileIdentity:
    identity = FileIdentity(
        _canonical_decimal(row["volume_id"]),
        _canonical_decimal(row["file_id"]),
    )
    file_key = str(row["file_key"])
    decimal = f"{identity.volume_id}:{identity.file_id}"
    if file_key not in {identity.packed_key, decimal}:
        raise FileIdentityError(
            "catalog file_key disagrees with its neutral identity fields"
        )
    return identity


def _code_revision(
    row: sqlite3.Row,
    resource_id: str,
) -> RevisionRef:
    payload = {
        "source_kind": "code",
        "source_identity": f"{row['volume_id']}:{row['physical_file_id']}",
        "source_revision": {
            "version_id": int(row["version_id"]),
            "size": int(row["size"]),
            "mtime_ns": int(row["mtime_ns"]),
            "birthtime_ns": int(row["birthtime_ns"]),
            "raw_content_xxh3_128": row["raw_xxh3_128"],
        },
    }
    identity = fingerprint_text(canonical_json(payload))
    state = (
        RevisionState.CURRENT
        if str(row["analysis_status"]) in {"complete", "text_only"}
        else RevisionState.PARTIAL
    )
    return RevisionRef(
        resource_id,
        f"revision:code:{identity.xxh3_128}",
        f"{row['analyzer_id']}:{row['analyzer_version']}",
        str(row["processing_signature"]),
        None,
        state,
    )


def _catalog_quality_warnings(row: sqlite3.Row) -> tuple[str, ...]:
    warnings: set[str] = set()
    source_status = str(row["source_status"]).casefold()
    catalog_status = str(row["catalog_status"]).casefold()
    uncertainty = str(row["uncertainty"]).casefold()
    if source_status not in {"complete", "done"}:
        warnings.add("catalog_source_status_partial")
    if uncertainty in {"alta", "high"}:
        warnings.add("catalog_uncertainty_requires_review")
    if catalog_status == "review":
        warnings.add("catalog_review_required")
    elif catalog_status == "error":
        warnings.add("catalog_classification_error")
    elif catalog_status != "classified":
        warnings.add("catalog_status_not_classified")
    return tuple(sorted(warnings))


def _catalog_revision(row: sqlite3.Row, resource_id: str) -> RevisionRef:
    payload = {
        "source_kind": str(row["source_kind"]),
        "file_key": str(row["file_key"]),
        "processing_signature": str(row["processing_signature"]),
        "size": int(row["size"]),
        "mtime_ns": int(row["mtime_ns"]),
    }
    identity = fingerprint_text(canonical_json(payload))
    state = (
        RevisionState.PARTIAL
        if _catalog_quality_warnings(row)
        else RevisionState.CURRENT
    )
    return RevisionRef(
        resource_id,
        f"revision:catalog:{identity.xxh3_128}",
        "document-catalog-v6",
        str(row["processing_signature"]),
        int(row["generation_id"]),
        state,
    )


def _catalog_references(value: object) -> tuple[Mapping[str, str], ...]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return ()
    if not isinstance(decoded, list):
        return ()
    result: list[Mapping[str, str]] = []
    for item in decoded[:64]:
        if isinstance(item, str) and item.strip():
            result.append({"identifier": item.strip()})
        elif isinstance(item, dict):
            identifier = item.get("identifier")
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            reference: dict[str, str] = {"identifier": identifier.strip()}
            for key in ("authority", "evidence"):
                member = item.get(key)
                if isinstance(member, str) and member.strip():
                    reference[key] = member.strip()
            result.append(reference)
    return tuple(result)


def _rank_matches(
    matches: Sequence[ExactEvidenceMatch],
) -> tuple[ExactEvidenceMatch, ...]:
    ordered = sorted(
        matches,
        key=lambda match: (
            (match.resource.current_path or "").casefold(),
            match.resource.resource_id,
            match.evidence.evidence_id,
        ),
    )
    return tuple(
        replace(match, source_rank=rank) for rank, match in enumerate(ordered, 1)
    )


def _basename_predicate(column: str) -> str:
    normalized = f"replace({column},'\\','/')"
    return (
        f"(({normalized})=? COLLATE NOCASE OR "
        f"(substr(({normalized}),-length(?))=? COLLATE NOCASE AND "
        f"substr(({normalized}),-(length(?)+1),1)='/'))"
    )


def _non_ascii_case_warning(term: ExactLookupTerm) -> tuple[str, ...]:
    if (
        term.kind
        in {
            ExactLookupKind.PATH,
            ExactLookupKind.NAME,
            ExactLookupKind.IDENTIFIER,
        }
        and not term.value.isascii()
    ):
        return ("sqlite_nocase_is_ascii_only",)
    return ()


# endregion [03]


# region [04] Inventory v7 adapter


_INVENTORY_KINDS = frozenset(
    {ExactLookupKind.PATH, ExactLookupKind.NAME, ExactLookupKind.HASH}
)
_FULL_INVENTORY_HASH = "xxh3_128_full_v1"


def _inventory_current_vector(
    connection: sqlite3.Connection,
    control: _QueryControl,
) -> tuple[tuple[tuple[str, int], ...], int, int, int]:
    requested = MAX_EXACT_TERMS * 16 + 1
    limit = _query_limit(control, requested)
    rows, steps = control.query(
        connection,
        """SELECT c.root,c.scan_id,c.updated_ns,s.status
        FROM inventory_checkpoints c
        LEFT JOIN scans s ON s.scan_id=c.scan_id
        AND s.root=c.root COLLATE NOCASE
        WHERE c.valid=1 ORDER BY c.root COLLATE NOCASE LIMIT ?""",
        (limit,),
    )
    if limit < requested and len(rows) == limit:
        raise _WorkBudgetExceeded("inventory vector exceeds row observation budget")
    if len(rows) > MAX_EXACT_TERMS * 16:
        raise RuntimeError("inventory publication vector exceeds exact lookup bound")
    if any(row["status"] != "complete" for row in rows):
        raise RuntimeError("inventory checkpoint is not a complete matching scan")
    heads = tuple((str(row["root"]), int(row["scan_id"])) for row in rows)
    updated = max((int(row["updated_ns"]) for row in rows), default=0)
    return heads, len(rows), updated, steps


def _inventory_row_match(
    row: sqlite3.Row,
    term: ExactLookupTerm,
    rank: int,
) -> ExactEvidenceMatch:
    identity = _inventory_identity(row["volume_id"], row["file_id"])
    source_identity = identity.packed_key
    resource, identity_warnings = _physical_resource(
        source_kind="file",
        owner="inventory",
        source_identity=source_identity,
        identity=identity,
        birthtime_ns=row["birthtime_ns"],
        path=str(row["path"]),
    )
    scan_id = int(row["scan_id"])
    revision_identity = fingerprint_text(
        canonical_json(
            {
                "resource_id": resource.resource_id,
                "scan_id": scan_id,
                "size": int(row["size"]),
                "mtime_ns": int(row["mtime_ns"]),
            }
        )
    )
    revision_id = f"revision:inventory:{revision_identity.xxh3_128}"
    revision = RevisionRef(
        resource.resource_id,
        revision_id,
        "inventory-v7",
        "inventory-physical-observation-v1",
        scan_id,
        RevisionState.CURRENT,
    )
    if term.kind is ExactLookupKind.HASH:
        algorithm = str(row["algorithm"])
        digest = bytes(row["digest"]).hex()
        identifiers = ((algorithm, digest),)
        section_kind = "content_fingerprint"
        section_id = f"{algorithm}:{digest}"
        reason = "published inventory full fingerprint matched exactly"
    elif term.kind is ExactLookupKind.NAME:
        observed_path = str(row["path"])
        identifiers = (
            ("file_name", observed_path.replace("\\", "/").rsplit("/", 1)[-1]),
        )
        section_kind = "current_path"
        section_id = observed_path
        reason = "published inventory file name matched exactly"
    else:
        observed_path = str(row["path"])
        identifiers = (("path", observed_path),)
        section_kind = "current_path"
        section_id = observed_path
        reason = "published inventory path matched exactly"
    evidence = EvidenceRef(
        _stable_exact_evidence_id(
            owner="inventory",
            resource_id=resource.resource_id,
            revision_id=revision.revision_id,
            section_kind=section_kind,
            section_id=section_id,
            identifiers=identifiers,
            extractor="inventory-v7",
            generation=scan_id,
        ),
        resource.resource_id,
        revision.revision_id,
        EvidenceMethod.STRUCTURAL,
        section_kind=section_kind,
        section_id=section_id,
        extractor="inventory-v7",
        extractor_version="7",
        generation=scan_id,
        identifiers=identifiers,
    )
    warnings = {
        *identity_warnings,
        "inventory_publication_head_is_mutable",
        *_non_ascii_case_warning(term),
    }
    if term.kind is ExactLookupKind.HASH:
        warnings.add("digest_equality_is_not_byte_comparison")
    return ExactEvidenceMatch(
        _ranking_name("inventory", term),
        term,
        resource,
        revision,
        evidence,
        rank,
        reason,
        generation=scan_id,
        warnings=tuple(sorted(warnings)),
    )


def _inventory_term_rows(
    connection: sqlite3.Connection,
    control: _QueryControl,
    heads: Sequence[tuple[str, int]],
    term: ExactLookupTerm,
    requested: int,
    path_scope: tuple[str, ...] | None,
) -> tuple[tuple[sqlite3.Row, ...], int, bool]:
    collected: list[sqlite3.Row] = []
    steps = 0
    truncated = False
    for batch in _head_batches(heads):
        remaining = requested - len(collected)
        if remaining <= 0:
            truncated = True
            break
        limit = _query_limit(control, remaining)
        expected = _expected_values(len(batch))
        base = f"""WITH expected(root,scan_id) AS (VALUES {expected})
        SELECT e.root,f.scan_id,f.path,f.volume_id,f.file_id,f.size,f.mtime_ns,
        f.birthtime_ns"""
        if term.kind is ExactLookupKind.HASH:
            base += ",fp.algorithm,fp.digest"
        base += """
        FROM expected e JOIN scans s ON s.scan_id=e.scan_id
        AND s.root=e.root COLLATE NOCASE AND s.status='complete'
        JOIN files f ON f.scan_id=e.scan_id"""
        if term.kind is ExactLookupKind.PATH:
            predicate = "f.path=? COLLATE NOCASE"
            term_parameters: tuple[object, ...] = (term.value,)
        elif term.kind is ExactLookupKind.NAME:
            predicate = _basename_predicate("f.path")
            term_parameters = (term.value, term.value, term.value, term.value)
        else:
            base += """ JOIN fingerprints fp ON fp.volume_id=f.volume_id
            AND fp.file_id=f.file_id AND fp.size=f.size
            AND fp.mtime_ns=f.mtime_ns AND fp.birthtime_ns=f.birthtime_ns"""
            predicate = "fp.algorithm=? AND fp.digest=?"
            term_parameters = (_FULL_INVENTORY_HASH, bytes.fromhex(term.value))
        scope_clause, scope_parameters = _path_scope_clause("f.path", path_scope)
        sql = (
            f"{base} WHERE {predicate}{scope_clause} "
            "ORDER BY f.path COLLATE NOCASE,f.scan_id LIMIT ?"
        )
        rows, used = control.query(
            connection,
            sql,
            (
                *_flatten_heads(batch),
                *term_parameters,
                *scope_parameters,
                limit,
            ),
        )
        collected.extend(rows)
        steps += used
        if len(rows) == limit and (limit < remaining or len(collected) >= requested):
            truncated = True
            break
    return tuple(collected), steps, truncated


def _lookup_inventory(
    path: Path,
    owner: OwnerSnapshot,
    terms: Sequence[ExactLookupTerm],
    control: _QueryControl,
    per_term_limit: int,
    path_scope: tuple[str, ...] | None,
) -> tuple[list[ExactEvidenceMatch], list[ExactOwnerReport]]:
    matches: list[ExactEvidenceMatch] = []
    reports: list[ExactOwnerReport] = []
    snapshot_heads = tuple(
        sorted((head.scope, head.generation) for head in owner.publications)
    )
    try:
        with _inventory_database(path) as connection:
            connection.execute("BEGIN")
            current_heads, current_count, current_updated, preflight_steps = (
                _inventory_current_vector(connection, control)
            )
            expected_count = _watermark_int(owner, "published_roots")
            expected_updated = _watermark_int(owner, "latest_checkpoint_updated_ns")
            vector_matches = (
                tuple(sorted(current_heads)) == snapshot_heads
                and expected_count == current_count
                and expected_updated == current_updated
            )
            if not vector_matches:
                for term in terms:
                    reports.append(
                        _report(
                            "inventory",
                            term,
                            ExactLookupStatus.PARTIAL,
                            executed=True,
                            available=True,
                            sqlite_steps=preflight_steps,
                            reason="inventory_changed_after_snapshot",
                        )
                    )
                connection.execute("ROLLBACK")
                return matches, reports
            if not snapshot_heads:
                for term in terms:
                    reports.append(
                        _report(
                            "inventory",
                            term,
                            ExactLookupStatus.COMPLETE,
                            executed=True,
                            available=True,
                            sqlite_steps=preflight_steps,
                            reason="inventory_snapshot_has_no_heads",
                        )
                    )
                connection.execute("ROLLBACK")
                return matches, reports
            for term in terms:
                if term.kind is ExactLookupKind.HASH and (
                    len(term.value) != 32
                    or term.algorithm not in {None, "xxh3_128", _FULL_INVENTORY_HASH}
                ):
                    reports.append(
                        _report(
                            "inventory",
                            term,
                            ExactLookupStatus.UNSUPPORTED,
                            executed=False,
                            available=True,
                            reason="inventory_hash_algorithm_unsupported",
                        )
                    )
                    continue
                rows_before = control.remaining_rows
                steps = 0
                truncated = False
                try:
                    rows, steps, truncated = _inventory_term_rows(
                        connection,
                        control,
                        snapshot_heads,
                        term,
                        per_term_limit + 1,
                        path_scope,
                    )
                except _WorkBudgetExceeded:
                    reports.append(
                        _report(
                            "inventory",
                            term,
                            ExactLookupStatus.PARTIAL,
                            executed=True,
                            available=True,
                            rows_observed=rows_before - control.remaining_rows,
                            sqlite_steps=steps,
                            truncated=True,
                            reason="exact_work_budget_exhausted",
                        )
                    )
                    continue
                observed = len(rows)
                invalid = 0
                term_matches: list[ExactEvidenceMatch] = []
                for row in rows:
                    control.checkpoint()
                    try:
                        term_matches.append(
                            _inventory_row_match(row, term, len(term_matches) + 1)
                        )
                    except (FileIdentityError, TypeError, ValueError):
                        invalid += 1
                all_ranked = _rank_matches(term_matches)
                ranked = all_ranked[:per_term_limit]
                known_omitted = max(0, len(all_ranked) - len(ranked))
                matches.extend(ranked)
                was_truncated = truncated or known_omitted > 0
                reason = (
                    "inventory_identity_invalid"
                    if invalid
                    else "inventory_publication_head_is_mutable"
                )
                warnings = ["inventory_exact_is_snapshot_constrained_not_as_of"]
                if term.kind is ExactLookupKind.NAME:
                    warnings.append("inventory_has_no_basename_index")
                if term.kind is ExactLookupKind.HASH:
                    warnings.append("inventory_has_no_digest_index")
                reports.append(
                    _report(
                        "inventory",
                        term,
                        ExactLookupStatus.PARTIAL,
                        executed=True,
                        available=True,
                        returned=len(ranked),
                        rows_observed=observed,
                        sqlite_steps=steps + preflight_steps,
                        truncated=was_truncated,
                        omitted_matches=known_omitted,
                        reason=(
                            "exact_result_limit_reached" if was_truncated else reason
                        ),
                        warnings=tuple(warnings),
                    )
                )
                preflight_steps = 0
            connection.execute("ROLLBACK")
    except (sqlite3.Error, RuntimeError, OSError) as exc:
        if control.cancellation_failure is exc:
            raise
        failure_reason = (
            "exact_work_budget_exhausted"
            if isinstance(exc, _WorkBudgetExceeded)
            else f"owner_read_failed:{type(exc).__name__}"
        )
        for term in terms[len(reports) :]:
            reports.append(
                _report(
                    "inventory",
                    term,
                    ExactLookupStatus.PARTIAL,
                    executed=True,
                    available=True,
                    truncated=isinstance(exc, _WorkBudgetExceeded),
                    reason=failure_reason,
                )
            )
    return matches, reports


# endregion [04]


# region [05] Code v2 adapter (best-effort, non-generational)


_CODE_KINDS = frozenset(
    {
        ExactLookupKind.PATH,
        ExactLookupKind.NAME,
        ExactLookupKind.HASH,
        ExactLookupKind.SYMBOL,
    }
)


def _code_current_vector(
    connection: sqlite3.Connection,
    control: _QueryControl,
) -> tuple[dict[str, int], int]:
    rows, steps = control.query(
        connection,
        """SELECT
        (SELECT COUNT(*) FROM files WHERE status='current') AS current_files,
        (SELECT COALESCE(MAX(version_id),0) FROM file_versions) AS latest_version_id,
        (SELECT COALESCE(MAX(analysis_run_id),0) FROM analysis_runs)
            AS latest_analysis_run_id
        LIMIT 1""",
    )
    row = rows[0]
    return (
        {
            "current_files": int(row["current_files"]),
            "latest_version_id": int(row["latest_version_id"]),
            "latest_analysis_run_id": int(row["latest_analysis_run_id"]),
        },
        steps,
    )


_CODE_SELECT = """SELECT v.version_id,f.volume_id,f.physical_file_id,
f.current_path,v.path_observed,v.size,v.mtime_ns,v.birthtime_ns,
v.raw_xxh3_128,v.processing_signature,v.analyzer_id,v.analyzer_version,
v.parser_kind,v.analysis_status"""


def _code_term_rows(
    connection: sqlite3.Connection,
    control: _QueryControl,
    term: ExactLookupTerm,
    latest_version_id: int,
    requested: int,
    path_scope: tuple[str, ...] | None,
) -> tuple[tuple[sqlite3.Row, ...], int, bool]:
    select = _CODE_SELECT
    join = ""
    if term.kind is ExactLookupKind.SYMBOL:
        select += ",s.symbol_id,s.kind AS symbol_kind,s.name,s.qualified_name,"
        select += "s.signature,s.confirmed,s.start_line,s.end_line"
        join = " JOIN symbols s ON s.version_id=v.version_id"
        predicate = "(s.name=? OR s.qualified_name=?)"
        term_parameters: tuple[object, ...] = (term.value, term.value)
        order = "s.qualified_name,s.symbol_id"
    elif term.kind is ExactLookupKind.PATH:
        predicate = "f.current_path=? COLLATE NOCASE"
        term_parameters = (term.value,)
        order = "f.current_path COLLATE NOCASE,v.version_id"
    elif term.kind is ExactLookupKind.NAME:
        predicate = _basename_predicate("f.current_path")
        term_parameters = (term.value, term.value, term.value, term.value)
        order = "f.current_path COLLATE NOCASE,v.version_id"
    else:
        predicate = "v.raw_xxh3_128=? COLLATE NOCASE"
        term_parameters = (term.value,)
        order = "f.current_path COLLATE NOCASE,v.version_id"
    scope_clause, scope_parameters = _path_scope_clause(
        "f.current_path",
        path_scope,
    )
    limit = _query_limit(control, requested)
    rows, steps = control.query(
        connection,
        f"""{select} FROM file_versions v
        JOIN files f ON f.current_version_id=v.version_id{join}
        WHERE f.status='current' AND v.invalidated_ns IS NULL
        AND v.version_id<=? AND {predicate}{scope_clause}
        ORDER BY {order} LIMIT ?""",
        (latest_version_id, *term_parameters, *scope_parameters, limit),
    )
    return rows, steps, len(rows) == limit


def _code_row_match(
    row: sqlite3.Row,
    term: ExactLookupTerm,
    rank: int,
) -> ExactEvidenceMatch:
    identity = _code_identity(row["volume_id"], row["physical_file_id"])
    source_identity = f"{row['volume_id']}:{row['physical_file_id']}"
    resource, identity_warnings = _physical_resource(
        source_kind="code",
        owner="code",
        source_identity=source_identity,
        identity=identity,
        birthtime_ns=row["birthtime_ns"],
        path=str(row["current_path"]),
    )
    revision = _code_revision(row, resource.resource_id)
    version_id = int(row["version_id"])
    identifiers: list[tuple[str, str]] = [("code_version_id", str(version_id))]
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    section_kind: str
    section_id: str
    snippet: str | None = None
    if term.kind is ExactLookupKind.SYMBOL:
        qualified = str(row["qualified_name"])
        identifiers.append(("symbol", qualified))
        start_line = int(row["start_line"])
        end_line = int(row["end_line"])
        symbol = qualified
        section_kind = "code_symbol"
        section_id = str(row["symbol_id"])
        snippet = str(row["signature"] or qualified)[:4_096]
        reason = "current structured code symbol matched exactly"
    elif term.kind is ExactLookupKind.HASH:
        observed_digest = str(row["raw_xxh3_128"]).casefold()
        identifiers.append(("raw_xxh3_128", observed_digest))
        section_kind = "raw_content_fingerprint"
        section_id = observed_digest
        reason = "current code raw-content fingerprint matched exactly"
    elif term.kind is ExactLookupKind.NAME:
        observed_path = str(row["current_path"])
        identifiers.append(
            ("file_name", observed_path.replace("\\", "/").rsplit("/", 1)[-1])
        )
        section_kind = "current_path"
        section_id = observed_path
        reason = "current structured code file name matched exactly"
    else:
        observed_path = str(row["current_path"])
        identifiers.append(("path", observed_path))
        section_kind = "current_path"
        section_id = observed_path
        reason = "current structured code path matched exactly"
    symbol_unconfirmed = bool(
        term.kind is ExactLookupKind.SYMBOL and int(row["confirmed"]) != 1
    )
    if symbol_unconfirmed:
        revision = replace(revision, state=RevisionState.PARTIAL)
        reason = "unconfirmed structured code symbol matched exactly"
    evidence = EvidenceRef(
        _stable_exact_evidence_id(
            owner="code",
            resource_id=resource.resource_id,
            revision_id=revision.revision_id,
            section_kind=section_kind,
            section_id=section_id,
            identifiers=identifiers,
            extractor=str(row["analyzer_id"]),
        ),
        resource.resource_id,
        revision.revision_id,
        EvidenceMethod.STRUCTURAL,
        start_line=start_line,
        end_line=end_line,
        symbol=symbol,
        section_kind=section_kind,
        section_id=section_id,
        snippet=snippet,
        extractor=str(row["analyzer_id"]),
        extractor_version=str(row["analyzer_version"]),
        identifiers=tuple(identifiers),
    )
    warnings = {
        *identity_warnings,
        "code_snapshot_visibility_best_effort_non_generational",
        *_non_ascii_case_warning(term),
    }
    if term.kind is ExactLookupKind.HASH:
        warnings.add("digest_equality_is_not_byte_comparison")
    if symbol_unconfirmed:
        warnings.add("code_symbol_unconfirmed")
    return ExactEvidenceMatch(
        _ranking_name("code", term),
        term,
        resource,
        revision,
        evidence,
        rank,
        reason,
        model_signature=str(row["processing_signature"]),
        warnings=tuple(sorted(warnings)),
    )


def _lookup_code(
    path: Path,
    owner: OwnerSnapshot,
    terms: Sequence[ExactLookupTerm],
    control: _QueryControl,
    per_term_limit: int,
    path_scope: tuple[str, ...] | None,
) -> tuple[list[ExactEvidenceMatch], list[ExactOwnerReport]]:
    matches: list[ExactEvidenceMatch] = []
    reports: list[ExactOwnerReport] = []
    expected = {
        name: _watermark_int(owner, name)
        for name in (
            "current_files",
            "latest_version_id",
            "latest_analysis_run_id",
        )
    }
    if any(value is None for value in expected.values()):
        return matches, [
            _report(
                "code",
                term,
                ExactLookupStatus.PARTIAL,
                executed=False,
                available=True,
                reason="code_snapshot_watermark_missing",
            )
            for term in terms
        ]
    try:
        connection = connect_code_state(path, readonly=True)
        try:
            connection.execute("BEGIN")
            current, preflight_steps = _code_current_vector(connection, control)
            if any(current[name] != expected[name] for name in current):
                for term in terms:
                    reports.append(
                        _report(
                            "code",
                            term,
                            ExactLookupStatus.PARTIAL,
                            executed=True,
                            available=True,
                            sqlite_steps=preflight_steps,
                            reason="code_changed_after_snapshot",
                        )
                    )
                connection.execute("ROLLBACK")
                return matches, reports
            latest_version_id = current["latest_version_id"]
            for term in terms:
                if term.kind is ExactLookupKind.HASH and (
                    len(term.value) != 32
                    or term.algorithm
                    not in {None, "xxh3_128", "raw_xxh3_128", "xxh3_128_raw_v1"}
                ):
                    reports.append(
                        _report(
                            "code",
                            term,
                            ExactLookupStatus.UNSUPPORTED,
                            executed=False,
                            available=True,
                            reason="code_hash_algorithm_unsupported",
                        )
                    )
                    continue
                try:
                    rows, steps, truncated = _code_term_rows(
                        connection,
                        control,
                        term,
                        latest_version_id,
                        per_term_limit + 1,
                        path_scope,
                    )
                except _WorkBudgetExceeded:
                    reports.append(
                        _report(
                            "code",
                            term,
                            ExactLookupStatus.PARTIAL,
                            executed=True,
                            available=True,
                            truncated=True,
                            reason="exact_work_budget_exhausted",
                        )
                    )
                    continue
                invalid = 0
                term_matches: list[ExactEvidenceMatch] = []
                for row in rows:
                    control.checkpoint()
                    try:
                        term_matches.append(
                            _code_row_match(row, term, len(term_matches) + 1)
                        )
                    except (FileIdentityError, TypeError, ValueError):
                        invalid += 1
                all_ranked = _rank_matches(term_matches)
                ranked = all_ranked[:per_term_limit]
                known_omitted = max(0, len(all_ranked) - len(ranked))
                matches.extend(ranked)
                was_truncated = truncated or known_omitted > 0
                warnings = ["code_exact_is_best_effort_non_generational"]
                if term.kind is ExactLookupKind.NAME:
                    warnings.append("code_has_no_basename_index")
                symbol_unconfirmed = any(
                    "code_symbol_unconfirmed" in match.warnings for match in ranked
                )
                if symbol_unconfirmed:
                    warnings.append("code_symbol_unconfirmed")
                report_reason = (
                    "exact_result_limit_reached"
                    if was_truncated
                    else (
                        "code_identity_invalid"
                        if invalid
                        else (
                            "code_symbol_unconfirmed"
                            if symbol_unconfirmed
                            else "code_owner_non_generational"
                        )
                    )
                )
                reports.append(
                    _report(
                        "code",
                        term,
                        ExactLookupStatus.PARTIAL,
                        executed=True,
                        available=True,
                        returned=len(ranked),
                        rows_observed=len(rows),
                        sqlite_steps=steps + preflight_steps,
                        truncated=was_truncated,
                        omitted_matches=known_omitted,
                        reason=report_reason,
                        warnings=tuple(warnings),
                    )
                )
                preflight_steps = 0
            connection.execute("ROLLBACK")
        finally:
            connection.close()
    except (sqlite3.Error, RuntimeError, OSError) as exc:
        if control.cancellation_failure is exc:
            raise
        failure_reason = (
            "exact_work_budget_exhausted"
            if isinstance(exc, _WorkBudgetExceeded)
            else f"owner_read_failed:{type(exc).__name__}"
        )
        for term in terms[len(reports) :]:
            reports.append(
                _report(
                    "code",
                    term,
                    ExactLookupStatus.PARTIAL,
                    executed=True,
                    available=True,
                    truncated=isinstance(exc, _WorkBudgetExceeded),
                    reason=failure_reason,
                )
            )
    return matches, reports


# endregion [05]


# region [06] Catalog v6 generational adapter


_CATALOG_KINDS = frozenset(
    {ExactLookupKind.PATH, ExactLookupKind.NAME, ExactLookupKind.IDENTIFIER}
)


_CATALOG_SELECT = """SELECT d.generation_id,d.source_kind,d.file_key,d.path,
d.volume_id,d.file_id,d.birthtime_ns,d.size,d.mtime_ns,d.source_status,
d.processing_signature,d.classifier_signature,d.confidence,d.uncertainty,
d.standard_references_json,d.catalog_status,d.updated_ns,
d.last_seen_catalog_run_id"""


def _catalog_valid_heads(
    connection: sqlite3.Connection,
    control: _QueryControl,
    heads: Sequence[tuple[str, int]],
) -> tuple[tuple[tuple[str, int], ...], int, int]:
    valid: list[tuple[str, int]] = []
    missing = 0
    steps = 0
    for batch in _head_batches(heads):
        limit = _query_limit(control, len(batch))
        expected = _expected_values(len(batch))
        rows, used = control.query(
            connection,
            f"""WITH expected(source_kind,generation_id) AS (VALUES {expected})
            SELECT e.source_kind,e.generation_id,g.status,g.source_kind AS actual_kind
            FROM expected e LEFT JOIN catalog_generations g
            ON g.generation_id=e.generation_id
            ORDER BY e.source_kind,e.generation_id LIMIT ?""",
            (*_flatten_heads(batch), limit),
        )
        steps += used
        if limit < len(batch) and len(rows) == limit:
            raise _WorkBudgetExceeded("catalog heads exceed row observation budget")
        by_head = {
            (str(row["source_kind"]), int(row["generation_id"])): row for row in rows
        }
        for head in batch:
            row = by_head.get(head)
            if (
                row is None
                or row["actual_kind"] != head[0]
                or row["status"] not in {"published", "superseded"}
            ):
                missing += 1
            else:
                valid.append(head)
    return tuple(valid), missing, steps


def _catalog_has_invalid_identifier_json(
    connection: sqlite3.Connection,
    control: _QueryControl,
    heads: Sequence[tuple[str, int]],
    path_scope: tuple[str, ...] | None,
) -> tuple[bool, int]:
    steps = 0
    for batch in _head_batches(heads):
        expected = _expected_values(len(batch))
        path_clause, path_parameters = _path_scope_clause("d.path", path_scope)
        rows, used = control.query(
            connection,
            f"""WITH expected(source_kind,generation_id) AS (VALUES {expected})
            SELECT 1 AS invalid FROM expected e
            JOIN catalog_generation_documents d
            ON d.generation_id=e.generation_id AND d.source_kind=e.source_kind
            WHERE d.active=1 AND d.catalog_status<>'error'
            AND NOT json_valid(d.standard_references_json){path_clause} LIMIT 1""",
            (*_flatten_heads(batch), *path_parameters),
        )
        steps += used
        if rows:
            return True, steps
    return False, steps


def _catalog_identifier_coverage_incomplete(
    connection: sqlite3.Connection,
    control: _QueryControl,
    heads: Sequence[tuple[str, int]],
    path_scope: tuple[str, ...] | None,
) -> tuple[bool, int]:
    """Detect rows whose classification cannot prove identifier absence."""

    steps = 0
    for batch in _head_batches(heads):
        expected = _expected_values(len(batch))
        path_clause, path_parameters = _path_scope_clause("d.path", path_scope)
        rows, used = control.query(
            connection,
            f"""WITH expected(source_kind,generation_id) AS (VALUES {expected})
            SELECT 1 AS incomplete FROM expected e
            JOIN catalog_generation_documents d
            ON d.generation_id=e.generation_id AND d.source_kind=e.source_kind
            WHERE d.active=1 AND (
                d.source_status NOT IN ('done','complete')
                OR d.catalog_status<>'classified'
                OR lower(d.uncertainty) IN ('alta','high')
            ){path_clause} LIMIT 1""",
            (*_flatten_heads(batch), *path_parameters),
        )
        steps += used
        if rows:
            return True, steps
    return False, steps


def _catalog_term_rows(
    connection: sqlite3.Connection,
    control: _QueryControl,
    heads: Sequence[tuple[str, int]],
    term: ExactLookupTerm,
    requested: int,
    source_scope: tuple[str, ...] | None,
    path_scope: tuple[str, ...] | None,
) -> tuple[tuple[sqlite3.Row, ...], int, bool]:
    collected: list[sqlite3.Row] = []
    steps = 0
    truncated = False
    for batch in _head_batches(heads):
        remaining = requested - len(collected)
        if remaining <= 0:
            truncated = True
            break
        limit = _query_limit(control, remaining)
        expected = _expected_values(len(batch))
        if term.kind is ExactLookupKind.PATH:
            predicate = "d.path=? COLLATE NOCASE"
            term_parameters: tuple[object, ...] = (term.value,)
        elif term.kind is ExactLookupKind.NAME:
            predicate = _basename_predicate("d.path")
            term_parameters = (term.value, term.value, term.value, term.value)
        else:
            predicate = """d.catalog_status<>'error'
            AND json_valid(d.standard_references_json)
            AND EXISTS(SELECT 1 FROM json_each(d.standard_references_json) reference
            WHERE CASE WHEN reference.type='text'
            THEN CAST(reference.value AS TEXT)
            ELSE json_extract(reference.value,'$.identifier') END
            =? COLLATE NOCASE)"""
            term_parameters = (term.value,)
        source_clause, source_parameters = _value_scope_clause(
            "d.source_kind",
            source_scope,
        )
        path_clause, path_parameters = _path_scope_clause("d.path", path_scope)
        rows, used = control.query(
            connection,
            f"""WITH expected(source_kind,generation_id) AS (VALUES {expected})
            {_CATALOG_SELECT} FROM expected e
            JOIN catalog_generation_documents d
            ON d.generation_id=e.generation_id AND d.source_kind=e.source_kind
            WHERE d.active=1 AND {predicate}{source_clause}{path_clause}
            ORDER BY d.source_kind,d.path COLLATE NOCASE LIMIT ?""",
            (
                *_flatten_heads(batch),
                *term_parameters,
                *source_parameters,
                *path_parameters,
                limit,
            ),
        )
        collected.extend(rows)
        steps += used
        if len(rows) == limit and (limit < remaining or len(collected) >= requested):
            truncated = True
            break
    return tuple(collected), steps, truncated


def _catalog_row_match(
    row: sqlite3.Row,
    term: ExactLookupTerm,
    rank: int,
) -> ExactEvidenceMatch:
    identity = _catalog_identity(row)
    file_key = str(row["file_key"])
    source_kind = str(row["source_kind"])
    resource, identity_warnings = _physical_resource(
        source_kind=source_kind,
        owner="catalog",
        source_identity=file_key,
        identity=identity,
        birthtime_ns=row["birthtime_ns"],
        path=str(row["path"]),
    )
    revision = _catalog_revision(row, resource.resource_id)
    generation = int(row["generation_id"])
    if term.kind is ExactLookupKind.IDENTIFIER:
        references = _catalog_references(row["standard_references_json"])
        matched = tuple(
            reference
            for reference in references
            if reference["identifier"].casefold() == term.value.casefold()
        )
        if not matched:
            raise ValueError("catalog SQL identifier match did not decode exactly")
        identifiers: list[tuple[str, str]] = [
            ("standard_identifier", reference["identifier"]) for reference in matched
        ]
        snippet_parts = [f"identifier={matched[0]['identifier']}"]
        for key in ("authority", "evidence"):
            value = matched[0].get(key)
            if value is not None:
                snippet_parts.append(f"{key}={value}")
        evidence_method = EvidenceMethod.INFERRED
        section_kind = "catalog_standard_identifier"
        section_id = matched[0]["identifier"]
        snippet = "; ".join(snippet_parts)[:4_096]
        reason = "published catalog standard identifier matched exactly"
    elif term.kind is ExactLookupKind.NAME:
        observed_path = str(row["path"])
        identifiers = [
            ("file_name", observed_path.replace("\\", "/").rsplit("/", 1)[-1])
        ]
        evidence_method = EvidenceMethod.STRUCTURAL
        section_kind = "current_path"
        section_id = observed_path
        snippet = None
        reason = "published catalog file name matched exactly"
    else:
        observed_path = str(row["path"])
        identifiers = [("path", observed_path)]
        evidence_method = EvidenceMethod.STRUCTURAL
        section_kind = "current_path"
        section_id = observed_path
        snippet = None
        reason = "published catalog path matched exactly"
    evidence = EvidenceRef(
        _stable_exact_evidence_id(
            owner="catalog",
            resource_id=resource.resource_id,
            revision_id=revision.revision_id,
            section_kind=section_kind,
            section_id=section_id,
            identifiers=identifiers,
            extractor="document-catalog",
            generation=generation,
        ),
        resource.resource_id,
        revision.revision_id,
        evidence_method,
        section_kind=section_kind,
        section_id=section_id,
        snippet=snippet,
        extractor="document-catalog",
        extractor_version="6",
        generation=generation,
        identifiers=tuple(dict.fromkeys(identifiers)),
    )
    return ExactEvidenceMatch(
        _ranking_name("catalog", term),
        term,
        resource,
        revision,
        evidence,
        rank,
        reason,
        confidence=float(row["confidence"]),
        model_signature=str(row["classifier_signature"]),
        generation=generation,
        warnings=tuple(
            sorted(
                {
                    *identity_warnings,
                    *_catalog_quality_warnings(row),
                    *_non_ascii_case_warning(term),
                }
            )
        ),
    )


def _lookup_catalog(
    path: Path,
    owner: OwnerSnapshot,
    terms: Sequence[ExactLookupTerm],
    control: _QueryControl,
    per_term_limit: int,
    source_scope: tuple[str, ...] | None,
    path_scope: tuple[str, ...] | None,
) -> tuple[list[ExactEvidenceMatch], list[ExactOwnerReport]]:
    matches: list[ExactEvidenceMatch] = []
    reports: list[ExactOwnerReport] = []
    format_head_scope = _catalog_head_sources_for_path_scope(path_scope)
    heads = tuple(
        sorted(
            (head.scope, head.generation)
            for head in owner.publications
            if (source_scope is None or head.scope in source_scope)
            and (format_head_scope is None or head.scope in format_head_scope)
        )
    )
    try:
        connection = connect_document_catalog(path, readonly=True)
        try:
            connection.execute("BEGIN")
            valid_heads, missing_heads, preflight_steps = _catalog_valid_heads(
                connection,
                control,
                heads,
            )
            invalid_identifier_json = False
            identifier_probe_steps = 0
            identifier_coverage_incomplete = False
            identifier_quality_steps = 0
            if (
                any(term.kind is ExactLookupKind.IDENTIFIER for term in terms)
                and valid_heads
            ):
                invalid_identifier_json, identifier_probe_steps = (
                    _catalog_has_invalid_identifier_json(
                        connection,
                        control,
                        valid_heads,
                        path_scope,
                    )
                )
                identifier_coverage_incomplete, identifier_quality_steps = (
                    _catalog_identifier_coverage_incomplete(
                        connection,
                        control,
                        valid_heads,
                        path_scope,
                    )
                )
            if not heads:
                for term in terms:
                    reports.append(
                        _report(
                            "catalog",
                            term,
                            ExactLookupStatus.COMPLETE,
                            executed=True,
                            available=True,
                            sqlite_steps=preflight_steps,
                            reason="catalog_snapshot_has_no_heads",
                        )
                    )
                connection.execute("ROLLBACK")
                return matches, reports
            for term in terms:
                if not valid_heads:
                    reports.append(
                        _report(
                            "catalog",
                            term,
                            ExactLookupStatus.PARTIAL,
                            executed=True,
                            available=True,
                            sqlite_steps=preflight_steps,
                            reason="catalog_snapshot_heads_unavailable",
                        )
                    )
                    continue
                try:
                    rows, steps, truncated = _catalog_term_rows(
                        connection,
                        control,
                        valid_heads,
                        term,
                        per_term_limit + 1,
                        source_scope,
                        path_scope,
                    )
                except _WorkBudgetExceeded:
                    reports.append(
                        _report(
                            "catalog",
                            term,
                            ExactLookupStatus.PARTIAL,
                            executed=True,
                            available=True,
                            truncated=True,
                            reason="exact_work_budget_exhausted",
                        )
                    )
                    continue
                invalid_rows = 0
                term_matches: list[ExactEvidenceMatch] = []
                for row in rows:
                    control.checkpoint()
                    try:
                        term_matches.append(
                            _catalog_row_match(row, term, len(term_matches) + 1)
                        )
                    except (FileIdentityError, TypeError, ValueError):
                        invalid_rows += 1
                all_ranked = _rank_matches(term_matches)
                ranked = all_ranked[:per_term_limit]
                known_omitted = max(0, len(all_ranked) - len(ranked))
                matches.extend(ranked)
                was_truncated = truncated or known_omitted > 0
                quality_warnings = tuple(
                    sorted(
                        {
                            warning
                            for match in ranked
                            for warning in match.warnings
                            if warning.startswith("catalog_")
                        }
                    )
                )
                incomplete = bool(
                    missing_heads
                    or invalid_rows
                    or was_truncated
                    or (
                        term.kind is ExactLookupKind.IDENTIFIER
                        and invalid_identifier_json
                    )
                    or (
                        term.kind is ExactLookupKind.IDENTIFIER
                        and identifier_coverage_incomplete
                    )
                    or quality_warnings
                    or _non_ascii_case_warning(term)
                )
                reason: str | None = None
                if was_truncated:
                    reason = "exact_result_limit_reached"
                elif missing_heads:
                    reason = "catalog_snapshot_heads_partially_unavailable"
                elif invalid_rows:
                    reason = "catalog_identity_or_provenance_invalid"
                elif (
                    term.kind is ExactLookupKind.IDENTIFIER and invalid_identifier_json
                ):
                    reason = "catalog_identifier_json_invalid"
                elif quality_warnings:
                    reason = quality_warnings[0]
                elif (
                    term.kind is ExactLookupKind.IDENTIFIER
                    and identifier_coverage_incomplete
                ):
                    reason = "catalog_identifier_coverage_incomplete"
                elif _non_ascii_case_warning(term):
                    reason = "unicode_casefold_not_provable"
                warnings: list[str] = []
                if term.kind is ExactLookupKind.NAME:
                    warnings.append("catalog_has_no_basename_index")
                if term.kind is ExactLookupKind.IDENTIFIER:
                    warnings.append("catalog_has_no_standard_identifier_index")
                    if identifier_coverage_incomplete:
                        warnings.append("catalog_identifier_coverage_incomplete")
                warnings.extend(quality_warnings)
                reports.append(
                    _report(
                        "catalog",
                        term,
                        (
                            ExactLookupStatus.PARTIAL
                            if incomplete
                            else ExactLookupStatus.COMPLETE
                        ),
                        executed=True,
                        available=True,
                        returned=len(ranked),
                        rows_observed=len(rows),
                        sqlite_steps=(
                            steps
                            + preflight_steps
                            + identifier_probe_steps
                            + identifier_quality_steps
                        ),
                        truncated=was_truncated,
                        omitted_matches=known_omitted,
                        reason=reason,
                        warnings=tuple(warnings),
                    )
                )
                preflight_steps = 0
                identifier_probe_steps = 0
                identifier_quality_steps = 0
            connection.execute("ROLLBACK")
        finally:
            connection.close()
    except (sqlite3.Error, RuntimeError, OSError) as exc:
        if control.cancellation_failure is exc:
            raise
        failure_reason = (
            "exact_work_budget_exhausted"
            if isinstance(exc, _WorkBudgetExceeded)
            else f"owner_read_failed:{type(exc).__name__}"
        )
        for term in terms[len(reports) :]:
            reports.append(
                _report(
                    "catalog",
                    term,
                    ExactLookupStatus.PARTIAL,
                    executed=True,
                    available=True,
                    truncated=isinstance(exc, _WorkBudgetExceeded),
                    reason=failure_reason,
                )
            )
    return matches, reports


# endregion [06]


# region [07] Public orchestration


_OWNER_KINDS: tuple[tuple[str, frozenset[ExactLookupKind]], ...] = (
    ("inventory", _INVENTORY_KINDS),
    ("code", _CODE_KINDS),
    ("catalog", _CATALOG_KINDS),
)


def _unavailable_reports(
    owner_name: str,
    owner: OwnerSnapshot | None,
    terms: Sequence[ExactLookupTerm],
) -> list[ExactOwnerReport]:
    state = "missing_from_snapshot" if owner is None else owner.state.value
    return [
        _report(
            owner_name,
            term,
            ExactLookupStatus.UNAVAILABLE,
            executed=False,
            available=False,
            reason=f"owner_unavailable:{state}",
        )
        for term in terms
    ]


def lookup_exact(
    paths: KnowledgeStatePaths,
    snapshot: KnowledgeSnapshot,
    request: ExactLookupRequest,
    *,
    cancellation_check: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] | None = None,
) -> ExactLookupResult:
    """Resolve typed exact terms without creating or mutating owner state."""

    control = _QueryControl(
        request.max_observed_rows,
        request.max_sqlite_steps,
        cancellation_check,
    )
    control.checkpoint()
    matches: list[ExactEvidenceMatch] = []
    reports: list[ExactOwnerReport] = []
    owner_timings: list[ExactOwnerTiming] = []
    clock = clock_ns or time.perf_counter_ns
    inventory_path_scope = _inventory_path_scope(
        request.source_kinds,
        request.formats,
    )
    code_path_scope = _code_path_scope(request.source_kinds, request.formats)
    catalog_source_scope, catalog_path_scope = _catalog_row_scopes(
        request.source_kinds,
        request.formats,
    )

    serial_terms = tuple(
        term for term in request.terms if term.kind is ExactLookupKind.SERIAL
    )
    for term in serial_terms:
        reports.append(
            _report(
                "knowledge",
                term,
                ExactLookupStatus.UNSUPPORTED,
                executed=False,
                available=False,
                reason="serial_field_not_contractual_in_phase1_owners",
            )
        )

    selected_owners = frozenset(request.owner_scope)
    for term in request.terms:
        if term.kind is ExactLookupKind.SERIAL:
            continue
        applicable = any(
            owner_name in selected_owners and term.kind in supported_kinds
            for owner_name, supported_kinds in _OWNER_KINDS
        )
        if not applicable:
            reports.append(
                _report(
                    "knowledge",
                    term,
                    ExactLookupStatus.UNSUPPORTED,
                    executed=False,
                    available=False,
                    reason="exact_term_has_no_owner_in_requested_scope",
                )
            )

    for owner_name, supported_kinds in _OWNER_KINDS:
        if owner_name not in selected_owners:
            continue
        terms = tuple(term for term in request.terms if term.kind in supported_kinds)
        if not terms:
            continue
        started_ns = clock()
        owner = _snapshot_owner(snapshot, owner_name)
        if owner is None or owner.state is not OwnerAvailability.AVAILABLE:
            owner_reports = _unavailable_reports(owner_name, owner, terms)
            reports.extend(owner_reports)
            owner_timings.append(
                ExactOwnerTiming(
                    owner_name,
                    tuple(report.name for report in owner_reports),
                    _duration_ns(clock, started_ns),
                    False,
                )
            )
            continue
        if control.remaining_rows <= 0 or (
            control.remaining_steps < SQLITE_PROGRESS_INTERVAL
        ):
            owner_reports = [
                _report(
                    owner_name,
                    term,
                    ExactLookupStatus.PARTIAL,
                    executed=False,
                    available=True,
                    truncated=True,
                    reason="exact_global_work_budget_exhausted",
                )
                for term in terms
            ]
            reports.extend(owner_reports)
            owner_timings.append(
                ExactOwnerTiming(
                    owner_name,
                    tuple(report.name for report in owner_reports),
                    _duration_ns(clock, started_ns),
                    False,
                )
            )
            continue
        if owner_name == "inventory":
            owner_matches, owner_reports = _lookup_inventory(
                paths.inventory,
                owner,
                terms,
                control,
                request.limit,
                inventory_path_scope,
            )
        elif owner_name == "code":
            owner_matches, owner_reports = _lookup_code(
                paths.code,
                owner,
                terms,
                control,
                request.limit,
                code_path_scope,
            )
        else:
            owner_matches, owner_reports = _lookup_catalog(
                paths.catalog,
                owner,
                terms,
                control,
                request.limit,
                catalog_source_scope,
                catalog_path_scope,
            )
        matches.extend(owner_matches)
        reports.extend(owner_reports)
        owner_timings.append(
            ExactOwnerTiming(
                owner_name,
                tuple(report.name for report in owner_reports),
                _duration_ns(clock, started_ns),
                any(report.executed for report in owner_reports),
            )
        )

    control.checkpoint()
    term_order = {term.term_id: index for index, term in enumerate(request.terms)}
    owner_order = {name: index for index, (name, _) in enumerate(_OWNER_KINDS)}
    ordered_matches = sorted(
        matches,
        key=lambda match: (
            term_order[match.term.term_id],
            owner_order.get(match.resource.owner, len(owner_order)),
            match.source_rank,
            match.evidence.evidence_id,
        ),
    )
    visible_matches = tuple(ordered_matches[: request.limit])
    locally_omitted = sum(report.omitted_matches for report in reports)
    omitted = locally_omitted + max(
        0,
        len(ordered_matches) - len(visible_matches),
    )
    global_truncated = omitted > 0 or any(report.truncated for report in reports)
    warnings: set[str] = set()
    if snapshot.consistency is not SnapshotConsistency.STABLE:
        warnings.add("knowledge_snapshot_not_stable")
    if omitted:
        warnings.add("exact_global_result_limit_reached")
    complete = (
        snapshot.consistency is SnapshotConsistency.STABLE
        and not global_truncated
        and bool(reports)
        and all(report.complete for report in reports)
    )
    return ExactLookupResult(
        snapshot.snapshot_id,
        visible_matches,
        tuple(reports),
        complete,
        global_truncated,
        omitted,
        request.max_observed_rows - control.remaining_rows,
        request.max_sqlite_steps - control.remaining_steps,
        tuple(sorted(warnings)),
        tuple(owner_timings),
    )


def lookup_plan_exact(
    paths: KnowledgeStatePaths,
    plan: KnowledgePlan,
    snapshot: KnowledgeSnapshot,
    *,
    candidate_limit: int | None = None,
    cancellation_check: Callable[[], None] | None = None,
    max_observed_rows: int = 4_096,
    max_sqlite_steps: int = DEFAULT_EXACT_SQLITE_STEPS,
    clock_ns: Callable[[], int] | None = None,
) -> ExactLookupResult | None:
    """Convenience boundary for the existing string-based Knowledge plan.

    ``None`` means that the plan contains no exact term and therefore no exact
    ranking was requested.  Unsupported typed terms remain explicit reports.
    """

    terms = classify_plan_exact_terms(plan)
    if not terms:
        return None
    effective_limit = plan.limit if candidate_limit is None else candidate_limit
    return lookup_exact(
        paths,
        snapshot,
        ExactLookupRequest(
            terms,
            limit=effective_limit,
            max_observed_rows=max(max_observed_rows, effective_limit),
            max_sqlite_steps=max_sqlite_steps,
            owner_scope=_plan_exact_owner_scope(plan),
            source_kinds=plan.source_kinds,
            formats=plan.formats,
        ),
        cancellation_check=cancellation_check,
        clock_ns=clock_ns,
    )


# endregion [07]
