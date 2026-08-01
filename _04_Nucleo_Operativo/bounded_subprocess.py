"""Hard-bounded subprocess capture with timeout and deterministic cleanup."""
# region [00] Contexto del módulo
# Módulo: _04_Nucleo_Operativo/bounded_subprocess.py
# Propósito: documentación embebida y separación visual de regiones.
# endregion [00]


# region [01] Dependencias del módulo
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO

from .isolated_process import WindowsKillOnCloseJob
# endregion [01]

# region [02] Implementación


_READ_CHUNK_BYTES = 64 * 1024
_READER_JOIN_SECONDS = 5.0
_PROCESS_REAP_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class SubprocessOutputLimitError(RuntimeError):
    """Raised after terminating a child whose captured stream exceeded its limit."""

    stream: str
    limit_bytes: int

    def __str__(self) -> str:
        return f"subprocess {self.stream} exceeded {self.limit_bytes} bytes"


def _drain_bounded_stream(
    stream: IO[bytes],
    output: bytearray,
    *,
    limit_bytes: int,
    stream_name: str,
    terminate_process_tree: Callable[[], None],
    overflow: list[tuple[str, int]],
    overflow_lock: threading.Lock,
    reader_errors: list[BaseException],
) -> None:
    try:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            retained = max(0, (limit_bytes + 1) - len(output))
            if retained:
                output.extend(chunk[:retained])
            if len(output) <= limit_bytes:
                continue
            with overflow_lock:
                if not overflow:
                    overflow.append((stream_name, limit_bytes))
                    terminate_process_tree()
            return
    except (OSError, ValueError) as exc:
        reader_errors.append(exc)


def _join_readers(
    readers: tuple[threading.Thread, ...],
    streams: tuple[IO[bytes], IO[bytes]],
) -> None:
    for reader in readers:
        reader.join(timeout=_READER_JOIN_SECONDS)
    if all(not reader.is_alive() for reader in readers):
        return
    for stream in streams:
        stream.close()
    for reader in readers:
        reader.join(timeout=_READER_JOIN_SECONDS)
    if any(reader.is_alive() for reader in readers):
        raise RuntimeError("subprocess output reader did not terminate")


def _start_bounded_process(
    command: tuple[str, ...],
    *,
    stdin: int | IO[bytes],
    creationflags: int,
) -> tuple[subprocess.Popen[bytes], WindowsKillOnCloseJob | None]:
    job: WindowsKillOnCloseJob | None = None
    effective_creationflags = creationflags
    if os.name == "nt":
        job = WindowsKillOnCloseJob()
        effective_creationflags |= job.suspended_creation_flag()
    try:
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=effective_creationflags,
        )
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is None:
        return process, None
    try:
        job.assign_suspended(process)
    except BaseException:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=_PROCESS_REAP_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        job.close()
        raise
    return process, job


def _cleanup_note(error: BaseException) -> str:
    return f"subprocess cleanup: {type(error).__name__}: {error}"


