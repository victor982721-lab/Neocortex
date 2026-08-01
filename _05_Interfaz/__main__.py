"""Allow development launches with ``python -m _05_Interfaz``."""
# region [00] Contexto del módulo
# Módulo: _05_Interfaz/__main__.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from .app import main
# endregion [01]

# region [02] Implementación


if __name__ == "__main__":
    raise SystemExit(main())
# endregion [02]
