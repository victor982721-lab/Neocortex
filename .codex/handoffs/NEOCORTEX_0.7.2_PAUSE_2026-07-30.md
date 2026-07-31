# Neocortex 0.7.2 — handoff operativo actualizado

> **Checkpoint autoritativo: 2026-07-31, cierre diferido por cuota semanal.**
> Esta actualización sustituye operativamente las secciones históricas del
> handoff original que aparece después del separador “Apéndice histórico”. No
> retomar CL6b, CL7, P2.5 ni W2: esas fases ya terminaron. La única tarea abierta
> es corregir y validar una canonicalización ACL de Windows, reconstruir el sdist
> y cerrar promoción/rollback/PATH sin abrir el state vivo.

## A. Estado ejecutivo actual

- Repositorio: `C:\Users\Victor\Neocortex\Repository`.
- Branch: `neocortex-0.7.2-work`.
- HEAD de fuente y artefactos aceptados antes de este handoff:
  `5cce0cf1ac43de9859f2d51dd836fe5a0493b206`.
- Estado Git al capturar este checkpoint: limpio.
- Implementación funcional 0.7.2: completa.
- Barreras de código, suite, cobertura, estática, packaging e instalación aislada:
  completas antes de la incidencia ACL descrita abajo.
- Runtime vivo versionado 0.7.2 `[full]`: instalado y válido.
- Launcher estable: continúa deliberadamente en 0.7.1; nunca fue reemplazado.
- Promoción/rollback/re-promoción: pendientes.
- PATH de usuario: pendiente; la ruta `bin` aparece cero veces.
- State vivo y corpus: no abiertos, migrados, reprocesados ni modificados.
- No hay intents de release pendientes: los tres intentos fallidos quedaron cerrados
  como `no_effect`, `performed=false`, `recovered=true`.
- El parche ACL propuesto **no se aplicó**. Git estaba limpio al iniciar la edición
  de este handoff.

## B. Autorizaciones y límite exacto

Victor autorizó explícitamente estas cuatro operaciones:

1. crear/instalar el runtime versionado bajo
   `%LOCALAPPDATA%\Programs\Neocortex\versions`;
2. instalar el extra `[full]` con `uv`;
3. promover, probar rollback a 0.7.1 y re-promover 0.7.2;
4. añadir `%LOCALAPPDATA%\Programs\Neocortex\bin` al PATH de usuario.

Las dos primeras ya terminaron. También autorizó normalizar únicamente el
propietario del launcher 0.7.1 a `SYNAPSIS\Victor`; esa normalización terminó y
conservó bytes, grupo y DACL.

Falta una autorización nueva y específica: modificar la validación NTFS para
canonicalizar sólo `SE_DACL_AUTO_INHERITED` (`0x0400`). El revisor bloqueó la
aplicación del parche hasta obtener esa autorización después de explicar el
riesgo. Victor eligió crear este handoff y continuar cuando se restablezca la
cuota. La siguiente sesión debe pedir esa autorización; no debe inferirla.

## C. Evidencia de implementación y barreras ya completadas

- Suite completa con cobertura: `3063 passed, 89 subtests passed` en 1271.67 s.
- Cobertura branch-aware: 41,829 statements; 36,354 líneas cubiertas;
  83.408 % combinado; 13,168 branches; 9,518 cubiertos; 72.281 % branch.
- Freeze exacto de HEAD: `3063 passed, 89 subtests passed` en 808.77 s.
- Ruff check global: verde.
- Ruff format: los 24 archivos Python cambiados por la campaña conformes; el árbol
  conserva 62 archivos legacy que Ruff reformatearía y no fueron alterados.
- mypy: 252 fuentes, cero errores.
- compileall y `uv pip check` del entorno dev: verdes.
- Matriz focal de artefactos después de los últimos ajustes: 118 pruebas verdes.
- Fases completadas: P2.5, W2 parent guards, versión 0.7.2, documentación y
  contratos de release/artifact policy.

Commits terminales relevantes antes del handoff:

- `55aad32` — `fix: retain NTFS release parent guards`
- `22251e4` — `release: declare Neocortex 0.7.2`
- `49ba592` — `fix: restore release barrier contracts`
- `be94413` — `fix: distinguish protected action domains`
- `cdc198e` — `style: format packaging contract tests`
- `95c8922` — `fix: align release manifest with artifact policy`
- `7524c08` — `fix: make artifact validation source-aware`
- `5cce0cf` — `fix: prune internal sources from sdist`

## D. Artefactos aceptados antes del parche ACL

Directorio:
`C:\Users\Victor\Neocortex\Laboratory\release-0.7.2-20260731-04\final`

- Wheel `neocortex_framework-0.7.2-py3-none-any.whl`
  - SHA-256: `AD38C60C97E75ED870700C617FFF9B6058BE21BE94C985AC3C5530B5E76556F9`
  - dos builds byte-idénticos;
  - 264 miembros;
  - payload lógico SHA-256:
    `1081636cd6de8500d57e3e1d1ff83952e516822bcd82385785555e00751ef0f7`.
