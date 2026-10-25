from __future__ import annotations


class MvgeError(Exception):
    def __init__(self, code: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        if cause is not None:
            self.__cause__ = cause


def to_error(err: Exception | str | None) -> MvgeError:
    if isinstance(err, MvgeError):
        return err
    if isinstance(err, Exception):
        return MvgeError("unknown", str(err), err)
    if isinstance(err, str):
        return MvgeError("unknown", err)
    return MvgeError("unknown", "Unknown error")


class SpellNotFoundError(MvgeError):
    def __init__(self, spell_name: str) -> None:
        super().__init__("spell_not_found", f"Spell not found: {spell_name}")


class SpellDiscoveryError(MvgeError):
    def __init__(self, message: str) -> None:
        super().__init__("spell_discovery_error", message)


class RateLimitError(MvgeError):
    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        limit_source: str | None = None,
        remedy_hint: str | None = None,
        reset_at: float | None = None,
        quota_limit: int | None = None,
        quota_remaining: int | None = None,
    ) -> None:
        super().__init__("rate_limited", message)
        self.retry_after = retry_after
        self.limit_source = limit_source
        self.remedy_hint = remedy_hint
        self.reset_at = reset_at
        self.quota_limit = quota_limit
        self.quota_remaining = quota_remaining


class AuthenticationError(MvgeError):
    def __init__(self, message: str) -> None:
        super().__init__("auth_failed", message)


class UpstreamTimeoutError(MvgeError):
    """Raised when the upstream Realm stalls and closes a stream mid-turn.

    Distinct from RateLimitError: the request was accepted and partial
    content may already have streamed, so the turn cannot be retried
    without duplicating transcript text.
    """

    def __init__(self, message: str) -> None:
        super().__init__("upstream_idle_timeout", message)


class MissingApiKeyError(MvgeError):
    """Raised when the Realm serving this turn has no resolved credential.

    Carries the Realm and its variable rather than baking one into the default
    message. The default this replaces named ``OPENROUTER_API_KEY``, so a path
    whose Realm was ``opencode`` sent the Summoner to set a credential that
    Realm never reads -- the wrong variable is worse than none, because setting
    it looks like the fix and the next run fails the same way.

    Names the variable as well as the Realm, matching the CLI's own missing-key
    message. An embedder that raises this through the library should not hand a
    Summoner less than the command line does.

    Every argument is optional so a bare ``raise MissingApiKeyError`` still
    builds. A bare one cannot know the Realm, so it asks for a credential
    without claiming to know whose it is.
    """

    def __init__(
        self,
        message: str = "",
        realm: str = "",
        api_key_env: str = "",
    ) -> None:
        self.realm = realm
        # A caller holding the Realm's table entry passes the variable that
        # entry names. One that does not gets the `<REALM>_API_KEY` shape, which
        # is the fallback the Realm tables derive for an unmapped Realm.
        # Duplicated rather than imported because the tables live in a leaf
        # package and core may not reach down into one -- see the
        # dependency_direction guard.
        if not api_key_env and realm:
            api_key_env = f"{realm.upper()}_API_KEY"
        self.api_key_env = api_key_env
        super().__init__(
            "missing_api_key",
            message or _missing_api_key_message(realm, api_key_env),
        )


def _missing_api_key_message(realm: str, api_key_env: str) -> str:
    if not realm:
        subject = "API key not found."
    else:
        subject = f"API key not found for Realm '{realm}'."
    if not api_key_env:
        return f"{subject} Set it in your environment or in a co-located .env file."
    return (
        f"{subject} Set {api_key_env} in your environment or in a co-located .env file."
    )


class SpellTimeoutError(MvgeError):
    def __init__(self, spell_name: str, timeout_ms: int) -> None:
        super().__init__(
            "spell_timeout",
            f"Spell '{spell_name}' timed out after {timeout_ms}ms",
        )
        self.timeout_ms = timeout_ms


class MaxTurnsExceededError(MvgeError):
    def __init__(self, max_turns: int) -> None:
        super().__init__("max_turns_exceeded", f"Max turns exceeded: {max_turns}")
        self.max_turns = max_turns


class TomeResumeError(MvgeError):
    def __init__(self, path: str, cause: Exception | None = None) -> None:
        super().__init__(
            "tome_resume_failed",
            f"Failed to resume tome: {path}",
            cause,
        )


class TomeIncompatibleError(MvgeError):
    """Raised when resuming a tome session with incompatible configuration."""

    def __init__(
        self,
        tome_id: str,
        message: str | None = None,
        *,
        issues: list[str] | None = None,
        model_mismatch: tuple[str | None, str | None] | None = None,
        missing_spells: list[str] | None = None,
        contemplation_mismatch: tuple[str | None, str | None] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.tome_id = tome_id
        self.issues = issues or []
        self.model_mismatch = model_mismatch
        self.missing_spells = missing_spells or []
        self.contemplation_mismatch = contemplation_mismatch

        if message is None:
            details: list[str] = []
            if model_mismatch:
                m0, m1 = model_mismatch
                details.append(f"model mismatch (session='{m0}', active='{m1}')")
            if missing_spells:
                details.append(f"missing spells: {', '.join(missing_spells)}")
            if contemplation_mismatch:
                c0, c1 = contemplation_mismatch
                details.append(
                    f"contemplation mismatch (session='{c0}', active='{c1}')"
                )
            if self.issues:
                details.extend(self.issues)
            detail_str = f": {'; '.join(details)}" if details else ""
            message = (
                f"Session '{tome_id}' is incompatible with active "
                f"configuration{detail_str}"
            )

        super().__init__("tome_incompatible", message, cause)
