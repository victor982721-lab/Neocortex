# _02_Deduplicacion

Etapa compartida y no destructiva de deduplicación para las rutas de PDF,
imágenes y documentos. El orden es deliberado:

1. inventario de metadatos dentro de una raíz;
2. candidatos por tamaño exacto;
3. firma parcial XXH3-128 solo para candidatos grandes;
4. XXH3-128 completo solo para colisiones restantes;
5. con política `exact`, comparación byte a byte durante la planeación;
6. plan que conserva el archivo con `mtime` más reciente.

La política `fast` aplaza la comparación byte a byte hasta una aplicación
autorizada; `--apply` siempre vuelve a validar identidad, metadatos y bytes
antes de enviar un duplicado a la Papelera.

No elimina, mueve ni renombra archivos.

```python
from _02_Deduplicacion import DedupIndex, DedupPlanner

with DedupIndex(r"C:\ruta\estado\dedup.sqlite3") as index:
    scan = index.scan(r"C:\Corpus\Entrada")
    plan = DedupPlanner(index).plan(scan.scan_id)

for group in plan.groups:
    print("CONSERVAR", group.keep.path)
    for redundant in group.redundant:
        print("CANDIDATO", redundant.path)
```

La deduplicación se ejecuta mediante el comando integrado y comparte inventario,
estado, bloqueo operativo y políticas de seguridad con las demás rutas:

```powershell
Neocortex --root C:\Corpus\Entrada
```

Para inspeccionar únicamente los 10 grupos de mayor ahorro potencial:

```powershell
Neocortex --root C:\Corpus\Entrada --show-groups 10
```

El estado usa la base integrada `dedup.sqlite3` dentro del directorio de estado
de NeoCortex. `python -m _02_Deduplicacion` permanece temporalmente como fachada
de compatibilidad: delega al flujo integrado, emite un aviso de obsolescencia y
no mantiene una segunda base predeterminada. No se generan listados o reportes
auxiliares.

La aplicación de acciones pertenece exclusivamente a `_04_Nucleo_Operativo`; este
punto de entrada directo siempre es no destructivo.

El inventario excluye las rutas configuradas y cualquier directorio con el
atributo oculto de Windows. La configuración predeterminada excluye AppData,
la infraestructura privada `.codex`/`.cache` y los árboles de trabajo
`Neocortex\Laboratory`, `Laboratories`, `TestTemp`, `Lab`, `Checkpoints`,
`Backups`, `external_backups` y `Repository\Laboratory`.
También omite por nombre metadatos de VCS, entornos y dependencias (`.venv`,
`venv`, `site-packages`, `node_modules`), caches de herramientas y bytecode
`.pyc`/`.pyo`, además de árboles `.CDX` y temporales reconocibles de
`pytest`/`tmp`/`basetemp`/`inline-snapshot`. Nombres ambiguos como `build` y
`dist` siguen siendo elegibles fuera de las raíces de proyecto conocidas. Los
árboles `build`, `dist` y `wheelhouse` del repositorio Neocortex, del framework
EPS canónico y de su referencia histórica en OneDrive se excluyen por ruta
exacta porque son artefactos reconstruibles, no fuentes del corpus.
La raíz elegida explícitamente no se excluye a sí misma. Antes y después del
recorrido se valida su ruta canónica e identidad durable; una raíz que sea o
pase a ser un enlace, junction o punto de reanálisis se rechaza. El inventario
tampoco sigue esos objetos dentro del árbol.

Desde el esquema 7, las filas se aíslan por `(scan_id, path)`. Una exploración
se mantiene `building`, termina como `complete` o `partial`, y sólo una
generación completa con conteos y bytes consistentes puede publicarse. Para
leer la generación publicada de una raíz en una sola instantánea use
`DedupIndex.published_snapshots(root)`; no empareje por separado
`inventory_checkpoint(root)` y `snapshots(scan_id)` bajo concurrencia.
Una interrupción cierra la generación como `partial` con el prefijo confirmado;
al iniciar otro flujo integrado también se recupera idempotentemente cualquier
`building` heredado de una terminación abrupta.

Los hashes se guardan por identidad de archivo, tamaño, `mtime_ns`,
`birthtime_ns`, algoritmo y versión, de modo que las ejecuciones posteriores
reutilizan únicamente resultados válidos. Un cambio detectado durante lectura
invalida ese archivo en vez de aceptar una firma inconsistente.

Dependencia optimizada: `xxhash` (implementación nativa de XXH3). La firma no es
criptográfica y nunca se considera prueba suficiente para eliminar: la última
etapa compara bytes completos.