- Sdist `neocortex_framework-0.7.2.tar.gz`
  - SHA-256: `A751615E8BCCCB48E365B9464825147D71A34B85E5DCC9AE97F375CD9B1C1176`
  - dos payloads lógicos independientes idénticos y publicación canonical;
  - 291 miembros;
  - payload lógico SHA-256:
    `09d2dbad549a50a39e4994169fd7f7f262a93ccb5b9b64286d03a2bafb49fbd4`.

El wheel probablemente no cambiará con el parche porque `tools/` no forma parte
del wheel. El sdist **sí debe reconstruirse** porque `MANIFEST.in` contiene
`recursive-include tools release_*.py`. No declarar finales los hashes anteriores
después del parche sin reconstrucción y validación reproducible.

## E. Runtime vivo 0.7.2 ya instalado

- Runtime ID:
  `0.7.2-wheel-xxh3_128-7bf6b6ae7b480bdcccc946df711cf014`.
- Raíz:
  `C:\Users\Victor\AppData\Local\Programs\Neocortex\versions\0.7.2-wheel-xxh3_128-7bf6b6ae7b480bdcccc946df711cf014`.
- Venv: subdirectorio `venv`.
- Instalación: wheel 0.7.2 con `[full]` y `constraints.txt` mediante `uv`.
- Paquetes instalados verificados: 52.
- `uv pip check`: verde.
- `Neocortex --version`: `Neocortex 0.7.2`.
- `--help`: exit 0.
- Probe Knowledge de estado ausente: exit 0 y no creó state.
- Launcher del runtime y copia inmutable en `bin`:
  SHA-256 `87503D628E10BA20C6F6D6021AC7E73AA85E2EA80736E2F5D08663D48F44D142`,
  longitud 46,080 bytes.

Copia inmutable autorizada para promoción:
`C:\Users\Victor\AppData\Local\Programs\Neocortex\bin\Neocortex-0.7.2-7bf6b6ae7b480bdcccc946df711cf014.exe`.

## F. Launcher estable y normalización ya ejecutada

- Ruta: `C:\Users\Victor\AppData\Local\Programs\Neocortex\bin\Neocortex.exe`.
- Versión actual: `Neocortex 0.7.1`.
- SHA-256 actual:
  `1D4FC0C654ACF0B34D300ABEC99839C5D263B44F05AA499947F44B12215716B1`.
- Propietario anterior: SID huérfano
  `S-1-5-21-770980993-4136550973-192376083-1003`.
- Propietario actual: `SYNAPSIS\Victor`.
- La normalización fue autorizada y verificada: hash, bytes, grupo y DACL no
  cambiaron; sólo cambió el propietario.

## G. Tres intentos fallidos, todos sin efecto

No se invocó con éxito `ReplaceFileW` en ninguno. El launcher estable permaneció
0.7.1 con el mismo hash. No quedan intents pendientes ni backups materiales.

1. `f4d42a79bde26228deac1278dc7ff4b5df526e3de57b67a056ea164705a860a7`
   - causa: `WinError 1307`; el SID propietario huérfano no podía reasignarse al
     backup;
   - result SHA-256:
     `21BAD5E3EB6A8D117AAD1D9A84EC3383904790E7AE0C5B9498B6C8154EFB6069`;
   - `no_effect`, `performed=false`, `recovered=true`.
2. `5f8cf23ddd5f2e422edb30d3d3f1a38fbba21771d783d77cfcdb88cdaceae75c`
   - causa: después de normalizar owner, el backup bajo
     `%LOCALAPPDATA%\Neocortex\release\backups` heredaba una DACL distinta de
     `bin`; la verificación exacta rechazó la copia;
   - result SHA-256:
     `6A9BB4CC3070D673D88BBD34E53281061FAFA3CACB99C32E9049E7CAEAD8870F`;
   - `no_effect`, `performed=false`, `recovered=true`.
3. `e0a181355fed34802d6b62142fcc5decd36a22befafafdd8a03ace130eb1b334`
   - se creó el directorio externo ACL-compatible
     `C:\Users\Victor\AppData\Local\Programs\Neocortex\release-backups`, hermano
     de `bin`, y se verificó que su DACL coincide con la de `bin`;
   - aun así, Windows eliminó un flag informativo al reaplicar el descriptor y la
     comparación binaria volvió a rechazar la copia;
   - result SHA-256:
     `9CF52EC4B5916BB986F35693D4499E6A613980F2F8B8592863D0E23821EC5E75`;
   - `no_effect`, `performed=false`, `recovered=true`.

Recibos:
`C:\Users\Victor\AppData\Local\Neocortex\release\receipts`.

Lock:
`C:\Users\Victor\AppData\Local\Neocortex\release\locks\launcher.lock`.

Directorio de backup que debe usarse al reanudar:
`C:\Users\Victor\AppData\Local\Programs\Neocortex\release-backups`.

El directorio anterior `%LOCALAPPDATA%\Neocortex\release\backups` existe y está
vacío. No borrarlo por rutina.

