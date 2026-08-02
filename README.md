# NeoCortex

NeoCortex es un framework incremental para descubrir, identificar, extraer,
clasificar, revisar y buscar documentos, imágenes, audio y código mediante un
inventario y estado compartidos. Conserva evidencia, incertidumbre y versiones
de procesamiento. El modo predeterminado no modifica el corpus.

## Empieza aquí

El flujo personal normal es deliberadamente corto:

1. Comprueba el runtime y el estado de la capacidad que necesitas.
2. Busca primero en el estado ya publicado.
3. Si falta cobertura, procesa una sola ruta sobre 20–50 archivos con un límite
   duro de 10–15 minutos.
4. Verifica una salida útil y repite la misma corrida para comprobar que es
   incremental.
5. Escala sólo después de revisar resultados, errores, velocidad y proyección.

`--all`, el watcher, una indexación Semantic completa, una auditoría integral y
un ciclo de release no son pruebas iniciales. Si el piloto no produce algo útil,
se detiene y se corrige. La continuación técnica vigente, con el estado
observado y la siguiente acción única, está en el
[handoff operativo 0.7.2](.codex/handoffs/NEOCORTEX_0.7.2_PAUSE_2026-07-30.md).

Esta precaución aplica al arranque y diagnóstico, no elimina la experiencia
simple buscada: una vez validado el entorno, `Neocortex --all --apply` debe ser
el comando cotidiano integrado que haga todo lo soportado y aplique sólo
acciones seguras. Las etapas aún no conectadas deben integrarse al comando, no
convertirse en trabajo manual para Victor.

## Topología canónica por usuario

La fuente, el runtime y el estado ocupan árboles separados:

```text
Fuente:       %USERPROFILE%\Neocortex\Repository
Runtime:      %LOCALAPPDATA%\Programs\Neocortex\versions\<runtime-id>\venv
Launcher:     %LOCALAPPDATA%\Programs\Neocortex\bin\Neocortex.exe
Estado:       %LOCALAPPDATA%\Neocortex\state
Autoanálisis: %LOCALAPPDATA%\Neocortex\self-analysis
```

Cada runtime es versionado e inmutable. El ejecutable de `bin` es la única ruta
estable que debe incorporarse al `PATH`; sólo se promueve después de validar el
artefacto y su entorno aislado. La invocación pública continúa siendo
`Neocortex`.

## Instalación compatible

El paquete actual requiere Windows y CPython `>=3.13,<3.14`. Instálelo primero
en un entorno virtual aislado fuera del repositorio; no ejecute `pip install .`
contra el Python global:

```powershell
$Repository = Join-Path $HOME 'Neocortex\Repository'
$RuntimeId = '0.7.2-artifact-id' # sustituya por el identificador validado
$Venv = Join-Path $env:LOCALAPPDATA "Programs\Neocortex\versions\$RuntimeId\venv"
py -3 -m venv $Venv
Set-Location -LiteralPath $Repository
& "$Venv\Scripts\python.exe" -m pip install -c constraints.txt ".[full]"
```

Para el uso personal de Victor, `full` es la instalación canónica: el comando
`Neocortex` debe exponer documentos, audio, imagen, Semantic y UI sin exigirle
elegir perfiles. Si una capacidad central aparece `unavailable`, se repara la
instalación o su declaración antes de operar; no se trata como una decisión
cotidiana del usuario.

Los extras individuales se conservan únicamente como detalle de empaquetado y
desarrollo:

| Extra | Runtime añadido |
|---|---|
| `documents` | PDF, fallback pdfminer y OCR documental |
| `audio` | transcripción local con faster-whisper |
| `image` | decodificación Pillow y clasificación NudeNet |
| `semantic` | embeddings texto/imagen con FastEmbed y NumPy |
| `ui` | interfaz PySide6 |
| `full` | unión compatible de los cinco dominios anteriores |

Algunas rutas conservan prerrequisitos externos que ningún extra de Python
puede instalar: `ffprobe` es obligatorio para `audio`; `tesseract` y `qpdf`
habilitan OCR y recuperación PDF degradables en `documents`; y `tesseract`
habilita el OCR documental degradable en `image`. El probe ligero sólo busca
estos ejecutables en `PATH`; no interpreta overrides de una ejecución concreta.

Para desarrollo sobre el runtime completo:

```powershell
& "$Venv\Scripts\python.exe" -m pip install -c constraints.txt ".[full,dev]"
```

