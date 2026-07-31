# _01_Enumeracion

Módulo Python sin dependencias externas para enumerar inicialmente la MFT de
un volumen NTFS local mediante `FSCTL_ENUM_USN_DATA`. Procesa un búfer a la vez,
captura el límite USN previo al recorrido y comprueba que el journal no haya
cambiado ni haya perdido ese límite al terminar.

```python
from _01_Enumeracion import enumerate_volume

with enumerate_volume("C:") as scan:
    print(scan.checkpoint)
    for entry in scan:
        print(entry.file_reference_number, entry.parent_reference_number,
              entry.name, entry.is_directory)
```

Para persistir relaciones y resolver rutas sin cargar toda la MFT en RAM:

```python
from _01_Enumeracion import SqlitePathIndex, enumerate_volume

with enumerate_volume("C:") as scan, SqlitePathIndex("mft_paths.sqlite3") as paths:
    paths.ingest(scan)
    print(paths.relative_path(12345))
```

El proceso necesita acceso de lectura al dispositivo de volumen (`\\.\C:`).
Si Windows lo deniega, se debe ejecutar desde un proceso elevado o delegar la
lectura a un servicio con permisos adecuados. El módulo no modifica archivos,
el journal ni el volumen.

La enumeración expone un `EnumerationCheckpoint`. Para consumir continuamente
los cambios posteriores:

```python
from _01_Enumeracion import consume_changes

with consume_changes("C:", checkpoint) as changes:
    for batch in changes:
        procesar(batch.records)
        guardar_cursor(batch.cursor_after)  # solo después de confirmar los datos
```

El lector espera como máximo un segundo entre comprobaciones de parada, usa un
búfer acotado y valida `journal_id`, `lowest_valid_usn` y retrocesos del cursor.
Si el journal fue recreado o perdió el historial requerido, genera
`JournalDiscontinuityError` en vez de omitir cambios silenciosamente.

El índice SQLite puede aplicar los registros y guardar el cursor en la misma
transacción:

```python
from _01_Enumeracion import SqlitePathIndex, consume_changes, enumerate_volume

with SqlitePathIndex("mft_paths.sqlite3") as paths, enumerate_volume("C:") as scan:
    checkpoint = scan.checkpoint
    paths.ingest(scan)
    paths.bind_checkpoint(checkpoint)

    with consume_changes("C:", paths.journal_cursor) as changes:
        for batch in changes:
            paths.apply_change_batch(batch)
```

`FSCTL_ENUM_USN_DATA` representa cada registro de archivo, no necesariamente
cada nombre independiente de un archivo con múltiples enlaces duros. Un
inventario que necesite preservar todos los enlaces duros debe complementar
esta enumeración con una estrategia específica para ellos.
