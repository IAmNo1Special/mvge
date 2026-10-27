from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
from collections.abc import Callable
from typing import Any, cast, get_args, get_type_hints

from pydantic import create_model

from mvge.core.spells import SpellContext

_IS_SYNC_WORKER: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_IS_SYNC_WORKER", default=False
)


def unwrap_callable(func: Callable[..., Any]) -> Callable[..., Any]:
    """Unwrap functools.partial and wrapper chains to find the root callable."""
    visited = set()
    current: Any = func
    while current not in visited:
        visited.add(current)
        if isinstance(current, functools.partial):
            current = current.func
        elif hasattr(current, "__wrapped__"):
            current = current.__wrapped__
        else:
            break
    if (
        not (
            inspect.isfunction(current)
            or inspect.ismethod(current)
            or inspect.isbuiltin(current)
        )
        and callable(current)
        and inspect.ismethod(current.__call__)
    ):
        return cast(Callable[..., Any], current.__call__)
    return cast(Callable[..., Any], current)


def find_context_parameter(func: Callable[..., Any]) -> str | None:
    """Find the parameter name annotated as SpellContext or named ctx/spell_context."""
    unwrapped = unwrap_callable(func)
    try:
        sig = inspect.signature(unwrapped)
    except (ValueError, TypeError):
        return None

    try:
        hints = get_type_hints(unwrapped)
    except Exception:
        hints = {}

    for param_name in sig.parameters:
        if param_name in ("ctx", "spell_context"):
            return param_name
        annotation = hints.get(param_name, sig.parameters[param_name].annotation)
        if annotation is SpellContext:
            return param_name
        args = get_args(annotation)
        if SpellContext in args:
            return param_name

    return None


def inject_casting_rationale_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inject required rationale properties into a spell JSON Schema."""
    result = dict(schema)
    properties = dict(result.get("properties", {}))
    required = list(result.get("required", []))

    if "spell_action" not in properties:
        properties["spell_action"] = {
            "type": "string",
            "description": (
                "Brief 2-8 word phrase in -ing form explaining why this specific "
                "spell is being cast (e.g. 'Reading configuration file to verify "
                "port')."
            ),
        }
    if "spell_summary" not in properties:
        properties["spell_summary"] = {
            "type": "string",
            "description": (
                "Brief 2-5 word noun phrase summarizing the task (e.g. "
                "'Configuration port check')."
            ),
        }

    if "spell_action" not in required:
        required.insert(0, "spell_action")
    if "spell_summary" not in required:
        required.insert(1, "spell_summary")

    result["properties"] = properties
    result["required"] = required
    return result


def generate_spell_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate a JSON schema from a spell function's signature and type hints."""
    sig = inspect.signature(func)
    type_hints = get_type_hints(func)
    ctx_param = find_context_parameter(func)

    fields: dict[str, tuple[Any, Any]] = {}
    for param_name, param in sig.parameters.items():
        if param_name in (
            "spell_cast_id",
            "params",
            "signal",
            "on_update",
            "client",
            "workspace_root",
        ):
            continue
        if ctx_param is not None and param_name == ctx_param:
            continue
        annotation = type_hints.get(param_name, Any)
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param_name] = (annotation, default)

    if not fields:
        schema_model = create_model("EmptyParams")
    else:
        schema_model = create_model(
            f"{func.__name__}Params", **cast(dict[str, Any], fields)
        )

    schema = schema_model.model_json_schema()
    clean_schema: dict[str, Any] = {
        "type": "object",
        "properties": schema.get("properties", {}),
    }
    if "required" in schema:
        clean_schema["required"] = schema["required"]

    return inject_casting_rationale_schema(clean_schema)


async def invoke_spell_callable(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Invoke either a synchronous or asynchronous spell callable.

    Runs synchronous callables in an executor thread, but avoids nesting deadlocks
    if already executing inside a worker thread.
    """
    unwrapped = unwrap_callable(func)
    if inspect.iscoroutinefunction(unwrapped) or inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)

    if _IS_SYNC_WORKER.get():
        return func(*args, **kwargs)

    def _worker() -> Any:
        token = _IS_SYNC_WORKER.set(True)
        try:
            return func(*args, **kwargs)
        finally:
            _IS_SYNC_WORKER.reset(token)

    return await asyncio.to_thread(_worker)


__all__ = [
    "find_context_parameter",
    "generate_spell_schema",
    "inject_casting_rationale_schema",
    "invoke_spell_callable",
    "unwrap_callable",
]
