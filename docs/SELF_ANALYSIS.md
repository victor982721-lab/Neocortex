# Autoanálisis protegido del código fuente

> **Estado del contrato.** Esta capacidad pertenece a la fuente `0.7.2` bajo
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
Neocortex --state-directory $State --code-review
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
estado, `<ROOT>\.codex-lab`, `<ROOT>\docs\audit_evidence`,
`<ROOT>\Laboratory`, el `*.egg-info` canónico y los directorios transitorios de
pruebas detectados de forma acotada en la raíz (`.pytest-*` y `.test-tmp*`).
También excluye VCS, entornos, cachés, build/dist/target/out, cobertura,
vendored, temporales, backups, bytecode, logs y bases SQLite mediante reglas
acotadas que se guardan completas en el manifest.

La firma pública de esa política tiene la forma
`inventory-exclusion-policy-v2:xxh3_128:<digest>`. XXH3 es una identidad de
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
2. el checkpoint publicado por Dedup v9 es válido, referencia exactamente el
   mismo `scan_id`, firma cruda de exclusión y cursor durable, y el scan
   completo conserva raíz, identidad y conteo de archivos coherentes;
3. la raíz viva mantiene identidad y el cursor USN vivo es compatible con el
   límite durable.

Si una evidencia falta o discrepa, `allow_incremental=False` fuerza un scan
completo sin invalidar el checkpoint existente. Por tanto, un checkpoint
publicado por un run que después falló no basta para autorizar incremental.

Si la consulta inicial del journal falla por acceso o indisponibilidad, el
autoanálisis ejecuta un único recorrido completo portable sin reconciliación
USN. En ese modo persiste nulos `journal_volume`, `journal_id`, `start_usn` y
`end_usn`, no publica checkpoint y registra `journal.status=unavailable`. No es
un snapshot atómico ni se presenta como inventario incremental. La ruta `code`
sí conserva su caché por identidad/metadatos, de modo que un replay sin cambios
relee el árbol pero no vuelve a extraer ni analizar contenido. La corrida normal
usa la misma enumeración portable cuando USN no está disponible, pero sí publica
un checkpoint Dedup v9 con cursor nulo para que sus consumidores comparen el
snapshot contra sus caches; USN es sólo un acelerador opcional.

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
`neocortex.self-analysis-manifest/v2`, limitado a 256 KiB, en el evento
`self-analysis-manifest`. Run completado y manifest se confirman juntos. El
documento liga:

- run, modo, raíz, identidad y estado;
- scan, modo de inventario, journal disponible con cursores o estado
  `unavailable`, reglas y firma de política;
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

El decoder conserva lectura estricta del manifest histórico v1. Un manifest v2
con journal no disponible puede ser válido como evidencia de una corrida
completada, pero necesariamente expone
`inventory_checkpoint_current=false`, `journal_status=unavailable` y
`current=false`.

Este status no crea, migra, repara ni hace checkpoint. Abre cada SQLite como
`mode=ro&immutable=1`, activa `query_only`, y compara identidad, tamaño y mtime
antes y después. La presencia de `-wal`, `-shm` o `-journal` junto a
`code.sqlite3`, `framework.sqlite3` o `dedup.sqlite3` —incluso un auxiliar vacío
o desacoplado— o una cerca inestable en cualquiera de ellas causa abstención
total con código `2`. No emite una vista parcial ni crea sidecars.

## Revisión determinista de la publicación

`--code-review` es el primer consumidor de mantenimiento del autoanálisis. No
se combina con `--self-analysis`: el productor debe completar y publicar
primero; después, la consulta lee ese snapshot con las mismas cercas estrictas.
No crea bases, no migra, no hace checkpoint y no modifica código ni estado.

```powershell
Neocortex --state-directory $State --code-review
Neocortex --state-directory $State --code-review --code-json
Neocortex --state-directory $State --code-review --code-review-limit 50 --code-json
```

El contrato `neocortex.code-review/v2` conserva dos capas. `findings` selecciona
sólo diagnósticos Python confirmados `high_complexity` y `long_function`,
enumera hasta 10 000 hotspots y mantiene el ranking v2 auditable. Devuelve 10
por defecto, primero uno por archivo y luego un segundo hasta completar;
`--code-review-limit` admite de 1 a 50 y exige JSON por encima de 10. La
puntuación bruta no cambió:

```text
complexity_bp
+ floor(length_bp / 4)
+ 250 * min(callers_estáticos_resueltos, 20)
```

Cada finding conserva rango, firma, fingerprints del archivo, versión del
analizador, valor/umbral y hasta tres callers resueltos. Separa callers y
módulos consumidores de producción, pruebas, fixtures, herramientas y
compatibilidad. `hotspot_id` identifica establemente la evidencia física y el
símbolo; `finding_id` identifica la interpretación bajo versiones concretas de
ranking y actionability.

