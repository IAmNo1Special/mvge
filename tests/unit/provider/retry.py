from __future__ import annotations

import time
from typing import Any

import pytest

from mvge.core.channel import (
    ChannelConfig,
    Model,
    RealmResponse,
)
from mvge.provider.retry import (
    CAPACITY_RETRY_BUDGET,
    DEFAULT_MAX_RETRY_DELAY_MS,
    DEFAULT_RETRY_POLICY,
    TRANSIENT_RETRY_BUDGET,
    RetryCallbacks,
    RetryPolicy,
    ServerRetryDelayTooLongError,
    is_capacity_saturated_realm_response,
    is_retryable_realm_response,
    is_retryable_status,
    realm_request_delay_ms,
    retry_budget_for,
    retry_invocation,
    retry_realm_request,
    transient_retry_budget,
)


def _model() -> Model:
    return Model(
        id="test-model",
        name="Test",
        realm="test",
        base_url="",
        api_key="",
    )


def _error(message: str, code: str | None = None) -> RealmResponse:
    return RealmResponse(
        model=_model(),
        error_message=message,
        error_code=code,
        stop_reason="error",
    )


def _window(
    message: str,
    seconds_from_now: float,
    code: str | None = None,
) -> RealmResponse:
    """A failure carrying the Realm's reported window reopening time."""
    return RealmResponse(
        model=_model(),
        error_message=message,
        error_code=code,
        stop_reason="error",
        reset_at=time.time() + seconds_from_now,
    )


def _ok() -> RealmResponse:
    return RealmResponse(model=_model(), stop_reason="stop")


class _Headers:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


class TestRetryPolicy:
    def test_defaults(self) -> None:
        assert DEFAULT_RETRY_POLICY.enabled is True
        assert DEFAULT_RETRY_POLICY.max_retries > 0
        assert DEFAULT_RETRY_POLICY.base_delay_ms > 0

    def test_disabled_policy_is_expressible(self) -> None:
        policy = RetryPolicy(enabled=False)
        assert policy.enabled is False


class TestClassifier:
    def test_non_error_is_not_retryable(self) -> None:
        assert is_retryable_realm_response(_ok()) is False

    def test_error_code_rate_limited_is_retryable(self) -> None:
        assert is_retryable_realm_response(_error("slow down", "rate_limited")) is True

    def test_error_code_auth_failed_is_not_retryable(self) -> None:
        assert is_retryable_realm_response(_error("nope", "auth_failed")) is False

    @pytest.mark.parametrize(
        "message",
        [
            "Provider returned error",
            "service unavailable",
            "connection refused",
            "socket hang up",
            "request timed out",
            "overloaded",
            "too many requests",
            "ResourceExhausted",
        ],
    )
    def test_transient_prose_is_retryable(self, message: str) -> None:
        assert is_retryable_realm_response(_error(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "insufficient_quota",
            "quota exceeded",
            "out of budget",
            "billing problem",
            "Monthly usage limit reached",
        ],
    )
    def test_quota_errors_are_not_retryable(self, message: str) -> None:
        assert is_retryable_realm_response(_error(message)) is False

    def test_quota_wins_over_transient_wording(self) -> None:
        # "429" reads as transient, but a quota limit is deterministic.
        assert is_retryable_realm_response(_error("429 quota exceeded")) is False

    def test_unknown_error_is_not_retryable(self) -> None:
        assert is_retryable_realm_response(_error("banana")) is False

    def test_empty_error_message_is_not_retryable(self) -> None:
        assert is_retryable_realm_response(_error("")) is False


