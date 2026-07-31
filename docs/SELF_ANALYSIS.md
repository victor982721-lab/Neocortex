# Autoanálisis protegido del código fuente

> **Estado del contrato.** Esta capacidad pertenece a la fuente `0.7.1` bajo
> `%USERPROFILE%\Neocortex\Repository`. Debe ejecutarse desde un runtime
> versionado bajo `%LOCALAPPDATA%\Programs\Neocortex\versions` y promoverse
> únicamente mediante el launcher estable
> `%LOCALAPPDATA%\Programs\Neocortex\bin\Neocortex.exe` después de validar el
> artefacto exacto.

## Finalidad y frontera

`--self-analysis` analiza una raíz de código explícita como evidencia no
confiable sin autorizar trabajo común sobre el corpus. El preset:

- fija `corpus_access_mode=analyze_only` y ejecuta exactamente la ruta `code`;
- alimenta `CodeRoute` desde el snapshot de inventario, no desde candidatos
  MIME;
- omite `DedupPlanner`, `FrameworkActions`, catálogo y organización;
- excluye código generado y vendorizado;
- escribe estado derivado en un directorio separado, pero no modifica los
  archivos de la raíz analizada.

No es una consulta read-only: crea o actualiza `framework.sqlite3`,
`dedup.sqlite3` y `code.sqlite3` en el estado indicado. El código observado se
trata como datos; nunca se ejecuta ni adquiere autoridad para pedir
herramientas, permisos, red o mutaciones.

## Preflight y disjunción obligatoria

La CLI exige que `--root` y `--state-directory` aparezcan de forma explícita.
Antes de crear estado valida una raíz local canónica, sin reparse, captura su
identidad física y comprueba que los dos árboles sean disjuntos en ambas
direcciones: el estado no puede ser la raíz, descendiente de ella ni ancestro
de ella. La frontera se vuelve a comprobar antes y después de crear el estado y
en los fences de E/S; un alias, reparse, cambio de identidad o intersección
indemostrable causa abstención.

El preset rechaza `--all`, `--apply`, `--route-only`, `--candidate-run`,
`--resume-run`, selecciones, operaciones directas, rutas distintas de `code`,
catálogo, organización y opciones de otras rutas. También rechaza habilitar
generated o vendored. Estos rechazos son parte del contrato, no sugerencias de
uso.

## Comandos canónicos y argv reproducible

Con un artefacto instalado que coincida con esta fuente, la forma canónica es:

```powershell
$Root = Join-Path $HOME 'Neocortex\Repository'
$State = Join-Path $env:LOCALAPPDATA 'Neocortex\self-analysis\smokes\run-id'
Neocortex --self-analysis --root $Root --state-directory $State
Neocortex --state-directory $State --code-status --code-json
```

El manifest no guarda una cadena para reinterpretar en un shell. Guarda dos
arrays `argv` acotados, `commands.analyze` y `commands.status`, cuyo primer
elemento es literalmente `Neocortex`. El argv de análisis incorpora los
límites efectivos de `code`, `--no-code-generated`, `--no-code-vendored` y,
cuando aplican, `--code-max-count` y `--retry-code-errors`. Esto permite
reproducir la configuración sin perder quoting ni depender de una línea humana
abreviada.

Antes de promover `bin\Neocortex.exe`, valide `--version` y `--help` mediante
la ruta exacta `versions\<runtime-id>\venv\Scripts\Neocortex.exe`. La forma
`py -3 -m neocortex` sólo diagnostica el árbol fuente y no sustituye la
instalación canónica.

## Política de inventario y firma

Full scan y reconciliación USN consumen el mismo
`InventoryExclusionPolicy`. El perfil excluye explícitamente el directorio de
estado, `<ROOT>\.codex-lab` y `<ROOT>\docs\audit_evidence`; también excluye
VCS, entornos, cachés, build/dist/target/out, cobertura, vendored, temporales,
backups, bytecode, logs y bases SQLite mediante reglas acotadas que se guardan
completas en el manifest.

La firma pública de esa política tiene la forma
`inventory-exclusion-policy-v1:xxh3_128:<digest>`. XXH3 es una identidad de
configuración no criptográfica; no es autenticación. Cambiar cualquier regla,
raíz explícita o versión cambia la firma y bloquea la reutilización de estado
incompatible.

## Puerta incremental de tres evidencias

Un checkpoint sólo autoriza reconciliación incremental cuando coinciden tres
propietarios:

1. el **último** run durable del framework para la raíz es `self_analysis`,
   `analyze_only`, tiene la misma firma de política y conserva la misma
   identidad física; no se retrocede a un run histórico compatible si el más
   reciente no coincide;
2. el checkpoint publicado por Dedup v8 es válido, referencia exactamente el
   mismo `scan_id`, firma cruda de exclusión y cursor durable, y el scan
   completo conserva raíz, identidad y conteo de archivos coherentes;
3. la raíz viva mantiene identidad y el cursor USN vivo es compatible con el
   límite durable.

