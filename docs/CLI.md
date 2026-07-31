# Guía de la interfaz de línea de comandos

La interfaz pública canónica de NeoCortex es el ejecutable instalado
`Neocortex`. La definición exacta de argumentos vive en
`_04_Nucleo_Operativo/cli_parser.py`; esta guía resume los contratos operativos
que conviene conocer antes de usar `--help`.

## Comprobación previa de la instalación

Ejecute primero comandos que no recorren el corpus ni escriben estado:

```powershell
Neocortex --version
Neocortex --help
Neocortex --ui --help
```

La versión mostrada debe coincidir con la versión que se pretende operar. Si
`--version` no existe, la versión no coincide o la ayuda no contiene las rutas
esperadas, deténgase: el launcher instalado y el árbol fuente no representan la
misma entrega. No use una ruta nueva hasta actualizar y volver a comprobar el
entrypoint.

Esta fuente declara `0.7.1`. La fuente canónica está en
`%USERPROFILE%\Neocortex\Repository`; los runtimes versionados viven bajo
`%LOCALAPPDATA%\Programs\Neocortex\versions` y el launcher estable es
`%LOCALAPPDATA%\Programs\Neocortex\bin\Neocortex.exe`. Valide primero el
ejecutable exacto del runtime y promueva `bin` sólo después de esa barrera.

Desde la raíz del repositorio, el siguiente comando sirve únicamente para
diagnosticar el árbol fuente; no sustituye la validación del ejecutable
instalado:

```powershell
py -3 -m neocortex --version
```

## Sintaxis y rutas

```text
Neocortex [opciones]
```

`--root` selecciona la raíz que se observará. Si se omite, se usa el perfil del
usuario actual. Confirme siempre la ruta antes de iniciar una corrida:

```powershell
$Root = 'C:\Datos'
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "La raíz no existe o no es un directorio: $Root"
}
```

Las rutas de contenido vigentes en la CLI son:

| Nombre | Contenido principal |
|---|---|
| `pdf` | Extracción, OCR, FTS, perfiles y relaciones PDF. |
| `docx` | Documentos y plantillas OOXML de texto. |
| `office` | XLSX, PPTX y ODT. |
| `audio` | Audio y pistas de vídeo admitidas mediante Whisper. |
| `image` | Clasificación y evidencia de imágenes. |
| `code` | Texto, estructura, símbolos y relaciones de código fuente. |

Se acepta una ruta, una lista separada por comas o `--all`:

```powershell
Neocortex --root $Root --route pdf
Neocortex --root $Root --route pdf,docx,image
Neocortex --root $Root --all
```

Estos comandos **no son consultas de sólo lectura**: recorren contenido y
actualizan las bases de estado aunque no se especifique `--apply`. Sin
`--apply` no deben mutar los archivos del corpus, pero sí producen inventario,
cachés, ejecuciones, diagnósticos y planes persistentes.

`--all` no se combina con `--route` ni con operaciones directas de consulta o
diagnóstico.

## Modos de ejecución

### Corrida integrada

Una corrida normal actualiza el inventario común y después ejecuta las rutas
seleccionadas. La omisión de `--apply` es el modo predeterminado no mutador del
corpus:

```powershell
Neocortex --root $Root --route pdf,docx
```

`InternalPathsPolicy` reserva por ruta e identidad el repositorio, runtime,
datos de aplicación, laboratorio de autoanálisis y launcher. Una raíz normal
dentro de esos árboles se rechaza; sus descendientes internos se excluyen del
inventario. El estado no puede ser igual ni ancestro del corpus. La firma
efectiva durable combina la firma cruda de exclusión con la firma de rutas
internas.

### Autoanálisis protegido

El preset `--self-analysis` exige raíz y estado explícitos, fuerza exactamente
la ruta `code` en modo `analyze_only` y rechaza `--all`, `--apply`,
route-only/resume, selección, catálogo, organización y opciones que no consume.
Los árboles de raíz y estado deben ser completamente disjuntos:

