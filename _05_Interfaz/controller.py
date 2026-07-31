"""Qt process controller for one supervised NeoCortex worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .protocol import MAX_MESSAGE_BYTES, MESSAGE_PREFIX, command_record, decode_message
from .run_request import RunRequest


# region [01] Supervised worker lifecycle

MAX_PROCESS_LINE_BYTES = MAX_MESSAGE_BYTES + len(MESSAGE_PREFIX.encode("utf-8")) + 2


class WorkerController(QObject):
    """Own exactly one child process and never detach operational work."""

    message_received = Signal(dict)
    output_received = Signal(str)
    running_changed = Signal(bool)
    execution_finished = Signal(int, str)
    startup_failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._stdout_discarding_oversized_line = False
        self._stderr_discarding_oversized_line = False
        self._last_lifecycle = ""

    @property
    def is_running(self) -> bool:
        return (
            self._process is not None
            and self._process.state() is not QProcess.ProcessState.NotRunning
        )

    @property
    def process_id(self) -> int:
        return 0 if self._process is None else int(self._process.processId())

    def start(self, request: RunRequest) -> None:
        if self.is_running:
            raise RuntimeError("Ya existe una ejecución supervisada por esta interfaz")
        validated = request.validated()
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(environment)

        if getattr(sys, "frozen", False):
            program = sys.executable
            arguments = ["--gui-worker", *validated.cli_arguments()]
            working_directory = validated.root
        else:
            program = sys.executable
            arguments = ["-m", "_05_Interfaz.worker", *validated.cli_arguments()]
            working_directory = Path(__file__).resolve().parents[1]

        process.setProgram(program)
        process.setArguments(arguments)
        process.setWorkingDirectory(os.fspath(working_directory))
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._process_finished)
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()
        self._stdout_discarding_oversized_line = False
        self._stderr_discarding_oversized_line = False
        self._last_lifecycle = ""
        self._process = process
        process.start()
        if not process.waitForStarted(5_000):
            detail = process.errorString() or "No fue posible iniciar el worker"
            self._dispose_process()
            self.startup_failed.emit(detail)
            raise RuntimeError(detail)
        self.running_changed.emit(True)

    def request_cancellation(self) -> bool:
        if not self.is_running or self._process is None:
            return False
        written = self._process.write(command_record("cancel"))
        self._process.waitForBytesWritten(1_000)
        return written > 0

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        self._ingest_output(
            self._stdout_buffer,
            self._process.readAllStandardOutput().data(),
            protocol=True,
        )

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        self._ingest_output(
            self._stderr_buffer,
            self._process.readAllStandardError().data(),
            protocol=False,
        )

    def _ingest_output(
        self,
        buffer: bytearray,
        data: bytes | bytearray | memoryview,
        *,
        protocol: bool,
    ) -> None:
        """Consume complete lines while keeping an unterminated line strictly bounded."""

        payload = data if isinstance(data, bytes) else bytes(data)
        flag_name = (
            "_stdout_discarding_oversized_line"
            if protocol
            else "_stderr_discarding_oversized_line"
        )
        cursor = 0
        while cursor < len(payload):
            if bool(getattr(self, flag_name)):
                newline = payload.find(b"\n", cursor)
                if newline < 0:
                    return
                setattr(self, flag_name, False)
                cursor = newline + 1
                continue

            newline = payload.find(b"\n", cursor)
            end = len(payload) if newline < 0 else newline + 1
            segment_size = end - cursor
            if len(buffer) + segment_size > MAX_PROCESS_LINE_BYTES:
                buffer.clear()
                stream_name = "stdout" if protocol else "stderr"
                self.output_received.emit(
                    f"Línea de {stream_name} descartada por exceder el límite "
                    f"de {MAX_PROCESS_LINE_BYTES} bytes"
                )
                if newline < 0:
                    setattr(self, flag_name, True)
                    return
                cursor = end
                continue

            buffer.extend(payload[cursor:end])
            cursor = end
            if newline >= 0:
                self._consume_lines(buffer, protocol=protocol)

    def _consume_lines(self, buffer: bytearray, *, protocol: bool) -> None:
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            raw = bytes(buffer[: newline + 1])
            del buffer[: newline + 1]
            text = raw.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            if protocol:
                try:
                    record = decode_message(raw)
                except (ValueError, TypeError) as exc:
                    self.output_received.emit(f"Registro de progreso inválido: {exc}")
                    continue
                if record is not None:
                    message_type = str(record.get("type", ""))
                    if message_type != "progress":
                        self._last_lifecycle = message_type
                    self.message_received.emit(record)
                    continue
            self.output_received.emit(text)

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        if self._process is not None:
            self.output_received.emit(self._process.errorString())

    def _process_finished(
        self,
        exit_code: int,
        _exit_status: QProcess.ExitStatus,
    ) -> None:
        self._flush_remaining_output()
        lifecycle = self._last_lifecycle or "finished"
        self._dispose_process()
        self.running_changed.emit(False)
        self.execution_finished.emit(exit_code, lifecycle)

    def _flush_remaining_output(self) -> None:
        if self._process is not None:
            self._ingest_output(
                self._stdout_buffer,
                self._process.readAllStandardOutput().data(),
                protocol=True,
            )
            self._ingest_output(
                self._stderr_buffer,
                self._process.readAllStandardError().data(),
                protocol=False,
            )
        for buffer, protocol, flag_name in (
            (
                self._stdout_buffer,
                True,
                "_stdout_discarding_oversized_line",
            ),
            (
                self._stderr_buffer,
                False,
                "_stderr_discarding_oversized_line",
            ),
        ):
            if bool(getattr(self, flag_name)):
                buffer.clear()
                setattr(self, flag_name, False)
                continue
            if buffer:
                self._ingest_output(buffer, b"\n", protocol=protocol)

    def _dispose_process(self) -> None:
        if self._process is not None:
            self._process.deleteLater()
        self._process = None


# endregion [01]
