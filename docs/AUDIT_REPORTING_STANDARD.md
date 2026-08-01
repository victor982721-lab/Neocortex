# Estándar para auditorías integrales y cierres de release

Este documento es la fuente de verdad del repositorio para redactar y cerrar
auditorías técnicas de NeoCortex. No sustituye los contratos del código, la
ayuda viva, los esquemas ni los procedimientos operativos; define cómo se
obtiene, distingue y comunica su evidencia.

Su uso es obligatorio únicamente cuando Victor solicita de forma explícita una
auditoría integral o un cierre de release. No aplica a correcciones focales,
documentación, configuración ni slices verticales ordinarios; esos trabajos
usan validación proporcional conforme a AGENTS.md. No convierta este estándar
en una barrera rutinaria para entregar una mejora usable.

## Principios

1. El estado vivo del entorno auditado es la fuente operativa de verdad.
2. Un informe anterior es una línea base histórica, no una descripción vigente.
3. Hechos verificados, reproducciones, inferencias, riesgos potenciales,
   decisiones deliberadas, limitaciones y puntos no comprobados se presentan por
   separado.
4. No se reutilizan cifras antiguas como resultados de una barrera nueva.
5. No se afirma que una especificación amplia está completa por una suite pequeña
   o una cobertura parcial.
6. Cada corrección debe asociar defecto, reproducción, prueba, cambio,
   validación, compatibilidad y rollback.
7. No se ocultan comandos fallidos, herramientas ausentes, permisos denegados ni
   criterios pendientes.
8. El corpus vivo no se modifica para demostrar una auditoría. Acciones sobre
   archivos se prueban únicamente bajo una raíz temporal canónica y contenida,
   con barrera fail-closed dentro del helper nativo, salvo autorización
   adicional inequívoca.
9. Los benchmarks antes/después usan la misma carga, entorno y estado de caché.
10. Los informes fechados son inmutables como registro histórico: una auditoría
    posterior crea otro archivo.

## Fuentes de verdad por contrato

| Contrato | Fuente primaria |
|---|---|
| Flags, defaults y ayuda CLI | Parser vigente y ejecución de `Neocortex --help`. |
| Comportamiento ejecutado | Código vigente y reproducción controlada. |
| Versión fuente | `neocortex.__version__`. |
| Versión desplegada | Launcher real e `importlib.metadata` en el intérprete operativo. |
| Python, dependencias, extras y entrypoints | `pyproject.toml`, constraints y metadatos construidos/instalados. |
| Esquemas | Constantes, DDL, migraciones, `PRAGMA user_version` e historial real. |
| Documentación | README como entrada y guías especializadas enlazadas. |
| Resultados de pruebas y métricas | Salida nueva de los comandos exactos registrados. |

Una prueba que importa el árbol desde el directorio del repositorio no demuestra
que el launcher instalado cargue esa misma versión. Wheel, instalación limpia y
entrypoint se validan fuera del árbol fuente.

## Salidas obligatorias

Toda auditoría produce **dos salidas**.

### A. Informe técnico completo

Crear un documento nuevo:

```text
docs/TECHNICAL_AUDIT_<AAAA-MM-DD>.md
```

Si ya existe un informe para la fecha, no se sobrescribe. Añadir hora local o un
sufijo inequívoco, por ejemplo:

```text
docs/TECHNICAL_AUDIT_2026-07-24_193500.md
docs/TECHNICAL_AUDIT_2026-07-24_CONTINUATION_01.md
```

El encabezado debe registrar como mínimo:

- fecha y hora de corte con zona horaria;
- ruta real auditada;
- versión fuente y versión desplegada;
- intérprete y plataforma;
- alcance y exclusiones;
- estado de la barrera: aprobada, parcial o bloqueada;
- confirmación sobre el corpus vivo.

### B. Resumen final visible

El cierre en terminal o respuesta debe ser breve, concreto, numérico y seguir
esta estructura. Sustituya marcadores únicamente con resultados de esta
ejecución:

```markdown
Completé la auditoría, mejora y validación técnica y documental de NeoCortex.

### Resultados principales

- Cambios funcionales y arquitectónicos más importantes.
- Defectos corregidos.
- Mejoras de integridad, recuperación o rendimiento.
- Cambios documentales relevantes.
- Migraciones o contratos modificados.
- Versión pública final, únicamente si se modificó y verificó.

### Validación final

- Pruebas y subpruebas aprobadas.
- Cobertura.
- Ruff.
- mypy.
- Validación de dependencias.
- Build, wheel, sdist, instalación y entrypoints.
- Integridad y contratos SQLite.
- Benchmarks comparables.
- Confirmación de que no se alteró el corpus vivo.

### Uso

Incluye solamente comandos actuales, comprobados y listos para ejecutar.

### Documentación

- Documentos creados o actualizados.
- Comandos y procedimientos comprobados.
- Fuente de verdad definida.
- Inconsistencias corregidas.

### Riesgos críticos aún abiertos

Enumera sólo riesgos reales y pendientes. Si no existen riesgos críticos confirmados, indícalo sin afirmar que el sistema es perfecto.

El reporte completo, con arquitectura, hallazgos, reproducciones, migraciones, benchmarks, rollback y manifiesto, está en:

<RUTA_REAL_DEL_REPORTE>
```

No esconda limitaciones en texto secundario. Si una cifra o barrera no se
obtuvo, escriba `no ejecutado`, `bloqueado` o `no verificado` y explique por qué;
no use `N/A` sin causa.

## Contenido mínimo del informe completo

El informe debe contener, en este orden o mediante una tabla de correspondencia
inequívoca, las siguientes materias:

1. Resumen ejecutivo.
2. Alcance y exclusiones.
3. Entorno y versiones.
4. Comparación con la auditoría anterior.
5. Inventario técnico.
6. Mapa arquitectónico actualizado.
7. Contratos públicos.
8. Línea base.
9. Estado de cada hallazgo anterior.
10. Defectos nuevos.
11. Riesgos potenciales.
12. Cambios implementados.
13. Migraciones y compatibilidad.
14. Auditoría de persistencia.
15. Auditoría de concurrencia y recuperación.
16. Auditoría de seguridad.
17. Análisis de rendimiento y recursos.
18. Benchmark antes/después.
19. Auditoría de dependencias y empaquetado.
20. Auditoría documental.
21. Matriz documentación-código-CLI-pruebas.
22. Documentos creados o actualizados.
23. Pruebas añadidas.
24. Resultados de suite, cobertura, lint y tipado.
25. Validación de instalación.
26. Limitaciones y puntos no verificados.
27. Riesgos residuales.
28. Cambios que deliberadamente no se realizaron.
29. Instrucciones de rollback.
30. Manifiesto de archivos modificados.
31. Próximos pasos priorizados.
32. Cierre de la barrera técnica.

Una sección puede referenciar una guía especializada vigente, pero el informe
debe resumir el resultado auditado y registrar la versión/ruta consultada. No se
duplica una arquitectura o esquema completo sólo para aumentar extensión.

## Formato obligatorio de hallazgos

Cada hallazgo importante contiene:

- **ID** estable, por ejemplo `NC-AUD-018`;
- **título**;
- **clase**: error confirmado, riesgo verificable, limitación deliberada,
  oportunidad, hipótesis o no cambiar;
- **severidad**: crítico, alto, medio, bajo o informativo;
- **confianza**;
- **estado**, elegido sin variantes ad hoc: `corregido y revalidado`,
  `corregido parcialmente`, `pendiente reproducido`, `pendiente por evidencia
  estructural`, `no reproducido`, `no aplicable`, `bloqueado` o `reemplazado
  por un contrato distinto`;
