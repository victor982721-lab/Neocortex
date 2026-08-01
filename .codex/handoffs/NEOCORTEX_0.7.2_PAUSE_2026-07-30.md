# Neocortex — handoff operativo actual

> Actualizado: 2026-08-01.
> Este archivo conserva su nombre anterior sólo para mantener la ruta conocida.
> Su contenido sustituye por completo el handoff de release del 30–31 de julio.
> El historial anterior permanece recuperable en Git; no debe ejecutarse como
> plan vigente.

## Resultado actual

Slice A entrega búsquedas Knowledge útiles sobre el estado publicado vivo.
Slice B entrega generación Semantic acotada, publicada, consultable y
reanudable sobre un piloto aislado de 35 PDF. Esta continuación eliminó el
churn de generaciones en replays exactos, hizo reutilizable un cambio sólo de
metadata, cerró el zombie CAS-loser e integró título como señal opcional de
Knowledge `discovery` sin convertirlo en evidencia.

No escalar todavía al estado vivo ni al watcher. La medición canónica corrigió
una afirmación anterior: el título mejora `Hit@10` y FamilyRecall en Knowledge,
pero no conserva a la vez las barreras predeclaradas de MRR, FamilyRecall y
Hit@5 para paráfrasis. Se probaron cortes y decaimientos generales; ninguno
cerró todos los gates, por lo que se revirtieron para evitar sobreajuste. El
modo predeterminado `evidence` permanece en el plan v2, sólo cuerpo.

## Verdad del entorno

- Fuente: `C:\Users\Victor\Neocortex\Repository`.
- Base de esta continuación: `main`; HEAD
  `621209c8fd8e8b38ef4568f2fad7cd48adb08af5`.
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

## Estado vivo preservado

- No se ejecutó productor, migración, checkpoint ni compactación sobre
  `%LOCALAPPDATA%\Neocortex\state`.
- Sólo se hicieron status/search/verify read-only acotados.
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

La generación inicia el clon de la base de forma lazy. Un replay exacto elide
el candidato completo; una revisión sólo de metadata reatacha el payload ya
publicado sin inferencia; una revisión de contenido obliga trabajo nuevo. Si
otro builder publica primero, el perdedor CAS queda terminal `failed` con
diagnóstico reintentable y la siguiente corrida parte del head vigente. Un job
`done` obsoleto se reconcilia o elimina antes de finalizar para que un título
reemplazado no bloquee permanentemente al sucesor.

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

## Artefacto y runtime final no promovido

Wheel canónico:
`C:\Users\Victor\Neocortex\Repository\dist\neocortex_framework-0.7.2-py3-none-any.whl`.

- SHA-256
  `098AC98DE1753A32305E173A56886870CE0580D5361B9FC12B99C51F8558CE5E`;
- XXH3-128 `2a82004cd76d5b87e961b48fb2febf51`;
- 266 miembros, RECORD/entrypoint/typing verificados.

Runtime `[full]`:
`C:\Users\Victor\AppData\Local\Programs\Neocortex\versions\0.7.2-wheel-xxh3_128-2a82004cd76d5b87e961b48fb2febf51\venv`.

- 52 paquetes; `uv pip check`, versión y ayuda verdes;
- `doctor capabilities`: las ocho capacidades están disponibles;
- una búsqueda Knowledge JSON con caracteres no CP1252 devolvió 10 hits, 1 307
  vectores, ocho señales de título y cero títulos como evidencia.

Candidato inmutable, no estable:
`C:\Users\Victor\AppData\Local\Programs\Neocortex\bin\Neocortex-0.7.2-2a82004cd76d5b87e961b48fb2febf51.exe`.

- SHA-256
  `7F58F2A0DAF1CE5D1A53B0098C354F68AF3B54AA794A1229DDEB0FE5465B580F`.

El wheel final de laboratorio está en
`Laboratory\release-0.7.2-20260801-generation-knowledge-rc3`. Los candidatos,
runtimes, releases y temporales intermedios se conservaron: la limpieza masiva
fuera del checkout no fue autorizada por la barrera de seguridad. Son
reproducibles y no afectan al candidato final. No promover ninguno sin
autorización explícita.

## Barreras

- Semantic: `383 passed` en los 21 módulos `test_semantic*.py`.
- Knowledge: `762 passed`; dos fallos estructurales preexistentes:
  `knowledge_search_code.py` 907 líneas y
  `knowledge_search_inventory.py` 910 frente al límite 900. No maquillarlos
  borrando blancos; modularizar cuando ese frente sea prioritario.
- CLI Knowledge: `32 passed`; incluye la regresión de consola CP1252.
- Planner/search focal: `156 passed`; contratos de integración: `20 passed`.
- Después de cerrar typing: `167 passed` y Mypy sin errores en 14 módulos.
- Wheel: 266 miembros, `RECORD`, entry point y typing verificados; runtime
  `[full]` con 52 paquetes compatibles y ocho capacidades disponibles.

## Próximos pasos, en orden

1. **Calibrar discovery con más etiquetas.** Ampliar las cinco consultas a un
   conjunto representativo cross-owner y comparar cuerpo, FTS y título con
   abstención. No cambiar peso/corte usando sólo estos qrels; `evidence` sigue
   siendo el default estable.
2. **Cerrar escala de cambios reales.** El replay exacto ya no clona, pero una
   generación con altas/bajas/cambios todavía materializa la base O(n). Añadir
   cursor/deadline durable al clon y una garantía de lectura fijada antes de
   habilitar poda o watcher.
   El plan textual read-only sigue siendo una proyección pre-tokenizador; para
   convertirlo en conteo exacto debe transportar juntos counter, límite y firma
   del tokenizador resueltos localmente, sin descargar modelos.
3. **No abrir índice de título ahora.** Medición del head 11: SQL cuerpo
   `31.344 ms`, título `4.503 ms`; escaneo Python `601.970/22.353 ms`. El costo
   dominante es carga/vectorización del modelo, no SQLite.
4. **Después de esos gates:** piloto imagen/OCR aislado de 20–50 elementos,
   segunda corrida incremental y sólo entonces integración con watcher.
5. **Operación canónica.** Si Victor autoriza expresamente promoción y PATH,
   promover sólo `2a82004c...`, probar rollback/re-promoción y retirar la
   función PowerShell sombreante. Hasta entonces, estable=0.7.1.

No abrir otra base, ANN, vector DB, pipeline, auditoría integral o corrida live
para resolver estos pasos. Preservar los cambios actuales y el único handoff.
