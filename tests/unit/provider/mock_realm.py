from __future__ import annotations

import json
from typing import Any

import httpx

from mvge.core.abort import (
    AbortError,
    AbortSignal,
)
from mvge.core.channel import (
    ChannelConfig,
    Model,
    MvgeResponse,
    RealmResponse,
    StopReason,
)
from mvge.provider.retry import retry_realm_request
from mvge.provider.sse import SSEChunk, SSEStreamingRealm

_REASONING_MODELS = ("openai/o1", "openai/o3")


def _supports_reasoning(model: Model) -> bool:
    if model.supported_parameters:
        return "reasoning" in model.supported_parameters
    return any(m in model.id for m in _REASONING_MODELS)


def _invocations_to_messages(invocations: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for inv in invocations:
        if hasattr(inv, "role") and inv.role == "system":
            messages.append({"role": "system", "content": inv.content or ""})
        elif hasattr(inv, "role") and inv.role == "user":
            messages.append({"role": "user", "content": inv.content or ""})
        elif hasattr(inv, "role") and inv.role == "assistant":
            if hasattr(inv, "content") and inv.content:
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in inv.content:
                    if block.get("type") == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif block.get("type") == "spell_cast":
                        sc = block.get("spell_cast", {})
                        tool_calls.append(
                            {
                                "id": sc.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": sc.get("name", ""),
                                    "arguments": json.dumps(sc.get("arguments", {})),
                                },
                            }
                        )
                if tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "".join(text_parts) or None,
                            "tool_calls": tool_calls,
                        }
                    )
                else:
                    messages.append(
                        {"role": "assistant", "content": "".join(text_parts)}
                    )
        elif hasattr(inv, "role") and inv.role in ("spellResult", "tool"):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": getattr(inv, "spell_cast_id", ""),
                    "content": inv.content[0].get("text", "") if inv.content else "",
                }
            )
    return messages


def _with_system_prompt(
    messages: list[dict[str, Any]], system_prompt: str
) -> list[dict[str, Any]]:
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        return [{"role": "system", "content": system_prompt}, *messages]
    return messages


class MockStreamingRealm(SSEStreamingRealm):
    realm_name: str = "openrouter"

    @property
    def is_router(self) -> bool:
        return True

    def __init__(
        self,
        api_key: str = "test-key",
        base_url: str = "https://openrouter.ai/api/v1",
        client: httpx.AsyncClient | None = None,
        realm_name: str = "openrouter",
    ) -> None:
        self.realm_name = realm_name
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
            client=client,
        )

    def _prepare_request_url_and_headers(
        self, model: Model
    ) -> tuple[str, dict[str, str]]:
        base_url = (
            model.base_url or self._base_url or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        api_key = model.api_key or self._api_key
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if model.headers:
            headers.update(model.headers)
        return url, headers

    def _prepare_request(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        messages = _with_system_prompt(
            _invocations_to_messages(invocations), config.system_prompt
        )
        url, headers = self._prepare_request_url_and_headers(model)

        payload: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
        }

        if (
            _supports_reasoning(model)
            and not config.exclude_contemplation
            and config.contemplation_level not in ("none", "off", "")
        ):
            reasoning: dict[str, Any] = {"effort": config.contemplation_level}
            if config.contemplation_budget is not None:
                reasoning["max_tokens"] = config.contemplation_budget
            payload["reasoning"] = reasoning

        if config.max_output_mana is not None:
            payload["max_tokens"] = min(config.max_tokens, config.max_output_mana)

        if config.tools:
            payload["tools"] = config.tools

        return url, headers, payload

    def _parse_sse_chunk(self, chunk: dict[str, Any]) -> SSEChunk | None:
        usage = chunk.get("usage")
        choices = chunk.get("choices") or []
        if not choices:
            if usage:
                return SSEChunk(usage=usage)
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        contemplation = delta.get("reasoning") or delta.get("reasoning_content")

        return SSEChunk(
            content=delta.get("content"),
            contemplation=contemplation,
            tool_calls=delta.get("tool_calls"),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )

    async def complete(
        self,
        model: Model,
        messages: list[dict[str, Any]],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> RealmResponse:
        """Run one non-channelled completion.

        Deliberately omits `tools`: this is used for standalone requests such
        as compaction summaries, where Spells must not be offered.
        """
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")
        effective_messages = _with_system_prompt(list(messages), config.system_prompt)
        url, headers = self._prepare_request_url_and_headers(model)
        payload: dict[str, Any] = {
            "model": model.id,
            "messages": effective_messages,
            "stream": False,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        async def do_request() -> Any:
            return await self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=config.timeout_ms / 1000,
            )

        response = await retry_realm_request(
            do_request, max_retries=config.max_retries, signal=signal
        )

        if response.status_code != 200:
            return self._parse_error(response).to_response(model)

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return RealmResponse(
                model=model,
                error_message="Realm returned no choices",
            )

        content = choices[0].get("message", {}).get("content") or ""
        usage = data.get("usage", {})
        return RealmResponse(
            model=model,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": content}],
                realm=self.realm_name,
                model=model.id,
                stop_reason=StopReason.STOP,
                mana_usage=self._parse_usage(usage),
            ),
            mana_used=usage.get("total_tokens", 0),
            stop_reason=StopReason.STOP.value,
        )


__all__ = [
    "MockStreamingRealm",
    "_invocations_to_messages",
    "_with_system_prompt",
]