## H. Diagnóstico ACL concluyente

Una sonda temporal creada mediante el mismo adaptador fue eliminada por identidad
antes de terminar. Comparó el descriptor del launcher con el descriptor de la copia:

- tamaño total: 680 bytes en ambos;
- propietario: mismo SHA-256
  `b5d94797172bcdb7279adb35fa56fc3f7f299598867f036ab3ef822c6fa593f2`;
- grupo: mismo SHA-256
  `91f6d7a81e653166da4381a220df27bef5cdbc443b49ef953036471517e675c1`;
- DACL: 604 bytes y mismo SHA-256
  `9232bf378f2bee03221c61e066a8f30c5280910dd51f2385f961a71ac4fdf10e`;
- SACL: ausente en ambos;
- control origen: 33796 (`0x8404`);
- control copia: 32772 (`0x8004`);
- única diferencia: `0x0400`, `SE_DACL_AUTO_INHERITED`.

Windows no conserva ese bit al aplicar mediante `SetKernelObjectSecurity` un
descriptor con owner, group y DACL idénticos. Es metadata derivada de herencia,
no un ACE ni un permiso. La comparación actual en
`tools/release_windows_ntfs.py::_finish_copy` compara bytes crudos y produce un
falso negativo.

## I. Parche exacto pendiente de autorización

Archivos previstos, todavía sin cambios:

- `tools/release_windows_ntfs_native.py`
- `tests/test_release_windows_ntfs.py`

Cambio mínimo previsto:

1. declarar `_SE_DACL_AUTO_INHERITED = 0x0400` y tamaño de header autorrelativo 20;
2. añadir `_canonical_security_descriptor(descriptor: bytes) -> bytes` que:
   - rechace descriptores menores de 20 bytes;
   - copie el buffer;
   - lea `control` little-endian en bytes 2:4;
   - limpie **sólo** `0x0400`;
   - preserve todos los demás bytes;
3. hacer que `_security_descriptor()` retorne el descriptor canonicalizado;
4. añadir dos pruebas:
   - sólo cambia `0x0400`, preserva el resto e idempotencia;
   - un header de 19 bytes se rechaza.

No limpiar `SE_DACL_PROTECTED`, `SE_DACL_AUTO_INHERIT_REQ`, owner, group, SACL,
DACL ni ningún otro flag. No relajar `_safe_source`, CAS, identidad física,
hashes de contenido, parent guards, recibos ni `ReplaceFileW`.

El intento de aplicar este parche fue rechazado por el revisor **antes de editar**.
No hay hunk parcial, `.rej`, `.orig` ni archivo modificado por ese intento.

## J. Secuencia obligatoria al reanudar

1. Leer esta actualización completa y comprobar en vivo:

   ```powershell
   Set-Location -LiteralPath 'C:\Users\Victor\Neocortex\Repository'
   git status --short --branch
   git rev-parse HEAD
   & 'C:\Users\Victor\AppData\Local\Programs\Neocortex\bin\Neocortex.exe' --version
   ```

2. Pedir autorización explícita a Victor para el parche exacto de la sección I.
3. Aplicar sólo ese parche, revisar `git diff` y `git diff --check`.
4. Ejecutar primero las dos regresiones focales. Para la suite NTFS completa,
   crear un laboratorio nuevo bajo `C:\Users\Victor\Neocortex\Laboratory`, con
   subdirectorios precreados para `TEMP`, `TMP`, `TMPDIR`,
   `PYTHONPYCACHEPREFIX`, pytest basetemp y cache, y definir
   `NEOCORTEX_AUDIT_LAB_ROOT` a esa raíz. No reutilizar state vivo.
5. Ejecutar como mínimo:

   ```powershell
   py -3 -B -m pytest -q tests/test_release_windows_ntfs.py -k 'security_descriptor_canonicalization'
   py -3 -B -m pytest -q tests/test_release_windows.py tests/test_release_windows_ntfs.py tests/test_release_artifacts.py
   py -3 -B -m ruff check tools/release_windows_ntfs_native.py tests/test_release_windows_ntfs.py
   py -3 -B -m ruff format --check tools/release_windows_ntfs_native.py tests/test_release_windows_ntfs.py
   py -3 -B -m mypy tools/release_windows_ntfs_native.py
   ```

6. Ejecutar de nuevo la barrera completa/freeze porque cambia fuente de release.
7. Crear un commit cohesionado del fix ACL.
8. Reconstruir dos wheels y dos sdists independientes desde el nuevo HEAD usando
   `py -3 -m build --no-isolation --outdir <directorio> .`; validar cada artefacto
   con `tools.release_artifacts.validate_release_artifact`, comparar payloads con
   `compare_logical_payloads` y publicar el sdist mediante `canonicalize_sdist`.
   Usar una raíz nueva de Laboratory; no sobrescribir el build aceptado `-04`.
9. Confirmar que el wheel nuevo es byte/payload idéntico al instalado. Si cambia,
   no mutar el runtime existente: crear otro runtime versionado e instalar `[full]`
   con `uv` y `constraints.txt`. Si no cambia, conservar el runtime actual.
