"""Single owner of every Sigil payload (issue #163).

Before this module, payload construction was spread across six call sites
that hand-wrote dict literals against the dataclasses declared in
``mvgeos_runes.types.SIGIL_HOOK_DATA_CLASSES`` -- and ``create_sigil_data``
silently handed runes the raw dict whenever a literal did not match. Rune
authors could not trust the documented types, so they branched on both
shapes at runtime.

The contract now: every payload is built here as its declared dataclass.
:func:`payload_for` builds from explicit fields at direct emission sites;
:func:`payload_for_event` builds from an event for event-driven hooks,
selecting only the keys the declared dataclass accepts (other event-bus
keys stay on the bus). A payload that cannot be built raises
:class:`PayloadBuildError` -- loud, never a raw dict.
"""

from __future__ import annotations

import dataclasses
from typing import Any, cast

from mvgeos_core.events import MvgeEvent
from mvgeos_core.loop import LoopContext
from mvgeos_runes.types import (
    SIGIL_HOOK_DATA_CLASSES,
    PrepareNextTurnData,
    SigilHook,
)


class PayloadBuildError(TypeError):
    """A Sigil payload could not be built from the available engine state."""


def payload_for(hook: SigilHook, **fields: Any) -> Any:
    """Build the declared payload dataclass for ``hook``.

    Args:
        hook: The sigil hook being emitted.
        fields: Keyword arguments for the declared dataclass.

    Returns:
        An instance of ``SIGIL_HOOK_DATA_CLASSES[hook]``.

    Raises:
        PayloadBuildError: the hook declares no dataclass, or ``fields``
            do not satisfy it. Never falls back to a raw dict.
    """
    data_class = SIGIL_HOOK_DATA_CLASSES.get(hook)
    if data_class is None:
        raise PayloadBuildError(f"no payload dataclass declared for hook {hook!r}")
    try:
        return data_class(**fields)
    except TypeError as exc:
        raise PayloadBuildError(
            f"cannot build {data_class.__name__} for hook {hook.value}: {exc}"
        ) from exc


def payload_for_event(hook: SigilHook, event: MvgeEvent) -> Any:
    """Build the declared payload for an event-driven hook from the event.

    Only the event keys the declared dataclass accepts become payload
    fields; the rest belong to the event bus, not the sigil contract.
    Missing required keys fail loud through :func:`payload_for`, which
    forces event producers to carry the contract's keys.
    """
    data_class = SIGIL_HOOK_DATA_CLASSES.get(hook)
    if data_class is None:
        raise PayloadBuildError(f"no payload dataclass declared for hook {hook!r}")
    accepted = {f.name for f in dataclasses.fields(cast(Any, data_class))}
    fields = {k: v for k, v in event.data.items() if k in accepted}
    return payload_for(hook, **fields)


def prepare_next_turn_payload(context: LoopContext) -> PrepareNextTurnData:
    """Build the documented PREPARE_NEXT_TURN payload from the loop context.

    Enum-valued context fields are rendered as their wire strings, matching
    the dataclass's documented ``str`` types.
    """
    return cast(
        PrepareNextTurnData,
        payload_for(
            SigilHook.PREPARE_NEXT_TURN,
            system_prompt=context.system_prompt,
            prompt_source=context.prompt_source.value,
            invocations=list(context.invocations),
            spell_names=[spell.name for spell in context.spells],
            contemplation_level=context.contemplation_level.value,
            max_tokens=context.max_tokens,
            temperature=context.temperature,
            spell_timeout_ms=context.spell_timeout_ms,
            contemplation_budget=context.contemplation_budget,
            exclude_contemplation=context.exclude_contemplation,
            max_turns=context.max_turns,
            queue_mode=context.queue_mode.value,
        ),
    )
