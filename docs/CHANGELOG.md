# Registro de cambios

Este archivo registra cambios observables del producto. Las cifras de pruebas,
cobertura y rendimiento pertenecen al informe técnico fechado de cada auditoría;
no se copian aquí para evitar que se conviertan en datos históricos sin contexto.

## [Sin publicar] - 2026-07-29

### Añadido

- Fachada Python lazy `neocortex.sdk` para los contratos y el servicio
  Knowledge read-only existentes, conservando identidad con imports legacy y
  declarada como paquete tipado PEP 561 mediante markers `py.typed` tanto en
  el facade como en su shim de implementación durante la transición.
- Contrato público `neocortex.capabilities` schema 1 con declaraciones
  estáticas por ruta, estados `available/degraded/unavailable` y probes de
  spec, metadata y paths que no importan engines ni cargan o descargan modelos.
- Telemetría Knowledge schema 1 en nanosegundos para planner, snapshots before
  y after por intento, owners/rankings, fusión, broker y compilación de
  contexto. Los retries conservan las fases de ambos intentos; Exact mide una
  vez por owner batch sin duplicar el tiempo por término.
- Preset `--self-analysis` para analizar una raíz de código explícita como
  `analyze_only`, con raíz/estado disjuntos, entrada directa desde inventario y
  exclusión obligatoria de generated/vendored, catálogo, organización y rutas
  MIME.
- Manifest `neocortex.self-analysis-manifest/v1` ligado transaccionalmente a la
  finalización. Conserva policy/firma, identidad, scan/USN, evidencia code,
  cuatro conteos cero y argv canónicos `analyze`/`status` como arrays acotados.
- Proyección `self_analysis` en `--code-status --code-json`, con estados de
  manifest y frescura separada para raíz, framework/code, checkpoint y journal.
- [Guía operativa del autoanálisis protegido](SELF_ANALYSIS.md), incluido un
  mini-root sintético reproducible.
- Topología canónica por usuario: fuente en
  `%USERPROFILE%\Neocortex\Repository`, runtimes versionados bajo
  `%LOCALAPPDATA%\Programs\Neocortex\versions`, launcher estable en `bin` y
  estado/autoanálisis bajo `%LOCALAPPDATA%\Neocortex`.
- `InternalPathsPolicy` con identidades físicas y firma versionada para
  repositorio, runtime, datos de aplicación, autoanálisis y launcher.

### Corregido

- La instalación base se limita a `rich` y `xxhash`; `documents`, `audio`,
  `image`, `semantic` y `ui` poseen extras explícitos, y `full` conserva la
  unión compatible del runtime integrado anterior. Pillow permanece declarado
  en cada dominio que lo importa directamente.
- Knowledge status/search/context y los facades Semantic ya no cargan Pillow ni
  schemas de owners por imports eager al inspeccionar estado ausente ni al leer
  un owner `image` existente.
- Full scan y USN comparten una `InventoryExclusionPolicy` con firma cruda
  `inventory-exclusion-policy-v1:xxh3_128:...`; Framework y watcher ligan la
  firma efectiva que también incorpora `InternalPathsPolicy`. La puerta
  incremental exige último run durable, checkpoint/scan y raíz/cursor vivos.
  Un fallo fuerza full scan sin fallback histórico.
- Route-only/resume code acepta cero candidatos únicamente cuando todas las
  rutas seleccionadas consumen `inventory_snapshot`. Sin run explícito exige
  que el owner durable más reciente de la raíz exacta sea `normal`, sin fallback
  histórico; rutas MIME o mixtas fallan antes de crear el run.
- `CodeState.finalize_graph()` consulta cancelación mediante un progress handler
  limitado a su transacción, hace rollback antes de propagar y retira el handler
  al salir. La publicación generacional del grafo sigue fuera de este cambio.

### Persistencia y seguridad

- Framework pasa de schema 19 a 20 mediante una migración aditiva que preserva
  filas y añade modo, identidad, estado/firma de policy y snapshots protegidos
  de acción. Checks, triggers y `CorpusMutationGuard` impiden que un run
  `analyze_only` alcance owners de mutación. No existe downgrade; rollback
  requiere backup consistente y paquete compatible.
- Dedup pasa de schema 7 a 8: añade
  `scans.inventory_policy_signature`, conserva scans, archivos y bytes, e
  invalida checkpoints legacy sin firma en vez de inventar evidencia.
