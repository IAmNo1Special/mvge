from __future__ import annotations

from mvge.core.errors import (
    AuthenticationError,
    MaxTurnsExceededError,
    MvgeError,
    RateLimitError,
    SpellNotFoundError,
    SpellTimeoutError,
    TomeResumeError,
    to_error,
)


def test_mvge_error_basic() -> None:
    err = MvgeError("test_code", "test message")
    assert err.code == "test_code"
    assert str(err) == "test message"


def test_mvge_error_with_cause() -> None:
    cause = ValueError("original")
    err = MvgeError("test_code", "test message", cause)
    assert err.__cause__ is cause


def test_to_error_with_mvge_error() -> None:
    original = MvgeError("original_code", "original message")
    result = to_error(original)
    assert result is original


def test_to_error_with_exception() -> None:
    original = ValueError("test value error")
    result = to_error(original)
    assert isinstance(result, MvgeError)
    assert result.code == "unknown"
    assert str(result) == "test value error"
    assert result.__cause__ is original


def test_to_error_with_string() -> None:
    result = to_error("test string error")
    assert isinstance(result, MvgeError)
    assert result.code == "unknown"
    assert str(result) == "test string error"


def test_to_error_with_none() -> None:
    result = to_error(None)
    assert isinstance(result, MvgeError)
    assert result.code == "unknown"
    assert str(result) == "Unknown error"


def test_spell_not_found_error() -> None:
    err = SpellNotFoundError("bash")
    assert err.code == "spell_not_found"
    assert "bash" in str(err)


def test_rate_limit_error() -> None:
    err = RateLimitError("rate limited", retry_after=60.0)
    assert err.code == "rate_limited"
    assert err.retry_after == 60.0


def test_rate_limit_error_no_retry() -> None:
    err = RateLimitError("rate limited")
    assert err.code == "rate_limited"
    assert err.retry_after is None


def test_rate_limit_error_diagnostics() -> None:
    err = RateLimitError(
        "Rate limit exceeded: free-models-per-day",
        retry_after=60.0,
        limit_source="openrouter_free_tier_daily",
        remedy_hint="Wait for daily reset",
        reset_at=1788566400.0,
        quota_limit=50,
        quota_remaining=0,
    )
    assert err.code == "rate_limited"
    assert err.retry_after == 60.0
    assert err.limit_source == "openrouter_free_tier_daily"
    assert err.remedy_hint == "Wait for daily reset"
    assert err.reset_at == 1788566400.0
    assert err.quota_limit == 50
    assert err.quota_remaining == 0


def test_authentication_error() -> None:
    err = AuthenticationError("auth failed")
    assert err.code == "auth_failed"
    assert str(err) == "auth failed"


def test_upstream_timeout_error() -> None:
    from mvge.core.errors import UpstreamTimeoutError

    err = UpstreamTimeoutError("Upstream idle timeout exceeded")
    assert err.code == "upstream_idle_timeout"
    assert str(err) == "Upstream idle timeout exceeded"


def test_spell_timeout_error() -> None:
    err = SpellTimeoutError("bash", 5000)
    assert err.code == "spell_timeout"
    assert "bash" in str(err)
    assert "5000" in str(err)
    assert err.timeout_ms == 5000


def test_max_turns_exceeded_error() -> None:
    err = MaxTurnsExceededError(10)
    assert err.code == "max_turns_exceeded"
    assert "10" in str(err)
    assert err.max_turns == 10


def test_tome_resume_error() -> None:
    cause = FileNotFoundError("not found")
    err = TomeResumeError("/path/to/tome", cause)
    assert err.code == "tome_resume_failed"
    assert "/path/to/tome" in str(err)
    assert err.__cause__ is cause


def test_tome_resume_error_no_cause() -> None:
    err = TomeResumeError("/path/to/tome")
    assert err.code == "tome_resume_failed"
    assert err.__cause__ is None


def test_missing_api_key_error_derived_env() -> None:
    from mvge.core.errors import MissingApiKeyError

    err = MissingApiKeyError(realm="openrouter")
    assert err.api_key_env == "OPENROUTER_API_KEY"
    assert "OPENROUTER_API_KEY" in str(err)
    assert "openrouter" in str(err)

    err_empty = MissingApiKeyError(realm="", api_key_env="")
    assert "API key not found." in str(err_empty)


def test_tome_incompatible_error_contemplation() -> None:
    from mvge.core.errors import TomeIncompatibleError

    err = TomeIncompatibleError(
        tome_id="sess-123",
        contemplation_mismatch=("low", "high"),
        missing_spells=["git_read"],
        issues=["custom issue"],
    )
    assert err.code == "tome_incompatible"
    assert "contemplation mismatch (session='low', active='high')" in str(err)
    assert "missing spells: git_read" in str(err)
    assert "custom issue" in str(err)
