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

El candidato focal `rc11` conserva `Neocortex --code-review` y mejora el propio
autoanálisis con Python analyzer v5/resolver v7. El top 10 sigue determinista;
el grafo pasa de 16 733/58 262 a 18 426/58 429 calls resueltas. Sobre las 57 428
calls comunes con rc6 añadió 1 622 bindings, corrigió 57 destinos y no perdió
ninguno. El fixture inicial mide `Precision@10=0.60` provisional; los 246 avisos
de dead code continúan suprimidos. El replay no-op conservó el digest
`33d8ba5de1b0f005b7763f12fc814ed8` con 504/504 cache hits.

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
- Base de esta continuación: `main`; HEAD
  `1e2535494d5d37192a98bfd8201f6c7e64b545bb`.
- El checkout fuente es `0.7.2`; la rama y el commit publicados deben
  verificarse en Git/PR porque este handoff también forma parte del corte.
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

Publicación actual rc11:
`C:\Users\Victor\Neocortex\Laboratory\code-review-self-analysis-20260802-rc11-state`.

- primera corrida: inventario 512 archivos y 46 directorios excluidos; 504
  candidatos Code, 504 procesados, 8 763 695 bytes y 19.027 s de pared;
- 14 540 símbolos, 80 567 referencias, 201 diagnósticos del analizador y cero
  errores/acciones;
- grafo actual: 176 `high_complexity`, 21 `long_function`, 246
  `probable_dead_symbol` y cero `unresolved_relative_import`;
- dependencias relativas: 1 009/1 009 resueltas;
- replay inmediato: 0 procesados/504 cache hits en 3.672 s, cero bytes y cero
  ms de lectura, análisis, persistencia y grafo;
- `--code-review` en ambos runs publicó el digest estable
  `33d8ba5de1b0f005b7763f12fc814ed8`; 461/461 Python completos, 185 hotspots
  únicos y 18 426/58 429 calls resueltas (31.54 %);
- sobre 57 428 calls comunes con rc6, v7 añadió 1 622 bindings, corrigió 57
  destinos y retiró cero; 8 988/11 960 calls import-bound quedaron resueltas,
  incluidos qualified names directos, un reexport confirmado o un submódulo
  físico único;
- el fixture rc6 fija seis `actionable` y cuatro `defer`: `Precision@10=0.60`
  provisional, no ground truth humano;
- el hotspot introducido durante el resolvedor bajó de 435 líneas/complejidad
  18 a 179/12; cuatro helpers quedan entre 40 y 95 líneas, complejidad 1–7, y
  `code_state` salió del top 10.

Triage manual preliminar del top 10: seis resultados señalan fronteras
estructurales plausiblemente accionables (Knowledge search, cola Semantic,
grafo de contexto, lookup de catálogo, clasificación documental e indexación
de imagen); cuatro son validadores/builders deliberadamente declarativos. El
caso más claro es `cli_parser.build_parser`: quedó rank 2 por 830 líneas y muchos
callers pese a complejidad 2. Esto demuestra utilidad real, pero también que la
próxima calibración debe distinguir orquestación algorítmica de declaraciones y
preservar precedencia/error contracts; no conviene retocar pesos por intuición.

Las publicaciones rc6 y `graph-resolver-v4-20260801-rc1\full-state` se conservan
como baselines históricos; no se mutaron para producir rc11.

La línea base anterior de 58 imports relativos no resueltos y 1 000 dead
candidates queda retirada. Los 246 dead restantes continúan siendo candidatos
diagnósticos, no una orden de refactor ni borrado.

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
- `git diff --check` limpio; sólo avisos de futura conversión LF→CRLF.

## Próximos pasos, en orden

1. **Ampliar la calibración del ranking.** Llevar el fixture provisional de 10
   a una muestra representativa de 30–50 candidatos, etiquetar builders,
   validadores, reglas y algoritmos, medir P@10/abstención y sólo entonces
   ajustar ranking o añadir detectores.
2. **Comparar publicaciones por el comando canónico.** La comparación manual
   rc6→rc11 ya distinguió 1 622 altas, 57 correcciones, cero pérdidas y caída
   del hotspot; convertirla en un diff read-only/determinista sin otra base.
3. **Calibrar `probable_dead_symbol`.** Muestrear los 246 candidatos contra
   calls dinámicas, imports y fixtures etiquetados. Mantenerlos suprimidos y no
   recomendar borrados mientras precision/abstención no pasen el gate.
4. **Cerrar el siguiente hotspot accionable.** Elegirlo desde la muestra
   calibrada, exigir prueba de comportamiento y demostrar caída de score en un
   autoanálisis nuevo, como se hizo con el resolvedor.
5. **Retomar otras fronteras después.** Imagen, calibración Semantic, soak del
   watcher y promoción del launcher siguen pendientes, pero quedan detrás del
   objetivo vigente de mejorar el autoanálisis. El estable permanece en 0.7.1
   hasta autorización explícita de promoción.

No abrir otra base, ANN, vector DB, pipeline, auditoría integral o corrida live
para resolver estos pasos. Preservar `Neocortex --all --apply` como interfaz
cotidiana simplificada después de pilotos y protecciones.
