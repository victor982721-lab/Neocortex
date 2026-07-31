"""Bounded Rust lexical analyzer with explicit non-parser provenance."""

from __future__ import annotations

import re
from pathlib import Path

from .code_analyzer_common import (
    SourceMap,
    comparison_fingerprints,
    searchable_chunks,
)
from .code_contracts import (
    AnalysisStatus,
    CodeAnalysis,
    CodeFileInput,
    CodeRouteConfig,
    DependencyRecord,
    DiagnosticRecord,
    DiagnosticSeverity,
    MetricRecord,
    ReferenceRecord,
    SymbolRecord,
)


# region [01] Bounded lexical patterns


_ITEM = re.compile(
    r"(?m)^[ \t]*(?P<visibility>pub(?:\([^\n)]*\))?\s+)?"
    r"(?P<prefix>(?:(?:async|unsafe|const|extern(?:\s+\"[^\"]+\")?)\s+)*)"
    r"(?P<kind>fn|struct|enum|trait|type|mod|union|static|const)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_IMPL = re.compile(
    r"(?m)^[ \t]*(?P<unsafe>unsafe\s+)?impl(?:\s*<[^\n{>]*>)?\s+"
    r"(?:(?P<trait>[A-Za-z_][\w:]*)\s+for\s+)?(?P<target>[A-Za-z_][\w:]*)"
)
_MACRO_RULES = re.compile(r"(?m)^[ \t]*(?:pub\s+)?macro_rules!\s*(?P<name>\w+)")
_USE = re.compile(r"(?m)^[ \t]*(?:pub\s+)?use\s+(?P<target>[^;\n]+)")
_MOD = re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>\w+)\s*;")
_CALL = re.compile(r"(?<![\w:])(?P<name>[A-Za-z_][\w:]*)\s*(?P<macro>!)?\s*\(")
_CONTROL_CALLS = frozenset({"if", "while", "for", "match", "loop", "return", "Some", "Ok", "Err"})
_BRANCH = re.compile(r"\b(?:if|for|while|match)\b|&&|\|\||\?")


def _matching_brace(text: str, start: int) -> int:
    """Return the first balanced closing brace after a declaration header."""

    brace = text.find("{", start, min(len(text), start + 16_384))
    if brace < 0:
        line_end = text.find("\n", start)
        return len(text) if line_end < 0 else line_end
    depth = 0
    quote: str | None = None
    escaped = False
    index = brace
    while index < len(text):
        character = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _delimiter_diagnostics(source_map: SourceMap) -> tuple[DiagnosticRecord, ...]:
    stack: list[tuple[str, int]] = []
    pairs = {')': '(', ']': '[', '}': '{'}
    quote: str | None = None
    escaped = False
    for index, character in enumerate(source_map.text):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in "([{":
            stack.append((character, index))
        elif character in ")]}":
            if not stack or stack[-1][0] != pairs[character]:
                return (
                    DiagnosticRecord(
                        source="neocortex-rust-lexical",
                        code="rust_unbalanced_delimiter",
                        severity=DiagnosticSeverity.ERROR,
                        message=f"unmatched closing delimiter {character!r}",
                        source_range=source_map.offset_range(index, index + 1),
                        tool_name="rust-lexical",
                        tool_version="1",
                        confirmed=True,
                        metadata={"scope": "lexical-only"},
                    ),
                )
            stack.pop()
    if stack:
        character, index = stack[-1]
        return (
            DiagnosticRecord(
                source="neocortex-rust-lexical",
                code="rust_unclosed_delimiter",
                severity=DiagnosticSeverity.ERROR,
                message=f"unclosed delimiter {character!r}",
                source_range=source_map.offset_range(index, index + 1),
                tool_name="rust-lexical",
                tool_version="1",
                confirmed=True,
                metadata={"scope": "lexical-only"},
            ),
        )
    return ()


# endregion [01]


# region [02] Analyzer


