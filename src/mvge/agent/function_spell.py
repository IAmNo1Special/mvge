from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mvge.core.abort import (
    AbortError,
    AbortSignal,
)
from mvge.core.errors import SpellDiscoveryError
from mvge.core.spell_schema import (
    find_context_parameter,
    generate_spell_schema,
    invoke_spell_callable,
    unwrap_callable,
)
from mvge.core.spells import (
    MvgeSpell,
    SpellContext,
    SpellExecutionMode,
    SpellResult,
    SpellStatus,
)
from mvge.runes.types import SpellDefinition

type SpellUnion = Callable[..., Any] | MvgeSpell | SpellDefinition


PEP723_REGEX = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)\r?\n(?P<content>(?:^#.*?\r?\n)+?)^# ///\r?$",
)


@dataclass(frozen=True)
class PEP723Metadata:
    """Metadata extracted from a PEP 723 inline script metadata block."""

    dependencies: list[str] = field(default_factory=list)
    requires_python: str | None = None
    raw_toml: dict[str, Any] = field(default_factory=dict)


def parse_pep723_metadata(source: str) -> PEP723Metadata | None:
    """Extract and parse PEP 723 (# /// script) metadata from Python source."""
    match = PEP723_REGEX.search(source)
    if not match:
        return None

    block_type = match.group("type")
    if block_type != "script":
        return None

    raw_content = match.group("content")
    toml_lines: list[str] = []
    for line in raw_content.splitlines():
        if line.startswith("# "):
            toml_lines.append(line[2:])
        elif line.startswith("#"):
            toml_lines.append(line[1:])
        else:
            toml_lines.append(line)
    toml_str = "\n".join(toml_lines)

    try:
        data = tomllib.loads(toml_str)
    except Exception as exc:
        raise ValueError(f"Invalid PEP 723 TOML metadata: {exc}") from exc

    raw_deps = data.get("dependencies", [])
    dependencies = [str(d) for d in raw_deps] if isinstance(raw_deps, list) else []
    requires_python = data.get("requires-python")
    return PEP723Metadata(
        dependencies=dependencies,
        requires_python=str(requires_python) if requires_python else None,
        raw_toml=data,
    )


def _uv_path_setup() -> tuple[bool, str | None]:
    """Whether ``uv run`` can execute, and a PATH dir to prepend if needed.

    Returns ``(True, None)`` when uv is already on PATH, ``(True, dir)``
    when it is only found in the standard per-user install location
    (``~/.local/bin``, which is not on PATH in every shell), and
    ``(False, None)`` when uv is not installed anywhere we look. The
    command itself stays a bare ``uv`` either way; only the subprocess
    environment gains the directory.
    """
    if shutil.which("uv") is not None:
        return True, None
    candidate = Path("~/.local/bin/uv").expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return True, str(candidate.parent)
    return False, None


class PEP723ScriptSpell(MvgeSpell):
    """An executable MvgeSpell executing a standalone PEP 723 Python script.

    Hermetically runs via uv run.
    """

    def __init__(
        self,
        script_path: Path,
        *,
        metadata: PEP723Metadata | None = None,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        execution_mode: SpellExecutionMode = SpellExecutionMode.PARALLEL,
        timeout: float = 120.0,
        cwd: Path | None = None,
        read_only: bool = False,
    ) -> None:
        self.script_path = script_path
        self.timeout = timeout
        self.cwd = cwd

        source = ""
        if script_path.is_file():
            with contextlib.suppress(Exception):
                source = script_path.read_text(encoding="utf-8")

        self.metadata = (
            metadata
            if metadata is not None
            else (parse_pep723_metadata(source) or PEP723Metadata())
        )

        spell_name = name or script_path.stem
        spell_desc = description
        if spell_desc is None:
            if "description" in self.metadata.raw_toml:
                spell_desc = str(self.metadata.raw_toml["description"])
            else:
                with contextlib.suppress(Exception):
                    tree = ast.parse(source)
                    doc = ast.get_docstring(tree)
                    if doc:
                        spell_desc = inspect.cleandoc(doc)
            if not spell_desc:
                spell_desc = f"Execute standalone script {spell_name} via uv run."

        spell_params = parameters
        if spell_params is None:
            if "parameters" in self.metadata.raw_toml and isinstance(
                self.metadata.raw_toml["parameters"], dict
            ):
                raw_p = self.metadata.raw_toml["parameters"]
                if "type" in raw_p or "properties" in raw_p:
                    spell_params = raw_p
                else:
                    spell_params = {"type": "object", "properties": raw_p}
            else:
                spell_params = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                }

        super().__init__(
            name=spell_name,
            description=spell_desc,
            parameters=spell_params,
            execution_mode=execution_mode,
            read_only=read_only,
        )
        if not spell_params.get("properties"):
            self._schema_model = None

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> str | SpellResult:
        if signal is not None and getattr(signal, "aborted", False):
            raise AbortError("Operation aborted")

        validated = self.prepare_arguments(params)
        args_payload = json.dumps(validated)

        env = os.environ.copy()
        env["MVGE_PARAMS"] = args_payload
        env["PYTHONUNBUFFERED"] = "1"

        uv_ok, uv_path_dir = _uv_path_setup()
        if not uv_ok:
            return SpellResult(
                spell_name=self.name,
                status=SpellStatus.ERROR,
                error_message=(
                    "uv executable not found: install uv or put it on PATH "
                    "so PEP 723 scripts can run"
                ),
            )
        if uv_path_dir is not None:
            env["PATH"] = uv_path_dir + os.pathsep + env.get("PATH", "")

        cmd = ["uv", "run", "--script", str(self.script_path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd or self.script_path.parent),
            env=env,
        )

        def _handle_abort() -> None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()

        if signal is not None:
            signal.on_abort(_handle_abort)

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=args_payload.encode("utf-8")),
                timeout=self.timeout,
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            return SpellResult(
                spell_name=self.name,
                status=SpellStatus.ERROR,
                error_message=f"Script execution timed out after {self.timeout}s",
            )
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            raise

        if signal is not None and getattr(signal, "aborted", False):
            raise AbortError("Operation aborted")

        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            err_msg = (
                stderr_str
                or stdout_str
                or f"Process exited with code {proc.returncode}"
            )
            return SpellResult(
                spell_name=self.name,
                status=SpellStatus.ERROR,
                content=stdout_str,
                error_message=err_msg,
            )

        if stdout_str:
            try:
                data = json.loads(stdout_str)
                if isinstance(data, dict) and ("status" in data or "content" in data):
                    status_val = data.get("status", "success")
                    status_enum = (
                        SpellStatus(status_val)
                        if status_val in SpellStatus._value2member_map_
                        else SpellStatus.SUCCESS
                    )
                    return SpellResult(
                        spell_name=self.name,
                        status=status_enum,
                        content=str(data.get("content", "")),
                        details=data.get("details", {}),
                        error_message=data.get("error_message"),
                        terminate=bool(data.get("terminate", False)),
                    )
            except Exception:
                pass

        return stdout_str or f"{self.name} completed"


