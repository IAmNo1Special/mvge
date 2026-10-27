"""Retry policy for Realm calls.

Two layers, mirroring Pi:

- `retry_realm_request` retries a single HTTP request against a Realm. It honours
  server-supplied `Retry-After` headers and backs off with capped, jittered
  exponential delay.
- `retry_invocation` retries a whole Invocation. Realms report transient trouble
  as a `RealmResponse` carrying an error rather than raising, so this layer
  classifies the response instead of catching exceptions.

Both take an optional `signal` (AbortSignal). When aborted, retry loops exit
promptly by raising AbortError.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mvge.core.abort import AbortError
from mvge.core.channel import RealmResponse

DEFAULT_MAX_RETRY_DELAY_MS = 60_000
_MAX_BACKOFF_MS = 8_000
_BACKOFF_BASE_MS = 500
_JITTER_FRACTION = 0.25

_RETRYABLE_STATUSES = (408, 409, 429)

# Realm/transport wording that indicates a transient failure worth retrying.
_RETRYABLE_PATTERNS = (
    "overloaded",
    "rate.?limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "524",
    "service.?unavailable",
    "server.?error",
    "internal.?error",
    "provider.?returned.?error",
    "network.?error",
    "connection.?error",
    "connection.?refused",
    "connection.?lost",
    "other side closed",
    "fetch failed",
    "getaddrinfo",
    "ENOTFOUND",
    "EAI_AGAIN",
    "upstream.?connect",
    "reset before headers",
    "socket hang up",
    "socket connection was closed",
    "timed? out",
    "timeout",
    "terminated",
    "websocket.?closed",
    "websocket.?error",
    "ended without",
    "stream ended before",
    "http2 request did not get a response",
    "retry delay",
    "you can retry your request",
    "try your request again",
    "please retry your request",
    "ResourceExhausted",
)

# Account limits and billing failures. Deterministic, so never retried, even
# when the wording also matches a transient pattern.
#
# "quota exhausted" and "free-models-per-day" are here because a drained
# daily allowance is the failure we actually spent days mistaking for capacity
# saturation. The wording that reaches this module is OpenRouter's own,
# `Rate limit exceeded: free-models-per-day`, and it carries no transient
# signal at all -- but the 429 status alone is enough for the caller to retry
# it three times and then tell the Summoner to wait hours for a window this
# engine will never sit out. "quota exhausted" additionally covers the
# rendered shape the CLI produces from the same allowance.
_NON_RETRYABLE_PATTERNS = (
    "GoUsageLimitError",
    "FreeUsageLimitError",
    "Monthly usage limit reached",
    "available balance",
    "insufficient_quota",
    "out of budget",
    "quota exceeded",
    "quota exhausted",
    "free-models-per-day",
    "billing",
)

# Endpoint at capacity: the Realm has no room for this request right now. This
# is a narrower class than _RETRYABLE_PATTERNS on purpose. A dropped connection
# or a socket hang up recovers in milliseconds; a saturated free endpoint
# recovers in seconds to minutes, measured as bursty windows rather than
# independent per-attempt failures. Retrying either on the same budget packs
# every attempt inside the same window and fails them all together, so only the
# capacity wording earns the minutes-scale budget in CAPACITY_RETRY_BUDGET.
#
# Deliberately absent: 429 and "rate.?limit". Those Realms say when to come back
# via `Retry-After`, which realm_request_delay_ms already honours exactly.
_CAPACITY_PATTERNS = (
    "overloaded",
    "at.?capacity",
    "no.?capacity",
    "capacity.?exceeded",
    "insufficient.?capacity",
    "no.?available.?capacity",
    "service.?unavailable",
    "server.?is.?busy",
    "too.?many.?concurrent",
)

_RETRYABLE_RE = re.compile("|".join(_RETRYABLE_PATTERNS), re.IGNORECASE)
_NON_RETRYABLE_RE = re.compile("|".join(_NON_RETRYABLE_PATTERNS), re.IGNORECASE)
_CAPACITY_RE = re.compile("|".join(_CAPACITY_PATTERNS), re.IGNORECASE)

# Error codes a Realm sets explicitly. Trusted ahead of the prose patterns.
_RETRYABLE_CODES = frozenset({"rate_limited"})
_NON_RETRYABLE_CODES = frozenset({"auth_failed"})
#: Runes that can classify their own saturation set one of these; it wins over
#: the prose patterns exactly as `rate_limited` already does.
_CAPACITY_CODES = frozenset({"overloaded", "capacity_exceeded", "server_overloaded"})


class ServerRetryDelayTooLongError(Exception):
    """Raised when a Realm asks for a longer retry delay than we will wait."""

    def __init__(self, delay_ms: float, max_delay_ms: float) -> None:
        super().__init__(
            f"Realm requested {delay_ms / 1000:.0f}s retry delay "
            f"(max: {max_delay_ms / 1000:.0f}s)"
        )
        self.delay_ms = delay_ms
        self.max_delay_ms = max_delay_ms


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded attempts with exponential backoff."""

    enabled: bool = True
    max_retries: int = 3
    base_delay_ms: float = 1000.0


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True)
class RetryBudget:
    """Attempts a retry loop may make, the wall-clock it may spend waiting, and
    the backoff schedule it waits on.

    `max_total_wait_ms` is the enforced bound: a loop stops once the sleeps it
    has already asked for plus the next one would exceed it. Bounding the
    accumulated wait rather than trusting the schedule means retuning the
    backoff constants cannot quietly turn a bounded retry into a stall.
    """

    max_attempts: int
    max_total_wait_ms: float
    base_delay_ms: float
    max_backoff_ms: float


