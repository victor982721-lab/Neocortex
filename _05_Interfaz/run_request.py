"""Validated UI execution requests translated into the stable CLI contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# region [01] Request schema

# The desktop UI intentionally exposes this bounded subset. Always serialize it
# explicitly: the canonical CLI may register additional routes such as ``code``.
ROUTE_ORDER = ("pdf", "docx", "office", "audio", "image")


@dataclass(frozen=True, slots=True)
class RunRequest:
    """One immutable execution request produced by the desktop UI."""

    root: Path
    routes: tuple[str, ...]
    apply: bool = False
    route_only: bool = False

    def validated(self) -> "RunRequest":
        root = self.root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"La raíz no es un directorio accesible: {root}")
        unknown = tuple(route for route in self.routes if route not in ROUTE_ORDER)
        if unknown:
            raise ValueError("Rutas desconocidas: " + ", ".join(unknown))
        normalized_routes = tuple(
            route for route in ROUTE_ORDER if route in frozenset(self.routes)
        )
        if self.route_only and not normalized_routes:
            raise ValueError("La ejecución aislada requiere al menos una ruta")
        if self.route_only and self.apply:
            raise ValueError(
                "La ejecución aislada es siempre no destructiva; desactiva Apply"
            )
        return RunRequest(
            root=root,
            routes=normalized_routes,
            apply=self.apply,
            route_only=self.route_only,
        )

    def cli_arguments(self) -> list[str]:
        request = self.validated()
        arguments = [
            "--root",
            str(request.root),
        ]
        selected = ",".join(request.routes) or "none"
        arguments.extend(("--route", selected))
        if request.route_only:
            arguments.append("--route-only")
        if request.apply:
            arguments.append("--apply")
        return arguments


# endregion [01]
