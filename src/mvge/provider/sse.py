from __future__ import annotations

import contextlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from mvge.provider.base import Realm
from mvge.provider.retry import (
    RetryBudget,
    ServerRetryDelayTooLongError,
    _sleep_ms,
    is_deterministic_realm_failure,
    is_retryable_realm_response,
    is_retryable_status,
    realm_request_delay_ms,
    retry_budget_for,
    transient_retry_budget,
)


@dataclass
class ParsedError:
    """Structured error extracted from an HTTP response or SSE error chunk."""

    message: str
    error_code: str | None = None
    retry_after: float | None = None
    limit_source: str | None = None
    remedy_hint: str | None = None
    reset_at: float | None = None
    quota_limit: int | None = None
    quota_remaining: int | None = None

    def __iter__(self) -> Any:
        return iter((self.message, self.error_code))

    def to_response(self, model: Model) -> RealmResponse:
        return RealmResponse(
            model=model,
            error_message=self.message,
            error_code=self.error_code,
            retry_after=self.retry_after,
            limit_source=self.limit_source,
            remedy_hint=self.remedy_hint,
            reset_at=self.reset_at,
            quota_limit=self.quota_limit,
            quota_remaining=self.quota_remaining,
        )


@dataclass
class SSEChunk:
    """Parsed Server-Sent Events delta or chunk."""

    content: str | None = None
    contemplation: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


logger = logging.getLogger(__name__)

_ERROR_LOG_BODY_CAP = 4000

# OpenRouter's daily allowance for `:free` models is 50 requests. A drained
# window no larger than this is a spent daily allowance, not upstream capacity.
FREE_TIER_DAILY_QUOTA = 50

# OpenRouter names the account-wide daily cap on free models in the refusal
# message; it is the only part of a router 402 that still says which limit ran
# out.
_FREE_TIER_DAILY_MARKER = "free-models-per-day"

_FREE_TIER_REMEDY_HINT = (
    "Add credits to your OpenRouter account, switch to a paid model, or wait "
    "for the daily reset."
)


def _get_header(
    headers: dict[str, Any] | Any,
    key: str,
    fallback: dict[str, Any] | None = None,
) -> str | None:
    for k in (key, key.lower(), key.title(), key.upper()):
        if headers and k in headers:
            return str(headers[k])
    if fallback:
        for k in (key, key.lower(), key.title(), key.upper()):
            if k in fallback:
                return str(fallback[k])
    return None


def _parse_rate_limits(
    headers: dict[str, Any] | Any,
    fallback_headers: dict[str, Any] | None = None,
) -> tuple[int | None, int | None, float | None]:
    quota_limit: int | None = None
    quota_remaining: int | None = None
    reset_at: float | None = None

    q_lim = _get_header(headers, "x-ratelimit-limit", fallback_headers)
    if q_lim is not None:
        with contextlib.suppress(Exception):
            quota_limit = int(float(q_lim))

    q_rem = _get_header(headers, "x-ratelimit-remaining", fallback_headers)
    if q_rem is not None:
        with contextlib.suppress(Exception):
            quota_remaining = int(float(q_rem))

    q_reset = _get_header(headers, "x-ratelimit-reset", fallback_headers)
    if q_reset is not None:
        with contextlib.suppress(Exception):
            val_f = float(q_reset)
            reset_at = val_f / 1000.0 if val_f > 1e11 else val_f

    return quota_limit, quota_remaining, reset_at


def _classify_limit_source(
    quota_limit: int | None,
    quota_remaining: int | None,
) -> str | None:
    """Name the exhausted window from the reported quota.

    Realms report the limit in `X-RateLimit-*` headers; `error.metadata` is
    optional and usually absent, so deriving the source from metadata alone
    leaves the free-tier message unreachable. A drained window no larger than
    the free-tier daily allowance is a spent allowance; any other reported
    window is upstream capacity. Returns None when no quota was reported at
    all, because an unnamed source beats a wrong name.
    """
    if quota_limit is None or quota_remaining is None:
        return None
    if quota_remaining == 0 and quota_limit <= FREE_TIER_DAILY_QUOTA:
        return "openrouter_free_tier_daily"
    return "upstream_rate_limit"