- La frontera normal rechaza un estado igual o ancestro del corpus y excluye
  los árboles internos descendientes; el autoanálisis mantiene disjunción
  completa entre raíz y estado.
- La finalización se abstiene salvo una única ruta code completada y ceros
  exactos en candidatos, acciones y organización. Run completado y manifest se
  publican en una sola transacción.
- El status de autoanálisis no crea ni migra: usa SQLite
  `mode=ro&immutable=1`, `query_only` y fences pre/post. Cualquier sidecar,
  incluso vacío o desacoplado, o una cerca inestable en code, framework o Dedup
  causa abstención total con código `2`, sin vista parcial ni cambios de estado.

### Compatibilidad y límites

- Instalar `neocortex-framework[full]` mantiene las dependencias del runtime
  completo; la base mínima no habilita por sí sola rutas opcionales. Los probes
  ligeros declaran prerrequisitos, no certifican cachés ni modelos ejecutables.
- `elapsed_milliseconds` conserva su semántica legacy del broker retornado. El
  nuevo envelope es aditivo y observacional: no cambia IDs, JSON sin
  telemetría, texto renderizado, presupuestos ni schemas SQLite. Las fases
  anidadas no son aditivas y una campaña comparable aún debe fijar fixture,
  snapshot y condiciones de cache.
- Este corte **no cambia la versión pública**: la fuente continúa en `0.7.1`.
  El launcher estable sólo debe promoverse desde un runtime versionado después
  de validar el artefacto exacto, dependencias, versión y ayuda.
- Los estados de autoanálisis pertenecen a árboles nuevos bajo
  `%LOCALAPPDATA%\Neocortex\self-analysis`, nunca dentro del repositorio; un
  mini-root no demuestra cobertura de la raíz canónica completa.

## [0.7.1] - 2026-07-26

### Añadido

- Frontera renderizada `untrusted-corpus-data-v1` que precede a todo
  `ContextBundle` y niega autoridad de instrucciones, herramientas y acciones a
  la evidencia recuperada.

### Corregido

- El analizador Python versión 2 emite símbolos de asignación sólo para nombres
  realmente enlazados, admite destructuring/`Starred`, omite atributos y
  subscripts y deduplica nombres repetidos.
- El vínculo entre run, inventario y candidatos de ruta se publica atómicamente
  después de completar la generación de candidatos. La reanudación valida scan,
  conteos e identidad de raíz; la recuperación legacy exige evidencia
  inequívoca y trabajo de ruta durable.
- Los subprocess acotados y workers aislados en Windows contienen el árbol
  propio mediante Job Objects kill-on-close asociados antes de reanudar el
  hijo, y cierran procesos, pipes y handles ante timeout, overflow o excepción.
- PDF e imagen cierran sus streams de candidatos en el mismo thread que creó la
  conexión SQLite, también al desenrollar error o cancelación; se elimina el
  cierre cross-thread observado durante la admisión de recursos.
- El staging semántico de texto reutiliza una sesión SQLite por fuente y limita
  cada transacción a 128 items o chunks, en lugar de abrir una conexión por
  operación. Fallo, cancelación y `BaseException` revierten el lote activo; el
  prefijo confirmado es reanudable y permanece fuera del head publicado.
- El worker semántico alcanza el punto fijo de reutilización exacta antes de
  cada claim, por lo que un payload producido por un batch satisface duplicados
  pendientes posteriores. También limpia leases propios ante
  `RuntimeError`, `KeyboardInterrupt` y `BaseException` sin enmascarar la causa.
  Duplicados ya reclamados dentro del mismo batch y escrituras por job siguen
  siendo límites conocidos.
- Un cache hit de código con ruta invariable realiza cero DML sobre `code_fts` y
  sólo actualiza presencia y observación. Cualquier cambio de ruta rechaza la
  caché; el procesamiento normal publica una versión sucesora y conserva
  inmutables la ruta y evidencia históricas.
- El resolver de símbolos y dependencias del grafo usa conjuntos TEMP indexados
  y joins set-oriented en lugar de consultas correlacionadas por relación;
  mantiene resultados ambiguos o no resueltos cuando no existe coincidencia
  única.
- Una reconciliación completa —sin límite ni selección— ejecuta `mark_missing`
  antes del grafo. La finalización reinicia membresías derivadas y, una vez
  resueltos sus conflictos, sincroniza sólo etiquetas FTS vigentes distintas
  mediante un mapa TEMP indexado y una única sentencia; moves y cambios de
  manifest no dejan proyectos obsoletos ni reescriben labels históricos.