class RuneSpellWrapper(MvgeSpell):
    """Adapter converting a Rune SpellDefinition into an executable MvgeSpell."""

    def __init__(
        self, spell_def: SpellDefinition | MvgeSpell, *, runner_origin: bool = True
    ) -> None:
        super().__init__(
            name=spell_def.name,
            description=spell_def.description,
            parameters=spell_def.parameters,
            execution_mode=getattr(
                spell_def, "execution_mode", SpellExecutionMode.PARALLEL
            ),
            read_only=getattr(spell_def, "read_only", False),
        )
        self._spell_def = spell_def
        # Engine-owned provenance mark: this wrapper was built by the engine
        # around a rune-registered spell. The approval policy trusts only
        # this mark (never the rune-chosen source_rune) when deciding
        # read-only auto-allow. Defaults to True (fail closed).
        self._runner_origin = runner_origin

    @property
    def runner_origin(self) -> bool:
        """Whether the engine wrapped this spell from a rune registration."""
        return self._runner_origin

    @property
    def source_rune(self) -> str | None:
        return getattr(self._spell_def, "source_rune", None)

    @property
    def spell_def(self) -> SpellDefinition | MvgeSpell:
        return self._spell_def

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> Any:
        return await self._spell_def.execute(
            spell_cast_id, params, signal=signal, on_update=on_update, ctx=ctx
        )


