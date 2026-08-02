# Neocortex — handoff operativo actual

> Actualizado: 2026-08-02.
> Este archivo conserva su nombre anterior sólo para mantener la ruta conocida.
> Su contenido sustituye por completo el handoff de release del 30–31 de julio.
> El historial anterior permanece recuperable en Git; no debe ejecutarse como
> plan vigente.

## Resultado actual

El candidato instalado `rc5` cierra los cinco pasos recomendados sobre estados
aislados: abstención Semantic/Knowledge por contrato exacto; clon Semantic
durable, reanudable y sujeto a deadline; lectores Code/Framework que preservan
quiescencia; decisión de imagen v10 validada con OCR; y watcher portable que usa
USN sólo como acelerador. Conserva además inventario Dedup v9, enlace exacto
Code↔Semantic y analizador Python v3/resolver v4.

El autoanálisis portable quedó fusionado mediante el PR #3 en
`2c5a046177ce3a1f2fdb5e882d847a8fbf136405`. El corte focal `rc12` quedó
fusionado mediante el PR #4 en
`147a500cc659b859e9eb997d261f9912b97ef847`: añade
`Neocortex --code-publication-diff`, amplía la calibración del ranking a 41
símbolos y cambia a `python-confirmed-hotspots-v2`. La `Precision@10`
provisional sube de 0.60 a 0.70 sin degradar P@20/30/40 y `build_parser` baja
del rango 2 al 39. Una muestra portable de 40 `probable_dead_symbol` encontró
36 usos demostrables, un contrato externo y tres candidatos de revisión; la
señal falla el gate de 0.90 y permanece suprimida.

El corte rc14 cierra el primer hotspot accionable producido por ese
ranking. `knowledge_search.execute_knowledge_search` pasa de 416 líneas y
complejidad 75 a un orquestador de 26 líneas; las fases conservan seams, orden,
cancelación, telemetría y completitud. El diff rc12→rc14 retira exactamente ese
hotspot, no añade ninguno y conserva cero resoluciones nuevas, corregidas o
perdidas sobre las calls comunes.

El corte focal rc16 añade el Actionability Gate al consumidor read-only. El
contrato `neocortex.code-review/v2` conserva el ranking bruto, pero antepone
hasta tres recomendaciones `act_now`, separa callers de producción/pruebas,
clasifica la construcción y expone riesgo, contratos y validación sugerida. En
la publicación real rc16 el validator del rango bruto 1 queda en
`characterize_first`; `_queue_job_rows_bounded`, rango bruto 2, es la primera
recomendación. Un rc15 intermedio fue rechazado porque el propio gate creó un
hotspot; la partición final lo retiró antes de aceptar el candidato.

El corte rc17 cierra la primera recomendación emitida por ese gate.
`semantic_generation_repository._queue_job_rows_bounded` pasa de 302 líneas y
complejidad 44 a un orquestador transaccional de 47/3. Las fases extraídas no
hacen commit y conservan orden, límite de jobs nuevos, reutilización lazy de la
base, rebind de metadata y reanudación. El diff rc16→rc17 retira sólo ese
hotspot, añade cero y conserva cero resoluciones corregidas o perdidas.

El corte rc18 cierra la recomendación siguiente.
`knowledge_context._derive_context_graph` pasa de 279 líneas/complejidad 43 a
un coordinador de nueve líneas. Validación, acumulación y materialización quedan
separadas sin cambiar orden, IDs estables ni rechazo atómico de evidencia
inconsistente. El diff rc17→rc18 retira sólo ese hotspot, añade cero y conserva
cero resoluciones nuevas, corregidas o perdidas.

La mejora es visible mediante el launcher del wheel. Una consulta positiva
conservó 10 resultados útiles; una consulta fuera de dominio descartó sus 30
candidatos y terminó con `abstained=1`, cero hits. El watcher rc5 ejecutó tres
ciclos portables sobre 20 archivos, todos exitosos, y después de `Ctrl+C`
`--code-status` devolvió `0` con cero sidecars SQLite.

No se promovió el launcher estable, no se tocó el estado durable ni el corpus
personal y no se movió, renombró ni borró ningún original. El único recorrido
completo fue el código del propio repositorio, sobre estado aislado.

## Verdad del entorno

- Fuente: `C:\Users\Victor\Neocortex\Repository`.
- Toda esta continuación se ejecutó con PowerShell 7.6.4 (`pwsh`).
- Base publicada al iniciar este corte: `main` en
  `f0096ab31089d250f8a00cf01e7fc67f1f71f2fa`, idéntico a `origin/main` tras
  fusionar el PR #6.
- El checkout fuente es `0.7.2`. El cierre del hotspot Semantic se rastrea en
  el PR #7; su implementación base es
  `67dc0c3aa51e70db33f862404390ed9d8c63a3cc`. La igualdad final entre `main` y
  `origin/main` se verifica después del merge porque el commit no puede
  autorreferenciar su propio hash desde este handoff.
- Launcher estable exacto:
  `C:\Users\Victor\AppData\Local\Programs\Neocortex\bin\Neocortex.exe`.
- El estable sigue en `Neocortex 0.7.1`, SHA-256
  `1D4FC0C654ACF0B34D300ABEC99839C5D263B44F05AA499947F44B12215716B1`.
- En la sesión con perfil, `Neocortex` está sombreado por una función que ejecuta
  `py -3 -m neocortex`; fuera del checkout puede resolver otra instalación.