def _resolve_limit_source(
    reported: str | None,
    quota_limit: int | None,
    quota_remaining: int | None,
) -> str | None:
    """Prefer a Realm-declared source, else classify from the quota headers."""
    if reported is not None:
        return reported
    return _classify_limit_source(quota_limit, quota_remaining)


def _names_doubled_free_tier_report(metadata: Any) -> bool:
    """Whether a 402 is one spent allowance, reported twice.

    `openrouter/free` walks free endpoints until they all refuse on the
    account-wide daily cap, then falls through to a provider that answers 402
    for a different reason -- depleted BYOK credits, typically. The surviving
    provider error becomes the top level and the router answers 402; the free
    model refusals survive only inside `metadata.previous_errors`, each with an
    empty `headers` map, and the 402 itself carries no `X-RateLimit-*` at all.

    So the quota is unreadable here and only the shape is left: a 402 whose
    previous errors are the daily free-model cap, reported again as one
    allowance rather than a billing problem to debug. A previous error that is
    some other rate limit disqualifies it -- the Summoner would be sent to fix
    an allowance that is not the one that ran out.
    """
    previous_errors = (
        metadata.get("previous_errors") if isinstance(metadata, dict) else None
    )
    if not isinstance(previous_errors, list) or not previous_errors:
        return False

    daily_refusals = 0
    for entry in previous_errors:
        if not isinstance(entry, dict):
            return False
        if entry.get("code") != 429:
            # The provider error the 402 already reports, kept for context.
            continue
        if _FREE_TIER_DAILY_MARKER not in str(entry.get("message") or ""):
            return False
        daily_refusals += 1

    return daily_refusals > 0


def _resolve_remedy_hint(
    reported: str | None,
    limit_source: str | None,
) -> str | None:
    """Fill in a remedy when the classified source has a known way out."""
    if reported is not None:
        return reported
    if limit_source == "openrouter_free_tier_daily":
        return _FREE_TIER_REMEDY_HINT
    return None


def _build_parsed_error(body: bytes, status_code: int, headers: Any) -> ParsedError:
    """Parse an HTTP error body into a ParsedError (pure, no I/O)."""
    try:
        error_data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        error_data = {}
    err_obj = error_data.get("error", {}) if isinstance(error_data, dict) else {}
    if not isinstance(err_obj, dict):
        err_obj = {"message": str(err_obj)} if err_obj else {}
    message = err_obj.get("message", f"HTTP {status_code}")
    if message:
        message = message.replace("\r\n", "\n").replace("\r", "\n").strip("\n")

    err_dict = err_obj if isinstance(err_obj, dict) else {}
    metadata = err_dict.get("metadata", {}) if isinstance(err_dict, dict) else {}
    declared_source = (
        metadata.get("limit_source") if isinstance(metadata, dict) else None
    )
    declared_hint = metadata.get("remedy_hint") if isinstance(metadata, dict) else None
    meta_headers = metadata.get("headers", {}) if isinstance(metadata, dict) else {}
    resp_headers = headers or {}

    quota_limit, quota_remaining, reset_at = _parse_rate_limits(
        resp_headers, meta_headers
    )

    limit_source = _resolve_limit_source(declared_source, quota_limit, quota_remaining)
    if limit_source is None and status_code == 402:
        limit_source = (
            "openrouter_free_tier_daily"
            if _names_doubled_free_tier_report(metadata)
            else None
        )
    remedy_hint = _resolve_remedy_hint(declared_hint, limit_source)

    # A 402 that carries the spent daily allowance is the same rate limit the
    # 429 shape reports, so it needs the same error code. Without one the turn
    # raises an opaque `Provider returned error` and nothing above this point
    # ever renders.
    if status_code == 429 or (
        status_code == 402 and limit_source == "openrouter_free_tier_daily"
    ):
        error_code = "rate_limited"
        if message == f"HTTP {status_code}":
            message = "Rate limit exceeded"
    elif status_code == 401:
        error_code = "auth_failed"
    else:
        error_code = None

    retry_after: float | None = None
    q_retry = _get_header(resp_headers, "retry-after", meta_headers)
    if q_retry is not None:
        with contextlib.suppress(Exception):
            retry_after = float(q_retry)

    return ParsedError(
        message=message,
        error_code=error_code,
        retry_after=retry_after,
        limit_source=limit_source,
        remedy_hint=remedy_hint,
        reset_at=reset_at,
        quota_limit=quota_limit,
        quota_remaining=quota_remaining,
    )