```powershell
$Lab = Join-Path $env:LOCALAPPDATA 'Neocortex\self-analysis\fixtures'
$MiniRoot = Join-Path $Lab 'mini-root'
$MiniState = Join-Path $Lab 'mini-state'
Neocortex --self-analysis --root $MiniRoot --state-directory $MiniState
Neocortex --state-directory $MiniState --code-status --code-json
```

La corrida usa el inventario como entrada directa de code, no crea candidatos
MIME y sólo completa si candidatos, acciones y organización conservan conteos
exactos de cero. Su manifest guarda policy/firma, identidades, frescura y los
argv canónicos `analyze`/`status` como arrays, no como texto de shell.

`--code-status --code-json` consulta ese manifest sin crear ni migrar estado.
Cada propietario exige un snapshot SQLite immutable y sidecar-free. Cualquier
`-wal`, `-shm` o `-journal` —incluso vacío o desacoplado— junto a
`code.sqlite3`, `framework.sqlite3` o `dedup.sqlite3`, o una cerca inestable en
cualquiera de ellas, causa abstención total con código `2` sin tocar el estado.
El contrato, la puerta incremental de tres evidencias y el mini-root permitido
se detallan en [SELF_ANALYSIS.md](SELF_ANALYSIS.md).

### Ruta sobre un snapshot retenido

`--route-only` omite inventario, planeación de duplicados, detección común y
acciones de archivos. Requiere al menos una ruta, usa por defecto el snapshot
retenido más reciente y rechaza `--apply`:

```powershell
Neocortex --route pdf --route-only
Neocortex --route pdf --route-only --candidate-run 40
```

`--resume-run RUN_ID` implica `--route-only` y continúa fases incompletas del
snapshot indicado:

```powershell
Neocortex --resume-run 40
```

La ruta code consume el inventario y admite un snapshot con cero candidatos:

```powershell
$State = 'C:\Estado\Neocortex'
Neocortex --root $Root --state-directory $State --route code --route-only
Neocortex --root $Root --state-directory $State --route code --route-only --candidate-run 40
Neocortex --root $Root --state-directory $State --resume-run 40
```

Sin `--candidate-run`, code examina el owner durable más reciente de la raíz
exacta y exige que sea `normal`; si no coincide, falla sin retroceder a un run
histórico aunque éste tenga filas MIME. Cero candidatos se acepta únicamente si todas las rutas seleccionadas
declaran `input_source=inventory_snapshot`; cualquier ruta MIME o combinación
mixta falla antes de crear o ejecutar el run. El preset `--self-analysis` sigue
rechazando route-only/resume.

La corrida fuente debe conservar un snapshot de enrutamiento publicado: scan
completo, candidatos durables cuando la ruta los consume y raíz con la misma
ruta e identidad física. Los runs actuales publican ese vínculo sólo después de
terminar la generación de candidatos. Un run legacy interrumpido sin `scan_id`
sólo puede recuperarse si su evento de inventario es único y válido, los conteos coinciden y ya existe
evidencia durable de ejecución de rutas; cualquier ambigüedad rechaza la
reanudación sin reconstruir estado por inferencia.

No presuponga que cualquier corrida antigua continúa retenida. Compruebe
primero `--status`.

### Interfaz gráfica

```powershell
Neocortex --ui
Neocortex --ui --root $Root
```

La GUI supervisa el mismo orquestador, pero expone deliberadamente cinco rutas:
PDF, DOCX, Office, audio e imagen. La ruta `code` se opera mediante la CLI. El
worker `--gui-worker` es un contrato interno y no debe invocarse manualmente.

## Consultas y diagnósticos sin recorrido

Los siguientes ejemplos no inician un inventario ni autorizan mutaciones del
corpus:

```powershell
Neocortex --status
Neocortex --status --status-run 40 --status-json
Neocortex --pdf-doctor
Neocortex --pdf-verify
Neocortex --audio-doctor
Neocortex --code-status
Neocortex --code-doctor
Neocortex --semantic-status
Neocortex --action-recovery-status --action-recovery-limit 100
Neocortex --retention-status
```

Una base ausente, dañada o con esquema incompatible puede producir salida `2`;
eso no convierte el diagnóstico en una operación de reparación. NeoCortex no
ofrece actualmente un único flag `--doctor`. Los diagnósticos disponibles son
específicos de PDF/OCR, audio, código y estado semántico.

También son consultas directas las búsquedas y vistas persistidas, por ejemplo:

```powershell
Neocortex --pdf-search 'transformador AND mantenimiento'
Neocortex --docx-search 'transformador AND mantenimiento'
Neocortex --office-search 'transformador AND mantenimiento'
Neocortex --audio-search 'transformador AND mantenimiento'
Neocortex --code-search 'sqlite3' --code-search-mode import --code-language python
Neocortex --semantic-search 'transformador mantenimiento' --semantic-search-mode all
Neocortex --catalog-preview 100
Neocortex --organization-preview 100 --organization-preview-status planned
Neocortex --review-candidates 100
Neocortex --review-decisions 100
Neocortex --review-evidence-list 100 --review-json
```

Estas consultas leen las bases existentes. No crean evidencia que todavía no
haya sido materializada y pueden terminar con `2` cuando el estado requerido no
está disponible. La búsqueda semántica usa modelos ya preparados en modo local;
no autoriza una descarga implícita.

### Knowledge Plane de sólo lectura (`0.7.0`)

Knowledge ofrece tres acciones planas y mutuamente excluyentes. Todas leen el
estado ya publicado; no recorren el corpus, crean directorios o bases, migran
esquemas, reparan estado ni descargan modelos:

```powershell
Neocortex --knowledge-status
Neocortex --knowledge-status --knowledge-json
Neocortex --knowledge-search 'protección de transformador' --knowledge-limit 50
Neocortex --knowledge-context 'protección de transformador' --knowledge-limit 20 --knowledge-context-characters 24000
```

`--knowledge-status` captura el estado lógico acotado de los diez propietarios.
Si el directorio indicado por `--state-directory` no existe, informa cada
propietario como `absent`, devuelve `0` y deja la ruta sin crear. Search y
context compilan una consulta sobre los propietarios disponibles; con todo el
estado ausente devuelven `4` (parcial), no un falso “sin resultados”.
Si la ruta existe pero no es un directorio, o no puede abrirse y enumerarse en
lectura, Knowledge falla de forma cerrada: no la transforma en diez owners
`absent`, no emite un JSON engañoso y la CLI devuelve el código fatal `1` con
`KnowledgeStateRootError`. Esto incluye enlaces o reparse points cuyo destino
ya no existe y cambios de presencia de la raíz durante una captura. Un archivo
de owner sólo se declara `absent` cuando su path realmente no existe; si el
path existe pero es directorio, enlace roto o inaccesible, se aplica el mismo
fallo fatal. La inspección del sistema de archivos es síncrona: en una ruta UNC
o unidad de red desconectada, la cancelación sólo puede observarse cuando
Windows devuelve el control de `stat`/enumeración.

Las opciones de consulta son:

| Opción | Contrato |
|---|---|
| `--knowledge-limit N` | Predeterminado `20`. Search acepta `1..1000`; context, `1..100`. |
| `--knowledge-context-characters N` | Presupuesto máximo de ContextBundle. Predeterminado `12000`; context acepta `1..1000000`. |
| `--knowledge-mode evidence` | Predeterminado. En el canal semántico conserva la mejor coincidencia por `(item, entidad)` para no perder chunks o evidencias distintas. |
| `--knowledge-mode discovery` | En el canal semántico conserva la mejor coincidencia por item para una vista más colapsada. |
| `--knowledge-history` | Incluye revisiones `historical`/`superseded`, excluidas de forma predeterminada, y activa la ruta temporal del plan. |
| `--knowledge-json` | Emite el contrato JSON de la acción seleccionada en lugar de la presentación humana. |