- `pwsh -NoProfile` no encuentra `Neocortex`: el `bin` canónico no está en PATH.
- No promover ni cambiar perfil/PATH sin autorización explícita de Victor. La
  pregunta ya se formuló y no hubo respuesta afirmativa.
- La normalización ACL/NTFS que permanece sólo en el checkout local
  (`tools/release_windows_ntfs_native.py` y su regresión) no forma parte de este
  corte Semantic/Knowledge. No publicarla, aplicarla ni integrarla sin cerrar su
  autorización y sus barreras de release por separado.
- También se preservó sin editar el cambio preexistente de Victor en
  `.codex/config.toml`.

## Estado vivo preservado

- No se ejecutó productor, migración, checkpoint ni compactación sobre
  `%LOCALAPPDATA%\Neocortex\state`.
- Sólo se hicieron status/search/verify read-only acotados.
- El autoanálisis del repositorio escribió exclusivamente en un estado aislado
  bajo `C:\Users\Victor\Neocortex\Laboratory`; no reutilizó estado vivo.
- Semantic live conserva aproximadamente 5.13 millones de jobs pendientes y
  cero embeddings publicados. No reanudar esa generación.
- No se modificó, movió, renombró ni borró ningún archivo del corpus.

## Slice A — Knowledge útil

Se corrigió la abstención global excesiva sin migrar el estado vivo:

- `knowledge status` mantiene la vista global y devuelve `6`/`7` ante cualquier
  owner incompatible/corrupto;
- `search` y `context` sólo se abstienen por los owners realmente presentes en
  `blocking_owners`;
- framework schema 19 se admite únicamente en lectura si satisface exactamente
  el contrato estructural 20 y se marca
  `legacy_schema_read_compatible:19->20`;
- inventory schema 7 continúa visible como incompatible, pero no bloquea una
  consulta que no necesita su ranking;
- DOCX FTS materializa primero su ranking acotado.

Evidencia live read-only:

- `protección diferencial de transformador`: `23.214 s` antes, `4.883 s`
  después; DOCX bajó de `19.347 s` a `1.134 s`, con las mismas filas, orden,
  snippets y scores;
- `IEC 61850 protección diferencial`: `4.530 s`;
- contexto de mantenimiento: `3.982 s`;
- el candidato final devolvió evidencia DOCX/PDF/XLSX útil en `6.033 s`; exit
  `4` fue parcial explícito por rankings ausentes, no un fallo.

## Slice B — Semantic publicado y acotado

La CLI y los servicios comparten un presupuesto: 50 items, 1 500 jobs nuevos o
reactivados y 900 segundos. Un límite conserva el head anterior, deja la
generación sin publicar y devuelve `2`. Sólo una enumeración `bounded-v1`
completa puede publicar.

El guard `exact-token-guard-v2` usa el tokenizador real antes de persistir,
revalida en el backend, falla si falta el contador y firma límite/revisión del
tokenizador. El replay exacto compara fingerprint y revisiones inmutables: no
crea jobs ni consume límites de items/jobs. Ahora tampoco crea otra generación,
clona membresía ni mueve el head publicado. Al cambiar el perfil, el head elimina
el perfil anterior sólo en las fuentes seleccionadas. La CLI también escapa
caracteres del corpus no codificables por la consola Windows, incluido JSON de
Knowledge.

Cada item textual publica al final un `semantic_metadata_title` derivado sólo
del basename, sin directorios ni extensión final, bajo
`semantic-basename-title-v1`. Cuerpo y título comparten una sola vectorización
de consulta y se fusionan por RRF con pesos `1.0`/`0.5`; la salida conserva la
procedencia y prefiere el snippet corporal. El título es mutable y advisory:
clasificación, evidencia y Knowledge `evidence` consumen sólo cuerpo. Knowledge
`discovery` usa un plan v3 con `semantic_title` opcional; el título sólo refuerza
la mejor evidencia del mismo recurso y revisión, nunca crea un hit ni aparece
como `EvidenceRef`. Un head legado sin títulos informa
`title_channel_not_indexed` sin bloquear la evidencia corporal.

La generación inicia el clon de la base de forma lazy y fija el head fuente,
conteo y high-watermark. `cursor_json.base_clone` conserva cursor, páginas y
conteo durable; cada página hace checkpoint, respeta el deadline común y puede
reanudar sin repetir lo confirmado. Antes de publicar verifica conteo y
high-watermark contra el snapshot fijado. Un replay exacto elide el candidato
completo; una revisión sólo de metadata reatacha el payload ya publicado sin
inferencia; una revisión de contenido obliga trabajo nuevo. Si otro builder
publica primero, el perdedor CAS queda terminal `failed` con diagnóstico
reintentable y la siguiente corrida parte del head vigente.

### Piloto fallido preservado

`C:\Users\Victor\Neocortex\Laboratory\semantic-pilot-20260801-1200`:

- 35 documentos, 972 páginas, 945 881 caracteres;
- 1 260 chunks/jobs, `606.756 s`, exit `2`, sin head;
- 70 `TextTokenLimitExceededError`, máximo 650 frente al límite 512.

El fallo se detuvo, se corrigió y nunca escaló a live.

### Piloto final

`C:\Users\Victor\Neocortex\Laboratory\semantic-pilot-20260801-token-guard-v2`:

- `pdf.sqlite3` SHA-256
  `04DC27BDF700F887D865E0824F497EC79B0F4889964FFE79A8F89061292AB816`;
- 35 documentos, 972 páginas, 1 272 chunks; p95 482 tokens, máximo 511,
  cero mayores de 512 e identidad idéntica en dos pasadas;
- primera publicación: `856.604 s`, 3 reusos, 1 269 inferencias, cero errores;
- generación 8 añadió 35 títulos en `9.349 s`, con 35 jobs y sin cambiar
  payload, revisión de item o revisión de chunk de ninguno de los 1 272 cuerpos;
- generación 9 repitió la fuente en `6.177 s`, con cero jobs;
- el candidato final produjo generación 11 en `7.336 s`, `ready`, 1 307
  miembros, cero jobs e integridad SQLite `ok`;
- replays de fuente: `7.995 s` y `7.618 s`, ambos
  `new_jobs=queued=reused=embedded=0` bajo límites `1/1`;
- replay con la implementación actual sobre copia aislada: `7.380 s`, devuelve
  directamente head 11, `new_jobs=queued=reused=embedded=0` y conserva exactamente
  11 generaciones, 17 948 miembros y 3 851 jobs. Sólo renueva
  `refresh_token`/`updated_ns` de los 35 items y 1 307 chunks observados;
- `semantic.sqlite3` final SHA-256
  `75EC03B4DD5237D7F3526B5E231415E13E1F127254BC3869E4374A9E806E2FA6`;
- el PDF fuente conservó exactamente su SHA-256 y cuerpos de generación 7 a 11
  tuvieron cero miembros ausentes o distintos.

La primera publicación observó ~1.48 chunks/s. Proyectar mecánicamente 5.13
millones de jobs daría unos 40 días continuos; no es autorización para live.

### Reevaluación canónica del ranking

Esta medición inicial de cinco consultas PDF se conserva como línea base; la
evaluación posterior de 12 consultas PDF/Code aparece en `Knowledge cross-owner`
y no borra las regresiones locales observadas aquí.

La corrida mediante el servicio real y el candidato instalado no reprodujo los
valores anteriores de `10/11`, `2/2` y MRR `0.750`; esos valores quedan
retirados. Semantic cuerpo+título fue completo en las cinco consultas, escaneó
1 272 cuerpos + 35 títulos y obtuvo `9/11`, `1/2` paráfrasis y MRR `0.700`, con
mediana `3.898 s` y máxima `4.073 s`.

Knowledge se comparó contra el candidato anterior, que no consumía título:

| Métrica | Knowledge cuerpo | `discovery` + título | Resultado |
|---|---:|---:|---|
| Hit@5, tres anclas | 3/3 | 3/3 | conserva |
| Hit@5, dos paráfrasis | 1/2 | 1/2 | no cierra |
| Hit@10 total | 4/5 | 5/5 | mejora |
| FamilyRecall@10 micro | 7/11 (63.6 %) | 8/11 (72.7 %) | mejora, bajo gate |
| FamilyRecall@10 macro | 60.0 % | 80.0 % | mejora, bajo gate |
| MRR@5 medio | 0.567 | 0.550 | regresión leve |
| Latencia `discovery` | — | mediana `4.598 s`; máxima `7.695 s` | bajo 15 s |

Cada consulta escaneó 1 307 vectores. Hubo señales `semantic_title` en hits,
pero cero `semantic_metadata_title` como evidencia. El resultado fue parcial
porque la copia sólo contiene owners PDF/Semantic; la cobertura ausente se
reportó con exit `4`. Se ensayaron top-5, top-10 y decaimientos RRF generales;
ninguno cerró simultáneamente recall y MRR, y todos se revirtieron.

## Artefactos no promovidos

### Candidato focal `rc18` — cierre de hotspot Knowledge context

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc18-knowledge-context\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `6A4826238775A3DFCEBFE0D909172C40FBE5F788B7F726EADFF47C436008C8C8`;
- 1 331 108 bytes, 271 miembros y `ZipFile.testzip()` limpio;
- `Neocortex 0.7.2`, `pip check` limpio e import de
  `knowledge_context.py` confirmado desde el `site-packages` del venv rc18;
  fuente e instalación comparten SHA-256
  `EC0C946EE4F3DCF5FE8506989434D8BA21222763EAAB12D4C750543477CB2BB5` y
  el coordinador instalado ocupa nueve líneas;
- una comparación diferencial rc17/rc18 de 19 399 bytes sobre relación Code
  válida, duplicado planeado y evidencia inválida produjo JSON exactamente
  idéntico, SHA-256
  `A1695E6856AF0E9C3876EDDD40C02A5721BF341BABD86F30536D3C1B20F1403E`;
- el launcher instalado reprodujo 515/515 cache hits con cero lectura,
  análisis, persistencia o grafo, y reprodujo review/diff sin cambiar ninguna
  SQLite ni crear sidecars. Este runtime no se promovió al launcher estable.

### Candidato focal `rc17` — cierre de hotspot Semantic queue

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc17-semantic-queue\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `3EEB5A7CEC59A5441F7CE0EA540A8F1BC615A0FC1397483F1E9BC7CAB7988608`;
- 1 330 500 bytes, 271 miembros y `ZipFile.testzip()` limpio;
- `Neocortex 0.7.2`, `pip check` limpio e import de
  `semantic_generation_repository.py` confirmado desde el `site-packages` del
  venv rc17; el símbolo instalado ocupa 47 líneas;
