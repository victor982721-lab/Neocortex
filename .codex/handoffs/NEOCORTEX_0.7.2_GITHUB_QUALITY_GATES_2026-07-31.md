# NeoCortex 0.7.2 — actualización GitHub de cierre

**Corte:** 2026-07-31 23:01:11 -06:00  
**Repositorio:** `victor982721-lab/Neocortex`  
**Rama:** `agent/release-quality-gates`  
**Pull request:** `#1` — borrador  
**Estado:** parche ACL implementado; validación GitHub y fusión pendientes  
**Efectos operativos:** ninguno

Este documento sustituye únicamente el estado de autorización y aplicación del
parche ACL descrito en el handoff de pausa del 30 de julio. No reemplaza la
evidencia histórica de runtime, launcher, recibos ni transiciones fallidas.

## Cambios implementados

1. `radon` forma parte del extra `dev` y está fijado en `constraints.txt`, porque
   la suite de release lo importa directamente.
2. GitHub Actions incorpora los checks `static`, `tests` y `artifacts` sobre
   Windows y CPython 3.13.
3. Los tests nativos se ejecutan dentro de una raíz de laboratorio separada del
   repositorio, del estado y del corpus.
4. Wheel y sdist se construyen dos veces desde snapshots fuente independientes,
   se comparan y se validan antes de publicar artefactos de CI.
5. La observación del descriptor NTFS canonicaliza exclusivamente
   `SE_DACL_AUTO_INHERITED` (`0x0400`), bit derivado por Windows. El contrato
   continúa exigiendo igualdad de owner, group, DACL, SACL, contenido, identidad
   física, parent guards y CAS.
6. Un descriptor self-relative menor de 20 bytes falla cerrado.
7. Se añadieron guía de contribución, plantilla de pull request y actualizaciones
   semanales de dependencias.

## Evidencia del primer ciclo de CI

El primer ciclo falló de forma útil y no produjo artefactos promovidos:

- `artifacts` detectó que el backend `setuptools.build_meta` no estaba instalado
  en el entorno `--no-isolation`; ahora el workflow instala la versión fijada.
- Ruff lint aprobó el árbol.
- Ruff format identificó una deuda heredada de 222 archivos, ajena al alcance de
  este PR. La barrera conserva lint global y exige formato para archivos Python
  nuevos; la normalización integral queda separada para evitar un diff masivo y
  no revisable.

Los resultados históricos de 3,063 pruebas y la matriz de 118 artefactos
pertenecen al HEAD anterior al parche y no certifican este pull request. La única
evidencia válida para fusionar será la ejecución verde de los tres checks sobre
el HEAD más reciente de PR #1.

## Límites preservados

Este pull request no:

- abre, migra o modifica el estado vivo;
- recorre o modifica el corpus;
- instala o altera el runtime versionado;
- promueve, reemplaza o revierte el launcher;
- modifica el PATH;
- elige una licencia propia;
- ejecuta una transición de release.

## Secuencia posterior

1. Obtener `static`, `tests` y `artifacts` verdes sobre el HEAD final del PR.
2. Revisar y fusionar sin bypass.
3. Reconstruir y revalidar wheel y sdist desde el commit fusionado.
4. Ejecutar promoción, rollback y repromoción controlados.
5. Incorporar `bin` exactamente una vez al PATH y verificar una sesión hija.
6. Configurar la ruleset de `main` después de que los checks existan en esa rama.
7. Emitir el handoff final con hashes y resultados del cierre operativo.