Search y context exigen una consulta no vacía de hasta 4096 caracteres. Las
opciones limit/history/mode sólo se admiten con esas dos acciones;
`--knowledge-context-characters` exige context y se valida antes de ejecutar el
handler. `--knowledge-json` también se admite con status. Knowledge rechaza
`--apply`, `--route` y cualquier segunda acción directa. Ejemplos estructurados:

```powershell
Neocortex --knowledge-search 'IEC-61850' --knowledge-mode discovery --knowledge-json
Neocortex --knowledge-search 'protección de relevador' --knowledge-history --knowledge-limit 100
Neocortex --knowledge-context 'mantenimiento de interruptor' --knowledge-mode evidence --knowledge-json
```

El snapshot es lógico, no una transacción distribuida. Si cambia durante las
dos observaciones se reintenta una vez el conjunto completo; un segundo cambio
se informa mediante código `5` en vez de presentar la vista como estable. Los
detalles de publicaciones y watermarks están en
[PERSISTENCE.md](PERSISTENCE.md).

### Conciliación de acciones inciertas

El conciliador de `file_actions` es acotado, paginado por keyset, idempotente y
de sólo lectura. No crea ni migra `framework.sqlite3` y nunca repite una
mutación:

```powershell
Neocortex --action-recovery-status --action-recovery-limit 100
Neocortex --action-recovery-status --action-recovery-after 250 --action-recovery-run 40
Neocortex --action-recovery-status --action-recovery-json
```

Sólo inspecciona estados `applying` y `recovery_required`. Clasifica cada efecto
como `confirmed`, `not_performed`, `ambiguous` o `impossible_to_check` y emite
una recomendación, sin modificar el estado. Los filtros y
`--action-recovery-json` exigen `--action-recovery-status`. El límite admitido
es 1..1000, `--action-recovery-after` no puede ser negativo y el run debe ser
positivo. El código es `2` únicamente si aparece una clasificación
`ambiguous`/`impossible_to_check` o si la consulta no puede abrir/validar el
estado; una página vacía o sólo confirmada/no realizada devuelve `0`.
`confirmed` sólo documenta que el efecto original parece ocurrido;
`not_performed` tampoco convierte la intención original en reutilizable.
Ninguna clasificación autoriza una nueva syscall. Una versión framework futura
o metadata de versión no canónica se rechaza con `2`.

`status` permanece estrictamente de sólo lectura. Para conservar una observación
en framework v19 use una operación `record` explícita y separada:

```powershell
Neocortex --action-recovery-record 42 --action-recovery-actor "Victor" --confirm-reconciliation-record
Neocortex --action-recovery-record 42 --action-recovery-actor "Victor" --confirm-reconciliation-record --action-recovery-json
Neocortex --action-recovery-record 42 --action-recovery-actor "operador-2" --action-recovery-expected-event 7 --confirm-reconciliation-record
```

`record` vuelve a clasificar la acción, abre sólo una base existente y agrega un
evento append-only con CAS, clave idempotente, actor, procedencia, firma y
evidencia. La confirmación autoriza la escritura SQLite y una migración aditiva
soportada de la base existente; nunca crea la base ni autoriza una mutación de
archivos. Repetir exactamente el mismo registro devuelve el mismo evento. Un
predecesor obsoleto o una observación incompatible se rechaza.

Un registro correcto de `ambiguous` o `impossible_to_check` devuelve `2` para
que la incertidumbre no quede oculta, aunque el evento sí haya sido confirmado
en SQLite. No existen todavía comandos `decide`, `authorize`, `recover` ni
`verify`, ni una decisión o autorización humana durable para una nueva
mutación. No intente emular esas fases cambiando filas o reutilizando una
autorización original.

### Plan de retención no destructivo