- el venv focal usa `--system-site-packages`; verifica este wheel, no sustituye
  la validación full hermética de rc5;
- el launcher instalado reprodujo review/diff sin cambiar ninguna SQLite ni
  crear sidecars. Este runtime no se promovió al launcher estable.

### Candidato focal `rc16` — Actionability Gate

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc16-actionability\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `152ADFA1B9EA0EA501CF974980BC8099C0030685FA5949FF04F1F3D2CB864A25`;
- 1 329 688 bytes, 271 miembros y `ZipFile.testzip()` limpio; los dos módulos
  nuevos de actionability/modelos están presentes;
- `Neocortex 0.7.2`, `pip check` limpio e imports confirmados desde el
  `site-packages` del venv rc16;
- el launcher instalado emitió `neocortex.code-review/v2`, 50 findings y tres
  recomendaciones, sin cambiar el SHA-256 de ninguna SQLite ni crear sidecars;
- este runtime no se promovió al launcher estable.

### Candidato focal `rc14` — cierre de hotspot Knowledge

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc14\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `1A2836C9B718E85F47BBB2691453BC0CDDA6C421E78706C54E545431FDEB4F54`;
- 1 322 579 bytes, 269 miembros y `ZipFile.testzip()` limpio;
- `Neocortex 0.7.2`, `pip check` limpio e import de `knowledge_search.py`
  confirmado desde el `site-packages` del propio venv;
- `execute_knowledge_search` instalado conserva su firma pública y ocupa 26
  líneas;
- el launcher rc14 reprodujo el diff rc12→rc14 sin modificar el SHA-256 de
  ninguna SQLite. Este runtime no se promovió al launcher estable.

### Candidato focal `rc12` — calibración y diff de publicaciones

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc12\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `AE95DB50A4ACB8EC958B90D89EF0A72DF88E00D862B3A87726C8E303F44A7D21`;
- 1 322 090 bytes y 269 miembros;
- `ZipFile.testzip()` limpio; `RECORD`, entry point y
  `_04_Nucleo_Operativo/code_publication_diff.py` presentes.

Runtime de smoke:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc12\venv`.

- `Neocortex 0.7.2`, `pip check` limpio e imports confirmados desde el
  `site-packages` del propio venv, no desde el checkout;
- el launcher rc12 reprodujo rc6→rc11 con digest
  `7870b9de799ff095c8c54ae3fbfc83f2`: 1 622 resoluciones nuevas, 57
  correcciones, cero pérdidas, dos hotspots añadidos y uno retirado;
- los SHA-256 de ambas SQLite permanecieron idénticos antes/después del diff;
- este venv usa `--system-site-packages` para un smoke focal. No sustituye la
  prueba full hermética de `rc5` ni autoriza promover el launcher estable.

### Candidato focal `rc11` — autoanálisis y code-review

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc11\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `950BDEA5161241C57A276A8C5A60FA8A01F4FB41B8C63CDCA588C867EE19139D`;
- 1 315 047 bytes y 268 miembros;
- `ZipFile.testzip()` limpio; `RECORD`, entry point y
  `_04_Nucleo_Operativo/code_review.py`/`code_state.py` presentes.

Runtime de smoke:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-self-analysis-20260802-rc11\venv`.

- `Neocortex 0.7.2`, Python analyzer v5, graph resolver v7, módulo importado
  desde ese venv y `pip check` limpio;
- el launcher instalado devolvió exit `0`, 10 findings y digest
  `33d8ba5de1b0f005b7763f12fc814ed8` sobre la publicación rc11;
- dos lecturas JSON consecutivas fueron byte a byte idénticas;
- este venv usa `--system-site-packages` para un smoke focal de empaquetado. No
  sustituye la prueba full hermética de `rc5` ni autoriza promover el launcher.

### Candidato full `rc5`

Wheel:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-final-20260802-rc5\wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `F06F7DFCD5B72F87CC5A1A6EEEDC446E9FEF9A4903955235A9BB764CB2AAC74C`;
- XXH3-128 `9b0034b5c1dc944cdfc1cb426a3da97c`;
- 1 304 214 bytes;
- 267 miembros; `RECORD`, entry point y ambos marcadores `py.typed` presentes;
- `ZipFile.testzip()` limpio; contiene las correcciones finales de provenance
  Semantic, quiescencia Framework y el puente Code↔Semantic.

Runtime aislado validado:
`C:\Users\Victor\Neocortex\Laboratory\neocortex-0.7.2-final-20260802-rc5\venv`.

- 54 distribuciones y `pip check` limpio;
- `Neocortex 0.7.2` y las ocho capacidades `available`;
- consulta positiva Semantic: 1 974 vectores recorridos, 30 candidatos exactos,
  17 retenidos tras los pisos y 10 hits fusionados;
- consulta OOD: 30/30 candidatos excluidos, `calibrated_abstentions=1` y cero
  hits; Knowledge devolvió cero evidencia y explicó
  `semantic_candidate_limit_reached_after_calibrated_abstention`;
- replay imagen v10: 30/30 cache hits, 3 candidatos documentales, 6 de contexto
  industrial, 8 fotos y cero errores/OCR nuevo.

Este runtime es candidato de laboratorio. No se creó ni promovió un nuevo
launcher en `bin`.

## Autoanálisis del propio framework