- **ubicación exacta** con archivo, símbolo y líneas de corte;
- **componente**;
- **comportamiento observado**;
- **evidencia**;
- **causa verificada o probable**, identificada como tal;
- **impacto**;
- **reproducción** y comando/fixture exactos;
- **recomendación**;
- **cambio aplicado**, si corresponde;
- **pruebas**;
- **resultado de validación**;
- **riesgo del cambio**;
- **efectos secundarios**;
- **compatibilidad**;
- **rollback**;
- **prioridad**.

Una preferencia estética no es un defecto. La severidad combina impacto,
probabilidad, detectabilidad y dificultad de recuperación; no se eleva para
forzar prioridad.

## Seguimiento de hallazgos anteriores

Mantener una tabla única para todos los IDs heredados:

| ID | Estado anterior | Estado actual | Evidencia nueva | Cambio realizado | Pruebas | Riesgo residual |
|---|---|---|---|---|---|---|

No declarar `corregido` por lectura superficial. Para un riesgo de caída,
cancelación o concurrencia, la evidencia apropiada incluye fault injection,
reanudación y lector concurrente cuando sean parte del contrato.

## Línea base y comandos

Antes de editar:

- inventariar el árbol actual;
- registrar instrucciones aplicables;
- capturar versiones de herramientas;
- verificar intérprete y launcher;
- ejecutar la línea base con comandos exactos;
- registrar fallos iniciales y su efecto;
- medir rendimiento antes de optimizar.

Cada comando registrado debe incluir:

- directorio de trabajo;
- intérprete/ejecutable resuelto;
- argumentos completos;
- código de salida;
- duración cuando sea relevante;
- condiciones de caché/carga;
- resultado resumido sin truncamiento silencioso.

En este entorno, las herramientas Python se invocan preferentemente como
`py -3 -m <módulo>` después de verificar el intérprete. No se sustituye el
launcher público por el módulo fuente durante la validación de instalación.

## Pruebas, cobertura y análisis estático

Registrar por separado:

- pruebas y subpruebas;
- fallos, errores, skips y xfails;
- cobertura total y alcance exacto de `--source`;
- Ruff y reglas usadas;
- mypy y archivos incluidos;
- métricas auxiliares, si aportan una decisión;
- pruebas focales por hallazgo;
- suite completa posterior a los cambios.

No comparar tiempos de suites con distinto número de pruebas como benchmark. No
presentar Ruff/mypy limpios sobre un subconjunto como validación de todo el árbol.

## Persistencia y migraciones

Para cada base modificada, registrar:

- propietario;
- versión antes/después;
- DDL, índices, triggers y FTS afectados;
- conexión/factory y pragmas;
- datos poblados de la prueba;
- conteos antes/después;
- idempotencia;
- interrupción y rollback;
- `integrity_check` y `foreign_key_check`;
- compatibilidad y procedimiento de restauración.

No probar migraciones sobre el único estado vivo. No simular downgrade cambiando
números de versión. El rollback de una migración sin downgrade implementado es
restaurar un backup SQLite consistente y el paquete compatible.

## Rendimiento

Todo benchmark declara:

- corpus real autorizado o sintético;
- cantidad de archivos y bytes;
- generación de datos y semilla, si aplica;
- estado frío/caliente/incremental;
- versión, máquina y procesos;
- pared y CPU;
- RSS pico y asignaciones Python por separado;
- I/O, tamaños de DB/WAL/temporales cuando sean medibles;
- equivalencia de resultados;
- repeticiones y dispersión.

El antes y después usa carga idéntica. Si no existe corpus representativo
autorizado, se informa esa limitación; un fixture sintético no demuestra
precisión o costo del corpus operativo.

## Empaquetado e instalación

La barrera mínima incluye:

1. construir wheel y sdist;
2. inspeccionar nombres y contenido, incluido `RECORD` del wheel;
3. crear un entorno virtual limpio fuera del repositorio;
4. instalar el wheel sin que el cwd permita importar el árbol;
5. ejecutar `pip check`;
6. comprobar versión, ayuda, entrypoint, `python -m neocortex` y GUI help;
7. probar el comportamiento sin extras opcionales y con los extras declarados
   que correspondan;