class FunctionSpell(MvgeSpell):
    """An executable MvgeSpell wrapping an arbitrary Python callable.

    Infers tool name from func.__name__, description from func.__doc__, and
    parameter JSON Schema via generate_spell_schema. Supports both async and
    sync callables. SpellResult outputs pass through unchanged (preserving
    status, details, and terminate); all other outputs are converted into
    model-ready strings.
    """

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        execution_mode: SpellExecutionMode = SpellExecutionMode.PARALLEL,
        read_only: bool = False,
    ) -> None:
        func_name = (
            name if name is not None else getattr(func, "__name__", "custom_spell")
        )
        if not isinstance(func_name, str):
            func_name = str(func_name)
        doc = description
        if doc is None:
            raw_doc = getattr(func, "__doc__", "") or ""
            doc = inspect.cleandoc(raw_doc) if raw_doc else f"Execute {func_name}."
        schema = parameters if parameters is not None else generate_spell_schema(func)
        super().__init__(
            name=func_name,
            description=doc,
            parameters=schema,
            execution_mode=execution_mode,
            read_only=read_only,
        )
        self.func = func

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> str | SpellResult:
        if signal is not None and getattr(signal, "aborted", False):
            raise AbortError("Operation aborted")

        validated = self.prepare_arguments(params)
        args = {k: v for k, v in validated.items() if v is not None}

        try:
            unwrapped = unwrap_callable(self.func)
            func_params = inspect.signature(unwrapped).parameters
            has_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in func_params.values()
            )
            ctx_param = find_context_parameter(unwrapped)
            if ctx_param is not None and ctx is not None:
                args[ctx_param] = ctx
            if "signal" in func_params and signal is not None:
                args["signal"] = signal
            if "on_update" in func_params and on_update is not None:
                args["on_update"] = on_update
            if "spell_cast_id" in func_params and spell_cast_id is not None:
                args["spell_cast_id"] = spell_cast_id

            if not has_var_kw:
                if "spell_action" not in func_params:
                    args.pop("spell_action", None)
                if "spell_summary" not in func_params:
                    args.pop("spell_summary", None)
        except (ValueError, TypeError):
            pass

        result = await invoke_spell_callable(self.func, **args)

        if isinstance(result, (SpellResult, str)):
            return result
        elif isinstance(result, BaseModel):
            return result.model_dump_json()
        elif isinstance(result, (dict, list)):
            return json.dumps(result)
        elif result is None:
            return f"{self.name} completed"
        else:
            return str(result)


def coerce_spell(
    spell: Callable[..., Any] | MvgeSpell | SpellDefinition,
) -> MvgeSpell:
    """Coerce a callable, MvgeSpell, or SpellDefinition into an executable MvgeSpell."""
    if isinstance(spell, MvgeSpell):
        return spell
    if isinstance(spell, SpellDefinition):
        return RuneSpellWrapper(spell, runner_origin=True)
    if callable(spell):
        return FunctionSpell(func=spell, read_only=getattr(spell, "read_only", False))
    raise TypeError(
        f"Expected Callable, MvgeSpell, or SpellDefinition, got {type(spell).__name__}"
    )