- El fastpath estable del grafo exige cero cambios, todos los candidatos como
  cache hits compatibles con los analizadores del runtime y un fence tipado
  `code-graph-resolver-v3` ligado al run completo inmediatamente anterior, con
  la misma firma de procesamiento. Fence ausente, malformado o stale —incluida
  la primera corrida sobre una base existente— fuerza finalización completa.
- La caché comprueba el analizador realmente disponible en el runtime, por lo
  que la aparición de un parser opcional reemplaza el fallback sin cambiar la
  firma declarativa ni reprocesar archivos ajenos; los cache hits preservan los
  contadores `partial`, `text_only`, `binary`, `skipped_limit` y `error`.
- La finalización de runs de código usa compare-and-swap sobre el owner
  `running`; cancelaciones quedan como `cancelled` y un run fallido, duplicado o
  inexistente no puede avanzar el fence del grafo.

### Cambiado

- La firma del analizador Python se elevó a versión 2 para que los resultados
  previos con semántica de asignación distinta no se reutilicen como caché.

### Compatibilidad y migración

- La entrega eleva la versión pública de `0.7.0` a `0.7.1` sin crear bases ni
  cambiar schemas persistentes. El fence versionado se guarda en `metadata` y
  avanza en la misma transacción que completa su `analysis_run`.
- La firma Python v2 invalida deliberadamente la caché producida con la
  semántica anterior; la reconstrucción derivada no requiere una migración.
- La evolución del staging semántico no cambia schema, API Python, CLI ni JSON;
  conserva la publicación generacional v6 y sus datos son compatibles con el
  rollback.
- El rollback a `0.7.0` no exige downgrade SQLite; puede reprocesar código por
  firma y recalcular el grafo, pero las versiones sucesoras siguen siendo
  legibles por el esquema 2.
- Permanecen las limitaciones del esquema 2: publicación no generacional,
  transacción global sin cancelación dentro de una sentencia SQL, empates de
  resolución conservados como ambiguos y firma global del registro que puede
  invalidar lenguajes no afectados.

## [0.7.0] - 2026-07-25

### Añadido

- Contratos inmutables y serialización estable para recursos, revisiones,
  evidencias, hits, snapshots y bundles de contexto del plano Knowledge.
- Snapshot lógico cross-owner de inventario, framework, extractores, catálogo,
  semántica y código, con observación antes/después, un único reintento y
  estados explícitos para propietarios ausentes, futuros, incompatibles o
  corruptos.
- Planificador determinista y búsqueda unificada read-only con rankings por
  propietario, fusión RRF por evidencia, filtros, diversidad por recurso,
  historial opcional y abstención/completitud explícitas.
- Compilador acotado de `ContextBundle` con citas estables, locators exactos,
  presupuesto de caracteres, truncación visible y contradicciones estructuradas.
- Operaciones CLI `--knowledge-status`, `--knowledge-search` y
  `--knowledge-context`, con salida humana/JSON y códigos de salida
  diferenciados para ausencia de resultados, parcialidad, cambio de snapshot,
  esquema futuro/incompatible, corrupción y cancelación.
- Evaluación reproducible de 17 escenarios golden para recuperación, ranking,
  evidencia, citas, staleness, duplicados y abstención.

### Corregido

- La resolución semántica de código interpreta `section_id` como el
  `chunk_index` persistido y exige clase, versión e identidad exactas; ya no
  cae al primer chunk ni devuelve una revisión stale ante evidencia inválida.

### Cambiado

- El modo discovery conserva el colapso compatible por elemento; el modo
  evidence preserva entidades y evidencias distintas sin alterar el contrato
  previo de discovery.
- Las representaciones físicas de inventario, extractores, catálogo, semántica
  y código se normalizan a una identidad estable común antes de fusionar
  evidencia entre propietarios.
- Las relaciones de deduplicación del inventario v7 sólo influyen cuando el
  plan conserva verificación byte a byte; Knowledge se abstiene ante planes no
  verificados y ninguna relación autoriza una acción sobre archivos.

### Compatibilidad y migración

- La entrega es aditiva y eleva la versión pública de `0.6.0` a `0.7.0`; las
  búsquedas existentes y el modo discovery permanecen compatibles.