`recommendations` filtra como máximo tres `act_now` mediante
`python-maintenance-actionability-v1`. La clasificación determinista distingue
algoritmos, builders, classifiers, initializers, lifecycle, orquestadores,
persistencia, recuperación, reglas y validators. Builders se difieren;
validators, reglas e invariantes exigen caracterización; y una construcción no
reconocida devuelve `insufficient_evidence`. Cada recomendación enumera riesgo,
evidencia exacta, contratos a preservar y validación sugerida. El score bruto y
todos los hotspots siguen disponibles: esta capa no es ground truth humano ni
autoriza modificar código. Cero hallazgos o cero `act_now` son respuestas
válidas; en el segundo caso `recommendation_status=abstained` explica la brecha.

`probable_dead_symbol` se informa únicamente como conteo suprimido. Una muestra
portable de 40 entre los 246 candidatos de rc11 encontró 36 usos demostrables,
un contrato externo y sólo tres candidatos de revisión. Su precisión máxima
provisional fue 0.075 y exige abstenerse en 37/40 casos; por tanto falló el gate
de 0.90 y no se habilita como finding ni como recomendación de borrado. La
resolución estática no observa dispatch dinámico, callbacks, registros ni todos
los contratos de importación.

El analizador Python conserva además el binding léxico de imports y aliases. El
resolvedor sólo lo usa cuando no existe shadowing local: primero exige un
qualified name único y, si el nombre procede de una fachada interna, permite un
único salto confirmado por `import_binding` o por un submódulo físico único del
paquete. Imports externos, aliases ambiguos, comprehensions y nombres
redefinidos permanecen sin enlazar. El porcentaje global de calls resueltas es
descriptivo —su denominador incluye builtins, APIs externas y dispatch
dinámico— y no debe convertirse en objetivo aislado de calidad.

La línea base de actionability vive en
`tests/fixtures/code_review/rc6_top10_actionability_v1.json`. La ampliación
representativa está en `rc11_top40_actionability_v2.json`: reúne la unión de
los top 40 de ambos rankings, 41 símbolos etiquetados como builders,
validadores, reglas, algoritmos y orquestadores. El ranking v2 elevó la
`Precision@10` provisional de 0.60 a 0.70 y dejó iguales P@20, P@30 y P@40;
`build_parser` pasó del rango 2 al 39. Es revisión estática reproducible, no
ground truth humano, y el score sigue sin representar riesgo calibrado.

La regresión temporal rc14 retira `execute_knowledge_search`: el rango bruto 1,
`GoldenCase._validate_required_feature`, queda como
`validator/characterize_first`, mientras
`semantic_generation_repository._queue_job_rows_bounded` se convierte en la
primera recomendación `act_now`. La siguiente publicación no vista debe volver
a medir ese gate; el resultado rc14 no demuestra calibración universal.

La consulta exige manifest válido, último run Code completado e identidades de
raíz/framework ligadas. Un snapshot full terminado con journal no disponible
puede leerse como `freshness=publication_only`, `current=false` y limitación
explícita: demuestra qué se publicó, no que el árbol vivo no cambió después.
Journal `advanced`/`discontinuous`, vínculo incompatible, sidecars o schema no
admitido causan abstención con código `2`. El digest excluye timestamps e IDs
SQLite de corrida para que un replay sin cambios conserve identidad de
contenido.

## Comparación read-only entre publicaciones

`--code-publication-diff` convierte la comparación de dos publicaciones Code
en una operación canónica, acotada y determinista. El argumento identifica el
estado baseline; `--state-directory` identifica la publicación actual:

```powershell
Neocortex --state-directory $CurrentState --code-publication-diff $BaselineState
Neocortex --state-directory $CurrentState --code-publication-diff $BaselineState --code-json
```

Ambos estados deben contener un último run completado, schema compatible y
`code.sqlite3` quiescente sin `-wal`, `-shm` ni `-journal`. La consulta abre las
bases como snapshots immutable, no migra, no hace checkpoint y no escribe
ningún owner. Compara como máximo 250 000 calls y 20 000 hotspots; conserva
conteos totales y hasta 20 ejemplos por clase.

Una call común exige la misma ruta relativa, rango de bytes y nombre. Sobre
esas calls informa resoluciones nuevas, corregidas, perdidas, estables y aún no
resueltas; los cambios de texto que desplazan un rango aparecen honestamente
como sitios exclusivos de una publicación. Los hotspots se identifican por
ruta relativa y qualified name. El conteo `probable_dead` se muestra sólo como
delta no calibrado y nunca autoriza cambios de código o corpus.

## Mini-root de laboratorio

Use contenido sintético y dos hermanos disjuntos. No copie el corpus real para
convertirlo en fixture:

```powershell
$Lab = Join-Path $env:LOCALAPPDATA 'Neocortex\self-analysis\fixtures'
$MiniRoot = Join-Path $Lab 'mini-root'
$MiniState = Join-Path $Lab 'mini-state'

Neocortex --self-analysis --root $MiniRoot --state-directory $MiniState --code-max-count 100
Neocortex --state-directory $MiniState --code-status --code-json
Neocortex --state-directory $MiniState --code-review --code-json
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
