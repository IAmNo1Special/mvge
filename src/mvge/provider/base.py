from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from mvge.core.abort import AbortSignal
from mvge.core.channel import (
    ChannelConfig,
    Model,
)
from mvge.core.errors import MvgeError


class NoRealmRegisteredError(MvgeError):
    """Raised when no Realm factory is registered for a requested model.

    ``realm`` and ``rune_name`` are carried as attributes rather than left for
    the caller to recover from the message. Both of the surfaces that act on this
    -- the CLI install prompt and the GUI badge -- used to split the message on
    ``"install "`` to find the Rune name, which couples them to the wording and
    fails quietly if it changes.
    """

    def __init__(self, message: str, realm: str = "", rune_name: str = "") -> None:
        super().__init__("no_realm_registered", message)
        self.realm = realm
        self.rune_name = rune_name


class Realm:
    @property
    def is_router(self) -> bool:
        """Return True if this realm routes requests to multiple upstream providers."""
        return False

    def stream(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError

    async def complete(
        self,
        model: Model,
        messages: list[dict[str, Any]],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> Any:
        """Run one non-channelled request and return the whole response.

        Used for standalone calls that are not part of a Tome's transcript,
        such as generating a compaction summary. Takes wire-format messages
        rather than Invocations because the caller composes the prompt.
        """
        raise NotImplementedError

    async def close(self) -> None:
        pass


logger = logging.getLogger(__name__)


class FallbackRealm(Realm):
    """Composite Realm routing to a fallback when the primary fails unrecoverably."""

    def __init__(
        self,
        primary: Realm,
        fallback: Realm,
        fallback_model: Model | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_model = fallback_model

    @property
    def is_router(self) -> bool:
        return self._primary.is_router or self._fallback.is_router

    async def complete(
        self,
        model: Model,
        messages: list[dict[str, Any]],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> Any:
        try:
            return await self._primary.complete(model, messages, config, signal=signal)
        except Exception as exc:
            logger.warning(
                "Primary realm %s failed (%s); routing to fallback realm %s",
                type(self._primary).__name__,
                exc,
                type(self._fallback).__name__,
            )
            target_model = self._fallback_model or model
            return await self._fallback.complete(
                target_model, messages, config, signal=signal
            )

    async def stream(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> AsyncIterator[Any]:
        emitted_any = False
        try:
            async for chunk in self._primary.stream(
                model, invocations, config, signal=signal
            ):
                emitted_any = True
                yield chunk
        except Exception as exc:
            if emitted_any:
                raise
            logger.warning(
                "Primary realm %s stream failed (%s); routing to fallback realm %s",
                type(self._primary).__name__,
                exc,
                type(self._fallback).__name__,
            )
            target_model = self._fallback_model or model
            async for chunk in self._fallback.stream(
                target_model, invocations, config, signal=signal
            ):
                yield chunk

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()


@runtime_checkable
class RealmFactory(Protocol):
    """Protocol for factories that construct Realm instances."""

    def __call__(
        self,
        api_key: str = "",
        base_url: str = "",
        **kwargs: Any,
    ) -> Realm:
        """Create and return a configured Realm instance."""
        ...


__all__ = [
    "FallbackRealm",
    "NoRealmRegisteredError",
    "Realm",
    "RealmFactory",
]