El vertical slice portable nació en `--self-analysis` y ya se generalizó al
flujo normal. Si la consulta USN falla con `NtfsUsnError` u `OSError`, una única
enumeración completa produce el snapshot durable y Code compara ese snapshot
contra sus versiones actuales. La corrida registra `inventory_mode=full`,
`attempts=1`, cero reconciliaciones y journal `unavailable`; publica un
checkpoint de inventario ligado a la política, sin cursor USN ficticio. USN es
un acelerador opcional para Windows, no una dependencia de corrección.

La política `inventory-exclusion-policy-v2` excluye el estado, Git, caches,
builds, bases derivadas, logs y raíces transitorias de pytest/laboratorio. El
manifest v2 representa explícitamente journal disponible/no disponible; el
decoder conserva lectura estricta de v1. Un status journal-free puede validar
la evidencia terminada, pero devuelve `current=false` porque una consulta
read-only no puede demostrar frescura posterior sin volver a recorrer la raíz.

Baseline calibrado rc12:
`C:\Users\Victor\Neocortex\Laboratory\code-review-self-analysis-20260802-rc12-state`.

- piloto inicial acotado: inventario 517 archivos, 509 candidatos, 509
  procesados, 8 888 058 bytes, 14 651 símbolos, 80 979 referencias, cero
  errores/acciones y 18.157 s de pared. El límite explícito de 1 000 conservó
  correctamente el run como `partial`, aunque no se alcanzó;
- segunda corrida completa: 0 procesados/509 cache hits, run `completed` y
  publicación elegible; la tercera corrida repitió 509/509 cache hits en
  3.759 s con cero bytes y cero ms de lectura, análisis, persistencia y grafo;
- `--code-review` publicó en ambos replays el digest estable
  `51196515f21c2268766e8d3d4aed1dc5`; 464/464 Python completos, 185 hotspots
  únicos y 18 508/58 739 calls resueltas;
- el top 10 v2 inicia con `knowledge_search.execute_knowledge_search`
  (complejidad 75, 416 líneas, 37 callers) y ya no contiene el builder
  declarativo `cli_parser.build_parser`;
- la muestra de actionability contiene 24 `actionable` y 17 `defer` en la unión
  de 41 candidatos. P@10 pasa de 6/10 a 7/10; P@20, P@30 y P@40 permanecen
  iguales;
- la muestra SHA-256 portable de dead code exige 37/40 abstenciones. Sólo
  `_query_vector`, `_text_probe` y `_enqueue_text_chunk_batch` quedaron como
  candidatos de revisión; no son autorización de borrado;
- el diff rc11→rc12 encontró 49 775 calls comunes, cero resoluciones nuevas,
  corregidas o perdidas y hotspots 185→185. Los sitios exclusivos reflejan
  desplazamientos de rango y tres Python nuevos, como declara la limitación del
  contrato.

Publicación del refactor rc14:
`C:\Users\Victor\Neocortex\Laboratory\code-review-self-analysis-20260802-rc14-state`.

- 517 archivos inventariados, 509 candidatos, 509 procesados, 8 894 324 bytes,
  14 679 símbolos, 80 993 referencias, 199 diagnósticos del analizador y cero
  errores/acciones en 18.260 s;
- 464/464 Python completos, 18 518/58 747 calls resueltas y 246 dead
  suprimidos;
- `--code-review` publica 184 hotspots y digest
  `e7e45591ab39c850d5049adacabedde3`;
- el diff rc12→rc14, digest `acc663603c843d26e8591433c64cecb3`,
  retira sólo `knowledge_search.execute_knowledge_search`, añade cero hotspots
  y conserva cero resoluciones nuevas, corregidas o perdidas;
- replay final mediante el launcher instalado rc14: 0 procesados/509 cache
  hits en 3.706 s, cero bytes y cero ms de lectura, análisis, persistencia y
  grafo; los digests de review/diff permanecieron estables;
- rc13 fue un piloto intermedio detenido: retiró el hotspot objetivo pero creó
  dos auxiliares y empeoró 185→186. La segunda partición eliminó ambos y cerró
  185→184; no se publicó ese wheel como resultado final.

Publicación del Actionability Gate rc16:
`C:\Users\Victor\Neocortex\Laboratory\code-review-self-analysis-20260802-rc16-actionability-state`.

- piloto explícitamente acotado: 525 archivos inventariados, 46 directorios
  excluidos, 515 candidatos procesados, 8 932 466 bytes, 14 785 símbolos,
  81 229 referencias, 201 diagnósticos, un proyecto y cero errores; el límite
  explícito conservó correctamente el run como `partial`;
- segunda corrida: 0 procesados/515 cache hits y publicación `completed`; el
  replay final repitió 515/515 cache hits, cero bytes y cero ms de lectura,
  análisis, persistencia y grafo;
- review de 50 findings: tres recomendaciones y digest estable
  `be6eb7aafddd34270136ad7b1093e468`; la primera es
  `semantic_generation_repository._queue_job_rows_bounded` (rango bruto 2),
  seguida de `_derive_context_graph` y `_lookup_catalog`;
- diff rc14→rc16, digest `75d3349478676823776bf37740091e64`:
  184 hotspots comunes, cero añadidos, retirados o con evidencia cambiada;
  58 200 calls comunes, 39 856 aún no resueltas, 18 344 resueltas sin cambio y
  cero resoluciones corregidas o perdidas;