Los mantenedores pueden combinar dominios para probar el empaquetado, por
ejemplo `.[documents,audio]`. Esa modularidad no cambia el producto personal
canónico. Pillow se declara directamente en `documents`, `image` y `semantic`
porque los tres dominios lo importan en sus rutas propias.

La API pública ligera permite inspeccionar prerrequisitos sin importar engines
ni cargar o descargar modelos:

```python
from neocortex.capabilities import inspect_runtime_capabilities

for capability in inspect_runtime_capabilities():
    print(capability.capability, capability.state.value)
```

La misma inspección está disponible como doctor canónico de sólo lectura:

```powershell
Neocortex doctor capabilities
Neocortex doctor capabilities --json
```

El doctor usa únicamente declaraciones, metadata de distribuciones, specs de
import y resolución de ejecutables. No importa engines opcionales, no carga o
descarga modelos y no crea estado.

`available` significa que todos los componentes declarados están presentes;
`degraded`, que falta sólo una función opcional —por ejemplo OCR, fallback PDF
o clasificador adulto—; y `unavailable`, que falta un requisito obligatorio de
la capacidad. Este probe no certifica cachés de modelos, idiomas OCR ni que un
backend pueda ejecutar inferencia; esas comprobaciones profundas permanecen
separadas y offline. Sus estados representan presencia, no conformidad con los
rangos de versiones de `pyproject.toml`: esa compatibilidad la deben cerrar el
resolver del entorno y `pip check` antes de promover el runtime.

Una instalación offline sólo es hermética si el wheelhouse contiene el cierre
completo de artefactos y del backend de build; consulte
[Instalación offline](docs/OFFLINE_INSTALLATION.md). Una instalación
`--no-deps` o con `--system-site-packages` sirve para pruebas acotadas, pero no
demuestra ese cierre.

Compruebe siempre el entrypoint de ese entorno antes de operar. Tras activarlo,
la invocación canónica es `Neocortex`; sin activación puede validar el ejecutable
por su ruta exacta:

```powershell
& "$Venv\Scripts\Neocortex.exe" --version
& "$Venv\Scripts\Neocortex.exe" --help
```

`Neocortex --status` es un diagnóstico del estado persistente, no una prueba de
instalación: en un entorno nuevo sin `framework.sqlite3` devuelve `2` de forma
esperada y no crea la base.

La versión fuente de esta entrega es `0.7.2`. Si el ejecutable exacto del
runtime no informa `0.7.2` o no reconoce las opciones de esta guía, deténgase y
valide el artefacto en un entorno aislado antes de promover el launcher estable.

## Primer uso y rutas

Una corrida sin `--apply` actualiza inventarios y cachés, pero preserva los
archivos originales. El primer uso debe cubrir una sola ruta y como máximo
20–50 archivos:

```powershell
Neocortex --root C:\Datos --route pdf --MaxCount 25
```

Las rutas vigentes son `pdf`, `docx`, `office`, `audio`, `image` y `code`.
Las listas y `--all` se reservan para después de aprobar cada ruta y su
proyección. Las búsquedas operan sobre estado ya construido, por ejemplo:

```powershell
Neocortex --code-search "dónde se valida el acceso a SQLite" --code-search-mode hybrid
Neocortex --pdf-search "transformador AND mantenimiento"
```

### Código con recuperación semántica integrada

Después de construir la ruta `code`, Semantic puede publicar embeddings de sus
chunks y sincronizar el puente existente de Code en la misma operación:

```powershell
Neocortex --semantic-index text --semantic-source code
Neocortex --code-search "dónde se valida el acceso a SQLite" --code-search-mode hybrid
```

La sincronización sólo acepta el head Semantic `ready` que acaba de publicarse y
liga cada chunk vigente de Code con item, modelo, espacio vectorial y generación
exactos. Un replay sin cambios no crea otra generación ni reescribe enlaces; si
cambia una versión, los enlaces anteriores quedan inactivos como historial.
`--code-status` informa enlaces activos, vigentes y obsoletos.

Al terminar una publicación Code, el productor hace checkpoint y retira los
sidecars reconstruibles sólo si el WAL quedó vacío. Si otro lector mantiene los
handles abiertos, la corrida no invalida el estado publicado, pero el status
quiescente se abstiene hasta que ese lector cierre y una corrida posterior pueda
retirar los auxiliares.
Los lectores operativos de una base quiescente usan una instantánea immutable
con cercas antes/después, por lo que búsqueda y listado ya no crean `-wal` o
`-shm`; si ya existe un writer activo, leen con SQLite read-only sin borrar ni
cerrar auxiliares ajenos.

