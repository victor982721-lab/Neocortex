# Neocortex 0.7.2 — handoff de pausa controlada

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