`--retention-status` inspecciona páginas acotadas de `semantic`, `catalog`,
`inventory` y `framework` sin crear, migrar, eliminar, hacer checkpoint o
ejecutar `VACUUM`:

```powershell
Neocortex --retention-status
Neocortex --retention-status --retention-store semantic --retention-min-age-days 30 --retention-batch-size 100
Neocortex --retention-status --retention-store semantic --retention-semantic-after 250 --retention-json
```

`--retention-store` puede repetirse. El lote permitido es 1..1000 y los cursores
`--retention-<store>-after` son keyset. Sin edad explícita, el plan informa
`policy_not_configured` y no declara filas elegibles por antigüedad. Conserva
siempre las publicaciones vigente y anterior, el último estado válido,
builders/leases vivos, cadenas base y evidencia humana o incierta; en particular
las referencias `semantic_evidence` y el último run `completed` de framework
actúan como holds. Los bytes son una cota inferior del payload `TEXT`/`BLOB`,
no espacio físico garantizado. Cada base tiene un snapshot estable, pero la
consulta no es atómica entre bases y una apertura SQLite read-only puede
participar en WAL/SHM. Devuelve `2` si algún store queda bloqueado por deriva o
dependencia incompatible; ausencia segura o un plan listo devuelve `0`.

No existen opciones `--retention-prepare`, `--retention-apply` o
`--retention-verify`. La salida de status no autoriza un `DELETE` manual ni
demuestra que todas las referencias cross-store hayan permanecido estables.

## Caché, selección y reintentos

La validación rápida de caché usa metadatos por defecto. Para volver a comprobar
bytes antes de reutilizar resultados se dispone de:

```powershell
Neocortex --root $Root --route pdf --pdf-cache-validation full
Neocortex --root $Root --route code --code-cache-validation full
```

`full` aumenta la E/S; no cambia la semántica del contenido ya validado. No hay
un comando público general para “limpiar toda la caché”. No borre bases, WAL o
SHM manualmente.

Los errores permanentes o ya cacheados no se reintentan sólo por usar `--all`.
Los overrides explícitos son:

```text
--retry-pdf-errors
--retry-docx-errors
--retry-office-errors
--retry-audio-errors
--retry-image-errors
--retry-code-errors
```

Use los filtros `--select-status`, `--select-error-type`,
`--select-recommendation`, `--select-path` y `--failed-pages-only` únicamente
con una ruta y un snapshot compatibles. Consulte la ayuda viva para rangos y
combinaciones exactos:

```powershell
Neocortex --help
```

## Salida JSON

No existe un `--json` global. Los contratos estructurados actuales se activan
por familia:

| Opción | Alcance |
|---|---|
| `--status-json` | `--status`; exige `--status`. |
| `--review-json` | Candidatos, decisiones y evidencia de revisión; emite JSON Lines determinista. |
| `--code-json` | Estado, manifest/frescura de autoanálisis, búsquedas, proyectos o reconstrucción conceptual de código. |
| `--action-recovery-json` | JSON determinista por acción o evento; exige `--action-recovery-status` o `--action-recovery-record`. |
| `--retention-json` | Un documento JSON del plan dry-run; exige `--retention-status`. |
| `--knowledge-json` | Snapshot, resultado de búsqueda o contexto Knowledge; exige exactamente una acción `--knowledge-*`. |

No combine una opción JSON con una operación de otra familia. La salida humana
puede evolucionar; para automatización use sólo el contrato JSON correspondiente
y compruebe siempre el código de salida.

## Códigos de salida