#: Transient failures clear in milliseconds, so they keep the historical
#: schedule. The total-wait ceiling is 3x the per-delay ceiling, which does not
#: bind at the default attempt count: three attempts wait 500 ms then 1000 ms.
#: It exists so a caller raising `max_retries` cannot buy an unbounded stall.
TRANSIENT_RETRY_BUDGET = RetryBudget(
    max_attempts=3,
    max_total_wait_ms=3 * _MAX_BACKOFF_MS,
    base_delay_ms=_BACKOFF_BASE_MS,
    max_backoff_ms=_MAX_BACKOFF_MS,
)

#: Capacity saturation at a free endpoint clears in seconds to minutes, and the
#: failures come in windows rather than at random, so the useful lever is
#: spacing, not attempt count. Eight attempts on this schedule wait 5 s, 10 s,
#: then 15 s each, i.e. 90 s spread across eight requests -- about 30x the
#: transient budget and long enough that attempts land in different windows.
#:
#: The ceiling is chosen, not tuned into the stratosphere. A stranger's first
#: run must still finish inside the five-minute onboarding promise, and a
#: genuinely dead Realm has to fail rather than hang. Every individual delay
#: stays under DEFAULT_MAX_RETRY_DELAY_MS (60 s), so a server-supplied
#: `Retry-After` above that cap still raises rather than stalling, and
#: max_total_wait_ms caps the run even if the schedule above changes.
#:
#: Tripwire: if a clean-machine quickstart still fails at a poor rate with this
#: budget in place, the saturation window is longer than 90 s and this ceiling
#: is the thing to raise.
CAPACITY_RETRY_BUDGET = RetryBudget(
    max_attempts=8,
    max_total_wait_ms=90_000.0,
    base_delay_ms=5_000.0,
    max_backoff_ms=15_000.0,
)

#: A window that reopens later than this cannot be waited out, so retrying it is
#: not merely slow -- it cannot succeed inside any budget this engine will
#: spend. The threshold is the largest wait we ever plan to make (the capacity
#: ceiling) or the largest single server-requested delay we will accept,
#: whichever is greater. Below it a drained window measured in seconds stays
#: retryable, because waiting out a per-minute rate limit is exactly what
#: retrying is for.
_MAX_RETRYABLE_WINDOW_MS = max(
    CAPACITY_RETRY_BUDGET.max_total_wait_ms, DEFAULT_MAX_RETRY_DELAY_MS
)