La búsqueda `semantic` consume únicamente esos enlaces exactos. Si falta el head,
la cobertura o el modelo local, declara `CODE_SEARCH_CHANNEL available=0` con la
causa y el modo exclusivamente semántico devuelve `2`; `hybrid` conserva las
señales léxicas y estructurales disponibles. El score es similitud no calibrada y
sólo evidencia de recuperación: no autoriza clasificación ni mutación. El cache
canónico es el del estado; `--semantic-model-cache DIRECTORIO` se reserva para
un cache local explícito, por ejemplo en un laboratorio aislado.

### Autoanálisis protegido de código

`--self-analysis` ejecuta sólo la ruta `code` sobre una raíz explícita en modo
`analyze_only`. Exige un estado externo cuyo árbol sea completamente disjunto,
omite candidatos MIME, acciones, catálogo y organización, y finaliza sólo si
los conteos de trabajo sobre el corpus permanecen en cero:

```powershell
$Lab = Join-Path $env:LOCALAPPDATA 'Neocortex\self-analysis\fixtures'
$MiniRoot = Join-Path $Lab 'mini-root'
$MiniState = Join-Path $Lab 'mini-state'
Neocortex --self-analysis --root $MiniRoot --state-directory $MiniState
Neocortex --state-directory $MiniState --code-status --code-json
Neocortex --state-directory $MiniState --code-review
```

El primer comando escribe inventario y estado de código; no es una consulta de
sólo lectura. El status sí es estrictamente read-only: cualquier `-wal`, `-shm`
o `-journal` junto a `code.sqlite3`, `framework.sqlite3` o `dedup.sqlite3`,
incluso vacío o desacoplado, causa abstención total con código `2` sin tocar el
estado. Consulte [Autoanálisis protegido](docs/SELF_ANALYSIS.md) antes de usar
el preset.

Si el proceso no puede abrir el journal USN, el autoanálisis degrada a un
recorrido completo portable. No publica checkpoint ni inventa cursor: el
manifest registra `journal.status=unavailable`, el status nunca afirma
`current=true` y la ruta code todavía reutiliza por caché los archivos sin
cambios.

`--code-review` convierte la publicación en una lista de mantenimiento
explicable. El envelope v3 conserva el ranking bruto y hasta tres recomendaciones
`act_now` de v2, y añade un único `work_package` como siguiente cambio coherente.
Ese paquete mantiene una sola recomendación raíz, enlaza como `contract_guard`
los hotspots alcanzables por llamadas estáticas confirmadas a uno o dos saltos,
y enumera contratos, orden, validación y gates de publicación. El horizonte de
planeación permanece fijo en 50 aunque la vista muestre 10; no agrupa por nombre,
directorio ni prefijo de módulo. `--code-review-limit N --code-json` amplía de 1
a 50 la vista auditable. La consulta es estrictamente read-only y el paquete es
consejo, nunca autorización de cambio. Los avisos heurísticos de código
probablemente muerto se cuentan pero no se recomiendan hasta calibrar su
resolución de llamadas. Un snapshot full completado sin USN se etiqueta
`publication_only`; un journal avanzado/discontinuo o un vínculo incompatible
causa abstención con código `2`.

`--code-publication-diff` compara dos publicaciones Code completadas sin
escribirlas. Informa calls comunes, resoluciones nuevas/corregidas/perdidas,
hotspots añadidos o retirados y el delta no calibrado de `probable_dead`. Exige
bases quiescentes, limita la enumeración y conserva ejemplos en `--code-json`.
Los cambios de rango aparecen como sitios exclusivos, no como una mejora o
regresión inventada.

La corrida normal usa el mismo baseline portable cuando USN no existe o deja
de estar disponible: publica el snapshot completo con cursor nulo y las rutas
comparan ese inventario contra sus caches. USN permanece como acelerador en
Windows, no como requisito de corrección. `journal_usn_span=unavailable`
distingue esa ejecución; las acciones continúan sujetas a sus revalidaciones
de identidad, contenido y destino.
El watcher aplica la misma política: USN despierta corridas cuando está
disponible y, sin cursor compatible, programa inventarios normales portables a
intervalos explícitos sin crear otro índice. Entre ciclos recarga el dueño
durable mediante una instantánea immutable cercada, por lo que no recrea
sidecars de Framework sobre una publicación quiescente.

### Knowledge Plane de sólo lectura