- rc15 fue rechazado al añadir el hotspot
  `code_review_actionability._construction`. La partición rc16 lo retiró y
  restauró exactamente la evidencia de hotspots de rc14.

Publicación del refactor Semantic rc17:
`C:\Users\Victor\Neocortex\Laboratory\code-review-self-analysis-20260802-rc17-semantic-queue-state`.

- piloto acotado: 525 archivos, 46 directorios excluidos, 515 candidatos,
  8 939 566 bytes, 14 813 símbolos, 81 253 referencias, 199 diagnósticos, un
  proyecto y cero errores; la corrida explícitamente limitada permaneció
  `partial` como exige el contrato;
- tras incorporar la regresión, la publicación final contiene 14 815 símbolos
  y 81 264 referencias. El replay desde el wheel rc17 obtuvo 0 procesados/515
  cache hits, cero bytes y cero ms de lectura, análisis, persistencia y grafo;
- review de 50 findings: digest
  `aca6e380664ca2c1947288f6b88a7b74`; el objetivo ya no aparece y la primera
  recomendación pasa a `knowledge_context._derive_context_graph`;
- diff rc16→rc17, digest `0f7857652222f06dd0943b1cc0027c30`:
  hotspots 184→183, 183 comunes, cero añadidos/cambiados y sólo el objetivo
  retirado; 58 063 calls comunes, 39 769 aún no resueltas, 18 294 resueltas sin
  cambio y cero resoluciones nuevas, corregidas o perdidas;
- las consultas review/diff conservaron idénticos todos los SHA-256 SQLite y
  dejaron cero sidecars.

Publicación del refactor Knowledge context rc18:
`C:\Users\Victor\Neocortex\Laboratory\code-review-self-analysis-20260802-rc18-knowledge-context-state`.

- piloto con límite explícito: 525 archivos, 46 directorios excluidos, 515
  candidatos, 8 948 192 bytes, 14 851 símbolos, 81 301 referencias, 197
  diagnósticos, un proyecto y cero errores/acciones; el run Code permaneció
  `partial` por contrato;
- la finalización reutilizó 515/515 entradas y el replay desde el wheel rc18
  repitió 515/515 cache hits con cero bytes y cero ms de lectura, análisis,
  persistencia y grafo;
- review de 50 findings: digest
  `a80573e88f52814bdc4349776846daec`; el objetivo desaparece y la primera
  recomendación pasa a `knowledge_exact._lookup_catalog`;
- diff rc17→rc18, digest `21053e149aff5292f448a747b6e4044b`:
  hotspots 183→182, 182 comunes, cero añadidos/cambiados y sólo el objetivo
  retirado; 58 568 calls comunes, 40 112 aún no resueltas, 18 456 resueltas sin
  cambio y cero resoluciones nuevas, corregidas o perdidas;
- status/review/diff mediante el launcher rc18 conservaron idénticos todos los
  SHA-256 SQLite y dejaron cero sidecars. `current=false` es la limitación
  explícita esperada de `journal_status=unavailable`, no una publicación
  inválida.

Las publicaciones rc6, rc11 y
`graph-resolver-v4-20260801-rc1\full-state` se conservan como baselines
históricos; no se mutaron. La línea base anterior de 58 imports relativos no
resueltos y 1 000 dead candidates queda retirada. Los 246 dead restantes
continúan siendo candidatos diagnósticos, no una orden de refactor ni borrado.

## Inventario normal portable — Dedup v9

El schema Dedup v9 permite que un checkpoint publicado conserve la terna USN
completa o la omita por completo. Raíz, política, scan publicado y timestamp
siguen siendo obligatorios; una terna parcial falla cerrada. La migración
exacta v8→v9 reconstruye sólo `inventory_checkpoints`, conserva las filas y
verifica conteos, claves foráneas e idempotencia. Un schema v8 desconocido se
rechaza sin mutarlo.

El coordinador intenta el recorrido USN cuando existe un cursor compatible y
cae al recorrido completo portable si el journal no está disponible o falla
durante la preparación. Un checkpoint portable nunca se reutiliza como cursor
USN. La reconciliación normal conserva la misma verdad publicada, caché de Code,
cancelación y reanudación. El watcher ya acepta checkpoints sin cursor: espera
`--watch-portable-interval-seconds` —300 s por defecto— y dispara una corrida
normal portable. Un fallo `NtfsUsnError`/`OSError` en el lector USN usa el mismo
fallback; otros errores conservan backoff. No crea cursor, base ni indexador
paralelo y todavía no se presenta como daemon multimodal completo.

Piloto previo de Dedup v9, conservado como evidencia:
`C:\Users\Victor\Neocortex\Laboratory\portable-inventory-v9-20260801-rc1`.

- Wheel:
  `wheelhouse\neocortex_framework-0.7.2-py3-none-any.whl`;
- SHA-256
  `4E7129974824580222358297844F932471A4083DEDB285364479E0CDA359B11D`;
- runtime con 53 paquetes, `pip check` limpio y las ocho capacidades
  disponibles;
- piloto CLI normal aislado de 25 archivos: primera corrida 25 procesados y 50
  símbolos en 1.875 s; replay 0 procesados/25 hits y cero bytes en 1.704 s;
  alta+modificación+rename+borrado 3 procesados/22 hits en 1.679 s; replay final
  0/25 y cero bytes en 1.648 s;