class TestCapacityClassifier:
    @pytest.mark.parametrize(
        "message",
        [
            "Upstream error from Nvidia: Service temporarily overloaded",
            "Model is overloaded",
            "Service Unavailable",
            "No capacity available for this model",
            "At capacity",
            "capacity exceeded",
            "insufficient capacity",
            "no available capacity",
            "server is busy",
            "too many concurrent requests",
        ],
    )
    def test_capacity_prose_is_capacity(self, message: str) -> None:
        assert is_capacity_saturated_realm_response(_error(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "connection refused",
            "socket hang up",
            "network error",
            "request timed out",
            "Provider returned error",
            "Internal error",
        ],
    )
    def test_transient_blips_are_not_capacity(self, message: str) -> None:
        # A millisecond-to-second failure must not buy a minutes-scale budget.
        assert is_retryable_realm_response(_error(message)) is True
        assert is_capacity_saturated_realm_response(_error(message)) is False

    def test_rate_limit_is_not_capacity(self) -> None:
        # The Realm states when to return via Retry-After, which the delay
        # function already honours exactly; capacity is the un-timed signal.
        assert is_capacity_saturated_realm_response(_error("429 slow down")) is False

    @pytest.mark.parametrize(
        "message",
        [
            "insufficient_quota",
            "quota exceeded",
            "billing problem",
            "Monthly usage limit reached",
            "429 quota exceeded",
            "overloaded: insufficient_quota",
        ],
    )
    def test_billing_and_quota_win_over_capacity(self, message: str) -> None:
        assert is_retryable_realm_response(_error(message)) is False
        assert is_capacity_saturated_realm_response(_error(message)) is False

    def test_explicit_capacity_code_beats_prose(self) -> None:
        response = _error("upstream said something unremarkable", "overloaded")
        assert is_capacity_saturated_realm_response(response) is True

    def test_auth_failed_code_wins_over_capacity_prose(self) -> None:
        response = _error("overloaded", "auth_failed")
        assert is_capacity_saturated_realm_response(response) is False

    def test_success_is_not_capacity(self) -> None:
        assert is_capacity_saturated_realm_response(_ok()) is False