10. Confirmar cero intents pendientes y reanudar la cadena desde:
    - result path:
      `C:\Users\Victor\AppData\Local\Neocortex\release\receipts\e0a181355fed34802d6b62142fcc5decd36a22befafafdd8a03ace130eb1b334.result.json`;
    - SHA-256:
      `9CF52EC4B5916BB986F35693D4499E6A613980F2F8B8592863D0E23821EC5E75`.
11. Construir `ReleaseLayout` con:
    - stable: `...\Programs\Neocortex\bin\Neocortex.exe`;
    - receipts: `...\Neocortex\release\receipts`;
    - backup: `...\Programs\Neocortex\release-backups`;
    - lock: `...\Neocortex\release\locks\launcher.lock`.
12. Ejecutar secuencialmente y encadenar cada `TransitionResult`:
    - `promote` hacia la copia inmutable 0.7.2;
    - `rollback` usando `promoted.external_backup_path`;
    - `repromote` hacia la misma copia inmutable 0.7.2.
13. Después de cada transición verificar por handle, SHA-256 y `--version`:
    - promoción: 0.7.2 / `87503D...D142`;
    - rollback: 0.7.1 / `1D4FC...16B1`;
    - re-promoción final: 0.7.2 / `87503D...D142`.
14. Si una transición falla antes de `ReplaceFileW`, usar únicamente
    `recover_pending_transition` para clasificarla; no reintentar ciegamente. Si
    el efecto de `ReplaceFileW` queda incierto, recuperar desde intent/recibos y
    evidencia física antes de cualquier otra mutación.
15. Sólo después de la re-promoción final:
    - guardar evidencia del PATH de usuario previo;
    - añadir exactamente una vez
      `C:\Users\Victor\AppData\Local\Programs\Neocortex\bin` al PATH de usuario;
    - no tocar PATH de máquina;
    - verificar una sesión hija con PATH reconstruido desde Machine + User;
    - confirmar `Get-Command Neocortex` y `Neocortex --version` = 0.7.2.
16. No abrir ni migrar `%LOCALAPPDATA%\Neocortex\state`; no ejecutar reproceso,
    watcher, daemon ni recorrido de corpus.
17. Actualizar este handoff con hashes/recibos finales, crear commit documental,
    verificar Git limpio y sólo entonces marcar el objetivo completo.

## K. Criterio terminal de aceptación

No declarar 0.7.2 cerrada hasta que todos sean ciertos:

- parche `0x0400` autorizado, probado y commiteado;
- suite/freeze y estática verdes en el HEAD posterior al parche;
- wheel/sdist reconstruidos y reproducibles; hashes finales registrados;
- runtime `[full]` coherente con el wheel final y `uv pip check` verde;
- promote, rollback y repromote con tres resultados `success` encadenados;
- launcher estable final 0.7.2 con hash exacto;
- backup 0.7.1 y recibos conservados fuera de `bin`;
- PATH de usuario contiene `bin` exactamente una vez y una sesión nueva resuelve
  `Neocortex 0.7.2`;
- state vivo y corpus permanecen intactos;
- handoff final actualizado y Git limpio.

## L. Riesgos y prohibiciones

- No sustituir el mecanismo NTFS por `Copy-Item`, `Move-Item`, `os.replace` o un
  reemplazo manual del launcher.
- No borrar recibos, backups, el runtime 0.7.1, el runtime 0.7.2 ni directorios de
  evidencia para “limpiar”.
- No modificar ACL globales ni permisos de padres. El único cambio ACL ya hecho
  fue el owner del archivo estable, autorizado y verificado.
- No usar `%LOCALAPPDATA%\Neocortex\release\backups` para la siguiente transición;
  su herencia DACL difiere de `bin`.
- No aceptar como equivalentes artefactos construidos desde HEAD distintos.
- No presentar los tres `no_effect` como promoción parcial: no hubo reemplazo.
- No repetir auditorías P0–P2.5/W2 ya aceptadas salvo regresión concreta.

---

## Apéndice histórico — handoff original del 30 de julio

El contenido siguiente se conserva como evidencia cronológica. Sus apartados de
“fase actual”, “trabajo pendiente” y “siguiente acción” están supersedidos por las
secciones A–L anteriores.

- Creado: 2026-07-30T19:13:13-06:00
- Repositorio: `C:\Users\Victor\Neocortex\Repository`
- Estado: objetivo incompleto, pausado en límite seguro; no está completado ni descartado.
- Fuente de recuperación primaria: branch local `neocortex-0.7.2-work`.
- Fuente de recuperación secundaria: checkpoint externo verificado.

## 1. Objetivo original de Neocortex 0.7.2

1. Integrar la Protected Content Policy en lectura, inventario y mutaciones.
2. Corregir Semantic dentro de Knowledge Plan: modalidades requeridas, completitud,
   warnings, presupuesto, path SQLite exacto, `candidate_limit` y validaciones públicas.