def _error_from_response(response: Any) -> ParsedError:
    """Extract error message, error code, and diagnostics from an HTTP response."""
    try:
        body = response.read()
    except Exception:
        body = b""
    return _build_parsed_error(
        body, response.status_code, getattr(response, "headers", {})
    )


async def _error_from_response_async(
    response: Any, attempt: int = 0, max_attempts: int = 1
) -> ParsedError:
    """Async variant for streaming responses; falls back to sync read().

    Logs at DEBUG for intermediate retry attempts, WARNING for the final attempt.
    """
    body = b""
    try:
        aread = getattr(response, "aread", None)
        if callable(aread):
            body = await aread()
        else:
            body = response.read()
    except Exception:
        body = b""
    parsed = _build_parsed_error(
        body, response.status_code, getattr(response, "headers", {})
    )
    is_final = attempt >= max_attempts - 1
    log_level = logging.WARNING if is_final else logging.DEBUG
    attempt_str = f"attempt {attempt + 1}/{max_attempts}"
    if parsed.message == f"HTTP {response.status_code}":
        logger.log(
            log_level,
            "Realm request %s failed with status %s and unreadable body",
            attempt_str,
            response.status_code,
        )
    else:
        snippet = parsed.message[:_ERROR_LOG_BODY_CAP]
        logger.log(
            log_level,
            "Realm request %s failed (%s): %s",
            attempt_str,
            response.status_code,
            snippet,
        )
    return parsed


