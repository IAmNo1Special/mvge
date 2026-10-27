"""Session-wide hermeticity guards for the workspace test suite.

TESTING.md section 2 forbids real secrets and credential-bearing
environment variables in tests. Two mechanisms defeated that:

``Mvge.__init__`` calls ``load_dotenv()`` on the caller's ``.env``, and
``load_dotenv`` writes into the process-wide ``os.environ`` without ever
rolling back. The lookup is caller-frame relative, so a test cannot
reliably steer it away from the repository's own ``.env`` -- which holds
real credentials. Whichever test constructed an agent first therefore
handed the developer's API keys to every test that ran after it.

The guard below restores the credential variables after each test. It is
deliberately scoped to those variables rather than the whole environment:
the auto-loading behaviour is exercised on purpose by
``test_dot_env_auto_loading``, and a blanket rollback would also revert
unrelated writes that background watcher threads may still be reading.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

#: Every variable the engine reads as a credential, sourced from the
#: ``os.environ.get`` calls in ``src/mvge`` rather than guessed.
#:
#: ``OPENCODE_API_KEY`` belongs here and was missing until 2026-10-11. Its
#: absence let one test's key leak into the next: the suite passed locally while
#: failing in CI, because a test that set ``OPENCODE_API_KEY`` left it set and
#: the following test read it. Isolation that is incomplete is worse than none,
#: because it hides exactly the ordering-dependent bugs this fixture is for.
CREDENTIAL_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "MVGE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "STORAGE_SECRET",
)


@pytest.fixture(autouse=True)
def _isolate_credential_env() -> Iterator[None]:
    """Keep one test's loaded credentials out of the next test's environment.

    Requests come before a test's own ``monkeypatch`` so this fixture
    finalises last, which means it has the final word on the restored
    state rather than deferring to whatever ran before it.
    """
    snapshot = {name: os.environ.get(name) for name in CREDENTIAL_ENV_VARS}
    try:
        yield
    finally:
        for name, value in snapshot.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(autouse=True)
def _isolate_global_agents_dir(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Point the global ``.agents`` layer at an empty directory.

    ``Mvge`` loads global runes *in addition to* the ones it is handed, so a
    test that sets up only a temporary rune directory still sees whatever the
    developer happens to have installed in ``~/.agents``. That is not a
    hypothetical: two integration tests assert an exact watcher count, and they
    failed on this machine purely because ``openrouter-realm`` and
    ``skill_evolution`` were installed globally. The suite was green or red
    depending on what the developer had installed, which is the worst possible
    property for a test to have.

    A test that wants the real global layer, or wants to assert the default
    resolution, sets or deletes ``MVGE_GLOBAL_DIR`` itself; this fixture only
    supplies a harmless default and restores whatever was there before.

    The resolvers read the variable on every call rather than caching it at
    import, which is what makes this work without reimporting anything.
    """
    previous = os.environ.get("MVGE_GLOBAL_DIR")
    os.environ["MVGE_GLOBAL_DIR"] = str(tmp_path_factory.mktemp("global-agents"))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("MVGE_GLOBAL_DIR", None)
        else:
            os.environ["MVGE_GLOBAL_DIR"] = previous