8. registrar licencias/avisos y archivos incluidos/excluidos;
9. probar actualización desde la versión anterior cuando exista migración o
   despliegue real que la requiera.

Una prueba unitaria que lee `pyproject.toml` no sustituye esta barrera.

## Auditoría documental

El informe incluye inventario con audiencia, finalidad, fuente de verdad,
propietario inferible y estado. La matriz mínima contrasta:

- documentación;
- código;
- ayuda CLI;
- ejecución;
- pruebas;
- configuración;
- versión instalada;
- esquemas.

Los ejemplos se clasifican como:

- ejecutados y comprobados;
- parseados/validados sin ejecutar;
- plantilla con valores que el usuario debe sustituir;
- no ejecutados por riesgo o falta de estado.

No se llama “comprobado” a un comando sólo porque aparece en Markdown.

## Manifiesto de cambios

El manifiesto final contiene una fila por archivo:

| Archivo | Acción | Finalidad | Hallazgo | Pruebas | Compatibilidad | Rollback |
|---|---|---|---|---|---|---|

Acción es `creado`, `modificado`, `migrado` o `eliminado`. En un repositorio sin
control de versiones, el informe registra además inventario/copia previa y plan
de restauración, sin afirmar un diff histórico que no pueda probarse.

## Barrera de cierre

Una auditoría sólo se declara cerrada cuando, o se marca explícitamente como
bloqueado si no fue posible:

- el árbol fue inventariado;
- la auditoría anterior fue contrastada;
- P0/P1 fueron revalidados;
- cada corrección tiene evidencia y prueba;
- migraciones modificadas se probaron pobladas;
- pruebas focales y suite completa se ejecutaron;
- lint y tipado se ejecutaron;
- bases temporales pasaron integridad;
- benchmarks comparables se repitieron;
- wheel, sdist e instalación limpia se validaron;
- comandos principales documentados se comprobaron;
- documentación y comportamiento final coinciden;
- rollback y manifiesto existen;
- la inspección posterior no encontró fugas del laboratorio y el inventario
  incidental registra pyc, caches, logs, builds, temporales, WAL/SHM y fixtures;
- el corpus vivo permaneció intacto;
- se creó un informe fechado nuevo;
- este estándar fue respetado;
- el resumen final usa la estructura obligatoria.

Una barrera parcial puede ser una entrega válida si el bloqueo se muestra en el
resumen y en el informe. No se transforma en aprobada omitiendo el criterio.

## Seguridad y preservación

- No reprocesar el corpus vivo salvo solicitud explícita.
- No ejecutar acciones destructivas reales durante una auditoría.
- Usar fixtures, bases temporales y backups SQLite consistentes.
- Antes de importar el proyecto, redirigir temporal, pycache, pytest, Coverage,
  build, distribuciones y venv a una única raíz de laboratorio canónica.
- El helper que invoque una syscall debe rechazar fuera de raíz, rutas relativas
  ambiguas, `..`, UNC y enlaces/reparses; validar raíz, volumen e identidad;
  registrar objetos creados y fallar cerrado si la contención no es demostrable.
- Al cerrar, inspeccionar fugas y retirar únicamente artefactos cuya identidad y
  procedencia pertenezcan al laboratorio; conservar los backups requeridos.
- No publicar contenido confidencial en informes.
- No dejar screenshots, rasterizados, logs o reportes auxiliares sólo para
  demostrar trabajo.
- No borrar evidencia incompatible; preservar y abstenerse.
- Confirmar PID, padre y línea de comandos antes de detener un proceso.

El informe final debe terminar distinguiendo con claridad entre barrera técnica
aprobada, mejora parcial validada y afirmación de integridad completa. La última
no se usa mientras existan riesgos críticos o puntos materiales no verificados.