class SSEStreamingRealm(Realm, ABC):
    """Abstract base class for Realms that stream completions over SSE."""

    realm_name: str = ""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._owned_client = client is None
        client_headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        # Only sent when there is a credential to put in it. `Bearer ` with
        # nothing after it is an illegal header value, and httpx raises
        # LocalProtocolError on it before the request leaves the process --
        # so a Realm that serves an unauthenticated endpoint (OpenCode Zen's
        # free tier needs no key) crashed for exactly the callers it exists to
        # serve. Found by driving the live gateway; a mock transport accepts the
        # malformed value, so no hermetic test would ever have caught it.
        if api_key:
            client_headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers=client_headers,
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )

    @abstractmethod
    def _prepare_request(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Construct the request URL, headers, and JSON body."""
        raise NotImplementedError

    @abstractmethod
    def _parse_sse_chunk(self, chunk: dict[str, Any]) -> SSEChunk | None:
        """Parse a decoded SSE JSON chunk into an SSEChunk delta."""
        raise NotImplementedError

    def _parse_usage(self, usage: dict[str, Any]) -> dict[str, float]:
        """Convert a provider usage payload into a standard Mana breakdown."""
        if not usage:
            return {}
        prompt = float(usage.get("prompt_tokens", 0))
        completion = float(usage.get("completion_tokens", 0))
        total = float(usage.get("total_tokens", prompt + completion))
        breakdown: dict[str, float] = {
            "input": prompt,
            "output": completion,
            "total": total,
        }
        reasoning = usage.get("completion_tokens_details", {}).get("reasoning_tokens")
        if reasoning:
            breakdown["contemplation"] = float(reasoning)
        return breakdown

    def _parse_error(self, response: Any) -> ParsedError:
        """Extract error message and code from a non-200 HTTP response."""
        return _error_from_response(response)

    async def _parse_error_async(
        self, response: Any, attempt: int = 0, max_attempts: int = 1
    ) -> ParsedError:
        """Async variant for streaming responses (uses aread with fallback)."""
        return await _error_from_response_async(response, attempt, max_attempts)

    def _realm_name(self, model: Model) -> str:
        """Derive the realm identifier to tag on MvgeResponse instances."""
        return self.realm_name or model.realm or "sse"

    async def stream(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> AsyncIterator[RealmResponse]:
        url, headers, payload = self._prepare_request(model, invocations, config)
        if payload.get("stream"):
            # Streaming providers only attach token usage when asked.
            # Unknown fields are ignored by JSON APIs, and setdefault lets a
            # realm override the default when it manages usage itself.
            stream_options = payload.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
                payload["stream_options"] = stream_options
            stream_options.setdefault("include_usage", True)
        # A failure re-rates the budget it is retrying on: capacity saturation
        # earns a minutes-scale ceiling, everything else stays transient. Only
        # the accumulated wait bounds the loop, so the run always terminates.
        budget: RetryBudget = transient_retry_budget(config.max_retries)
        attempt = 0
        waited_ms = 0.0

        while attempt < budget.max_attempts:
            if signal is not None and signal.aborted:
                raise AbortError("Operation aborted")

            async with self._client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=config.timeout_ms / 1000,
            ) as response:
                try:
                    # Every path that reaches the retry decision below sets this.
                    failure: RealmResponse | None = None
                    if response.status_code == 200:
                        streamed_any = False
                        async for item in self._consume_stream(model, response):
                            if signal is not None and signal.aborted:
                                raise AbortError("Operation aborted")
                            if (
                                item.error_message
                                and not streamed_any
                                and is_retryable_realm_response(item)
                            ):
                                budget = retry_budget_for(item, budget.max_attempts)
                                if attempt < budget.max_attempts - 1:
                                    failure = item
                                    break
                            streamed_any = True
                            yield item
                        if failure is None:
                            return
                    else:
                        parsed_err = await self._parse_error_async(
                            response, attempt, budget.max_attempts
                        )
                        if not is_retryable_status(
                            response.status_code, response.headers
                        ):
                            yield parsed_err.to_response(model)
                            return
                        failure = parsed_err.to_response(model)
                        # A retryable status is not on its own a reason to retry:
                        # every 429 is a retryable status, including a drained
                        # allowance that reopens in hours. Only a failure we know
                        # is permanent vetoes the loop; an uninformative message
                        # does not, so status-driven retries keep working.
                        if is_deterministic_realm_failure(failure):
                            yield failure
                            return
                        budget = retry_budget_for(failure, budget.max_attempts)
                    assert failure is not None
                finally:
                    if signal is not None and signal.aborted:
                        await response.aclose()

                if attempt >= budget.max_attempts - 1:
                    yield failure
                    return

                try:
                    delay_ms = realm_request_delay_ms(
                        response.headers,
                        attempt,
                        base_delay_ms=budget.base_delay_ms,
                        max_backoff_ms=budget.max_backoff_ms,
                    )
                except ServerRetryDelayTooLongError as exc:
                    yield RealmResponse(
                        model=model,
                        error_message=f"{exc}. {failure.error_message or ''}",
                        error_code=failure.error_code,
                    )
                    return

                if waited_ms + delay_ms > budget.max_total_wait_ms:
                    # Bounded: the ceiling is spent, so fail with the Realm's own
                    # message instead of stalling past the budget.
                    yield failure
                    return

            await _sleep_ms(delay_ms, signal)
            waited_ms += delay_ms
            attempt += 1

    async def _consume_stream(
        self,
        model: Model,
        response: Any,
    ) -> AsyncIterator[RealmResponse]:
        text_parts: list[str] = []
        contemplation_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage_acc: dict[str, Any] = {}
        finish_reason: str | None = None
        final_yielded: bool = False
        streamed_any = False

        async for line in response.aiter_lines():
            if not line:
                continue
            line_str = line if isinstance(line, str) else line.decode("utf-8")
            if not line_str.startswith("data:"):
                continue
            data = line_str[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if isinstance(chunk, dict) and chunk.get("error"):
                err = chunk["error"]
                err_msg = (
                    err.get("message", "Stream error")
                    if isinstance(err, dict)
                    else str(err)
                )
                err_code = err.get("code") if isinstance(err, dict) else None
                meta = err.get("metadata", {}) if isinstance(err, dict) else {}
                meta_hdrs = meta.get("headers", {}) if isinstance(meta, dict) else {}
                quota_limit, quota_remaining, reset_at = _parse_rate_limits(meta_hdrs)
                declared_source = (
                    meta.get("limit_source") if isinstance(meta, dict) else None
                )
                limit_source = _resolve_limit_source(
                    declared_source, quota_limit, quota_remaining
                )
                declared_hint = (
                    meta.get("remedy_hint") if isinstance(meta, dict) else None
                )

                error_response = RealmResponse(
                    model=model,
                    error_message=err_msg,
                    error_code=str(err_code) if err_code is not None else None,
                    limit_source=limit_source,
                    remedy_hint=_resolve_remedy_hint(declared_hint, limit_source),
                    quota_limit=quota_limit,
                    quota_remaining=quota_remaining,
                    reset_at=reset_at,
                )
                if streamed_any and is_retryable_realm_response(error_response):
                    # Transient failure after partial content was already
                    # emitted: the outer retry loop cannot replay the turn
                    # without duplicating transcript text, so classify for
                    # friendly handling upstream.
                    error_response.error_code = "upstream_idle_timeout"
                yield error_response
                return

            parsed = self._parse_sse_chunk(chunk)
            if parsed is None:
                continue

            if parsed.usage:
                usage_acc = parsed.usage

            if parsed.contemplation:
                contemplation_parts.append(parsed.contemplation)
                streamed_any = True
                yield RealmResponse(
                    model=model,
                    invocation=MvgeResponse(
                        role="assistant",
                        content=[
                            {"type": "contemplation", "text": parsed.contemplation}
                        ],
                        realm=self._realm_name(model),
                        model=model.id,
                    ),
                    stop_reason="pending",
                )

            if parsed.content:
                text_parts.append(parsed.content)
                streamed_any = True
                yield RealmResponse(
                    model=model,
                    invocation=MvgeResponse(
                        role="assistant",
                        content=[{"type": "text", "text": parsed.content}],
                        realm=self._realm_name(model),
                        model=model.id,
                    ),
                    stop_reason="pending",
                )

            for tc in parsed.tool_calls or []:
                index = tc.get("index", 0)
                acc = tool_calls_acc.setdefault(
                    index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.get("id"):
                    acc["id"] = tc["id"]
                func = tc.get("function", {})
                if func.get("name"):
                    acc["name"] = func["name"]
                if func.get("arguments"):
                    acc["arguments"] += func["arguments"]

            if parsed.finish_reason:
                finish_reason = parsed.finish_reason
                if parsed.usage or usage_acc:
                    yield self._build_final_response(
                        model=model,
                        text_parts=text_parts,
                        contemplation_parts=contemplation_parts,
                        tool_calls_acc=tool_calls_acc,
                        finish_reason=finish_reason,
                        usage=parsed.usage or usage_acc,
                    )
                    final_yielded = True
                    return
                # Usage not seen yet: providers like OpenRouter send the
                # usage chunk AFTER the finish_reason chunk, so keep
                # consuming instead of dropping it.
                continue

        if not final_yielded and (
            finish_reason is not None
            or text_parts
            or contemplation_parts
            or tool_calls_acc
        ):
            yield self._build_final_response(
                model=model,
                text_parts=text_parts,
                contemplation_parts=contemplation_parts,
                tool_calls_acc=tool_calls_acc,
                finish_reason=finish_reason or "stop",
                usage=usage_acc,
            )

    def _build_final_response(
        self,
        model: Model,
        text_parts: list[str],
        contemplation_parts: list[str],
        tool_calls_acc: dict[int, dict[str, Any]],
        finish_reason: str,
        usage: dict[str, Any],
    ) -> RealmResponse:
        blocks: list[dict[str, Any]] = []
        if contemplation_parts:
            blocks.append(
                {
                    "type": "contemplation",
                    "text": "".join(contemplation_parts),
                }
            )
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})

        for index in sorted(tool_calls_acc):
            acc = tool_calls_acc[index]
            try:
                arguments = json.loads(acc["arguments"]) if acc["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            blocks.append(
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": acc["id"],
                        "name": acc["name"],
                        "arguments": arguments,
                    },
                }
            )

        if tool_calls_acc:
            stop_reason = StopReason.SPELL_USE
        elif finish_reason == "length":
            stop_reason = StopReason.LENGTH
        else:
            stop_reason = StopReason.STOP

        mana_usage = self._parse_usage(usage)
        total_tokens = int(mana_usage.get("total", usage.get("total_tokens", 0)))

        invocation = MvgeResponse(
            role="assistant",
            content=blocks,
            realm=self._realm_name(model),
            model=model.id,
            stop_reason=stop_reason,
            mana_usage=mana_usage,
        )
        return RealmResponse(
            model=model,
            invocation=invocation,
            mana_used=total_tokens,
            stop_reason=stop_reason.value,
        )

    async def close(self) -> None:
        if (
            self._owned_client
            and self._client is not None
            and not self._client.is_closed
        ):
            await self._client.aclose()


__all__ = [
    "ParsedError",
    "SSEChunk",
    "SSEStreamingRealm",
    "_build_parsed_error",
    "_error_from_response",
    "_error_from_response_async",
]