Si una evidencia falta o discrepa, `allow_incremental=False` fuerza un scan
completo sin invalidar el checkpoint existente. Por tanto, un checkpoint
publicado por un run que después falló no basta para autorizar incremental.

## Ceros durables y guards de mutación

La publicación final exige exactamente una ruta `code` completada y verifica
en la misma transacción que los conteos de `route_candidates`, `file_actions`,
`run_actions` y eventos de organización sean todos cero. Sólo entonces marca el
run `completed` y agrega su manifest; cualquier discrepancia revierte la
finalización.

La defensa no depende sólo del preset. Framework v20 persiste el modo, raíz e
identidad protegidas y la firma de inventario; sus triggers vuelven inmutables
esas fronteras y rechazan vincular acciones a un run `analyze_only`. Además,
`CorpusMutationGuard` rechaza el run junto a los owners de acciones,
organización y recuperación, vuelve a verificar identidad y propaga
`ProtectedAnalysisRootError` en lugar de degradarlo a un fallo operativo
permisivo. Las primitivas físicas mantienen sus preflights identity-bound y
*no-replace*; no existe un fallback por ruta para el autoanálisis.

## Manifest y status estrictamente read-only

La finalización publica un único
`neocortex.self-analysis-manifest/v1`, limitado a 256 KiB, en el evento
`self-analysis-manifest`. Run completado y manifest se confirman juntos. El
documento liga:

- run, modo, raíz, identidad y estado;
- scan, modo de inventario, cursores USN, reglas y firma de política;
- ruta `code`, `input_source=inventory_snapshot`, firma y summary efectivos;
- los cuatro conteos de seguridad en cero;
- los dos arrays argv canónicos.

La consulta compatible es `--code-status --code-json`. Sólo añade
`self_analysis` cuando el último run de code está ligado a un autoanálisis; un
run normal conserva `self_analysis: null`. `manifest_status` puede ser
`valid`, `missing`, `ambiguous` o `invalid`. La frescura separa identidad de
raíz, vínculo code/framework, checkpoint de inventario y estado del journal
(`unchanged`, `advanced`, `discontinuous` o `unavailable`); `current=true`
requiere todas las cercas positivas y journal sin cambios.

Este status no crea, migra, repara ni hace checkpoint. Abre cada SQLite como
`mode=ro&immutable=1`, activa `query_only`, y compara identidad, tamaño y mtime
antes y después. La presencia de `-wal`, `-shm` o `-journal` junto a
`code.sqlite3`, `framework.sqlite3` o `dedup.sqlite3` —incluso un auxiliar vacío
o desacoplado— o una cerca inestable en cualquiera de ellas causa abstención
total con código `2`. No emite una vista parcial ni crea sidecars.

## Mini-root de laboratorio

Use contenido sintético y dos hermanos disjuntos. No copie el corpus real para
convertirlo en fixture:

```powershell
$Lab = Join-Path $env:LOCALAPPDATA 'Neocortex\self-analysis\fixtures'
$MiniRoot = Join-Path $Lab 'mini-root'
$MiniState = Join-Path $Lab 'mini-state'

Neocortex --self-analysis --root $MiniRoot --state-directory $MiniState --code-max-count 100
Neocortex --state-directory $MiniState --code-status --code-json
```

El ejemplo presupone que un fixture sintético ya creó `$MiniRoot`; no autoriza
crear, copiar o limpiar datos fuera del laboratorio. Use un estado nuevo por
secuencia aislada; para validar full→incremental/no-op/cambio, reutilice ese
mismo estado sólo dentro de la secuencia controlada y con la misma configuración.
Cierre writers y confirme quiescencia antes de ejecutar status.

## Topología y validación operativa

El repositorio canónico es `%USERPROFILE%\Neocortex\Repository`; sus estados de
autoanálisis pertenecen a árboles nuevos bajo
`%LOCALAPPDATA%\Neocortex\self-analysis`, nunca dentro del repositorio. Cada
smoke debe usar un estado independiente y el ejecutable exacto del runtime que
se pretende promover. Un mini-root valida el contrato técnico, pero no sustituye
la validación explícita de la raíz completa ni autoriza reutilizar su estado.

## Route-only/resume con cero candidatos

El modo genérico code-only ya usa `RouteAdapter.input_source` y puede consumir
un inventario durable aunque haya cero `route_candidates`:

```powershell
$Root = 'C:\Datos'
$State = 'C:\Estado\Neocortex'
$RunId = 40
Neocortex --root $Root --state-directory $State --route code --route-only
Neocortex --root $Root --state-directory $State --route code --route-only --candidate-run $RunId
Neocortex --root $Root --state-directory $State --resume-run $RunId
```

Sin `--candidate-run`, examina el owner durable más reciente de la raíz exacta
y exige que sea `normal`; una discrepancia falla sin retroceder a un run
histórico aunque contenga filas MIME. Cero candidatos sólo se
admite cuando todas las rutas seleccionadas declaran
`input_source=inventory_snapshot`; una ruta MIME o selección mixta falla antes
de crear o ejecutar el run. Este contrato no relaja el preset:
`--self-analysis` continúa rechazando route-only y resume por diseño.