3. Modularizar incrementalmente los hotspots conservando APIs, JSON, `plan_id`,
   excepciones, cancelación y limpieza.
4. Preparar, validar e instalar 0.7.2 en un runtime versionado, promover el launcher
   de forma atómica y comprobar rollback a 0.7.1 sin tocar state vivo.

## 2. Fase y paso actuales

- P0 y P1: aceptados.
- P2: aceptado hasta Knowledge Search CL6a.
- Paso primario actual: **P2.4 / CL6b catalog extraction, tests-first**.
- El contrato focal existe y está rojo: 27 fallos y 22 éxitos.
- P3/W2 NTFS parent guards está abierto en una línea paralela, sin cambio productivo.
- La intervención de pausa completó snapshot, Git local, normalización y este handoff.

## 3. Trabajo completado

- P0 Protected Content Policy integrada y aceptada en gates previos.
- P1 Semantic/Knowledge Plan corregido y aceptado; el E2E sintético permanece
  intencionalmente incompleto cuando falta el ranking semántico de texto.
- P2 modularización aceptada a través de CL6a.
- CL6a: 80/80 focales, 30/30 adversariales y 906/906 amplias; Ruff, format,
  mypy y compileall verdes.
- W1.4 de release Windows: 130/130 verde antes de abrir W2.
- Snapshot externo: 510 filas, 506 fuentes comparadas, cero diferencias,
  cero SQLite, cero rutas prohibidas y cero patrones de credenciales.
- Git for Windows 2.55.0.3 instalado; repositorio local creado sin remotes.
- Política Codex validada con `codex-cli 0.146.0-alpha.3.1` y manual oficial vigente.
- Git normal/commit permitido; reset, clean, force-push y borrado forzado de ramas
  comprobados como `forbidden` mediante `codex execpolicy check`.

## 4. Trabajo parcialmente completado

- CL6b: el test de extracción de catálogo está creado; falta el helper productivo y
  los wrappers late-bound de la fachada.
- W2: las regresiones parent-guard están rojas; dos aserciones heredadas de cleanup
  todavía esperan un único handle y tienen una corrección exacta pendiente.
- CL7: sólo diseño/read-only; faltan dos regresiones funcionales y el helper.
- P2.5 `knowledge_contracts.py`: caracterización existente; matriz funcional no ejecutada.
- El goal sigue incompleto y sólo está detenido a petición del usuario.

## 5. Trabajo todavía no iniciado

- Implementación productiva CL6b.
- Implementación CL7 y extracción P2.5.
- Corrección productiva W2 parent guards.
- Versión 0.7.2, documentación final, reporte técnico, barrera completa, coverage,
  wheel/sdist finales, instalación, promoción de launcher y rollback.

## 6. Archivos modificados

La lista byte-exacta del árbol preservado está en el manifiesto del checkpoint y en
el commit raíz `90d2b711b1f81de7d6e67f871fc40b7b6e5aec42`. La procedencia anterior a Git no
permite distinguir honestamente cada archivo modificado de cada archivo creado; no
se inventa esa distinción. Archivos focales modificados o caracterizados:

- `_02_Deduplicacion/inventory_scan.py`
- `_04_Nucleo_Operativo/protected_content.py`
- `_04_Nucleo_Operativo/knowledge_planner.py`
- `_04_Nucleo_Operativo/knowledge_search.py`
- `_04_Nucleo_Operativo/knowledge_contracts.py`
- `_04_Nucleo_Operativo/semantic_planner.py`
- `_04_Nucleo_Operativo/semantic_service_contracts.py`
- `tools/release_windows_ntfs.py`
- `tools/release_windows_ntfs_native.py`
- pruebas focales correspondientes bajo `tests/`.

Durante la pausa se modificaron además:

- `AGENTS.md`
- `C:\Users\Victor\.codex\AGENTS.override.md` (fuera de Git)
- `C:\Users\Victor\.codex\config.toml` (fuera de Git)

## 7. Archivos creados

Extracciones productivas ya presentes y caracterizadas:

- `_04_Nucleo_Operativo/semantic_contract_payloads.py`
- `_04_Nucleo_Operativo/semantic_contract_validation.py`
- `_04_Nucleo_Operativo/semantic_plan_errors.py`
- `_04_Nucleo_Operativo/semantic_plan_owners.py`
- `_04_Nucleo_Operativo/semantic_plan_results.py`
- `_04_Nucleo_Operativo/semantic_plan_scratch.py`
- `_04_Nucleo_Operativo/knowledge_planner_exact.py`
- `_04_Nucleo_Operativo/knowledge_planner_intents.py`
- `_04_Nucleo_Operativo/knowledge_planner_steps.py`
- `_04_Nucleo_Operativo/knowledge_search_contracts.py`
- `_04_Nucleo_Operativo/knowledge_search_fusion.py`
- `_04_Nucleo_Operativo/knowledge_search_content.py`
- `_04_Nucleo_Operativo/knowledge_search_inventory.py`
- `_04_Nucleo_Operativo/knowledge_search_code.py`