def _is_unreachable_window(reset_at: float | None) -> bool:
    """Whether a reported window reopens too late for a retry to help.

    Structured, so it holds for any Realm that reports one, and for a drained
    allowance whose wording this module has never seen. `reset_at` is epoch
    seconds as parsed by the Realm layer; an absent or already-elapsed window
    imposes no bound.
    """
    if reset_at is None:
        return False
    return (reset_at - time.time()) * 1000 > _MAX_RETRYABLE_WINDOW_MS


@dataclass
class RetryCallbacks:
    """Optional hooks fired around each retry. None of these may raise."""

    on_retry_scheduled: Callable[[int, int, float, str], Awaitable[None]] | None = None
    on_retry_attempt_start: Callable[[], Awaitable[None]] | None = None
    on_retry_finished: Callable[[bool, int, str | None], Awaitable[None]] | None = None


class _HeaderLike(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...


def _deterministic_reason(response: RealmResponse) -> str | None:
    """Why a failure is a known permanent refusal, or None when it is not known.

    One source of truth for the precedence, shared by every classifier below.
    Deliberately one-sided: it returns a reason only when we are *certain* the
    failure will not clear on its own. Wording that merely fails to look
    transient is not certainty -- a Realm may report a retryable status with an
    uninformative message -- so that leaves the caller free to keep retrying on
    the strength of the status alone.
    """
    if not response.error_message:
        return "no error message"
    message = response.error_message
    if response.error_code in _NON_RETRYABLE_CODES:
        return f"explicit non-retryable code {response.error_code!r}"
    if _is_unreachable_window(response.reset_at):
        return f"window reopens in more than {_MAX_RETRYABLE_WINDOW_MS / 1000:.0f}s"
    if _NON_RETRYABLE_RE.search(message):
        return "account limit or billing wording"
    return None


def is_deterministic_realm_failure(response: RealmResponse) -> bool:
    """Whether a failure should veto a retry the caller already justified.

    This is the veto a caller needs when it has other evidence for retrying --
    typically a retryable HTTP status -- and needs to know the failure will not
    clear. `is_retryable_realm_response` answers "does this look transient?",
    which is a different and stricter question: it also says no to a message
    carrying no transient signal at all. Use this one when the absence of a
    signal must not be enough to stop a retry; only a positive claim is.
    """
    return _deterministic_reason(response) is not None


def is_retryable_realm_response(response: RealmResponse) -> bool:
    """Whether a failed Realm response looks transient enough to retry.

    Precedence, most specific first: a known permanent refusal, then an explicit
    retryable `error_code`, then transient wording.

    Account wording outranks a retryable code on purpose. Every 429 is stamped
    `rate_limited` (`sse.py::_build_parsed_error`), so with the code checked
    first the deterministic patterns could never fire for a rate-limit refusal
    -- which is exactly the shape a drained daily allowance arrives in.
    `rate_limited` is a claim about the transport; an account refusal is a claim
    about the account, and the latter is the more specific one.
    """
    if _deterministic_reason(response) is not None:
        return False
    if response.error_code in _RETRYABLE_CODES:
        return True
    return bool(_RETRYABLE_RE.search(response.error_message or ""))


def is_capacity_saturated_realm_response(response: RealmResponse) -> bool:
    """Whether a failure says the Realm is at capacity, not merely flaky.

    Capacity saturation and a transient blip get separate retry budgets
    (`retry_budget_for`), so this must be the narrower claim of the two. It
    shares `is_retryable_realm_response`'s precedence: an account, quota, or
    spent-allowance refusal is deterministic however it is phrased, and so is a
    window that reopens after every budget this engine will spend. Neither is
    capacity, and a capacity code does not override either.
    """
    if _deterministic_reason(response) is not None:
        return False
    if response.error_code in _CAPACITY_CODES:
        return True
    return bool(_CAPACITY_RE.search(response.error_message or ""))


def transient_retry_budget(max_attempts: int) -> RetryBudget:
    """The budget for a failure that has not yet been classified as capacity."""
    return RetryBudget(
        max_attempts=max(1, max_attempts),
        max_total_wait_ms=TRANSIENT_RETRY_BUDGET.max_total_wait_ms,
        base_delay_ms=TRANSIENT_RETRY_BUDGET.base_delay_ms,
        max_backoff_ms=TRANSIENT_RETRY_BUDGET.max_backoff_ms,
    )


def retry_budget_for(response: RealmResponse, max_attempts: int) -> RetryBudget:
    """The budget a classified failure earns.

    Capacity earns a longer schedule and a minutes-scale ceiling. Its attempt
    count is a floor a caller can raise but not lower, because a caller asking
    for three retries means "three retries", not "three retries unless the
    endpoint is saturated"; the absolute ceiling stays in CAPACITY_RETRY_BUDGET
    either way. Every other failure keeps the transient budget.
    """
    if not is_capacity_saturated_realm_response(response):
        return transient_retry_budget(max_attempts)
    return RetryBudget(
        max_attempts=max(max_attempts, CAPACITY_RETRY_BUDGET.max_attempts),
        max_total_wait_ms=CAPACITY_RETRY_BUDGET.max_total_wait_ms,
        base_delay_ms=CAPACITY_RETRY_BUDGET.base_delay_ms,
        max_backoff_ms=CAPACITY_RETRY_BUDGET.max_backoff_ms,
    )


async def _sleep_ms(delay_ms: float, signal: Any | None = None) -> None:
    """Sleep for delay_ms, respecting abort signal.

    If multiple calls to _sleep_ms share the same signal, all will be cancelled
    when the signal is aborted. This is intentional shared-cancellation behavior.
    """
    if delay_ms <= 0:
        return
    if signal is not None and signal.aborted:
        raise AbortError("Operation aborted")
    sleep_task = asyncio.create_task(asyncio.sleep(delay_ms / 1000))

    def _on_abort() -> None:
        if not sleep_task.done():
            sleep_task.cancel()

    if signal is not None:
        signal.on_abort(_on_abort)
        if signal.aborted:
            raise AbortError("Operation aborted")
    try:
        await sleep_task
    except asyncio.CancelledError:
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted") from None
        raise


async def retry_invocation(
    produce: Callable[[], Awaitable[RealmResponse]],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    callbacks: RetryCallbacks | None = None,
    signal: Any | None = None,
) -> RealmResponse:
    """Run an Invocation, retrying transient Realm failures.

    Returns the first successful response, or the final failure once retries are
    exhausted or the error is classified as deterministic.
    """
    max_attempts = policy.max_retries if policy.enabled else 0
    attempt = 0
    last_error: str | None = None

    while True:
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")
        response = await produce()

        if not response.error_message:
            if last_error is not None and callbacks and callbacks.on_retry_finished:
                await callbacks.on_retry_finished(True, attempt, None)
            return response

        if attempt >= max_attempts or not is_retryable_realm_response(response):
            if last_error is not None and callbacks and callbacks.on_retry_finished:
                await callbacks.on_retry_finished(
                    False, attempt, response.error_message
                )
            return response

        attempt += 1
        last_error = response.error_message
        delay_ms = policy.base_delay_ms * (2 ** (attempt - 1))

        if callbacks and callbacks.on_retry_scheduled:
            await callbacks.on_retry_scheduled(
                attempt, max_attempts, delay_ms, last_error
            )

        await _sleep_ms(delay_ms, signal)

        if callbacks and callbacks.on_retry_attempt_start:
            await callbacks.on_retry_attempt_start()


def _parse_delay(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except ValueError:
        return None


def realm_request_delay_ms(
    headers: _HeaderLike,
    retry_index: int,
    max_retry_delay_ms: float | None = None,
    base_delay_ms: float | None = None,
    max_backoff_ms: float | None = None,
) -> float:
    """Delay before the next HTTP attempt.

    A Realm-supplied `Retry-After` wins. Anything longer than the cap raises
    rather than stalling the run; pass `max_retry_delay_ms=0` to disable it.
    Otherwise backoff is exponential, capped, and jittered downward so parallel
    callers do not retry in lockstep. `base_delay_ms` and `max_backoff_ms` let a
    caller apply a `RetryBudget`'s schedule; both default to this module's.
    """
    cap = (
        DEFAULT_MAX_RETRY_DELAY_MS if max_retry_delay_ms is None else max_retry_delay_ms
    )

    requested = _parse_delay(headers.get("retry-after-ms"))
    if requested is None:
        seconds = _parse_delay(headers.get("retry-after"))
        requested = None if seconds is None else seconds * 1000

    if requested is not None:
        if cap > 0 and requested > cap:
            raise ServerRetryDelayTooLongError(requested, cap)
        return float(requested)

    base = _BACKOFF_BASE_MS if base_delay_ms is None else base_delay_ms
    ceiling = _MAX_BACKOFF_MS if max_backoff_ms is None else max_backoff_ms
    exponential = min(base * (2**retry_index), ceiling)
    return float(exponential * (1 - random.random() * _JITTER_FRACTION))


def is_retryable_status(status: int, headers: _HeaderLike | None = None) -> bool:
    """Whether an HTTP status from a Realm is worth retrying.

    An explicit `x-should-retry` header from the Realm overrides the status.
    """
    if headers is not None:
        should_retry = headers.get("x-should-retry")
        if should_retry == "true":
            return True
        if should_retry == "false":
            return False
    return status in _RETRYABLE_STATUSES or status >= 500


def _status_of(error: Exception) -> int | None:
    status = getattr(error, "status", None)
    return status if isinstance(status, int) else None


def _headers_of(error: Exception) -> _HeaderLike | None:
    headers = getattr(error, "headers", None)
    return headers if headers is not None and hasattr(headers, "get") else None


def _is_retryable_request_error(error: Exception) -> bool:
    headers = _headers_of(error)
    status = _status_of(error)
    if status is None:
        return bool(headers and headers.get("x-should-retry") == "true")
    return is_retryable_status(status, headers)


async def retry_realm_request[T](
    request: Callable[[], Awaitable[T]],
    max_retries: int = 0,
    max_retry_delay_ms: float | None = None,
    signal: Any | None = None,
) -> T:
    """Run one HTTP request against a Realm, retrying transient failures."""
    remaining = max_retries

    while True:
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")
        try:
            return await request()
        except Exception as error:
            if remaining <= 0 or not _is_retryable_request_error(error):
                raise
            retry_index = max_retries - remaining
            remaining -= 1
            headers = _headers_of(error)
            delay_ms = realm_request_delay_ms(
                headers if headers is not None else _EMPTY_HEADERS,
                retry_index,
                max_retry_delay_ms,
            )
            await _sleep_ms(delay_ms, signal)


_EMPTY_HEADERS: dict[str, Any] = {}


__all__ = [
    "CAPACITY_RETRY_BUDGET",
    "DEFAULT_MAX_RETRY_DELAY_MS",
    "DEFAULT_RETRY_POLICY",
    "TRANSIENT_RETRY_BUDGET",
    "RetryBudget",
    "RetryCallbacks",
    "RetryPolicy",
    "ServerRetryDelayTooLongError",
    "is_capacity_saturated_realm_response",
    "is_deterministic_realm_failure",
    "is_retryable_realm_response",
    "is_retryable_status",
    "realm_request_delay_ms",
    "retry_budget_for",
    "retry_invocation",
    "retry_realm_request",
    "transient_retry_budget",
]