- la reconciliación final sin límite dejó Code `completed`, 25 archivos
  actuales, 50 símbolos, cero diagnósticos y checkpoint Dedup v9 portable;
- una copia poblada real migró v8→v9 y ejecutó autoanálisis: 500 archivos de
  inventario, 494 candidatos Code, 27 procesados/467 hits, 14 232 símbolos,
  70 868 referencias, 189 diagnósticos y cero errores en 4.963 s;
- el SHA-256 de la base v8 fuente permaneció
  `8DFA95A4159C8D6DDB4E532FC5A1AE4867387CBE16D234BC79F3FD411C0A1490`;
  sólo la copia aislada se migró;
- el launcher estable, el estado durable y el corpus real permanecieron
  intactos.

## Code ↔ Semantic y grafo v4

`code-semantic-link-v1` sincroniza `code.embedding_links` sólo después de una
publicación Semantic `ready`. Exige coincidencia exacta de identidad física,
versión Code, chunk, item, modelo, espacio y generación. El lector revalida
ambos heads; los scores conservan `retrieval_evidence_only` y
`uncalibrated_similarity` y nunca autorizan mutación.

Piloto:
`C:\Users\Victor\Neocortex\Laboratory\code-semantic-20260801-rc1\pilot-code-30`.

- 30 archivos, 1 326 símbolos y 7 045 referencias;
- 732 embeddings y 91 enlaces Code activos/vigentes;
- replay exacto con cero jobs y mismo head;
- seis consultas: lexical Hit@5 `2/6`, MRR `0.333`; Semantic/hybrid Hit@5
  `5/6`, MRR `0.708`; las cuatro paráfrasis de familia fueron recuperadas;
- sin modelo local, `semantic` se abstiene con exit `2` y `hybrid` conserva los
  canales deterministas.

La calibración del grafo etiquetó 40 imports y 40 dead candidates antes de
editar: 40/40 imports sí tenían target indexado y 37/40 dead candidates tenían
una referencia estática demostrable. Analyzer v3 preserva imports relativos y
resolver v4 prioriza ruta léxica y scope de módulo/clase; ante ambigüedad se
abstiene y conserva el fallback global sólo para targets únicos.

Al publicar Code se hace checkpoint del WAL y se retiran sidecars sólo si son
reconstruibles y el WAL está vacío. Un lector externo puede diferir la limpieza
sin revertir el run; el status quiescente se abstiene hasta una corrida posterior.
Las búsquedas/listados Code sobre una base quiescente usan una instantánea
immutable cercada y ya no recrean sidecars. Si detectan un writer, conservan el
lector WAL read-only y nunca limpian archivos ajenos.

La recarga del dueño durable del watcher aplica el mismo contrato estricto a
Framework. El defecto real se reprodujo: `mode=ro` recreaba un WAL vacío y SHM
después de cada publicación. `rc5` usa el snapshot immutable sólo cuando no hay
sidecars y entra a backoff ante actividad. Tres ciclos portables más cancelación
dejaron Code, Framework y Dedup sin sidecars; `--code-status` terminó en `0`.

## Knowledge cross-owner

Estado combinado:
`C:\Users\Victor\Neocortex\Laboratory\knowledge-cross-owner-20260801-rc1`.

Se combinaron los pilotos publicados de 35 PDF y 30 archivos Code. La generación
13 quedó `ready` en 490.616 s con 732 embeddings Code nuevos, cero errores y 91
enlaces. El head contiene 2 039 miembros: 1 272 cuerpos PDF, 35 títulos PDF, 702
cuerpos Code y 30 títulos Code. El replay con límites `1/1` devolvió el mismo
head, cero jobs y exit `0`.

Doce consultas etiquetadas —seis PDF, seis Code— cubrieron 18 targets; tres
consultas adicionales midieron abstención. Todas las capturas válidas tuvieron
snapshot estable:

| Variante | Targets @10 | Hit@5 | MRR | Recall macro | Mediana | Máxima |
|---|---:|---:|---:|---:|---:|---:|
| FTS sola | 5/18 | 3/12 | 0.2500 | 0.2500 | 1 ms | 30 ms |
| FTS + cuerpo (`evidence`) | 16/18 | 11/12 | 0.8194 | 0.8889 | 3.049 s | 3.296 s |
| FTS + cuerpo + título (`discovery`) | 17/18 | 12/12 | 0.9167 | 0.9583 | 3.109 s | 3.287 s |

`discovery` produjo 119 hits con `semantic_title` y cero títulos como evidencia.
Ganó la paráfrasis GOOSE y completó los tres targets del flujo portable de
inventario, pero perdió uno de dos targets en una consulta Semantic; no se cambió
el peso `0.5`.

FTS se abstuvo en 3/3 consultas fuera de dominio. La campaña posterior añadió
15 positivos, OOD claros y negativos técnicamente cercanos. Para el contrato
exacto Jina/body se fijaron pisos por owner: PDF `0.50`, Code `0.46`. El filtro
exige firma, backend, pipeline y owner exactos; vectores reutilizados obtienen el
contrato desde `payload_provenance` y un conflicto queda sin calibrar. La política
no forma parte del modelo registrado, por lo que los heads existentes continúan
compatibles.

En el smoke rc5 la positiva retuvo 17/30 candidatos y sus 10 primeros hits
relevantes; la OOD descartó 30/30 y devolvió cero. Algunos negativos cercanos
permanecen por encima del piso: los scores siguen siendo similitud de recuperación,
no probabilidad o certeza. El exit `4` de Knowledge refleja owners/canales
ausentes y su candidate limit, no evidencia inventada.