Creaciones de la pausa:

- `.gitignore`
- `.codex/config.toml`
- `.codex/rules/neocortex-git.rules`
- este handoff Markdown y su JSON complementario.

`_04_Nucleo_Operativo/knowledge_search_catalog.py` **no existe** todavía.

## 8. Cambios de cada subagente

- `cl7_golden_probe`: read-only; confirmó contratos tuple 4/5; sin golden run ni archivos.
- `p25_functional_probes`: se detuvo antes de imports/probes; sin archivos.
- `p25_tests_plan/package_member_inventory`: inventario read-only completado; sin archivos.
- `sandbox_blocker_audit`: diagnosticó la topología de sandbox; sin bypass ni archivos.
- `w2_patch_design_exact`: interrumpido en diseño read-only; sin archivos.
- Ningún subagente modificó archivos durante la pausa y no queda ninguno ejecutándose.

## 9. Parches aplicados y pendientes

- La intención de los parches P0 de Protected Content está integrada y aceptada;
  su aplicación por hunk ocurrió antes de Git y no se reconstruye por inferencia.
- CL6b: ningún parche productivo aplicado; sólo contrato rojo.
- W2: corrección de dos oráculos pendiente en
  `checkpoint\pending\W2_ORACLE_PATCH.md`; debe aplicarse antes de producción W2.
- No hay `.rej`, `.orig`, `.partial` ni parche a medio aplicar.

## 10. Hashes verificados

- Checkpoint `MANIFEST.tsv`: `4FFB729ED0B25E71A9D5E3FDD63BA2C27645D208A814193B4FFC83C2D0FAC8BD`
- Recovery report: `7CD55E5C4D16E2871AEAEFD9EBA8D2A496DDC0A734A6B054938EABB521EA9FA0`
- CL6b test: `036A60ECAFF86915DB23089F24ECEC11ECC3FAB87E98050BE557BADAC471BC0D`
- `knowledge_search.py`: `DC4B8302D5ECB12E1AE27EC1219F8C32D7540AEC15BD94DE0605009829A6D220`
- W2 test: `093338E8B0BB91EF510C70779D4FC96094A100795D01F9378CCBCE914C5078D3`
- `release_windows_ntfs.py`: `F553B557641551B682E684E51F4ED422D665C2F51FA0E52311F142BDD3BB1A5E`
- `release_windows_ntfs_native.py`: `C80F450042B5531778833F8EB898697414D53E59E902E69F1B39FA185F85C32B`
- `knowledge_contracts.py`: `FCCACB21EAE6C126F48E7CA86F4E2B8E80F38C4095CD47A9AB8F92F85FC42161`
- Global config original/current: `AFE77EB6F33C9C49C66D51627E0A2FDE0D11EAD403A42C5A810391AC57A7C97D` /
  `036E733CD13FAFAFBB05359DB569E5A31D0F1D21DA1B14AA35456C5B4F0CB22E`
- Global AGO original/current: `1C1057431E5C8E7001AB3C823E68F4C514B20269B95A18581313EA222D7BDEB5` /
  `5610CFC02CC7E503E8AF2E44D9205F7EB54F155E9C4669CB4DC8959F122C7DCA`
- Project AGENTS actual: `CC43EDDEF7775B960B0B18D6D497ED298DD4A53C8667663A183127F8995AD64F`
- Project config: `77918E9C2240A311EAFBB66E4191A74764B521CC51F31902B778489CAC322434`
- Git rules: `896E344BB742449C0E0734E37CCC5F30C8C00DF20858E577516EEED37F9C98AB`

## 11. Pruebas verdes

- CL6a focal: 80/80.
- CL6a adversarial: 30/30.
- Regresión amplia conocida: 906/906.
- W1.4 release Windows: 130/130.
- P0/P1 aceptados en gates previos.
- Config global y de proyecto: TOML válido.
- Execpolicy: Git normal y commit `allow`; operaciones destructivas probadas `forbidden`.

## 12. Pruebas rojas esperadas

- `tests/test_knowledge_search_catalog_extraction_contract.py`: 27 failed, 22 passed.
- `tests/test_release_windows_ntfs.py`: 14 failed, 41 passed antes de corregir dos
  oráculos obsoletos; el resto corresponde a W2 tests-first.
- El E2E sintético de P1 que carece de ranking semántico de texto debe continuar
  incompleto; no convertirlo en verde debilitando el contrato.

## 13. Fallos CL6b, W2 y otros todavía abiertos

- CL6b: falta `knowledge_search_catalog.py` y wrappers late-bound; no tocar otros canales.
- W2: TOCTOU por parent-swap; mantener cuatro guards sin `FILE_SHARE_DELETE`, validar
  en el mismo handle y cerrar lock primero, luego guards en reversa preservando la
  primera `BaseException`.
- CL7: `exact_truncated` debe forzar incomplete y un ranking no semántico requerido
  no puede sustituirse por otro ranking del mismo canal.