| Código | Contrato observado |
|---:|---|
| `0` | Ayuda/versión o ejecución/consulta completada según su contrato. |
| `1` | Excepción fatal no normalizada o fallo interno del worker de GUI. No es el código de una validación ordinaria de argumentos. |
| `2` | Error de argumentos detectado por `argparse` o por la validación posterior, como una combinación incompatible; estado requerido ausente o incompatible; diagnóstico fallido —incluida abstención total de `--code-status` ante cualquier sidecar o cerca inestable en code/framework/Dedup—; error de acciones u organización; conciliación con efecto ambiguo/imposible —incluso si su evento fue registrado—; plan de retención bloqueado; o, con `--strict-exit-codes`, errores/parciales retenidos por una ruta. El watcher también devuelve `2` si conserva corridas fallidas o errores de fuente. |
| `3` | Knowledge terminó con snapshot estable y cobertura completa, pero search/context no obtuvo evidencia. |
| `4` | Knowledge produjo una respuesta parcial o no soportada; incluye propietarios necesarios ausentes. |
| `5` | El snapshot Knowledge volvió a cambiar durante el único reintento global acotado. |
| `6` | Knowledge encontró al menos un esquema futuro o incompatible y se abstuvo. |
| `7` | Knowledge detectó una base corrupta y se abstuvo. |
| `130` | Cancelación por teclado o cancelación del watcher. |
| otro no cero | Fallo no normalizado. Trátelo como fatal y preserve la evidencia. |

Para las acciones Knowledge la precedencia es `7`, `6`, `5`, `4`, `3`, `0`:
un problema de integridad o compatibilidad nunca queda oculto por una página
vacía o parcial. El status con propietarios simplemente ausentes conserva `0`;
la ausencia pasa a `4` cuando impide completar search/context.

Sin `--strict-exit-codes`, errores de documentos individuales pueden quedar
registrados aunque la corrida general termine con `0`. Para automatización que
exija integridad completa de todas las rutas:

```powershell
Neocortex --root $Root --all --strict-exit-codes
```

## Operaciones que requieren autorización explícita

`--apply` permite que una corrida integrada ejecute únicamente las mutaciones
que satisfacen el contrato físico de `0.7.0`. Los rename de extensión y los
movimientos de organización requieren NTFS local, mismo volumen, archivo
regular con un único hard link, ausencia de reparse y operación ligada a handles
retenidos con *no-replace*. UNC, otros filesystems, directorios, múltiples hard
links y movimientos entre volúmenes se abstienen.

Los candidatos de Papelera (duplicados, vacíos y PDF irrecuperables) se siguen
planeando en dry-run, pero su aplicación está deshabilitada porque la API
disponible opera por ruta. Con `--apply` terminan `skipped` con evidencia de
abstención; no se invoca `Send2Trash`. No hay flag para degradar a la operación
path-bound.

La organización persistida dispone además de una autorización directa distinta:

```powershell
Neocortex --organization-apply --organization-max-actions 100
```

Un plan que cruzó la frontera nativa sin confirmación queda
`recovery_required`, reserva su destino y no vuelve a seleccionarse para
aplicación. Se consulta sin mutar con:

```powershell
Neocortex --organization-preview 100 --organization-preview-status recovery_required
```

No copie estos comandos como prueba de instalación. Antes de cualquiera de las
dos autorizaciones, revise [SECURITY.md](SECURITY.md) y
[RECOVERY.md](RECOVERY.md), cree un backup SQLite consistente y confirme la raíz
y los planes. El watcher y `--route-only` rechazan `--apply`.

## Operaciones con otros efectos laterales

- `--semantic-prepare-models` adquiere o carga explícitamente modelos.
- Una primera ruta de audio puede descargar el modelo Whisper salvo que se use
  `--audio-local-models-only`.
- `--semantic-index`, `--semantic-classify`, `--catalog-documents`,
  `--organization-plan`, `--review-record` y `--review-evidence-sync` escriben
  estado, aunque no muten archivos originales.
- Semántica y catálogo construyen staging invisible y sólo cambian su
  generación publicada mediante una transacción CAS completa.
- `--watch` permanece en primer plano hasta cancelarse y genera nuevas corridas
  integradas.

Consulte [OPERATIONS.md](OPERATIONS.md) antes de usar watcher, reanudación,
límites de recursos o mantenimiento.
