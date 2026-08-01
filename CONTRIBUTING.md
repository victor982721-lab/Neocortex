# Contribuir a NeoCortex

NeoCortex protege corpus, estado persistente y operaciones de release mediante
contratos fail-closed. La disciplina del repositorio debe mantener el mismo
nivel de rigor: ningún cambio se considera aceptado sólo porque compile o porque
una métrica histórica haya sido verde.

## Flujo de cambios

1. Cree una rama corta desde `main`; no trabaje directamente sobre la rama
   protegida.
2. Mantenga cada pull request cohesionado y describa explícitamente qué contratos
   toca y cuáles no.
3. Use fixtures y raíces de laboratorio. No abra, migre ni procese el corpus o el
   estado operativo para validar un cambio.
4. Espere los tres checks de GitHub Actions: `static`, `tests` y `artifacts`.
5. Revise el diff final, los resultados de CI y cualquier cambio de artefacto
   antes de fusionar.

Los cambios puramente mecánicos que modifiquen archivos Python también cambian
el árbol certificado. Deben ejecutar otra vez las barreras aplicables; no pueden
heredar automáticamente los resultados de un commit anterior.

## Barreras automatizadas

El workflow `Windows quality gates` ejecuta sobre Windows y CPython 3.13:

- instalación con `constraints.txt` y `pip check`;
- Ruff lint y formato;
- mypy sobre las fuentes canónicas;
- suite completa de pytest con cobertura de ramas y umbral combinado de 83 %;
- laboratorio NTFS aislado fuera del repositorio y del estado operativo;
- dos builds independientes de wheel y sdist;
- validación de artefactos, igualdad de payload lógico, wheel byte-idéntico y
  publicación canónica del sdist.

Una barrera roja no debe omitirse ni convertirse en warning para cerrar una
entrega. Corrija la causa o documente el bloqueo sin presentar el cambio como
completo.

## Configuración recomendada de `main`

Después de fusionar el workflow por primera vez, configure una ruleset de
GitHub para `main` con estas condiciones:

- exigir pull request antes de fusionar;
- exigir los checks `static`, `tests` y `artifacts`;
- exigir que la rama esté actualizada con `main`;
- bloquear force-push y eliminación de la rama;
- resolver todas las conversaciones antes de fusionar;
- no permitir bypass automático por aplicaciones.

Mientras exista un solo mantenedor, no es necesario exigir una aprobación de
otra persona; la revisión del diff y los checks sí son obligatorios. Cuando haya
más mantenedores, añada al menos una aprobación independiente para cambios de
persistencia, mutación, release o seguridad.

## Dependencias

Las dependencias directas de cada extra se declaran en `pyproject.toml` y sus
versiones validadas se fijan en `constraints.txt`. Toda dependencia importada por
la suite debe pertenecer al extra `dev`; no dependa de paquetes instalados de
forma accidental en una máquina concreta.

Las transitivas continúan bajo el resolver de sus dependencias propietarias. Una
campaña de release debe resolverlas en un entorno limpio, conservar el inventario
efectivo y ejecutar `pip check`. Un lock multiplataforma sólo debe introducirse
cuando se genere y valide específicamente para Windows y CPython 3.13.

## Cambios sensibles

Para schemas, migraciones, acciones de filesystem, ACL, identidad física,
recuperación, publicación generacional o release:

- describa el invariante exacto que cambia;
- agregue una regresión focal y pruebas de fallo;
- preserve abstención, CAS, recibos, guards y rollback;
- pruebe con datos sintéticos dentro del laboratorio;
- documente cualquier artefacto que deba reconstruirse;
- no promueva launchers ni modifique PATH, corpus o estado desde CI.

## Licencia y distribución

El repositorio no declara todavía una licencia propia. No publique ni redistribuya
NeoCortex como software abierto hasta que el propietario elija explícitamente la
licencia y la política de `NOTICE`. El inventario de terceros no concede por sí
mismo derechos sobre el código de NeoCortex.