class RustAnalyzer:
    """Extract Rust items without pretending that regex evidence is an AST."""

    analyzer_id = "neocortex-rust-lexical"
    analyzer_version = "1"
    languages = frozenset({"rust"})

    def analyze(self, source: CodeFileInput, config: CodeRouteConfig) -> CodeAnalysis:
        text = source.text
        source_map = SourceMap.build(text)
        module = Path(source.snapshot.path).stem
        symbols: list[SymbolRecord] = []
        references: list[ReferenceRecord] = []
        dependencies: list[DependencyRecord] = []
        metrics: list[MetricRecord] = []
        diagnostics = list(_delimiter_diagnostics(source_map))

        impl_spans: list[tuple[int, int, str, str | None]] = []
        for match in _IMPL.finditer(text):
            end = _matching_brace(text, match.start())
            target = match.group("target")
            trait = match.group("trait")
            impl_spans.append((match.start(), end, target, trait))
            name = f"impl {trait + ' for ' if trait else ''}{target}"
            symbols.append(
                SymbolRecord(
                    kind="impl",
                    name=name,
                    qualified_name=f"{module}.{name}",
                    signature=match.group(0).strip(),
                    source_range=source_map.offset_range(match.start(), end),
                    visibility=None,
                    confirmed=False,
                    metadata={"trait": trait, "target": target, "evidence": "lexical"},
                )
            )
            if trait:
                references.append(
                    ReferenceRecord(
                        "implements_trait",
                        trait,
                        source_map.offset_range(match.start(), match.end()),
                        f"{module}.{name}",
                        trait,
                        False,
                        0.8,
                        "rust-lexical:impl-header",
                    )
                )

        for match in _ITEM.finditer(text):
            kind = match.group("kind")
            name = match.group("name")
            end = _matching_brace(text, match.start())
            containing_impl = next(
                (
                    item
                    for item in impl_spans
                    if item[0] < match.start() < item[1]
                ),
                None,
            )
            symbol_kind = "method" if kind == "fn" and containing_impl else kind
            parent = None
            qualified = f"{module}.{name}"
            if containing_impl is not None:
                parent_name = f"impl {containing_impl[2]}"
                parent = f"{module}.{parent_name}"
                qualified = f"{parent}.{name}"
            signature_end = text.find("{", match.end(), min(len(text), match.end() + 8192))
            if signature_end < 0 or signature_end > end:
                signature_end = min(end, text.find("\n", match.end()) if "\n" in text[match.end():] else end)
            signature = text[match.start() : signature_end].strip()[:4096]
            complexity = None
            if kind == "fn":
                complexity = 1 + len(_BRANCH.findall(text[match.end() : end]))
                metrics.append(
                    MetricRecord(
                        "lexical_complexity",
                        complexity,
                        qualified,
                        False,
                        "rust-lexical-v1",
                    )
                )
                if complexity >= config.complexity_warning:
                    diagnostics.append(
                        DiagnosticRecord(
                            source=self.analyzer_id,
                            code="high_complexity_inferred",
                            severity=DiagnosticSeverity.WARNING,
                            message=f"{qualified} has lexical complexity {complexity}",
                            source_range=source_map.offset_range(match.start(), end),
                            tool_name="rust-lexical",
                            tool_version=self.analyzer_version,
                            confirmed=False,
                            confidence=0.65,
                            metadata={"threshold": config.complexity_warning},
                        )
                    )
            symbols.append(
                SymbolRecord(
                    kind=symbol_kind,
                    name=name,
                    qualified_name=qualified,
                    signature=signature,
                    source_range=source_map.offset_range(match.start(), end),
                    parent_qualified_name=parent,
                    visibility="public" if match.group("visibility") else "private",
                    confirmed=False,
                    complexity=complexity,
                    metadata={
                        "prefix": match.group("prefix").strip(),
                        "evidence": "rust-lexical-item",
                    },
                )
            )

        for match in _MACRO_RULES.finditer(text):
            name = match.group("name")
            end = _matching_brace(text, match.start())
            symbols.append(
                SymbolRecord(
                    "macro",
                    name,
                    f"{module}.{name}",
                    f"macro_rules! {name}",
                    source_map.offset_range(match.start(), end),
                    visibility="private",
                    confirmed=False,
                    metadata={"evidence": "rust-lexical-macro"},
                )
            )

        for match in _USE.finditer(text):
            target = match.group("target").strip()
            root = target.split("::", 1)[0].lstrip(":")
            source_range = source_map.offset_range(match.start(), match.end())
            references.append(
                ReferenceRecord(
                    "import",
                    target,
                    source_range,
                    module,
                    target,
                    False,
                    0.9,
                    "rust-lexical:use",
                )
            )
            if root not in {"crate", "self", "super", "std", "core", "alloc"}:
                dependencies.append(
                    DependencyRecord(
                        root,
                        "rust_use",
                        source_range=source_range,
                        confirmed=False,
                        confidence=0.8,
                        evidence="rust-lexical:use-root",
                    )
                )
        for match in _MOD.finditer(text):
            references.append(
                ReferenceRecord(
                    "module_declaration",
                    match.group("name"),
                    source_map.offset_range(match.start(), match.end()),
                    module,
                    match.group("name"),
                    False,
                    0.9,
                    "rust-lexical:mod",
                )
            )
        definition_starts = {item.source_range.start_byte for item in symbols}
        for match in _CALL.finditer(text):
            name = match.group("name")
            if name in _CONTROL_CALLS:
                continue
            source_range = source_map.offset_range(match.start(), match.end())
            if source_range.start_byte in definition_starts:
                continue
            references.append(
                ReferenceRecord(
                    "macro_call" if match.group("macro") else "call",
                    name,
                    source_range,
                    module,
                    name,
                    False,
                    0.6,
                    "rust-lexical:call-shape",
                )
            )

        structure = "\n".join(
            f"{item.kind}\0{item.qualified_name}\0{item.signature or ''}"
            for item in symbols
        )
        fingerprints = comparison_fingerprints(
            source.raw_bytes,
            source.text,
            "rust",
            structure=structure,
        )
        metrics.extend(
            (
                MetricRecord("line_count", len(source_map.lines), provenance="rust-lexical"),
                MetricRecord("symbol_count", len(symbols), provenance="rust-lexical"),
                MetricRecord("reference_count", len(references), provenance="rust-lexical"),
            )
        )
        return CodeAnalysis(
            input=source,
            status=(AnalysisStatus.PARTIAL if diagnostics else AnalysisStatus.TEXT_ONLY),
            analyzer_id=self.analyzer_id,
            analyzer_version=self.analyzer_version,
            parser_kind="rust-lexical-fallback",
            symbols=tuple(symbols),
            references=tuple(references),
            dependencies=tuple(dependencies),
            diagnostics=tuple(diagnostics),
            metrics=tuple(metrics),
            chunks=searchable_chunks(source.text, config.chunk_chars),
            provenance={
                "parser": None,
                "lexical_analyzer": self.analyzer_version,
                "syntax_confirmed": False,
                "cargo_tools_executed": False,
            },
            **fingerprints,
        )


# endregion [02]


__all__ = ["RustAnalyzer"]
