# _03_Progreso

Sistema de progreso reutilizable basado en eventos absolutos. Los módulos de
trabajo solo emiten `ProgressEvent`; la interfaz puede usar `RichProgress`,
`NullProgress` o cualquier callback compatible.

```python
from _03_Progreso import ProgressEvent, RichProgress

with RichProgress() as progress:
    progress(ProgressEvent("ejemplo", "lectura", "Leyendo", 50, 100, "archivos"))
```

`ProgressEvent.metrics` transporta contadores estructurados mediante
`ProgressMetric`. Rich presenta, cuando la ruta los publica, hits de caché,
errores cacheados, trabajo nuevo, reintentos, resultados, timeouts, trabajo en
curso, elementos restantes, OCR y esperas de recursos. Los reporteros headless
reciben los mismos valores sin analizar texto de terminal.

El módulo no tiene punto de entrada ejecutable y no inicia tareas por sí solo.
