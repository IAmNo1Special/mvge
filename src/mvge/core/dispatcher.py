from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.core.abort import AbortError, AbortSignal
from mvge.core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalReasonCode,
    ApprovalRequest,
    deny,
    normalize_arguments,
)
from mvge.core.channel import MvgeResponse, StopReason
from mvge.core.errors import (
    MvgeError,
    SpellNotFoundError,
    SpellTimeoutError,
    to_error,
)
from mvge.core.events import ContentType, MvgeEvent, MvgeEventType
from mvge.core.spell_schema import unwrap_callable
from mvge.core.spells import (
    SpellContext,
    SpellExecutionMode,
    SpellResult,
    SpellResultMessage,
)
from mvge.core.truncate import (
    MAX_SPELL_RESULT_BYTES,
    format_size,
    truncate_head,
)

if TYPE_CHECKING:
    from mvge.core.loop import EmitSink, LoopCallbacks, LoopContext

_TRUNCATED_SPELL_CALL = (
    "Spell '{name}' was not cast: the response hit the output Mana limit, so its "
    "arguments may be truncated. Re-issue the spell cast with complete arguments."
)


def _apply_backstop(
    result_text: str, details: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Cap bypassing spell output so one cast cannot poison the Mana Pool.

    Spell-level truncation runs first; this fires only when a spell
    bypassed its own bound. Preserves the spell's original totals.
    """
    if len(result_text.encode("utf-8")) <= MAX_SPELL_RESULT_BYTES:
        return result_text, details
    capped = truncate_head(result_text, max_bytes=MAX_SPELL_RESULT_BYTES)
    existing = details.get("truncation")
    if isinstance(existing, dict):
        merged = dict(existing)
        shown = capped.text.count("\n") + (1 if capped.text else 0)
        try:
            total = int(merged.get("total_lines", capped.total_lines))
        except (TypeError, ValueError):
            total = capped.total_lines
        merged["shown_start"] = total - shown + 1 if shown else None
        merged["shown_end"] = total if shown else None
        merged["backstop_applied"] = True
        details["truncation"] = merged
    else:
        details["truncation"] = {
            "version": 1,
            "truncated": True,
            "strategy": "head",
            "total_lines": capped.total_lines,
            "shown_start": capped.shown_start,
            "shown_end": capped.shown_end,
            "total_bytes": capped.total_bytes,
            "full_output_path": None,
            "backstop_applied": True,
        }
    notice = (
        f"\n\n[Dispatcher backstop: output exceeded "
        f"{format_size(MAX_SPELL_RESULT_BYTES)} limit.]"
    )
    return f"{capped.text}{notice}", details


@dataclass
class BatchResult:
    """The outcome of a batch of spell cast requests."""

    messages: list[SpellResultMessage] = field(default_factory=list)
    terminate: bool = False


@dataclass
class _GateOutcome:
    """What the critical approval gate decided for one cast.

    Exactly one of the two fields is set: ``approved_arguments`` carries the
    frozen copy the dispatcher must execute, ``denial`` carries the synthetic
    result returned to the model when the cast is denied.
    """

    approved_arguments: dict[str, Any] | None = None
    denial: SpellResultMessage | None = None


_DENIAL_REASON_TEXT: dict[ApprovalReasonCode, str] = {
    ApprovalReasonCode.USER: "the approver denied this cast",
    ApprovalReasonCode.RULE: "a policy rule denied this cast",
    ApprovalReasonCode.READ_ONLY: "policy denied this cast",
    ApprovalReasonCode.FAILURE: "approval could not be completed",
}


def _denial_message(
    spell_cast_id: str,
    spell_name: str,
    reason_code: ApprovalReasonCode,
    request_digest: str,
    safe_detail: str,
) -> SpellResultMessage:
    """Build the synthetic approval_denied result for a denied cast."""
    text = f"approval_denied: {_DENIAL_REASON_TEXT[reason_code]} ({safe_detail})"
    return SpellResultMessage(
        spell_cast_id=spell_cast_id,
        spell_name=spell_name,
        content=[{"type": "text", "text": text}],
        details={
            "approval_denied": True,
            "outcome": ApprovalOutcome.DENY.value,
            "reason_code": reason_code.value,
            "request_digest": request_digest,
        },
        is_error=True,
        terminate=False,
    )


def _extract_code_representation(code_obj: Any) -> str:
    """Serialize code object attributes into a deterministic string representation."""
    parts = [
        code_obj.co_code.hex(),
        str(code_obj.co_varnames),
        str(code_obj.co_names),
    ]
    for const in code_obj.co_consts:
        if hasattr(const, "co_code"):
            parts.append(_extract_code_representation(const))
        else:
            parts.append(repr(const))
    return "|".join(parts)


def _spell_code_digest(spell: Any) -> str:
    """Engine-owned sha256 digest of the spell's implementation source.

    Never rune-chosen: computed here from the underlying executable target or
    class the engine is about to execute, so a code change invalidates persisted
    allow rules on the next cast.
    """
    candidate = None
    if hasattr(spell, "func") and spell.func is not None:
        candidate = spell.func
    elif hasattr(spell, "spell_def") and spell.spell_def is not None:
        defn = spell.spell_def
        candidate = getattr(defn, "_handler", None) or getattr(defn, "execute", None)
    elif hasattr(spell, "execute") and callable(spell.execute):
        candidate = spell.execute

    target: Any = unwrap_callable(candidate) if candidate is not None else None

    code_repr = ""
    if target is not None and hasattr(target, "__code__"):
        code_repr = _extract_code_representation(target.__code__)

    source_payload = ""
    if target is not None:
        with contextlib.suppress(OSError, TypeError):
            source_payload = inspect.getsource(target)

    if not source_payload:
        cls = spell.__class__
        with contextlib.suppress(OSError, TypeError):
            source_payload = inspect.getsource(cls)

    components: list[str] = []
    if source_payload:
        components.append(source_payload)
    if code_repr:
        components.append(code_repr)
    if not components:
        cls = spell.__class__
        components.append(f"{cls.__module__}.{cls.__qualname__}")
        if target is not None:
            components.append(str(getattr(target, "__qualname__", target)))

    raw = "\n---\n".join(components)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _spell_identity(spell: Any) -> dict[str, str]:
    """Engine-derived spell identity for approval policy matching.

    Never rune-chosen: the name plus the engine's own attribution of where
    the spell came from. ``runner_origin`` is the trust-relevant bit — set by
    the engine when it wraps a rune-registered spell. ``source_rune`` is
    display/attribution-only: a rune can squat on any name with
    ``source_rune=None``. ``source_scope`` says whose code the spell runs
    as (``"agent"`` for engine builtins, ``"rune"`` for rune-registered
    spells); ``code_digest`` is the engine-computed source digest so a
    code change invalidates persisted allow rules.
    """
    runner_origin = bool(getattr(spell, "runner_origin", False))
    source_rune = getattr(spell, "source_rune", None)
    cls = spell.__class__
    if runner_origin:
        source_id = str(source_rune) if source_rune else cls.__name__
        source_scope = "rune"
    else:
        source_id = f"{cls.__module__}.{cls.__qualname__}"
        source_scope = "agent"
    return {
        "name": spell.name,
        "source_kind": "rune" if runner_origin else "builtin",
        "source_id": source_id,
        "source_scope": source_scope,
        "code_digest": _spell_code_digest(spell),
        "runner_origin": "true" if runner_origin else "false",
        "read_only": "true" if bool(getattr(spell, "read_only", False)) else "false",
    }


class SpellDispatcher:
    """Deep module for executing spell cast batches concurrently or sequentially.

    Encapsulates parallel execution (asyncio.gather), sequential fallback,
    error isolation, sigil hook callbacks, timeout boundaries, and request-order
    preservation behind a single seam.
    """

    async def dispatch_batch(
        self,
        inv: MvgeResponse,
        context: LoopContext,
        callbacks: LoopCallbacks,
        emit: EmitSink,
        signal: AbortSignal | None = None,
    ) -> BatchResult:
        spell_casts = [
            item["spell_cast"]
            for item in inv.content or []
            if item.get("type") == ContentType.SPELL_CAST
        ]
        if not spell_casts:
            return BatchResult()

        if inv.stop_reason == StopReason.LENGTH:
            return await self._handle_truncated(spell_casts, emit)

        # If any spell in batch requests sequential, run sequentially
        has_sequential = False
        for spell_cast in spell_casts:
            spell_name = spell_cast.get("name")
            spell = context.get_spell(spell_name) if spell_name else None
            if (
                spell is not None
                and spell.execution_mode == SpellExecutionMode.SEQUENTIAL
            ):
                has_sequential = True
                break

        if has_sequential:
            return await self._dispatch_sequential(
                spell_casts, context, callbacks, emit, signal
            )
        return await self._dispatch_parallel(
            spell_casts, context, callbacks, emit, signal
        )

    async def _handle_truncated(
        self,
        spell_casts: list[dict[str, Any]],
        emit: EmitSink,
    ) -> BatchResult:
        messages: list[SpellResultMessage] = []
        for spell_cast in spell_casts:
            msg = _TRUNCATED_SPELL_CALL.format(name=spell_cast["name"])
            await emit(
                MvgeEvent(
                    type=MvgeEventType.SPELL_CASTING_START,
                    data={
                        "spellCastId": spell_cast["id"],
                        "spellName": spell_cast["name"],
                        "arguments": spell_cast.get("arguments", {}),
                    },
                )
            )
            await emit(
                MvgeEvent(
                    type=MvgeEventType.SPELL_CASTING_END,
                    data={"spellCastId": spell_cast["id"], "error": msg},
                )
            )
            messages.append(
                SpellResultMessage(
                    spell_cast_id=spell_cast["id"],
                    spell_name=spell_cast["name"],
                    content=[{"type": "text", "text": msg}],
                    is_error=True,
                )
            )
        return BatchResult(messages=messages, terminate=False)

    async def _dispatch_sequential(
        self,
        spell_casts: list[dict[str, Any]],
        context: LoopContext,
        callbacks: LoopCallbacks,
        emit: EmitSink,
        signal: AbortSignal | None = None,
    ) -> BatchResult:
        messages: list[SpellResultMessage] = []
        for spell_cast in spell_casts:
            if signal is not None and signal.aborted:
                raise AbortError("Operation aborted")
            res = await self._execute_single_spell(
                spell_cast, context, callbacks, emit, signal
            )
            if res is not None:
                messages.append(res)

        terminate = bool(messages) and all(m.terminate for m in messages)
        return BatchResult(messages=messages, terminate=terminate)

    async def _dispatch_parallel(
        self,
        spell_casts: list[dict[str, Any]],
        context: LoopContext,
        callbacks: LoopCallbacks,
        emit: EmitSink,
        signal: AbortSignal | None = None,
    ) -> BatchResult:
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")

        tasks = [
            self._execute_single_spell(spell_cast, context, callbacks, emit, signal)
            for spell_cast in spell_casts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        messages: list[SpellResultMessage] = []
        for res in results:
            if isinstance(res, SpellResultMessage):
                messages.append(res)
            elif isinstance(res, AbortError):
                raise res
        terminate = bool(messages) and all(m.terminate for m in messages)
        return BatchResult(messages=messages, terminate=terminate)

    async def _apply_approval_gate(
        self,
        spell_cast: dict[str, Any],
        spell: Any,
        context: LoopContext,
        gate: Callable[[ApprovalRequest], Awaitable[ApprovalDecision]],
        emit: EmitSink,
    ) -> _GateOutcome:
        """Run lookup-after validation through the critical approval gate.

        Validates and normalizes the arguments, freezes a deep copy, and asks
        the gate for a decision on exactly that copy. Any validation failure,
        gate exception, malformed response, or stale digest denies the cast
        with a synthetic ``approval_denied`` result: the model is never left
        with a silently discarded cast.
        """
        spell_name = spell_cast["name"]
        spell_cast_id = spell_cast["id"]

        async def deny_cast(
            reason_code: ApprovalReasonCode,
            safe_detail: str,
            request_digest: str = "",
        ) -> _GateOutcome:
            await emit(
                MvgeEvent(
                    type=MvgeEventType.SPELL_CASTING_END,
                    data={
                        "spellCastId": spell_cast_id,
                        "denied": True,
                        "reason": safe_detail,
                    },
                )
            )
            return _GateOutcome(
                denial=_denial_message(
                    spell_cast_id, spell_name, reason_code, request_digest, safe_detail
                )
            )

        arguments = spell_cast.get("arguments", {})
        if not isinstance(arguments, dict):
            return await deny_cast(
                ApprovalReasonCode.FAILURE, "arguments must be a JSON object"
            )

        spell_action = str(arguments.get("spell_action", ""))
        spell_summary = str(arguments.get("spell_summary", ""))

        # Gate-side schema validation for every spell kind that carries one.
        # A spell with no parameter schema is a schemaless cast:
        # prepare_arguments is then a pass-through and the arguments reach
        # the gate unvalidated. The request carries the flag so policy can
        # never match an allow rule and presenters must show an
        # "unvalidated arguments" warning.
        prepare = getattr(spell, "prepare_arguments", None)
        schema_validated = callable(prepare) and (
            getattr(spell, "_schema_model", None) is not None
        )
        if callable(prepare):
            try:
                arguments = prepare(arguments)
            except Exception:
                return await deny_cast(
                    ApprovalReasonCode.FAILURE, "arguments failed schema validation"
                )

        approved = copy.deepcopy(arguments)
        try:
            frozen_view, digest = normalize_arguments(approved)
        except ValueError:
            return await deny_cast(
                ApprovalReasonCode.FAILURE, "arguments are not JSON-serializable"
            )

        request = ApprovalRequest(
            cast_id=spell_cast_id,
            spell_name=spell_name,
            spell_identity=_spell_identity(spell),
            arguments=frozen_view,
            argument_digest=digest,
            project_root=context.project_root,
            tome_id=context.tome_id,
            agent_name=context.agent_name,
            schema_validated=schema_validated,
            spell_action=spell_action,
            spell_summary=spell_summary,
            sub_mvge_id=context.sub_mvge_id,
            parent_spell_cast_id=context.parent_spell_cast_id,
        )

        try:
            decision = await gate(request)
        except asyncio.CancelledError:
            # Explicit fail-closed: the gate callback is a generic awaitable
            # and may not convert cancellation into a denial itself. A
            # cancelled gate must never let the cast through.
            decision = deny(request, ApprovalReasonCode.FAILURE)
        except Exception:
            decision = deny(request, ApprovalReasonCode.FAILURE)

        if (
            not isinstance(decision, ApprovalDecision)
            or decision.request_digest != request.argument_digest
        ):
            # Malformed response or a decision bound to a different request:
            # stale, discard, deny.
            decision = deny(request, ApprovalReasonCode.FAILURE)

        if decision.outcome is ApprovalOutcome.DENY:
            return await deny_cast(
                decision.reason_code,
                "denied by the approval gate",
                decision.request_digest,
            )
        return _GateOutcome(approved_arguments=approved)

    async def _execute_single_spell(
        self,
        spell_cast: dict[str, Any],
        context: LoopContext,
        callbacks: LoopCallbacks,
        emit: EmitSink,
        signal: AbortSignal | None = None,
    ) -> SpellResultMessage | None:
        spell_name = spell_cast["name"]
        spell_cast_id = spell_cast["id"]

        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")

        if callbacks.before_spell_cast is not None:
            blocked = await callbacks.before_spell_cast(
                {"spell_cast": spell_cast, "spell_name": spell_name}
            )
            if blocked:
                return None

        spell = context.get_spell(spell_name)
        if spell is None:
            # Unknown spells never reach the approval gate: there is nothing
            # to approve. Lookup-before-gate ordering keeps them out.
            # But the cast must not vanish: with no result message the
            # loop ends the turn on the spell-calling response itself and
            # the user sees nothing at all. Surface the failure to the
            # model as an error result, so it can retry with a real
            # spell name -- the same rule as any other spell failure.
            err: MvgeError = SpellNotFoundError(spell_name)
            await emit(
                MvgeEvent(
                    type=MvgeEventType.SPELL_CASTING_END,
                    data={
                        "spellCastId": spell_cast_id,
                        "error": err.code,
                        "message": str(err),
                    },
                )
            )
            return SpellResultMessage(
                spell_cast_id=spell_cast_id,
                spell_name=spell_name,
                content=[{"type": "text", "text": str(err)}],
                is_error=True,
                terminate=False,
            )

        # Lookup and validation happen before the critical gate: unknown or
        # invalid casts fail here and never prompt. The gate approves a frozen
        # copy of the validated arguments, and the dispatcher executes exactly
        # that copy.
        execution_arguments: dict[str, Any] = spell_cast.get("arguments", {})
        if callbacks.approval_gate is not None:
            outcome = await self._apply_approval_gate(
                spell_cast, spell, context, callbacks.approval_gate, emit
            )
            if outcome.denial is not None:
                return outcome.denial
            if outcome.approved_arguments is None:
                raise RuntimeError("Approval gate returned neither approval nor denial")
            execution_arguments = outcome.approved_arguments

        raw_args = spell_cast.get("arguments", {})
        spell_action = (
            str(raw_args.get("spell_action", "")) if isinstance(raw_args, dict) else ""
        )
        spell_summary = (
            str(raw_args.get("spell_summary", "")) if isinstance(raw_args, dict) else ""
        )

        ctx = SpellContext(
            spell_cast_id=spell_cast_id,
            signal=signal,
            on_update=None,
            backend=context.backend,
            workspace_root=context.workspace_root,
            project_root=Path(context.project_root) if context.project_root else None,
            tome_id=context.tome_id or None,
            agent_name=context.agent_name or None,
            sub_mvge_id=context.sub_mvge_id,
            spell_action=spell_action,
            spell_summary=spell_summary,
        )

        await emit(
            MvgeEvent(
                type=MvgeEventType.SPELL_CASTING_START,
                data={
                    "spellCastId": spell_cast_id,
                    "spellName": spell_name,
                    "arguments": execution_arguments,
                    "spell_action": spell_action,
                    "spell_summary": spell_summary,
                },
            )
        )

        # A spell may override the loop's wall-clock timeout, or opt out
        # of it with 0 when its duration is bounded by the work itself (a
        # wait:true summon, bounded by the child's max_turns and provider
        # timeouts). The parent turn's abort still cancels the cast either
        # way; only the wall clock is removed. No override means the loop
        # timeout applies. Only a real int counts as an override: a
        # spec-mocked spell carries a mock attribute, and doing arithmetic
        # on it would turn every cast into a spurious error result.
        raw_timeout_ms = getattr(spell, "timeout_ms", None)
        spell_timeout_ms = (
            raw_timeout_ms
            if isinstance(raw_timeout_ms, int) and not isinstance(raw_timeout_ms, bool)
            else None
        )
        effective_timeout_ms = (
            spell_timeout_ms
            if spell_timeout_ms is not None
            else context.spell_timeout_ms
        )

        try:
            if signal is not None and signal.aborted:
                raise AbortError("Operation aborted")
            try:
                execute_kwargs: dict[str, Any] = {"signal": signal}
                sig = inspect.signature(spell.execute)
                if "ctx" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                ):
                    execute_kwargs["ctx"] = ctx

                if spell_timeout_ms == 0:
                    raw_result = await spell.execute(
                        spell_cast_id,
                        execution_arguments,
                        **execute_kwargs,
                    )
                else:
                    raw_result = await asyncio.wait_for(
                        spell.execute(
                            spell_cast_id,
                            execution_arguments,
                            **execute_kwargs,
                        ),
                        timeout=effective_timeout_ms / 1000,
                    )
            except asyncio.CancelledError as exc:
                if signal is not None and signal.aborted:
                    raise AbortError("Operation aborted") from exc
                raise

            is_error = False
            terminate = False
            result_content: Any = raw_result
            result_details: dict[str, Any] = {}

            if isinstance(raw_result, SpellResult):
                result_content = raw_result.content
                is_error = raw_result.status == "error"
                terminate = raw_result.terminate
                result_details = dict(raw_result.details)
                if is_error and raw_result.error_message:
                    # The model reads the result text, not is_error: an
                    # error with empty content arrives as an empty
                    # result and is read as success, so a blocked
                    # action gets reported as completed. Surface the
                    # error in the text itself, and keep it in details
                    # for structured consumers.
                    if result_content:
                        result_content = (
                            f"{result_content}\nError: {raw_result.error_message}"
                        )
                    else:
                        result_content = f"Error: {raw_result.error_message}"
                    result_details.setdefault("error_message", raw_result.error_message)

            if callbacks.after_spell_result is not None:
                chained = await callbacks.after_spell_result(
                    {
                        "spell_name": spell_name,
                        "spell_cast_id": spell_cast_id,
                        "result": result_content,
                    }
                )
                if isinstance(chained, dict):
                    result_content = chained.get("result", result_content)

            result_text, result_details = _apply_backstop(
                str(result_content), result_details
            )
            await emit(
                MvgeEvent(
                    type=MvgeEventType.SPELL_CASTING_END,
                    data={"spellCastId": spell_cast_id, "result": result_text},
                )
            )
            return SpellResultMessage(
                spell_cast_id=spell_cast_id,
                spell_name=spell_name,
                content=[{"type": "text", "text": result_text}],
                details=result_details or None,
                is_error=is_error,
                terminate=terminate,
            )
        except TimeoutError:
            err = SpellTimeoutError(spell_name, effective_timeout_ms)
        except AbortError:
            raise
        except Exception as exc:
            err = to_error(exc)

        await emit(
            MvgeEvent(
                type=MvgeEventType.SPELL_CASTING_END,
                data={"spellCastId": spell_cast_id, "error": str(err)},
            )
        )
        return SpellResultMessage(
            spell_cast_id=spell_cast_id,
            spell_name=spell_name,
            content=[{"type": "text", "text": str(err)}],
            is_error=True,
            terminate=False,
        )