- No se crea una base nueva, no cambia ningún esquema y no existe migración de
  estado. Knowledge abre únicamente bases existentes en modo de consulta.
- El rollback consiste en instalar el paquete anterior: esta capa read-only no
  deja estado que revertir ni autoriza mutaciones mediante hits, evidencias o
  contexto.

## [0.6.0] - 2026-07-25

### Añadido

- Registro durable de observaciones de conciliación en
  `file_action_reconciliation_events`, append-only, con CAS de predecesor,
  clave idempotente, actor, procedencia, firma del conciliador y evidencia
  autocontenida. El registro nunca autoriza ni repite una mutación.
- Operación CLI explícita `--action-recovery-record`, separada del
  `--action-recovery-status` de sólo lectura, con confirmación de escritura,
  salida humana/JSON y códigos de salida conservadores.
- Planificador de retención `--retention-status` estrictamente dry-run para
  inventario, framework, catálogo y semántica, paginado por keyset y con
  publicaciones, builders, leases y evidencia protegidos.
- Inventario técnico reproducible de metadatos de licencias y artefactos de
  terceros; no se eligió una licencia para NeoCortex ni se creó `NOTICE`.
- Barrera de contención para fixtures nativos: raíz canónica única, rechazo de
  rutas fuera de ella, registro por identidad e inspección posterior de fugas.
- Guía de instalación offline que distingue validación `--no-deps`, entornos
  heredados y resolución hermética mediante un wheelhouse completo.
- Journal USN sintético contenido para probar coordinación, cancelación y
  reanudación sin abrir el volumen raw.

### Corregido

- Las observaciones sobre acciones `recovery_required` ya no desaparecen al
  terminar el proceso y dos writers no pueden avanzar el mismo frontier de
  conciliación de manera incompatible.
- Lectores y writers de SQLite inventariados aplican contratos explícitos de
  existencia, FK, `query_only`, timeout, rollback y cierre según el propietario;
  los diagnósticos endurecidos no crean una base ausente.
- La cobertura de ramas de riesgo incluye fallos, cancelación, concurrencia,
  migración y rollback de conciliación, contención, retención y conexiones.
- El reinicio distingue una intención `started` abandonada antes de la frontera
  —fallo sin efecto intentado— de una acción `applying` incierta; las
  transiciones repetidas con evidencia contradictoria ya no se aceptan como
  idempotentes.
- Un recibo de Papelera sólo puede confirmar la acción cuyas rutas liga, y el
  lector de conciliación se abstiene ante un esquema framework futuro o
  metadata de versión no canónica.
- El planner de retención bloquea generaciones semánticas referenciadas por
  evidencia y conserva el último run completado de framework. La poda
  owner-local de inventario falla cerrado sin holds cross-store explícitos y,
  cuando se proporcionan, conserva publicación vigente, anterior y scans
  referenciados.
- La cancelación durante una consulta SQL del planner se normaliza como
  `RetentionPlanningCancelled` en lugar de escapar como error SQLite genérico.

### Cambiado

- Framework pasa de esquema 18 a 19 mediante una migración aditiva y
  transaccional que crea el log de conciliación sin reinterpretar acciones
  legacy.
- La versión pública pasa de `0.5.0` a `0.6.0` por el nuevo contrato CLI y el
  esquema framework 19.
- `finalize_graph` conserva deliberadamente su transacción global: los
  benchmarks y pruebas de snapshot/rollback no justificaron fragmentarla sin
  diseñar antes un esquema generacional completo.
- Esta continuación no cambia la CLI pública, los esquemas ni la versión
  `0.6.0`; endurece contratos internos y sus regresiones.

### Compatibilidad y límites

- `--action-recovery-record` puede migrar una base framework existente desde
  un esquema soportado después de la confirmación explícita; no crea una base
  ausente. El rollback exige restaurar un backup SQLite consistente y el
  paquete anterior; nunca editar números de versión.
- Sólo se persisten observaciones de conciliación. No existen todavía comandos
  `decide`, `authorize`, `recover` o `verify`, ni una autorización durable
  para ejecutar una nueva mutación.
- Retención no elimina, no aplica cuotas, no ejecuta `VACUUM` ni hace
  checkpoints. Los bytes informados son una cota inferior del payload SQLite,
  no espacio físico garantizado como recuperable.
- No existen todavía `retention prepare/apply/verify`; el planner no autoriza
  un `DELETE` manual ni sustituye un journal reanudable por propietario.
