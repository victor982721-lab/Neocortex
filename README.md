# NeoCortex

NeoCortex es un framework incremental para descubrir, identificar, extraer,
clasificar, revisar y buscar documentos, imágenes, audio y código mediante un
inventario y estado compartidos. Conserva evidencia, incertidumbre y versiones
de procesamiento. El modo predeterminado no modifica el corpus.

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
& "$Venv\Scripts\python.exe" -m pip install -c constraints.txt .
```

La base mínima instala únicamente `rich` y `xxhash`. Conserva el entrypoint,
inventario, código y las consultas Knowledge sobre estado existente, sin
forzar runtimes de documentos, audio, imagen, embeddings o UI. Las capacidades
opcionales se instalan por dominio:

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

Para el runtime integrado completo y para desarrollo:

```powershell
& "$Venv\Scripts\python.exe" -m pip install -c constraints.txt ".[full]"
& "$Venv\Scripts\python.exe" -m pip install -c constraints.txt ".[full,dev]"
```

También se pueden combinar sólo los dominios necesarios, por ejemplo
`.[documents,audio]`. Pillow se declara directamente en `documents`, `image`
y `semantic` porque los tres dominios lo importan en sus rutas propias.

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
archivos originales:

```powershell
Neocortex --root C:\Datos --route pdf,docx
```

Las rutas vigentes son `pdf`, `docx`, `office`, `audio`, `image` y `code`.
También se admite una lista separada por comas o `--all`. Las búsquedas operan
sobre estado ya construido, por ejemplo:

```powershell
Neocortex --code-search "dónde se valida el acceso a SQLite" --code-search-mode hybrid
Neocortex --pdf-search "transformador AND mantenimiento"
```

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
```

El primer comando escribe inventario y estado de código; no es una consulta de
sólo lectura. El status sí es estrictamente read-only: cualquier `-wal`, `-shm`
o `-journal` junto a `code.sqlite3`, `framework.sqlite3` o `dedup.sqlite3`,
incluso vacío o desacoplado, causa abstención total con código `2` sin tocar el
estado. Consulte [Autoanálisis protegido](docs/SELF_ANALYSIS.md) antes de usar
el preset.

### Knowledge Plane de sólo lectura

La Fase 1 ofrece una recuperación unificada sobre el estado ya producido por
inventario, FTS de documentos, catálogo, semantic y código. Captura un
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

El preflight semántico proyecta trabajo exacto sobre las cachés durables sin
cargar modelos, crear jobs ni mutar estado durable:

```powershell
Neocortex --semantic-plan text --semantic-plan-json
Neocortex --semantic-plan image --semantic-plan-max-scratch-bytes 536870912
```

El plan informa recursos, contenido único, reutilización, bytes vectoriales
como cota inferior y solicitudes al modelo como rango inferior/superior. El
tiempo de modelo sólo tiene rango cuando la API de servicio recibe una
calibración exacta compatible; la CLI no inventa esa calibración. Cada base
física se observa en su propia transacción con fences de cambio y el SQLite
scratch privado tiene una cuota dura predeterminada de 512 MiB.

La planificación de imagen es deliberadamente *cache-only*: no reabre
originales. Por ello informa `originals_verified=false`,
`execution_ready=null` y `complete=false`; un plan calculado no certifica que
la ejecución posterior esté lista.

## Uso seguro

`--apply` y `--organization-apply` son autorizaciones explícitas para mutar
archivos; no son necesarias para indexar o buscar. En `0.7.0`, rename y
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
- [Cierre de Fase 1 Knowledge](docs/KNOWLEDGE_EVOLUTION_2026-07-26_010033.md)
- [Handoff de evolución técnica](docs/TECHNICAL_EVOLUTION_HANDOFF_2026-07-29_082142.md)
- [Persistencia y migraciones](docs/PERSISTENCE.md)
- [Recuperación y rollback](docs/RECOVERY.md)
- [Seguridad y operaciones sobre archivos](docs/SECURITY.md)
- [Registro de cambios](docs/CHANGELOG.md)
- [Inventario técnico de licencias de terceros](docs/THIRD_PARTY_LICENSE_INVENTORY.md)
- [Estándar de cierre de auditorías](docs/AUDIT_REPORTING_STANDARD.md)
- [Núcleo operativo](_04_Nucleo_Operativo/README.md)

Las auditorías históricas se conservan sin sobrescritura. El informe integral
de la campaña 0.7.1 está en
[docs/TECHNICAL_EVOLUTION_2026-07-26_173000.md](docs/TECHNICAL_EVOLUTION_2026-07-26_173000.md).
La instrucción vigente para continuar la evolución está en
[docs/TECHNICAL_EVOLUTION_HANDOFF_2026-07-29_082142.md](docs/TECHNICAL_EVOLUTION_HANDOFF_2026-07-29_082142.md).