La Fase 1 implementa el contrato de recuperación unificada sobre el estado ya
producido por inventario, FTS de documentos, catálogo, Semantic y código.
Su disponibilidad operativa no se presupone: primero se ejecuta
`--knowledge-status`. `status` conserva la vista global y devuelve `6` o `7`
ante cualquier owner incompatible o corrupto; `search` y `context` sólo se
abstienen cuando ese owner aparece en `blocking_owners`. Un owner severo ajeno
a los rankings requeridos permanece visible sin ocultar evidencia sana.

Framework schema 19 es la única compatibilidad legacy explícita: se admite
sólo en lectura cuando satisface exactamente el contrato estructural esperado,
se marca `legacy_schema_read_compatible:19->20` y nunca se migra.

Cuando los owners requeridos por la consulta son utilizables, Knowledge captura un
`KnowledgeSnapshot` lógico —no una transacción distribuida—, construye un plan
determinista, fusiona rankings sin confundir sus scores y compila contexto con
citas y presupuesto explícitos. El modo `evidence`, predeterminado, puede
conservar varias evidencias concretas del mismo recurso; `discovery` prioriza un
resultado semantic por recurso.

```powershell
Neocortex --knowledge-status
Neocortex --knowledge-search "protección diferencial de transformador" --knowledge-mode evidence
Neocortex --knowledge-context "protección diferencial de transformador" --knowledge-limit 12
```

Estas operaciones no crean `knowledge.sqlite3`, no migran bases, no reprocesan
el corpus y no autorizan mutaciones. Cada `ContextBundle` marca primero la
frontera `untrusted-corpus-data-v1`, antes de la consulta y de la evidencia
dinámica: el contenido recuperado es dato no confiable y no tiene autoridad
para emitir instrucciones, seleccionar herramientas ni autorizar acciones. El
payload se preserva y cita para mantener su trazabilidad; no se promociona a
instrucciones del consumidor.

La API Python canónica y tipada PEP 561 `neocortex.sdk` expone los mismos
contratos, planner, snapshot y `KnowledgeSearchService` sin retirar los imports
legacy. El golden actual usa candidatos de owner scripted: valida contratos y
orquestación, pero no sustituye una evaluación humana ni demuestra calidad
sobre el corpus real. El grafo transversal entre owners y una superficie MCP
pertenecen a una fase posterior. Consulte
[Knowledge Plane](docs/KNOWLEDGE.md) para contratos, completitud, códigos de
salida y límites verificables.

### Plan semántico de sólo lectura

El preflight semántico hace un inventario exacto de las cachés durables sin
cargar modelos, crear jobs ni mutar estado durable:

```powershell
Neocortex --semantic-plan text --semantic-plan-json
Neocortex --semantic-plan image --semantic-plan-max-scratch-bytes 536870912
```

El plan informa recursos, contenido único, reutilización, bytes vectoriales
como cota inferior y solicitudes al modelo como rango inferior/superior. El
canal textual se marca
`model_only_request_range_from_pre_tokenizer_content_projection`: el productor
liga después el tokenizador exacto y puede dividir más chunks. Por eso ese rango
no sustituye el piloto acotado. Imagen sin OCR conserva proyección exacta. El
tiempo de modelo sólo tiene rango cuando la API de servicio recibe una
calibración exacta compatible; la CLI no inventa esa calibración. Cada base
física se observa en su propia transacción con fences de cambio y el SQLite
scratch privado tiene una cuota dura predeterminada de 512 MiB.

La planificación de imagen es deliberadamente *cache-only*: no reabre
originales. Por ello informa `originals_verified=false`,
`execution_ready=null` y `complete=false`; un plan calculado no certifica que
la ejecución posterior esté lista.

Antes de cualquier indexación compruebe `--semantic-status`. Cero modelos
publicados o cero embeddings significa que la señal semántica aún no está
entregada. `--semantic-index` usa por defecto un único presupuesto compartido
de 50 items nuevos o cambiados, 1 500 jobs durables nuevos o reactivados y
900 segundos. Los replays exactos no consumen los dos primeros límites.

La indexación textual publica también un título durable derivado únicamente del
basename, sin directorios ni la extensión final. La búsqueda lo mantiene como
señal semántica separada y advisory: fusiona cuerpo (peso `1.0`) y título (peso
`0.5`) por RRF, conserva la procedencia de ambos y devuelve el snippet corporal
cuando existe. Clasificación, evidencia materializada y el modo Knowledge
`evidence` continúan consumiendo sólo contenido. El modo Knowledge `discovery`
puede usar el título únicamente como prior de recurso con peso `0.5`, y sólo si
ese mismo recurso y revisión ya tienen evidencia corporal; nunca lo serializa
como evidencia. Un título nunca autoriza mover, renombrar o borrar.
Un head legado sin ese canal informa `title_channel_not_indexed` hasta una
publicación acotada compatible.