- El grafo de código permanece en esquema 2 y `NC-AUD-015` sigue abierto como
  trabajo de diseño generacional.
- La instalación global observada no fue actualizada por esta auditoría.

## [0.5.0] - 2026-07-24

### Añadido

- Primitiva Windows de rename/move ligada a handles retenidos, FileId y volumen,
  relativa al directorio destino y sin reemplazo.
- Evidencia de frontera y recibo en `file_actions`, clave idempotente y bitácora
  `file_action_events` protegida contra update/delete.
- Conciliador CLI read-only `--action-recovery-status`, con paginación/filtro de
  run, salida humana/JSON Lines y clasificaciones explícitas.
- Publicación generacional semántica v6 mediante revisiones congeladas, miembros
  de generación y un head CAS por modelo.
- Publicación generacional de catálogo v6 mediante staging por fuente, puntero
  CAS y proyección `documents` compatible.
- Lease de vida cross-process del watcher por raíz+estado, con lock del sistema
  operativo, metadatos acotados y recuperación conservadora de metadata stale.

### Corregido

- Rename de extensión y organización ya no caen a una syscall final por ruta:
  operan sólo sobre archivos regulares de un hard link en NTFS local y mismo
  volumen; UNC, otros filesystems, reparses, directorios y cross-volume se
  abstienen.
- Un fallo después de la frontera nativa conserva `recovery_required` en lugar
  de permitir que la acción se clasifique como fallo sin efecto o se repita.
- Los planes documentales inciertos reservan su destino y no vuelven a entrar al
  selector automático.
- Las búsquedas semánticas oficiales no observan miembros parciales y los
  lectores del catálogo mantienen la generación anterior durante fallo,
  cancelación o conflicto de publicación.
- La resolución semántica conserva evidencia publicada inmutable, pero usa la
  ruta viva sólo cuando coincide la identidad completa; después de organizar un
  archivo no devuelve el origen obsoleto ni acepta un `SearchHit` con espacio o
  modalidad distintos del modelo persistido.
- `semantic_status` usa una sola conexión y snapshot WAL para conteos,
  generaciones y summaries; los summaries se agregan en una consulta acotada,
  eliminando el patrón N+1 de conexiones y statements.
- Ocho factories de subsistemas y las conexiones principales de framework
  verifican FK, `query_only` en lectura y cierre ante aborto de configuración,
  sin reconstruir tablas ni abrir bases operativas.

### Cambiado

- La aplicación de candidatos de Papelera está deshabilitada: el dry-run sigue
  planificando, pero `--apply` registra abstención `skipped` porque la API
  disponible es path-bound. Se retiró `Send2Trash` de las dependencias.
- La versión pública pasa de `0.4.1` a `0.5.0` por el cambio deliberadamente
  restrictivo de mutaciones y por los esquemas framework 18, catálogo 6 y
  semántica 6.

### Migración y compatibilidad

- Framework 17→18 valida el layout conocido, preserva conteos y agrega columnas
  de recuperación; no inventa recibos para acciones legacy.
- Catálogo 5→6 exige un contrato v5 exacto, importa una generación publicada por
  fuente, conserva historial/planes y valida conteos/FK.
- Semántica 5→6 exige el contrato e historial exactos, importa únicamente la
  vista legacy activa y consistente por hash, valida conteos/FK/integridad y
  conserva tablas legacy.
- Las tres migraciones son transaccionales y se abstienen ante objetos no
  comprendidos. No existe downgrade por edición de versión: restaure el backup
  completo y el paquete compatible.
- La distribución global observada permaneció en `0.3.0`; esta auditoría no la
  actualizó ni promovió `0.5.0` fuera de entornos de validación.

### Limitaciones conocidas

- No existe todavía política global de retención/poda para generaciones
  fallidas, parciales o abandonadas (`NC-AUD-014`).
- `finalize_graph` conserva su transacción global (`NC-AUD-015`) y algunas
  conexiones/FK mantienen políticas desiguales (`NC-AUD-017`).
- El conciliador no modifica acciones, no persiste todavía una transición o
  evento `reconciled` y no autoriza reintentos; los planes de organización
  inciertos requieren revisión manual.
- SQL externo sobre tablas legacy no recibe la garantía generacional de los
  repositorios oficiales.
- El proyecto no declara licencia propia ni se añadió una durante esta entrega
  (`NC-AUD-021`).

