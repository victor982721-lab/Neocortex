"""Process-local serialization for all SQLite writes to persistent PDF state."""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock


_PDF_WRITE_LOCK = RLock()


@contextmanager
def serialized_pdf_write():
    """Permit one parent writer transaction while extraction remains parallel."""

    with _PDF_WRITE_LOCK:
        yield