Si se agota un límite, la salida marca `truncated=1`, devuelve `2` y conserva la
generación sin publicar; el head anterior no cambia. Una generación
`bounded-v1` sólo puede publicarse después de confirmar la enumeración completa.
Valide primero sobre 20–50 elementos: embeddings, publicación, búsquedas reales
y segunda corrida incremental.

## Uso seguro

`--apply` y `--organization-apply` son autorizaciones explícitas para mutar
archivos; no son necesarias para indexar o buscar. En `0.7.2`, rename y
organización sólo operan sobre un archivo regular con un único hard link, en
NTFS local y en el mismo volumen, mediante handles retenidos y semántica
*no-replace*. Rutas UNC, otros filesystems, reparses, directorios y movimientos
entre volúmenes provocan abstención. La planeación en seco conserva candidatos
de Papelera, pero la aplicación por ruta está deshabilitada y se registra como
`skipped`; `Send2Trash` ya no es una dependencia.

Una acción que cruzó la frontera de mutación sin poder confirmar el registro
queda `recovery_required` y nunca se repite automáticamente. `status` sólo
clasifica; `record` persiste explícitamente esa observación append-only, sin
autorizar ni ejecutar una recuperación:

```powershell
Neocortex --action-recovery-status --action-recovery-limit 100
Neocortex --action-recovery-status --action-recovery-json
Neocortex --action-recovery-record 42 --action-recovery-actor "Victor" --confirm-reconciliation-record --action-recovery-json
```

No existen todavía fases productivas `decide`, `authorize`, `recover` o
`verify`. `confirmed` y `not_performed` son clasificaciones de evidencia,
no permisos para repetir una syscall.

El planificador de retención es también diagnóstico y no destructivo. No poda,
no aplica cuotas ni ejecuta `VACUUM` o checkpoints. Conserva las publicaciones
vigente/anterior, evidencia semántica, el último run válido y holds cross-store;
no existen `prepare/apply/verify` productivos:

```powershell
Neocortex --retention-status
Neocortex --retention-status --retention-store semantic --retention-min-age-days 30 --retention-json
```

Los lectores oficiales de catálogo y semántica seleccionan únicamente la
generación publicada; el staging incompleto permanece invisible y la
publicación cambia su puntero mediante una transacción CAS. Los embeddings y
clasificaciones probabilísticas nunca autorizan por sí solos una mutación.
Antes de actualizar una instalación con bases existentes, realice un backup
consistente mediante la API SQLite; no copie sólo el `.sqlite3` si puede existir
WAL.

## Documentación

- [Guía de CLI](docs/CLI.md)
- [Operación y watcher](docs/OPERATIONS.md)
- [Instalación offline y wheelhouse](docs/OFFLINE_INSTALLATION.md)
- [Arquitectura](docs/ARCHITECTURE.md)
- [Autoanálisis protegido de código](docs/SELF_ANALYSIS.md)
- [Knowledge Plane](docs/KNOWLEDGE.md)
- [Handoff operativo vigente](.codex/handoffs/NEOCORTEX_0.7.2_PAUSE_2026-07-30.md)
- [Persistencia y migraciones](docs/PERSISTENCE.md)
- [Recuperación y rollback](docs/RECOVERY.md)
- [Seguridad y operaciones sobre archivos](docs/SECURITY.md)
- [Registro de cambios](docs/CHANGELOG.md)
- [Inventario técnico de licencias de terceros](docs/THIRD_PARTY_LICENSE_INVENTORY.md)
- [Estándar de cierre de auditorías](docs/AUDIT_REPORTING_STANDARD.md)
- [Núcleo operativo](_04_Nucleo_Operativo/README.md)

### Referencia histórica; no es flujo de trabajo

Las auditorías y handoffs anteriores se conservan como evidencia, pero no deben
ejecutarse como instrucciones vigentes:

- [Cierre histórico de Fase 1 Knowledge](docs/KNOWLEDGE_EVOLUTION_2026-07-26_010033.md)
- [Informe integral histórico 0.7.1](docs/TECHNICAL_EVOLUTION_2026-07-26_173000.md)
- [Handoff técnico histórico 0.7.1](docs/TECHNICAL_EVOLUTION_HANDOFF_2026-07-29_082142.md)