## [0.4.1] - 2026-07-24

### Añadido

- Guía de la CLI canónica con rutas, operaciones directas, ámbitos JSON, códigos
  de salida y ejemplos comprobables.
- Guía operativa para ejecución normal, watcher en primer plano, cancelación,
  reanudación, límites de recursos, diagnóstico y mantenimiento.
- Guía de recuperación y rollback con copias consistentes mediante la API de
  backup de SQLite, validación de integridad y tratamiento conservador de
  acciones cuyo resultado sea incierto.
- Guía de seguridad para simulación, autorización de mutaciones, límites de
  confianza de evidencia semántica y riesgos residuales de TOCTOU.
- Estándar permanente para el cierre de auditorías, con informe técnico fechado,
  resumen visible, manifiesto, evidencia y barrera de validación.
- Publicación generacional del inventario deduplicador mediante el esquema 7,
  con lector público `published_snapshots(root)` y migración conservadora desde
  el esquema 6.

### Corregido

- Las generaciones de inventario ya no se sobrescriben por una ruta global; el
  checkpoint sólo selecciona una exploración completa con conteos y bytes
  consistentes.
- Las exploraciones parciales no se publican ni se anuncian como completas, y
  un lote USN ambiguo no se aplica ni adelanta el último cursor durable seguro.
- La migración v1→v2 del historial del catálogo preserva filas conocidas y se
  abstiene ante columnas, triggers u objetos reservados desconocidos.
- Las acciones interrumpidas después de registrar intención quedan como
  `recovery_required`; el estado terminal ya no interpreta un PID reutilizado
  como propietario vivo y muestra el conteo incierto.
- Las conexiones de sincronización de cachés y derivados PDF activan claves
  foráneas; el lector aislado de perfiles PDF abre SQLite en modo de sólo
  lectura.
- La fuente semántica de imágenes selecciona sólo la generación de inventario
  válida más reciente y no mezcla filas de generaciones no publicadas.
- Las validaciones de argumentos posteriores al parseo usan el mismo código de
  salida `2` que `argparse`.

- La documentación de deduplicación distingue ahora el modo `fast`, que aplaza
  la comparación byte a byte hasta la aplicación, del modo `exact`, que la
  realiza al construir el plan. La aplicación sigue requiriendo verificación
  exacta antes de eliminar un duplicado.
- La misma documentación describe la exclusión de forma precisa: se protege el
  subárbol `AppData` del perfil efectivo; no se excluye por nombre cualquier
  directorio arbitrario llamado `AppData`.
- Los procedimientos de copia y restauración dejan de sugerir que copiar sólo
  un archivo `.sqlite3` abierto sea suficiente cuando existen WAL y SHM.
- La documentación operativa hace explícito que el watcher actual permanece en
  primer plano y que la primera preparación semántica o de audio puede adquirir
  modelos si no se activa el modo exclusivamente local.

### Compatibilidad

- La versión fuente y de paquete pasa de `0.4.0` a `0.4.1` por correcciones de
  integridad y por la migración de inventario 6→7; no se elevó por la auditoría
  documental por sí sola.
- Al primer uso escritor con `0.4.1`, `dedup.sqlite3` esquema 6 migra de forma
  transaccional a 7. Realice antes un backup SQLite consistente. Un esquema 6
  con estructura desconocida se conserva y la actualización se abstiene.
- Antes de una actualización debe comprobarse que el ejecutable `Neocortex`
  resuelve a la distribución esperada. La instalación pública `0.3.0`
  observada al iniciar la auditoría no representaba este árbol.
- No se documenta ni se admite un downgrade basado en alterar manualmente
  `PRAGMA user_version`.

### Limitaciones conocidas

- Permanecen riesgos abiertos de TOCTOU en syscalls por ruta, conciliación
  automática de efectos inciertos, visibilidad parcial semántica y de catálogo,
  transacción global del grafo de código, aplicación desigual de claves
  foráneas y retención incompleta.
- La evidencia semántica y probabilística es asesora: no autoriza por sí sola
  movimientos, renombres, reemplazos ni eliminaciones.
- No se ejecutó ninguna mutación sobre el corpus vivo para preparar estas guías.

## [0.4.0] - sin fecha de publicación verificada

La versión está declarada en el paquete fuente y en `pyproject.toml`. No se
reconstruye aquí un historial de cambios sin evidencia documental verificable.