class TestSpentAllowanceIsNotRetried:
    """A drained daily allowance is deterministic for hours, not milliseconds.

    These are the exact strings the engine emits, not paraphrases. The first is
    OpenRouter's own wording on a 429 and carries no transient signal at all --
    the retry came from the status code alone.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock "
            "1000 free model requests per day",
            "Daily free-model quota exhausted (50/50 requests). Resets at 00:00 UTC.",
            "quota exhausted",
        ],
    )
    def test_real_quota_exhaustion_message_is_not_retryable(self, message: str) -> None:
        assert is_retryable_realm_response(_error(message)) is False
        assert is_capacity_saturated_realm_response(_error(message)) is False

    def test_free_models_per_day_wins_over_rate_limit_wording(self) -> None:
        # "Rate limit exceeded" reads as transient, but this is a daily
        # allowance that will not change for hours.
        response = _error("Rate limit exceeded: free-models-per-day.", "rate_limited")
        assert is_retryable_realm_response(response) is False

    def test_quota_exhaustion_is_not_promoted_to_capacity(self) -> None:
        # Otherwise a spent allowance would inherit the 90-second budget, which
        # is the exact failure this change exists to remove.
        assert is_capacity_saturated_realm_response(_error("quota exhausted")) is False

    def test_window_beyond_any_budget_is_not_retryable(self) -> None:
        # The measured live case: a drained window reopening in 14 hours, with
        # wording that otherwise looks like an upstream error.
        response = _window("Provider returned error", 14 * 3600, "rate_limited")
        assert is_retryable_realm_response(response) is False
        assert is_capacity_saturated_realm_response(response) is False

    def test_window_beyond_any_budget_beats_the_retryable_code(self) -> None:
        # A Realm may assert `rate_limited` and still report a window we can
        # never sit out; the structured window is the more specific claim.
        response = _window("Rate limit exceeded", 30 * 24 * 3600, "rate_limited")
        assert is_retryable_realm_response(response) is False

    @pytest.mark.parametrize("seconds", [5, 30, 60])
    def test_window_inside_the_budget_stays_retryable(self, seconds: int) -> None:
        # A drained per-minute window is measured in seconds, and waiting it
        # out is precisely what retrying is for.
        response = _window("Rate limit exceeded", seconds, "rate_limited")
        assert is_retryable_realm_response(response) is True

    def test_absent_window_leaves_the_prose_decision_untouched(self) -> None:
        response = _error("overloaded", "rate_limited")
        assert is_retryable_realm_response(response) is True
        assert is_capacity_saturated_realm_response(response) is True

    def test_window_beats_capacity_wording(self) -> None:
        # An "overloaded" message with a 14-hour window is a spent allowance
        # wearing upstream's wording, not a capacity blip.
        response = _window("Service temporarily overloaded", 14 * 3600)
        assert is_retryable_realm_response(response) is False
        assert is_capacity_saturated_realm_response(response) is False


class TestRetryBudget:
    def test_capacity_budget_is_materially_longer_than_transient(self) -> None:
        capacity = retry_budget_for(_error("overloaded"), 3)
        transient = retry_budget_for(_error("socket hang up"), 3)
        assert capacity.max_attempts >= 2 * transient.max_attempts
        assert capacity.max_total_wait_ms >= 3 * transient.max_total_wait_ms
        assert capacity.base_delay_ms > transient.base_delay_ms

    def test_transient_budget_is_unchanged_by_capacity_work(self) -> None:
        budget = retry_budget_for(_error("socket hang up"), 3)
        assert budget.max_attempts == 3
        assert budget.base_delay_ms == TRANSIENT_RETRY_BUDGET.base_delay_ms
        assert budget.max_backoff_ms == TRANSIENT_RETRY_BUDGET.max_backoff_ms

    def test_capacity_attempt_count_is_a_floor_not_a_ceiling(self) -> None:
        # A caller asking for three retries means three retries unless the
        # endpoint is saturated; it must not be able to lower the floor.
        assert retry_budget_for(_error("overloaded"), 3).max_attempts == (
            CAPACITY_RETRY_BUDGET.max_attempts
        )
        assert retry_budget_for(_error("overloaded"), 1).max_attempts == (
            CAPACITY_RETRY_BUDGET.max_attempts
        )
        assert retry_budget_for(_error("overloaded"), 40).max_attempts == 40

    def test_transient_attempt_count_still_follows_the_caller(self) -> None:
        assert transient_retry_budget(1).max_attempts == 1
        assert transient_retry_budget(7).max_attempts == 7
        assert transient_retry_budget(0).max_attempts == 1

    def test_capacity_ceiling_is_bounded(self) -> None:
        # Long enough to outlast a saturation window, short enough that a first
        # run still finishes inside the five-minute onboarding promise.
        assert 60_000 <= CAPACITY_RETRY_BUDGET.max_total_wait_ms <= 180_000

    def test_no_capacity_delay_reaches_the_server_delay_cap(self) -> None:
        # realm_request_delay_ms raises rather than stall past this cap, so a
        # capacity budget that used delays above it could never be honoured.
        assert CAPACITY_RETRY_BUDGET.max_backoff_ms < DEFAULT_MAX_RETRY_DELAY_MS

    def test_capacity_schedule_fits_inside_its_own_ceiling(self) -> None:
        waits = [
            min(
                CAPACITY_RETRY_BUDGET.base_delay_ms * (2**index),
                CAPACITY_RETRY_BUDGET.max_backoff_ms,
            )
            for index in range(CAPACITY_RETRY_BUDGET.max_attempts - 1)
        ]
        # Jitter only shortens a wait, so the unjittered schedule is the bound.
        assert sum(waits) <= CAPACITY_RETRY_BUDGET.max_total_wait_ms
        assert len(waits) >= 5  # several windows' worth of spacing, not three tries

    def test_transient_ceiling_does_not_bind_at_the_default(self) -> None:
        # Three transient attempts wait 500 ms then 1000 ms; the ceiling is
        # headroom for callers who raise max_retries, not a behaviour change.
        assert 2 * TRANSIENT_RETRY_BUDGET.base_delay_ms < (
            TRANSIENT_RETRY_BUDGET.max_total_wait_ms
        )

    def test_transient_attempt_count_matches_the_channel_default(self) -> None:
        # These two defaults are independent today; a divergence would mean the
        # transient budget silently overrides a caller's configured retries.
        assert ChannelConfig(model=_model()).max_retries == (
            TRANSIENT_RETRY_BUDGET.max_attempts
        )


class TestRetryInvocation:
    @pytest.mark.asyncio
    async def test_returns_first_success_without_retrying(self) -> None:
        calls = 0

        async def produce() -> RealmResponse:
            nonlocal calls
            calls += 1
            return _ok()

        result = await retry_invocation(produce, DEFAULT_RETRY_POLICY)

        assert calls == 1
        assert result.stop_reason == "stop"

    @pytest.mark.asyncio
    async def test_retries_transient_error_then_succeeds(self) -> None:
        calls = 0

        async def produce() -> RealmResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _error("overloaded")
            return _ok()

        policy = RetryPolicy(max_retries=3, base_delay_ms=0)
        result = await retry_invocation(produce, policy)

        assert calls == 2
        assert result.stop_reason == "stop"

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_error(self) -> None:
        calls = 0

        async def produce() -> RealmResponse:
            nonlocal calls
            calls += 1
            return _error("insufficient_quota")

        policy = RetryPolicy(max_retries=5, base_delay_ms=0)
        result = await retry_invocation(produce, policy)

        assert calls == 1
        assert result.error_message == "insufficient_quota"

    @pytest.mark.asyncio
    async def test_stops_after_max_retries(self) -> None:
        calls = 0

        async def produce() -> RealmResponse:
            nonlocal calls
            calls += 1
            return _error("overloaded")

        policy = RetryPolicy(max_retries=2, base_delay_ms=0)
        result = await retry_invocation(produce, policy)

        assert calls == 3  # initial call plus two retries
        assert result.error_message == "overloaded"

    @pytest.mark.asyncio
    async def test_disabled_policy_never_retries(self) -> None:
        calls = 0

        async def produce() -> RealmResponse:
            nonlocal calls
            calls += 1
            return _error("overloaded")

        await retry_invocation(produce, RetryPolicy(enabled=False))

        assert calls == 1

    @pytest.mark.asyncio
    async def test_emits_callbacks_around_retry(self) -> None:
        scheduled: list[tuple[int, int, float, str]] = []
        started = 0
        finished: list[tuple[bool, int, str | None]] = []

        async def on_scheduled(
            attempt: int, max_attempts: int, delay_ms: float, message: str
        ) -> None:
            scheduled.append((attempt, max_attempts, delay_ms, message))

        async def on_start() -> None:
            nonlocal started
            started += 1

        async def on_finished(
            success: bool, attempt: int, final_error: str | None
        ) -> None:
            finished.append((success, attempt, final_error))

        calls = 0

        async def produce() -> RealmResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _error("overloaded")
            return _ok()

        await retry_invocation(
            produce,
            RetryPolicy(max_retries=2, base_delay_ms=0),
            callbacks=RetryCallbacks(
                on_retry_scheduled=on_scheduled,
                on_retry_attempt_start=on_start,
                on_retry_finished=on_finished,
            ),
        )

        assert len(scheduled) == 1
        assert scheduled[0][0] == 1
        assert started == 1
        assert finished == [(True, 1, None)]

    @pytest.mark.asyncio
    async def test_no_callbacks_when_first_call_succeeds(self) -> None:
        finished: list[Any] = []

        async def on_finished(
            success: bool, attempt: int, final_error: str | None
        ) -> None:
            finished.append(success)

        async def produce() -> RealmResponse:
            return _ok()

        await retry_invocation(
            produce,
            DEFAULT_RETRY_POLICY,
            callbacks=RetryCallbacks(on_retry_finished=on_finished),
        )

        assert finished == []

    @pytest.mark.asyncio
    async def test_backoff_doubles_per_attempt(self) -> None:
        delays: list[float] = []

        async def on_scheduled(
            attempt: int, max_attempts: int, delay_ms: float, message: str
        ) -> None:
            delays.append(delay_ms)

        async def produce() -> RealmResponse:
            return _error("overloaded")

        await retry_invocation(
            produce,
            RetryPolicy(max_retries=3, base_delay_ms=100),
            callbacks=RetryCallbacks(on_retry_scheduled=on_scheduled),
        )

        assert delays == [100, 200, 400]


class TestIsRetryableStatus:
    @pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 524])
    def test_transient_statuses_retry(self, status: int) -> None:
        assert is_retryable_status(status, _Headers()) is True

    @pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 422])
    def test_client_errors_do_not_retry(self, status: int) -> None:
        assert is_retryable_status(status, _Headers()) is False

    def test_x_should_retry_true_overrides_status(self) -> None:
        headers = _Headers({"x-should-retry": "true"})
        assert is_retryable_status(400, headers) is True

    def test_x_should_retry_false_overrides_status(self) -> None:
        headers = _Headers({"x-should-retry": "false"})
        assert is_retryable_status(503, headers) is False


class TestRealmRequestDelay:
    def test_honours_retry_after_ms_header(self) -> None:
        headers = _Headers({"retry-after-ms": "2500"})
        assert realm_request_delay_ms(headers, 0) == 2500

    def test_honours_retry_after_seconds_header(self) -> None:
        headers = _Headers({"retry-after": "3"})
        assert realm_request_delay_ms(headers, 0) == 3000

    def test_rejects_server_delay_beyond_cap(self) -> None:
        headers = _Headers({"retry-after": "120"})
        with pytest.raises(ServerRetryDelayTooLongError):
            realm_request_delay_ms(headers, 0)

    def test_allows_long_delay_when_cap_disabled(self) -> None:
        headers = _Headers({"retry-after": "120"})
        assert realm_request_delay_ms(headers, 0, max_retry_delay_ms=0) == 120_000

    def test_exponential_backoff_is_capped_at_eight_seconds(self) -> None:
        headers = _Headers()
        for index in range(10):
            assert realm_request_delay_ms(headers, index) <= 8000

    def test_exponential_backoff_applies_jitter(self) -> None:
        headers = _Headers()
        # 0.5 * 2^3 = 4s, jitter keeps it within (0.75 * 4000, 4000]
        delays = {realm_request_delay_ms(headers, 3) for _ in range(50)}
        assert all(3000 <= delay <= 4000 for delay in delays)
        assert len(delays) > 1  # jitter actually varies

    def test_default_cap_is_sixty_seconds(self) -> None:
        assert DEFAULT_MAX_RETRY_DELAY_MS == 60_000

    def test_capacity_schedule_still_applies_jitter(self) -> None:
        headers = _Headers()
        # Parallel callers must not retry in lockstep, and that matters most on
        # the long capacity waits, so the fix must not spend jitter to tighten
        # spacing. Index 2 is pinned at the 15 s ceiling before jitter.
        delays = {
            realm_request_delay_ms(
                headers,
                2,
                base_delay_ms=CAPACITY_RETRY_BUDGET.base_delay_ms,
                max_backoff_ms=CAPACITY_RETRY_BUDGET.max_backoff_ms,
            )
            for _ in range(50)
        }
        assert all(11_250 <= delay <= 15_000 for delay in delays)
        assert len(delays) > 1  # jitter actually varies

    def test_capacity_schedule_spins_longer_than_the_transient_one(self) -> None:
        headers = _Headers()
        transient = realm_request_delay_ms(headers, 2)
        capacity = realm_request_delay_ms(
            headers,
            2,
            base_delay_ms=CAPACITY_RETRY_BUDGET.base_delay_ms,
            max_backoff_ms=CAPACITY_RETRY_BUDGET.max_backoff_ms,
        )
        assert capacity >= 3 * transient

    def test_server_delay_cap_still_applies_to_a_capacity_schedule(self) -> None:
        # The ceiling protects the run; a capacity budget must not widen it.
        headers = _Headers({"retry-after": "90"})
        with pytest.raises(ServerRetryDelayTooLongError):
            realm_request_delay_ms(
                headers,
                0,
                base_delay_ms=CAPACITY_RETRY_BUDGET.base_delay_ms,
                max_backoff_ms=CAPACITY_RETRY_BUDGET.max_backoff_ms,
            )


class TestRetryRealmRequest:
    @pytest.mark.asyncio
    async def test_returns_first_success(self) -> None:
        calls = 0

        async def request() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        assert await retry_realm_request(request, max_retries=3) == "ok"
        assert calls == 1

    @pytest.mark.asyncio
    async def test_retries_retryable_status(self) -> None:
        calls = 0

        async def request() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _status_error(503)
            return "ok"

        assert await retry_realm_request(request, max_retries=2) == "ok"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_status(self) -> None:
        calls = 0

        async def request() -> str:
            nonlocal calls
            calls += 1
            raise _status_error(400)

        with pytest.raises(_StatusError):
            await retry_realm_request(request, max_retries=3)
        assert calls == 1

    @pytest.mark.asyncio
    async def test_x_should_retry_header_forces_retry(self) -> None:
        calls = 0

        async def request() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _status_error(400, {"x-should-retry": "true"})
            return "ok"

        assert await retry_realm_request(request, max_retries=2) == "ok"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_x_should_retry_false_blocks_retry(self) -> None:
        calls = 0

        async def request() -> str:
            nonlocal calls
            calls += 1
            raise _status_error(503, {"x-should-retry": "false"})

        with pytest.raises(_StatusError):
            await retry_realm_request(request, max_retries=3)
        assert calls == 1

    @pytest.mark.asyncio
    async def test_raises_after_exhausting_retries(self) -> None:
        calls = 0

        async def request() -> str:
            nonlocal calls
            calls += 1
            raise _status_error(500)

        with pytest.raises(_StatusError):
            await retry_realm_request(request, max_retries=2)
        assert calls == 3

    @pytest.mark.asyncio
    async def test_retry_invocation_aborted_before_start(self) -> None:
        from mvge.core.abort import (
            AbortController,
            AbortError,
        )

        controller = AbortController()
        controller.abort()

        async def produce() -> RealmResponse:
            return _error("transient")

        with pytest.raises(AbortError):
            await retry_invocation(produce, signal=controller.signal)

    @pytest.mark.asyncio
    async def test_retry_invocation_aborted_during_sleep(self) -> None:
        import asyncio

        from mvge.core.abort import (
            AbortController,
            AbortError,
        )

        controller = AbortController()

        async def produce() -> RealmResponse:
            return _error("overloaded")

        policy = RetryPolicy(max_retries=3, base_delay_ms=2000)

        async def abort_soon() -> None:
            await asyncio.sleep(0.05)
            controller.abort()

        asyncio.create_task(abort_soon())

        with pytest.raises(AbortError):
            await retry_invocation(produce, policy=policy, signal=controller.signal)

    def test_parse_delay_invalid_returns_none(self) -> None:
        from mvge.provider.retry import _parse_delay

        assert _parse_delay("not-a-number") is None
        assert _parse_delay(None) is None
        assert _parse_delay(" 12.5 ") == 12.5


class _StatusError(Exception):
    def __init__(self, status: int, headers: _Headers) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.headers = headers


def _status_error(status: int, headers: dict[str, str] | None = None) -> _StatusError:
    return _StatusError(status, _Headers(headers))