- P2.5: matriz de probes no ejecutada.
- El fallo previo de `apply_patch` deja de ser bloqueo operativo porque ya no es obligatorio.

## 14. Fixtures bajo revisión

- `tests/test_knowledge_search_catalog_extraction_contract.py`
- `tests/test_release_windows_ntfs.py`
- `tests/fixtures/knowledge/phase1_golden_v1.json`
- futuras regresiones CL7 indicadas arriba.
- matriz funcional P2.5 aún no materializada/ejecutada.

## 15. Comandos importantes ya ejecutados

```powershell
py -3 -B -m pytest -q tests/test_knowledge_search_catalog_extraction_contract.py
py -3 -B -m pytest -q tests/test_release_windows_ntfs.py
winget.exe install --id Git.Git --exact --source winget --accept-source-agreements --accept-package-agreements --silent --disable-interactivity
codex.exe --version
codex.exe execpolicy check --pretty --rules .codex\rules\neocortex-git.rules -- git status
codex.exe execpolicy check --pretty --rules .codex\rules\neocortex-git.rules -- git reset --hard
```

Git se inicializó localmente, se configuró identidad sólo en este repositorio y se
verificaron `status`, `diff --check`, branch, HEAD y ausencia de remotes.

## 16. Resultados de Ruff, mypy, pytest y demás gates conocidos

- Ruff check: verde en la última barrera CL6a.
- Ruff format check: verde en la última barrera CL6a.
- mypy: verde en la última barrera CL6a.
- compileall: verde en la última barrera CL6a.
- pytest: 80/80 focal, 30/30 adversarial y 906/906 amplio en CL6a.
- No se ejecutó suite completa, coverage, build, wheel, sdist ni instalación durante la pausa.
- Bytecode del árbol: baseline conocido 252; no se hizo limpieza destructiva.

## 17. Branch y commit actual

- Branch: `neocortex-0.7.2-work`
- HEAD al redactar/sellar este handoff: `8951ba6649cd7df0242feedd900430d727da0fa2`
- Status antes de crear el handoff: limpio.
- Remotes: ninguno.
- El commit que incorpora este documento será el nuevo HEAD; su hash se verifica en
  el informe final porque un commit no puede contener su propio SHA de forma estable.

## 18. Commits creados durante esta pausa

1. `90d2b711b1f81de7d6e67f871fc40b7b6e5aec42` — `checkpoint: preserve paused Neocortex 0.7.2 work`
2. `8951ba6649cd7df0242feedd900430d727da0fa2` — `chore: modernize Codex workflow and Git policy`
3. `docs: record Neocortex 0.7.2 pause handoff` — hash asignado al sellar este documento.

El primer commit es un checkpoint intermedio de la evolución 0.7.2, no una copia
pura del estado original 0.7.1.

## 19. Ruta y hash del snapshot externo

- Ruta: `C:\Users\Victor\Neocortex\Checkpoints\2026-07-30_0.7.2_pause_183043`
- Hash representativo: SHA-256 de `MANIFEST.tsv`
  `4FFB729ED0B25E71A9D5E3FDD63BA2C27645D208A814193B4FFC83C2D0FAC8BD`.
- Verificación: 510 filas, 506 fuentes, cero diferencias, cero SQLite, secretos o
  rutas prohibidas. `RECOVERY_VERIFICATION.md` SHA-256:
  `7CD55E5C4D16E2871AEAEFD9EBA8D2A496DDC0A734A6B054938EABB521EA9FA0`.

## 20. Configuraciones e instrucciones modificadas

- Global `C:\Users\Victor\.codex\config.toml`: Git/red permanentes, snapshots por
  fase, apply_patch opcional, paralelismo/subagentes opcionales, handoff persistente,
  `approval_policy="on-request"`, `approvals_reviewer="user"`,
  `sandbox_mode="workspace-write"`, `network_access=true`.
- Global `AGENTS.override.md`: Git/red sin autorizaciones rutinarias; subagentes
  opcionales y escritores aislados.
- Proyecto `AGENTS.md`: flujo Git/checkpoint, red habilitada y gates proporcionales.
- Proyecto `.codex/config.toml`: raíces escribibles sólo Laboratory/Checkpoints,
  sandbox Windows elevado y red habilitada.
- Proyecto `.codex/rules/neocortex-git.rules`: Git `allow` y operaciones destructivas
  específicas `forbidden`.
- Se retiraron de la configuración activa los hooks que bloqueaban todo Git,
  imponían apply_patch/fan-out o impedían detener un goal pendiente. Se conservó
  únicamente `SessionStart` para continuidad acotada.
- Modelo, razonamiento, proveedor, autenticación, MCP, cuenta y telemetría no cambiaron.
- La configuración persistida requiere recarga de Codex al reanudar; no se abrió
  otra sesión sólo para probarla.

## 21. Riesgos o incertidumbres reales

- No existe Git pre-0.7.2; por eso el commit raíz no separa con certeza histórica
  archivos nuevos y modificados. El checkpoint/manifiesto es la evidencia exacta.