def run_bounded_capture(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    input_bytes: bytes | None = None,
    timeout_seconds: float,
    stdout_limit_bytes: int,
    stderr_limit_bytes: int,
    creationflags: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    """Run a child with bounded capture and exact Windows descendant cleanup.

    Output pipes are drained concurrently. On Windows the child is created
    suspended, assigned by exact process handle to a kill-on-close Job Object,
    and only then resumed. Timeout, overflow, caller exceptions, and normal
    return all reap the direct child and release the Job so descendants cannot
    survive the call.
    """

    if not arguments:
        raise ValueError("subprocess arguments cannot be empty")
    if timeout_seconds <= 0:
        raise ValueError("subprocess timeout must be positive")
    if stdout_limit_bytes < 0 or stderr_limit_bytes < 0:
        raise ValueError("subprocess output limits cannot be negative")

    command = tuple(os.fspath(argument) for argument in arguments)
    with tempfile.TemporaryFile() as input_stream:
        if input_bytes is not None:
            input_stream.write(input_bytes)
            input_stream.seek(0)
            stdin: int | IO[bytes] = input_stream
        else:
            stdin = subprocess.DEVNULL
        process, job = _start_bounded_process(
            command,
            stdin=stdin,
            creationflags=creationflags,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        overflow: list[tuple[str, int]] = []
        overflow_lock = threading.Lock()
        reader_errors: list[BaseException] = []
        termination_errors: list[BaseException] = []
        cleanup_errors: list[BaseException] = []
        termination_lock = threading.Lock()
        terminated = False

        def terminate_process_tree() -> None:
            nonlocal terminated
            with termination_lock:
                if terminated:
                    return
                terminated = True
                if job is not None:
                    try:
                        job.terminate()
                        return
                    except OSError as error:
                        termination_errors.append(error)
                try:
                    if process.poll() is None:
                        process.kill()
                except OSError as error:
                    termination_errors.append(error)

        readers = (
            threading.Thread(
                target=_drain_bounded_stream,
                kwargs={
                    "stream": process.stdout,
                    "output": stdout,
                    "limit_bytes": stdout_limit_bytes,
                    "stream_name": "stdout",
                    "terminate_process_tree": terminate_process_tree,
                    "overflow": overflow,
                    "overflow_lock": overflow_lock,
                    "reader_errors": reader_errors,
                },
                name="neocortex-subprocess-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=_drain_bounded_stream,
                kwargs={
                    "stream": process.stderr,
                    "output": stderr,
                    "limit_bytes": stderr_limit_bytes,
                    "stream_name": "stderr",
                    "terminate_process_tree": terminate_process_tree,
                    "overflow": overflow,
                    "overflow_lock": overflow_lock,
                    "reader_errors": reader_errors,
                },
                name="neocortex-subprocess-stderr",
                daemon=True,
            ),
        )
        started_readers: list[threading.Thread] = []
        timed_out = False
        primary_error: BaseException | None = None
        returncode: int | None = None
        try:
            for reader in readers:
                reader.start()
                started_readers.append(reader)
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_process_tree()
        except BaseException as error:
            primary_error = error
            terminate_process_tree()

        if overflow:
            terminate_process_tree()
        if job is not None:
            try:
                job.close()
            except OSError as error:
                cleanup_errors.append(error)
        try:
            if process.poll() is None:
                returncode = process.wait(timeout=_PROCESS_REAP_SECONDS)
            else:
                returncode = process.returncode
        except BaseException as error:
            cleanup_errors.append(error)
            try:
                process.kill()
            except OSError as kill_error:
                cleanup_errors.append(kill_error)
            try:
                returncode = process.wait(timeout=_PROCESS_REAP_SECONDS)
            except BaseException as reap_error:
                cleanup_errors.append(reap_error)

        try:
            _join_readers(tuple(started_readers), (process.stdout, process.stderr))
        except BaseException as error:
            cleanup_errors.append(error)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError) as error:
                cleanup_errors.append(error)

    captured_stdout = bytes(stdout)
    captured_stderr = bytes(stderr)
    diagnostic_errors = termination_errors + cleanup_errors
    if primary_error is not None:
        for diagnostic_error in diagnostic_errors:
            primary_error.add_note(_cleanup_note(diagnostic_error))
        raise primary_error
    if timed_out:
        timeout_error = subprocess.TimeoutExpired(
            command,
            timeout_seconds,
            output=captured_stdout,
            stderr=captured_stderr,
        )
        for cleanup_error in diagnostic_errors:
            timeout_error.add_note(_cleanup_note(cleanup_error))
        raise timeout_error
    if overflow:
        stream_name, limit_bytes = overflow[0]
        overflow_error = SubprocessOutputLimitError(stream_name, limit_bytes)
        for cleanup_error in diagnostic_errors:
            overflow_error.add_note(_cleanup_note(cleanup_error))
        raise overflow_error
    if reader_errors:
        raise RuntimeError("subprocess output capture failed") from reader_errors[0]
    if diagnostic_errors:
        raise RuntimeError("subprocess cleanup failed") from diagnostic_errors[0]
    if returncode is None:
        raise RuntimeError("subprocess did not expose a terminal return code")
    return subprocess.CompletedProcess(
        command,
        returncode,
        captured_stdout,
        captured_stderr,
    )


__all__ = ["SubprocessOutputLimitError", "run_bounded_capture"]
# endregion [02]