## Barreras

- Semantic completo más CLI, Code↔Semantic y extracción Knowledge: `448 passed`.
- Watcher, cancelación, Framework/status y frontera normal: `96 passed` en la
  barrera final; la ampliada inventario/watcher/imagen aprobó `139 passed`.
- Imagen v10: `68 passed`; replay real 30/30 cache hits y cero errores.
- Code ampliado: `144 passed`, `1 deselected` por el límite estructural conocido.
- Code-review y fronteras CLI focales: `139 passed`, `2 subtests`; Ruff y Mypy
  limpios sobre su implementación y contratos. La prueba real aislada devolvió
  exit `0`, 10 findings y cero mutación de estado.
- Ranking v2, diff de publicaciones, calibración dead, Code/CLI y autoanálisis:
  `279 passed`, `2 subtests`; la barrera focal previa aprobó `80 passed`.
  Ruff/format limpios en 11 archivos y Mypy sin errores en los cinco módulos
  fuente del corte.
- Actionability Gate v2, CLI, diff, persistencia y autoanálisis: `190 passed`;
  la regresión focal aprobó `38 passed`. Ruff/format limpios en ocho archivos y
  Mypy sin errores en los cinco módulos fuente. Wheel rc16 íntegro, `pip check`
  limpio y review read-only con cero cambios SQLite/sidecars.
- Refactor Semantic queue: línea base `53 passed`, barrera focal final
  `54 passed` y barrera integrada `194 passed`. La regresión inyecta un fallo
  después del upsert y comprueba rollback completo del slice. Ruff/format y
  Mypy limpios; wheel rc17, procedencia del import, replay, hashes y sidecars
  verificados.
- Refactor Knowledge context: línea base `36 passed` y barrera focal final
  `37 passed`; la barrera Knowledge/CLI amplia obtuvo `802 passed` y reprodujo
  sólo los dos límites estructurales preexistentes de 907/910 líneas frente a
  900 en archivos no modificados. Ruff/format, `git diff --check` y Mypy limpios;
  wheel rc18, procedencia, replay, hashes y sidecars verificados.
- Refactor Knowledge: línea base y dos repeticiones de `84 passed`; la barrera
  amplia final obtuvo `770 passed`, `2 deselected` después de reproducir por
  separado los dos límites estructurales preexistentes de 907/910 líneas frente
  a 900, fuera del archivo modificado.
- Resolver imports/aliases/reexports y refactor del hotspot: `137 passed`; Ruff
  0.15 y Mypy 2.1 limpios en los tres módulos fuente modificados. El piloto de
  30 archivos tuvo 30/30 cache hits en replay y la publicación completa 504/504.
- Knowledge + CLI amplio previo: `800 passed`; dos fallos estructurales
  preexistentes permanecen fuera de este slice:
  `knowledge_search_code.py` 907 líneas y
  `knowledge_search_inventory.py` 910 frente al límite 900. No maquillarlos
  borrando blancos.
- Ruff y formato limpios sobre todos los Python modificados del corte; Mypy 2.1
  sin errores en 37 módulos fuente.
- Wheel de 267 miembros, venv full nuevo, 54 distribuciones, `pip check`, versión
  y ocho capacidades verificados. Los smokes finales usaron el launcher rc5.
- Wheel rc12 de 269 miembros, `ZipFile.testzip()`, `pip check`, procedencia del
  import y launcher instalados verificados. El estable no se modificó.
- `git diff --check` limpio; sólo avisos de futura conversión LF→CRLF.

## Próximos pasos, en orden

1. **Caracterizar y cerrar la siguiente recomendación `act_now`.** Continuar con
   `knowledge_exact._lookup_catalog`. Antes de partirla, fijar consultas
   representativas de catálogo, orden/límites, provenance, lectura estricta y
   abstención; conservar su firma y un único caller de producción.
2. **Demostrar la mejora con rc19.** Repetir autoanálisis y
   `--code-publication-diff` contra rc18; exigir retirar el hotspot objetivo sin
   añadir otro, cero pérdidas/correcciones de resolución y replay 100 % cache
   hit.
3. **No ampliar Publication Diff todavía.** v1 respondió la decisión rc18 con
   identidad del hotspot, añadidos/retirados, evidencia y resolución. Evolucionar
   a v2 o añadir vistas por módulo sólo cuando una comparación real no permita
   decidir.
4. **Caracterizar el classifier siguiente.** Después de Exact, evaluar
   `document_taxonomy.classify_document` contra fixture representativo con
   abstención e incertidumbre antes de tocar su partición.
5. **Fortalecer dead-code sin habilitar borrado.** Enseñar al grafo evidencia de
   callbacks, registries y contratos que ya explican los 37 falsos positivos;
   repetir la misma muestra y mantener la señal suprimida hasta pasar el gate.

Imagen, calibración Semantic, soak del watcher y promoción del launcher siguen
pendientes, pero quedan detrás del objetivo vigente de mejorar el autoanálisis.
El estable permanece en 0.7.1 hasta autorización explícita de promoción.

No abrir otra base, ANN, vector DB, pipeline, auditoría integral o corrida live
para resolver estos pasos. Preservar `Neocortex --all --apply` como interfaz
cotidiana simplificada después de pilotos y protecciones.