- CL6b y W2 están tests-first rojos; ejecutar producción antes de sus contratos sería riesgo.
- Los hashes de payload wheel/sdist son baselines de inventario, no artefactos finales.
- La configuración persistida no sustituye el contexto congelado de esta sesión;
  debe recargarse al reanudar.
- `project_doc_max_bytes` no se aumentó: `AGENTS.md` mide 21,129 bytes y el default
  oficial es 32 KiB, suficiente.

## 22. Estado confirmado de launcher, runtime y state

- Launcher `C:\Users\Victor\AppData\Local\Programs\Neocortex\bin\Neocortex.exe`:
  no modificado ni promovido.
- Runtime activo conocido 0.7.1 bajo
  `C:\Users\Victor\AppData\Local\Programs\Neocortex\versions\0.7.1-wheel-xxh3_128-239015b3f762232c94119af44e3056c8\venv`:
  no modificado.
- State `C:\Users\Victor\AppData\Local\Neocortex\state`: no abierto ni modificado.
- Corpus real: no recorrido ni modificado.
- La instalación de Git no tocó ningún componente de Neocortex.

## 23. La siguiente única acción recomendada

Implementar **sólo CL6b catalog extraction**: crear
`_04_Nucleo_Operativo/knowledge_search_catalog.py` y convertir los helpers de
catálogo en wrappers late-bound de `knowledge_search.py`, preservando firmas,
excepciones, JSON, owner timings y cancelación. No iniciar CL7, P2.5 ni W2 en paralelo.

## 24. Comando o conjunto focal exacto al reanudar

Después del cambio mínimo CL6b:

```powershell
py -3 -B -m pytest -q tests/test_knowledge_search_catalog_extraction_contract.py
```

Si queda 49/49 verde, ejecutar inmediatamente:

```powershell
py -3 -B -m pytest -q tests/test_knowledge_search.py tests/test_knowledge_catalog_search.py tests/test_knowledge_search_completeness.py
py -3 -B -m ruff check _04_Nucleo_Operativo/knowledge_search.py _04_Nucleo_Operativo/knowledge_search_catalog.py tests/test_knowledge_search_catalog_extraction_contract.py
py -3 -B -m mypy _04_Nucleo_Operativo/knowledge_search.py _04_Nucleo_Operativo/knowledge_search_catalog.py
```

## 25. Criterio para avanzar a la siguiente fase

Avanzar desde CL6b sólo cuando:

- las 49 pruebas focales estén verdes;
- los helpers de catálogo vivan en el módulo extraído y la fachada conserve imports
  históricos/late binding;
- JSON, excepciones, cancelación, owner timings y orden sean byte/contract-equivalentes;
- las regresiones de Knowledge Search indicadas estén verdes;
- Ruff y mypy focales estén verdes;
- `git diff --check` esté limpio y exista un commit cohesionado.

## 26. Procedimiento para recuperar el repositorio desde Git

No usar reset ni restore. Crear un worktree aislado para comparar o recuperar:

```powershell
Set-Location -LiteralPath 'C:\Users\Victor\Neocortex\Repository'
git status --short --branch
git log --oneline -3
git worktree add -b recover-neocortex-0.7.2-pause 'C:\Users\Victor\Neocortex\Recovery\0.7.2-pause' neocortex-0.7.2-work
```

Trabajar en el nuevo worktree, comparar archivos y copiar sólo lo necesario. No borrar
el árbol original ni eliminar la branch de recuperación hasta verificar la integración.

## 27. Procedimiento para recuperar desde el snapshot externo

1. Verificar el manifiesto:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Users\Victor\Neocortex\Checkpoints\2026-07-30_0.7.2_pause_183043\MANIFEST.tsv'
```

Debe devolver `4FFB729ED0B25E71A9D5E3FDD63BA2C27645D208A814193B4FFC83C2D0FAC8BD`.

2. Leer `RECOVERY_VERIFICATION.md` y `CHECKPOINT_SUMMARY.txt`.
3. Crear un destino nuevo, por ejemplo
   `C:\Users\Victor\Neocortex\Recovery\snapshot-2026-07-30`; no sobrescribir el repo.
4. Copiar `checkpoint\repository\` al destino nuevo y volver a calcular hashes según
   `MANIFEST.tsv`.
5. Restaurar `codex_originals` sólo de forma dirigida si se desea revertir configuración;
   nunca copiar `auth.json`, bases/state, runtimes o secretos.
6. Comparar el árbol recuperado contra Git y promover archivos individualmente mediante
   un commit nuevo; no usar limpieza destructiva.

## Reanudación obligatoria

- Reanudar en esta misma conversación.
- Volver a leer los `AGENTS.md` vigentes.
- Volver a cargar o verificar `config.toml`.
- Leer primero este handoff.
- Comprobar branch, commit y `git status`.
- Comparar el estado con el manifiesto del checkpoint.
- No repetir caracterización o auditoría ya documentada.
- Continuar exactamente desde “Siguiente acción”.
- No tocar launcher, runtime ni state hasta que los gates correspondientes lo autoricen.
