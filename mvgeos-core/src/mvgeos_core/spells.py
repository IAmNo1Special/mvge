from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError, create_model

from mvgeos_core.abort import AbortSignal


class ExecutionMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


SpellExecutionMode = ExecutionMode


class SpellStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"


@dataclass(frozen=True)
class ExecResult:
    """Result of an execution backend command execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@runtime_checkable
class ExecutionBackend(Protocol):
    """Protocol for command execution and file operations in a runtime substrate."""

    async def exec_command(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_ms: int | None = None,
        signal: AbortSignal | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ExecResult: ...

    async def read_bytes(self, path: Path) -> bytes: ...

    async def write_bytes(self, path: Path, data: bytes) -> None: ...


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if sys.platform != "win32":
            import os
            import signal as sig

            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, sig.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        else:
            import subprocess

            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


class LocalExecutionBackend:
    """Local host OS execution backend."""

    async def exec_command(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_ms: int | None = None,
        signal: AbortSignal | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if signal is not None and signal.aborted:
            return ExecResult(
                exit_code=-1,
                stdout="",
                stderr="Execution aborted before start",
                timed_out=False,
            )

        exec_env = dict(env) if env is not None else None
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=exec_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=sys.platform != "win32",
        )

        if signal is not None:

            def _on_abort() -> None:
                _kill_process_tree(proc)

            signal.on_abort(_on_abort)

        timed_out = False
        try:
            if timeout_ms is not None:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_ms / 1000.0
                )
            else:
                stdout_b, stderr_b = await proc.communicate()
        except TimeoutError:
            timed_out = True
            _kill_process_tree(proc)
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=2.0
                )
            except (TimeoutError, Exception):
                stdout_b, stderr_b = b"", b"Execution timed out"
        except asyncio.CancelledError:
            _kill_process_tree(proc)
            raise

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if on_output is not None and stdout:
            on_output(stdout)

        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )

    async def read_bytes(self, path: Path) -> bytes:
        return await asyncio.to_thread(path.read_bytes)

    async def write_bytes(self, path: Path, data: bytes) -> None:
        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)


@dataclass(frozen=True)
class SpellContext:
    """Runtime envelope injected into a spell execution."""

    spell_cast_id: str
    signal: AbortSignal | None = None
    on_update: Callable[[Any], None] | None = None
    backend: ExecutionBackend | None = None
    workspace_root: Path | None = None
    project_root: Path | None = None
    tome_id: str | None = None
    agent_name: str | None = None
    sub_mvge_id: str | None = None
    spell_action: str = ""
    spell_summary: str = ""
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpellResult:
    spell_name: str
    status: SpellStatus = SpellStatus.SUCCESS
    content: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    terminate: bool = False


@dataclass
class SpellResultMessage:
    role: str = "spellResult"
    spell_cast_id: str = ""
    spell_name: str = ""
    content: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] | None = None
    is_error: bool = False
    timestamp: float = 0.0
    terminate: bool = False


class SpellSignal(Protocol):
    cancelled: bool

    def cancel(self) -> None: ...

    def raise_if_aborted(self) -> None: ...


SpellUpdateCallback = Callable[[dict[str, Any]], None]


@dataclass
class MvgeSpell:
    name: str
    description: str
    parameters: dict[str, Any]
    execution_mode: SpellExecutionMode = SpellExecutionMode.PARALLEL
    read_only: bool = False
    _schema_model: type[BaseModel] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.parameters:
            self._schema_model = create_model(
                f"{self.name}_Schema",
                **self._convert_to_model_fields(self.parameters),
            )

    def _convert_to_model_fields(self, params: dict[str, Any]) -> dict[str, Any]:
        """Convert JSON Schema properties to Pydantic model fields."""
        fields: dict[str, tuple[type, Any]] = {}
        props = params.get("properties", {})
        required = params.get("required", [])
        for name, prop in props.items():
            field_type = self._json_type_to_python(prop)
            if name in ("spell_action", "spell_summary"):
                fields[name] = (field_type, "")
            else:
                fields[name] = (field_type, ... if name in required else None)
        return fields

    def _json_type_to_python(self, prop: dict[str, Any]) -> type:
        """Convert JSON Schema type to Python type."""
        json_type = prop.get("type")
        if not json_type and "anyOf" in prop:
            types = [
                t.get("type")
                for t in prop["anyOf"]
                if isinstance(t, dict) and t.get("type") != "null"
            ]
            if "integer" in types:
                return int
            if "number" in types:
                return float
            if "boolean" in types:
                return bool
            if "array" in types:
                return list[Any]
            if "object" in types:
                return dict[str, Any]
            json_type = "string"

        if json_type == "string":
            return str
        elif json_type == "integer":
            return int
        elif json_type == "number":
            return float
        elif json_type == "boolean":
            return bool
        elif json_type == "array":
            items = prop.get("items", {})
            if items:
                _ = self._json_type_to_python(items)
                # Use list[Any] and let Pydantic handle validation at runtime
                return list[Any]
            return list[Any]
        elif json_type == "object":
            return dict[str, Any]
        return Any

    def prepare_arguments(self, args: dict[str, Any]) -> dict[str, Any]:
        if self._schema_model is None:
            return args
        try:
            validated = self._schema_model.model_validate(args)
            return validated.model_dump()
        except ValidationError as e:
            raise ValueError(f"Invalid arguments for spell {self.name}: {e}") from e

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> dict[str, Any] | str | SpellResult:
        raise NotImplementedError


__all__ = [
    "ExecResult",
    "ExecutionBackend",
    "ExecutionMode",
    "LocalExecutionBackend",
    "MvgeSpell",
    "SpellContext",
    "SpellExecutionMode",
    "SpellResult",
    "SpellResultMessage",
    "SpellSignal",
    "SpellStatus",
    "SpellUpdateCallback",
]