def discover_spells_from_dir(
    spells_dir: Path,
    *,
    on_file_error: Callable[[Path, Exception], None] | None = None,
) -> list[MvgeSpell]:
    """Auto-discover spells from a spells/ directory.

    Discovery precedence:
    1. If `spells/__init__.py` defines `__all__`, load and coerce each listed attribute.
    2. Otherwise, scan all non-underscore `.py` files in alphabetical order:
       - Match a callable named after the file stem (e.g. `def bash(...)` in `bash.py`).
       - Fallback: Look for a single public function defined in that file.
       - If ambiguous or no candidate is found, raise `SpellDiscoveryError`.

    When `on_file_error` is given, per-file failures (import errors,
    ambiguous discovery) are reported through it and the file is skipped
    instead of raising — one broken spell file cannot take down the whole
    directory. Used by `Mvge.reload()` for tolerant re-discovery. The
    default (`None`) keeps strict construction-time behavior: the first
    failure raises.
    """
    if not spells_dir.is_dir():
        return []

    init_file = spells_dir / "__init__.py"
    if init_file.is_file():
        try:
            spec = importlib.util.spec_from_file_location(
                f"mvge_spells_{spells_dir.name}", init_file
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "__all__") and module.__all__:
                    init_spells: list[MvgeSpell] = []
                    for name in module.__all__:
                        if hasattr(module, name):
                            obj = getattr(module, name)
                            init_spells.append(coerce_spell(obj))
                        else:
                            raise SpellDiscoveryError(
                                f"Spell '{name}' listed in __all__ of '{init_file}' "
                                f"was not found in the module."
                            )
                    return init_spells
        except Exception as exc:
            if on_file_error is None:
                raise
            on_file_error(init_file, exc)
            return []

    py_files = sorted(
        f
        for f in spells_dir.iterdir()
        if f.is_file() and f.suffix == ".py" and not f.name.startswith("_")
    )
    if not py_files:
        return []

    spells: list[MvgeSpell] = []
    for py_file in py_files:
        try:
            spells.extend(_discover_spells_from_file(py_file, spells_dir))
        except Exception as exc:
            if on_file_error is None:
                raise
            on_file_error(py_file, exc)
            continue

    return spells


def _discover_spells_from_file(py_file: Path, spells_dir: Path) -> list[MvgeSpell]:
    """Discover spells from a single file; raises on any failure."""
    try:
        source = py_file.read_text(encoding="utf-8")
    except Exception:
        source = ""

    pep_meta = parse_pep723_metadata(source)
    if pep_meta is not None:
        return [PEP723ScriptSpell(py_file, metadata=pep_meta)]

    module_name = f"mvge_spells_{spells_dir.name}_{py_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if not spec or not spec.loader:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 1. Match function name == py_file.stem
    if hasattr(module, py_file.stem):
        candidate = getattr(module, py_file.stem)
        if callable(candidate) or isinstance(candidate, MvgeSpell):
            return [coerce_spell(candidate)]

    # 2. Look for single public function defined in this file
    public_candidates: list[tuple[str, Any]] = []
    for attr_name, attr_val in inspect.getmembers(module):
        if attr_name.startswith("_"):
            continue
        is_valid_spell = isinstance(attr_val, MvgeSpell) or (
            callable(attr_val)
            and getattr(attr_val, "__module__", "") == module.__name__
        )
        if is_valid_spell:
            public_candidates.append((attr_name, attr_val))

    if len(public_candidates) == 1:
        return [coerce_spell(public_candidates[0][1])]

    init_path = spells_dir / "__init__.py"
    raise SpellDiscoveryError(
        f"Could not discover spell in '{py_file.name}': No function "
        f"named '{py_file.stem}' found, and no '__all__' was defined "
        f"in '{init_path}'. Either define 'def {py_file.stem}(...)' "
        f"or export it in '__all__'."
    )


__all__ = [
    "FunctionSpell",
    "PEP723Metadata",
    "PEP723ScriptSpell",
    "RuneSpellWrapper",
    "SpellUnion",
    "coerce_spell",
    "discover_spells_from_dir",
    "parse_pep723_metadata",
]
