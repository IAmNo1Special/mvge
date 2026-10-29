"""Engine reload: Mvge.reload(), the /reload command, and reload auditing.

Reload is engine machinery: it rebuilds runes, the system prompt, spells,
and the harness mid-session, transactionally. Every executed reload writes
exactly one record to the user-scope audit log
(``$MVGE_GLOBAL_DIR/extensions/audit.jsonl`` when set, else
``~/.agents/extensions/audit.jsonl``).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import mvge.agent.mvge as mvge_module
from mvge.agent import Mvge
from mvge.agent.commands import CommandAction, CommandDispatcher
from mvge.agent.environment import MvgeEnvironment
from mvge.agent.harness.reload import (
    _RETIRED_SHUTDOWN_ATTEMPTS,
    _RETIRED_SHUTDOWN_BACKOFF_SECONDS,
    _restore_provider_registrations,
    _snapshot_provider_registrations,
)
from mvge.agent.protocol import MvgeAgent, ReloadResult
from mvge.agent.rune_lifecycle import RuneLifecycle
from mvge.core.channel import MvgeResponse, RealmResponse, StopReason
from mvge.core.layers import ResolvedLayer, Scope
from mvge.provider.base import Realm
from mvge.provider.realms import realm_for_model_id
from mvge.provider.registry import RealmRegistry

BASE_PERSONA = "# PERSONA_BASE\nYou are the base persona.\n"


def _make_registry(realm: Realm | None = None) -> RealmRegistry:
    """A hermetic realm registry: no network, no global state touched.

    The factory is registered under whatever prefix the default model's Realm
    has. Hardcoding a vendor prefix here tied this helper to one default model,
    so every change of default broke all 31 tests that use it without any of
    them being about the default.
    """
    registry = RealmRegistry()
    stub = realm if realm is not None else MagicMock(spec=Realm)
    registry.register_realm_factory(
        realm_for_model_id("openrouter/free"), lambda **kwargs: stub
    )
    return registry


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the fake user home at tmp so all config stays hermetic.

    POSIX resolves ``~`` from ``HOME``; Windows' ``ntpath.expanduser``
    ignores ``HOME`` and reads ``USERPROFILE`` instead, so both must be
    redirected or the agent resolves the real user profile on Windows.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def _make_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    realm: Realm | None = None,
    **kwargs: Any,
) -> Mvge:
    """Build an agent rooted at a fake HOME so all config stays in tmp."""
    _isolate_home(tmp_path, monkeypatch)
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir(parents=True, exist_ok=True)
    config_dir = _config_dir(tmp_path)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "SYSTEM.md").write_text(BASE_PERSONA, encoding="utf-8")
    return Mvge(
        api_key="test-key",
        name="reload-test",
        model_id=kwargs.pop("model_id", "openrouter/free"),
        tome_dir=tmp_path / "tomes",
        caller_dir=caller_dir,
        runes_paths=[str(tmp_path / "runes")],
        provider_registry=_make_registry(realm),
        **kwargs,
    )


def _config_dir(tmp_path: Path) -> Path:
    """The agent's config directory under whichever global layer is in force.

    Mirrors production resolution rather than restating it: the layer is
    ``$MVGE_GLOBAL_DIR`` when set and ``~/.agents`` otherwise. Hardcoding
    one of the two here silently diverged the moment ``resolve_config_dir``
    started honouring the override, and the tests wrote a SYSTEM.md the
    agent was not watching.
    """
    override = os.environ.get("MVGE_GLOBAL_DIR")
    root = Path(override).expanduser() if override else tmp_path / ".agents"
    return root / "agents" / "reload-test"


def _boom(**kwargs: Any) -> Any:
    raise RuntimeError("disk on fire")


_PROBE_RUNE_PY = """\
from mvge.runes.types import SigilHook, SpellDefinition


class ProbeSpell(SpellDefinition):
    async def execute(self, spell_cast_id, params, signal=None, on_update=None):
        return {"status": "ok"}


def rune_factory(api):
    api.register_spell(ProbeSpell(name="probe_spell", description="Probe spell"))
    api.register_command("probe-cmd", "Probe command")

    async def on_start(data):
        data.base_prompt = data.base_prompt + "\\n[RUNE_MARKER]"
        return data

    api.on(SigilHook.BEFORE_MVGE_START, on_start)
"""

_REHYDRATE_BOOM_RUNE_PY = """\
from mvge.runes.types import SpellDefinition


class ProbeSpell(SpellDefinition):
    async def execute(self, spell_cast_id, params, signal=None, on_update=None):
        return {"status": "ok"}


class _BoomState:
    def rehydrate(self, payload):
        raise RuntimeError("rehydrate exploded")


def rune_factory(api):
    api.register_spell(ProbeSpell(name="probe_spell", description="Probe spell"))
    return _BoomState()
"""

_PROVIDER_RUNE_PY = """\
import os
from pathlib import Path


def rune_factory(api):
    name = Path(os.environ["PROBE_PROVIDER_NAME"]).read_text(encoding="utf-8")
    api.register_provider(name.strip(), {"baseUrl": "https://probe.test/v1"})
"""


def _write_probe_rune(
    tmp_path: Path,
    body: str = _PROBE_RUNE_PY,
    manifest_extra: dict[str, Any] | None = None,
) -> None:
    """Install a real on-disk rune registering a spell, a command, and a
    prompt-mutating hook, so reload tests observe genuine rune state."""
    rune_dir = tmp_path / "runes" / "probe-rune"
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "name": "probe-rune",
        "version": "1.0.0",
        "description": "Probe rune",
        "entry_point": "rune.py",
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (rune_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (rune_dir / "rune.py").write_text(body, encoding="utf-8")


def _write_function_spell(tmp_path: Path, name: str) -> None:
    spells_dir = tmp_path / "caller" / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    (spells_dir / f"{name}.py").write_text(
        f'def {name}() -> str:\n    """A function spell."""\n    return "{name}"\n',
        encoding="utf-8",
    )


class _BlockingRealm(Realm):
    """Stub realm that holds a turn open until released."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(  # type: ignore[override]
        self,
        model: Any,
        invocations: list[Any],
        config: Any,
        signal: Any | None = None,
    ) -> Any:
        self.entered.set()
        await self.release.wait()
        yield RealmResponse(
            model=model,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "done"}],
                stop_reason=StopReason.STOP,
            ),
            mana_used=0,
            stop_reason=StopReason.STOP.value,
        )


@pytest.mark.asyncio
async def test_reload_rebuilds_prompt_from_edited_system_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "PERSONA_BASE" in agent.system_prompt
        (_config_dir(tmp_path) / "SYSTEM.md").write_text(
            "# PERSONA_BETA\nYou are Beta.\n", encoding="utf-8"
        )
        result = await agent.reload()
        assert result.ok
        assert result.prompt_changed
        assert "PERSONA_BETA" in agent.system_prompt
        assert "PERSONA_BASE" not in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_reuses_explicit_environment_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reload re-discovers with the inputs the caller's environment used.

    An environment built with a project_dir (the CLI/GUI pattern) must
    keep its project layer across reloads, not silently drop it.
    """
    project_dir = tmp_path / "proj"
    (project_dir / ".agents").mkdir(parents=True, exist_ok=True)
    (project_dir / ".agents" / "SYSTEM.md").write_text(
        "# PERSONA_PROJ\nProject persona.\n", encoding="utf-8"
    )
    # Isolate HOME before resolving, mirroring _make_agent, so the
    # environment is built against the sandbox rather than the real user.
    _isolate_home(tmp_path, monkeypatch)
    env = MvgeEnvironment.resolve(
        agent_name="reload-test",
        project_dir=project_dir,
        allow_unknown_agent=True,
    )
    agent = _make_agent(tmp_path, monkeypatch, environment=env)
    await agent.initialize()
    try:
        assert "PERSONA_PROJ" in agent.system_prompt
        result = await agent.reload()
        assert result.ok
        assert not result.prompt_changed
        assert "PERSONA_PROJ" in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_twice_produces_byte_identical_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        first = await agent.reload()
        assert first.ok
        prompt_after_first = agent.system_prompt
        second = await agent.reload()
        assert second.ok
        assert not second.prompt_changed
        assert agent.system_prompt == prompt_after_first
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_discovers_new_spell_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    # The caller spells dir does not exist yet at initialization time;
    # reload must still pick it up when it appears.
    spells_dir = tmp_path / "caller" / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    try:
        assert "morning_greeting" not in agent.enabled_spells
        (spells_dir / "morning_greeting.py").write_text(
            "def morning_greeting(name: str) -> str:\n"
            '    """Greet someone in the morning."""\n'
            '    return f"Good morning, {name}"\n',
            encoding="utf-8",
        )
        result = await agent.reload()
        assert result.ok
        assert result.spells_changed
        assert "morning_greeting" in agent.enabled_spells
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_failed_reload_preserves_last_known_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        before_prompt = agent.system_prompt
        before_spells = agent.enabled_spells
        original = mvge_module.resolve_system_prompt
        monkeypatch.setattr(mvge_module, "resolve_system_prompt", _boom)
        result = await agent.reload()
        assert not result.ok
        assert "disk on fire" in result.message
        assert "preserved" in result.message
        assert agent.system_prompt == before_prompt
        assert agent.enabled_spells == before_spells
        assert any("disk on fire" in d.message for d in agent.diagnostics)
        monkeypatch.setattr(mvge_module, "resolve_system_prompt", original)
        recovered = await agent.reload()
        assert recovered.ok
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_failed_reload_preserves_rune_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed reload keeps last-known-good rune state, not just prompt."""
    _write_probe_rune(tmp_path)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe-cmd" in agent.registered_commands
        assert "probe_spell" in agent.enabled_spells
        assert "[RUNE_MARKER]" in agent.system_prompt
        monkeypatch.setattr(mvge_module, "resolve_system_prompt", _boom)
        result = await agent.reload()
        assert not result.ok
        # Registrations, commands, spells, and the hook-derived prompt
        # section all survive the failed rebuild.
        assert "probe-cmd" in agent.registered_commands
        assert "probe_spell" in agent.enabled_spells
        assert "[RUNE_MARKER]" in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_reload_queues_while_turn_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    realm = _BlockingRealm()
    agent = _make_agent(tmp_path, monkeypatch, realm=realm)
    await agent.initialize()
    (_config_dir(tmp_path) / "SYSTEM.md").write_text(
        "# PERSONA_NEW\nYou are New.\n", encoding="utf-8"
    )
    audit_path = tmp_path / "global" / "extensions" / "audit.jsonl"
    try:
        turn = asyncio.create_task(agent.run("hello"))
        await asyncio.wait_for(realm.entered.wait(), timeout=5)
        before_prompt = agent.system_prompt
        result = await agent.reload()
        assert result.ok
        assert result.queued
        assert agent.reload_pending
        # A second request during the same turn coalesces into the one
        # queued execution; nothing is audited until the turn boundary.
        again = await agent.reload()
        assert again.ok
        assert again.queued
        assert not audit_path.exists()
        # The in-flight turn is untouched; the queued reload has not applied.
        assert agent.system_prompt == before_prompt
        realm.release.set()
        await asyncio.wait_for(turn, timeout=10)
        # Turn boundary drained the queue: reload applied exactly once,
        # with exactly one audit record for the coalesced execution.
        assert not agent.reload_pending
        assert "PERSONA_NEW" in agent.system_prompt
        records = _audit_records(tmp_path)
        assert len(records) == 1
        assert records[0]["outcome"] == "ok"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_turn_boundary_reload_failure_keeps_request_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the drained reload raises unexpectedly at the turn boundary, the
    pending flag is preserved: the request is not silently dropped."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    realm = _BlockingRealm()
    agent = _make_agent(tmp_path, monkeypatch, realm=realm)
    await agent.initialize()
    try:

        async def _boom() -> Any:
            raise RuntimeError("reload exploded")

        assert agent._harness is not None
        monkeypatch.setattr(agent._harness, "_reload_builder", _boom)
        turn = asyncio.create_task(agent.run("hello"))
        await asyncio.wait_for(realm.entered.wait(), timeout=5)
        # Queue a reload via the public API while the turn is in flight.
        result = await agent.reload()
        assert result.queued
        assert agent.reload_pending
        realm.release.set()
        await asyncio.wait_for(turn, timeout=10)
        # The reload blew up at the boundary; the request must survive so
        # the next turn boundary retries it instead of losing it.
        assert agent.reload_pending
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_requires_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    result = await agent.reload()
    assert not result.ok
    assert "not initialized" in result.message
    # A rejected reload performs no work and writes no audit record: the
    # log records executed reloads, not rejected calls.
    assert not (tmp_path / "global" / "extensions").exists()


def _audit_records(tmp_path: Path) -> list[dict[str, Any]]:
    lines = (
        (tmp_path / "global" / "extensions" / "audit.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    return [json.loads(line) for line in lines]


@pytest.mark.asyncio
async def test_reload_writes_exactly_one_audit_record_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert (await agent.reload()).ok
        assert (await agent.reload()).ok
        records = _audit_records(tmp_path)
        assert len(records) == 2
        for record in records:
            assert record["rune"] == "engine"
            assert record["op"] == "reload"
            assert record["outcome"] == "ok"
            assert record["code"] == "reload_ok"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_failed_reload_writes_failed_audit_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    original = mvge_module.resolve_system_prompt
    monkeypatch.setattr(mvge_module, "resolve_system_prompt", _boom)
    try:
        result = await agent.reload()
        assert not result.ok
        records = _audit_records(tmp_path)
        assert len(records) == 1
        assert records[0]["rune"] == "engine"
        assert records[0]["op"] == "reload"
        assert records[0]["outcome"] == "failed"
        assert "disk on fire" in records[0]["message"]
    finally:
        monkeypatch.setattr(mvge_module, "resolve_system_prompt", original)
        await agent.close()


@pytest.mark.asyncio
async def test_audit_write_failure_is_surfaced_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        monkeypatch.setattr(
            "mvge.runes.rune_audit.RuneAuditLog.append_event",
            lambda self, record: (_ for _ in ()).throw(OSError("read-only fs")),
        )
        result = await agent.reload()
        # §4.6 (normative): an audit append failure turns the result into
        # audit_failed (ok=False) — never a silent ok — and the message
        # states plainly that the reload IS live but unrecorded.
        assert not result.ok
        assert "audit_failed" in result.message
        assert "IS live" in result.message
        assert any("audit" in d.message.lower() for d in agent.diagnostics)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_audit_write_unexpected_error_is_surfaced_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-OSError audit failure (e.g. a serialization bug) is caught and
    surfaced as an audit failure, not raised out of reload()."""
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        monkeypatch.setattr(
            "mvge.runes.rune_audit.RuneAuditLog.append_event",
            lambda self, record: (_ for _ in ()).throw(
                ValueError("unserializable record")
            ),
        )
        result = await agent.reload()
        assert not result.ok
        assert "audit_failed" in result.message
        assert "IS live" in result.message
        assert any("audit" in d.message.lower() for d in agent.diagnostics)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_diagnoses_broken_spell_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spell file that fails re-import is dropped with a diagnostic."""
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    spells_dir = tmp_path / "caller" / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    try:
        (spells_dir / "broken_spell.py").write_text(
            "def broken_spell(:\n", encoding="utf-8"
        )
        result = await agent.reload()
        assert result.ok
        assert "broken_spell" not in agent.enabled_spells
        assert "spell file(s) skipped" in result.message
        assert any("broken_spell.py" in d.message for d in agent.diagnostics)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_rehydrate_failure_is_tolerated_with_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An isolated rehydrate error does not fail the reload."""
    _write_probe_rune(tmp_path, body=_REHYDRATE_BOOM_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe_spell" in agent.enabled_spells
        result = await agent.reload()
        assert result.ok
        assert "rehydrate error(s)" in result.message
        assert any("rehydrate exploded" in d.message for d in agent.diagnostics)
        # The rest of the rebuild still landed.
        assert "probe_spell" in agent.enabled_spells
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_watcher_handover_failure_is_live_but_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-swap watcher failure: the new state IS live, loudly reported."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    (_config_dir(tmp_path) / "SYSTEM.md").write_text(
        "# PERSONA_NEW\nYou are New.\n", encoding="utf-8"
    )
    try:

        async def _boom_start(self: Any) -> None:
            raise OSError("too many watchers")

        monkeypatch.setattr(RuneLifecycle, "start", _boom_start)
        result = await agent.reload()
        assert not result.ok
        assert "IS live" in result.message
        assert "watcher" in result.message.lower()
        # The swap already happened: the new prompt is live.
        assert "PERSONA_NEW" in agent.system_prompt
        # Exactly one audit record, a structured failure.
        records = _audit_records(tmp_path)
        assert len(records) == 1
        assert records[0]["outcome"] == "failed"
        assert records[0]["code"] == "watcher_sync_failed"
        assert "IS live" in records[0]["message"]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_watches_newly_appeared_spells_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spells dir appearing after init is watched once reload runs."""
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    spells_dir = tmp_path / "caller" / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    (spells_dir / "late_spell.py").write_text(
        'def late_spell() -> str:\n    """A late spell."""\n    return "late"\n',
        encoding="utf-8",
    )
    try:
        result = await agent.reload()
        assert result.ok
        assert "late_spell" in agent.enabled_spells
        # The new dir is now trigger-watched: adding another spell file
        # fires a real watcher-triggered reload.
        (spells_dir / "later_spell.py").write_text(
            'def later_spell() -> str:\n    """A later spell."""\n    return "later"\n',
            encoding="utf-8",
        )
        for _ in range(200):
            if "later_spell" in agent.enabled_spells:
                break
            await asyncio.sleep(0.05)
        assert "later_spell" in agent.enabled_spells
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_reuses_custom_prompt_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reload replays a caller-supplied custom prompt, not the default."""
    _isolate_home(tmp_path, monkeypatch)
    env = MvgeEnvironment.resolve(
        agent_name="reload-test",
        custom_prompt="CUSTOM PERSONA",
        allow_unknown_agent=True,
    )
    agent = _make_agent(tmp_path, monkeypatch, environment=env)
    await agent.initialize()
    try:
        assert "CUSTOM PERSONA" in agent.system_prompt
        result = await agent.reload()
        assert result.ok
        assert not result.prompt_changed
        assert "CUSTOM PERSONA" in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_retired_shutdown_failure_surfaces_in_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retired-watcher shutdown failure is recorded, not swallowed."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        original_shutdown = RuneLifecycle.shutdown
        calls = 0

        async def _flaky_shutdown(self: Any) -> None:
            nonlocal calls
            calls += 1
            if calls <= _RETIRED_SHUTDOWN_ATTEMPTS:
                # Every attempt on the retired lifecycle fails; the new
                # one must still shut down cleanly.
                raise OSError("stuck observer")
            await original_shutdown(self)

        monkeypatch.setattr(RuneLifecycle, "shutdown", _flaky_shutdown)
        result = await agent.reload()
        # The rebuild itself succeeded; only the retired cleanup failed.
        assert result.ok
        records = _audit_records(tmp_path)
        assert len(records) == 1
        assert records[0]["outcome"] == "ok"
        assert "stuck observer" in records[0]["retired_shutdown_error"]
        assert any("stuck observer" in d.message for d in agent.diagnostics)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_retired_shutdown_retries_back_off_between_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retired-shutdown attempts are spaced by a backoff delay instead of
    firing back-to-back, so a transient teardown race gets a beat to settle."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    original_shutdown = RuneLifecycle.shutdown
    try:

        async def _always_fails(self: Any) -> None:
            raise OSError("stuck observer")

        monkeypatch.setattr(RuneLifecycle, "shutdown", _always_fails)
        start = time.monotonic()
        result = await agent.reload()
        elapsed = time.monotonic() - start
        assert result.ok
        # Two gaps between three attempts.
        assert elapsed >= 2 * _RETIRED_SHUTDOWN_BACKOFF_SECONDS * 0.9
    finally:
        monkeypatch.setattr(RuneLifecycle, "shutdown", original_shutdown)
        await agent.close()


@pytest.mark.asyncio
async def test_retired_shutdown_transient_failure_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient retired-watcher shutdown failure is retried, not recorded."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        original_shutdown = RuneLifecycle.shutdown
        calls = 0

        async def _flaky_shutdown(self: Any) -> None:
            nonlocal calls
            calls += 1
            if calls <= 2:
                # The retired lifecycle's first attempts fail; a retry
                # recovers before the attempts run out.
                raise OSError("stuck observer")
            await original_shutdown(self)

        monkeypatch.setattr(RuneLifecycle, "shutdown", _flaky_shutdown)
        result = await agent.reload()
        assert result.ok
        assert calls == 3
        records = _audit_records(tmp_path)
        assert len(records) == 1
        assert records[0]["outcome"] == "ok"
        assert records[0].get("retired_shutdown_error") is None
        assert not any("stuck observer" in d.message for d in agent.diagnostics)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_gateway_view_narrows_candidate_spells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spell_gateway rune narrows the rebuilt spell view on reload."""
    _write_probe_rune(tmp_path, manifest_extra={"spell_gateway": True})
    _write_function_spell(tmp_path, "extra_spell")
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert agent.enabled_spells == ["probe_spell"]
        result = await agent.reload()
        assert result.ok
        # The candidate's spell set and prompt were assembled under the
        # gateway view: the function spell stays hidden in both.
        assert agent.enabled_spells == ["probe_spell"]
        assert "  - probe_spell" in agent.system_prompt
        assert "  - extra_spell" not in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_transfers_explicit_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly configured allowlist survives reload on the new runner."""
    _write_probe_rune(tmp_path)
    _write_function_spell(tmp_path, "extra_spell")
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert agent.runner is not None
        agent.runner.set_global_spell_allowlist(["extra_spell"])
        assert agent.enabled_spells == ["extra_spell"]
        result = await agent.reload()
        assert result.ok
        # The explicit policy transferred to the candidate runner: the
        # view is unchanged and gateway mode did not engage over it.
        assert agent.runner is not None
        assert agent.runner.get_global_spell_allowlist() == ["extra_spell"]
        assert agent.enabled_spells == ["extra_spell"]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_second_reload_keeps_rune_spell_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An allowlist naming a rune spell survives a second reload.

    The first reload disposes the retired runner, which strips its own
    spells from its own allowlist. The second reload's candidate build
    must therefore read the live allowlist from the live runner, not
    the retired one, or the entry silently vanishes on reload two.
    """
    _write_probe_rune(tmp_path)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert agent.runner is not None
        agent.runner.set_global_spell_allowlist(["probe_spell"])
        first = await agent.reload()
        assert first.ok
        assert agent.runner is not None
        assert agent.runner.get_global_spell_allowlist() == ["probe_spell"]
        second = await agent.reload()
        assert second.ok
        assert agent.runner is not None
        assert agent.runner.get_global_spell_allowlist() == ["probe_spell"]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_broken_rune_manifest_preserves_last_known_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previously working rune with a newly broken manifest fails reload."""
    _write_probe_rune(tmp_path)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe-cmd" in agent.registered_commands
        assert "probe_spell" in agent.enabled_spells
        assert "[RUNE_MARKER]" in agent.system_prompt
        # Break the manifest after it loaded fine.
        (tmp_path / "runes" / "probe-rune" / "manifest.json").write_text(
            "{not json", encoding="utf-8"
        )
        result = await agent.reload()
        assert not result.ok
        assert "probe-rune" in result.message
        # Last-known-good rune state preserved: the broken candidate
        # never swapped.
        assert "probe-cmd" in agent.registered_commands
        assert "probe_spell" in agent.enabled_spells
        assert "[RUNE_MARKER]" in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_removed_rune_unloads_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a rune's directory is a legitimate removal, not a failure."""
    _write_probe_rune(tmp_path)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe-cmd" in agent.registered_commands
        shutil.rmtree(tmp_path / "runes" / "probe-rune")
        result = await agent.reload()
        assert result.ok
        assert "probe-cmd" not in agent.registered_commands
        assert "probe_spell" not in agent.enabled_spells
        assert "[RUNE_MARKER]" not in agent.system_prompt
    finally:
        await agent.close()


_DISPOSE_RUNE_PY = """\
import os
from pathlib import Path

from mvge.runes.types import SpellDefinition


class ProbeSpell(SpellDefinition):
    async def execute(self, spell_cast_id, params, signal=None, on_update=None):
        return {"status": "ok"}


def rune_factory(api):
    api.register_spell(ProbeSpell(name="probe_spell", description="Probe spell"))

    def _dispose():
        Path(os.environ["RELOAD_PROOF_DISPOSE_PATH"]).open(
            "a", encoding="utf-8"
        ).write("disposed\\n")

    api.on_dispose(_dispose)
"""


@pytest.mark.asyncio
async def test_reload_disposes_retired_rune_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A surviving rune's old instance is disposed when reload swaps it.

    Reload replaces the live runner with a candidate runner; discarding
    the old runner must not silently drop its on_dispose callbacks, or
    a rune's cleanup (background work, connections, temp state) leaks
    on every reload while unload_rune disposes it correctly.
    """
    dispose_path = tmp_path / "disposed.txt"
    monkeypatch.setenv("RELOAD_PROOF_DISPOSE_PATH", str(dispose_path))
    _write_probe_rune(tmp_path, body=_DISPOSE_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe_spell" in agent.enabled_spells
        assert not dispose_path.exists()
        result = await agent.reload()
        assert result.ok
        assert dispose_path.read_text(encoding="utf-8") == "disposed\n"
        # The new instance is live: its spell survives the swap.
        assert "probe_spell" in agent.enabled_spells
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_failed_reload_does_not_dispose_live_rune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed reload preserves last-known-good rune state, undisposed."""
    dispose_path = tmp_path / "disposed.txt"
    monkeypatch.setenv("RELOAD_PROOF_DISPOSE_PATH", str(dispose_path))
    _write_probe_rune(tmp_path, body=_DISPOSE_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe_spell" in agent.enabled_spells
        (tmp_path / "runes" / "probe-rune" / "manifest.json").write_text(
            "{not json", encoding="utf-8"
        )
        result = await agent.reload()
        assert not result.ok
        assert not dispose_path.exists()
        assert "probe_spell" in agent.enabled_spells
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_disposes_removed_rune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rune deleted from disk is disposed, not just unregistered."""
    dispose_path = tmp_path / "disposed.txt"
    monkeypatch.setenv("RELOAD_PROOF_DISPOSE_PATH", str(dispose_path))
    _write_probe_rune(tmp_path, body=_DISPOSE_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe_spell" in agent.enabled_spells
        shutil.rmtree(tmp_path / "runes" / "probe-rune")
        result = await agent.reload()
        assert result.ok
        assert dispose_path.read_text(encoding="utf-8") == "disposed\n"
        assert "probe_spell" not in agent.enabled_spells
    finally:
        await agent.close()


_GEN_CMD_RUNE_PY = """\
import os
from pathlib import Path


def rune_factory(api):
    counter = Path(os.environ["RELOAD_PROOF_GEN_PATH"])
    try:
        n = int(counter.read_text(encoding="utf-8").strip()) + 1
    except Exception:
        n = 1
    counter.write_text(str(n), encoding="utf-8")

    def _handler(args=""):
        return f"gen{n}"

    api.register_command("gen-cmd", "Generation command", handler=_handler)
"""


@pytest.mark.asyncio
async def test_reload_serves_new_rune_commands_not_stale_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After reload, rune commands come from the candidate runner.

    The dispatcher resolves commands through get_registered_commands,
    so a runner reference that still points at the retired runner
    either serves the disposed old handler or, once retired runes are
    disposed, serves nothing at all. Either way the REPL loses the
    command it just reloaded.
    """
    gen_path = tmp_path / "gen.txt"
    monkeypatch.setenv("RELOAD_PROOF_GEN_PATH", str(gen_path))
    _write_probe_rune(tmp_path, body=_GEN_CMD_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        before = {c.name: c for c in agent.get_registered_commands()}
        assert before["gen-cmd"].handler("") == "gen1"
        result = await agent.reload()
        assert result.ok
        after = {c.name: c for c in agent.get_registered_commands()}
        assert after["gen-cmd"].handler("") == "gen2"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_reuses_explicit_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reload replays an explicitly supplied config dir, not ~/.agents."""
    _isolate_home(tmp_path, monkeypatch)
    cfg = tmp_path / "custom-cfg"
    cfg.mkdir()
    (cfg / "SYSTEM.md").write_text("# CFG PERSONA\nYou are Cfg.\n", encoding="utf-8")
    env = MvgeEnvironment.resolve(
        agent_name="reload-test",
        config_dir=cfg,
        allow_unknown_agent=True,
    )
    agent = _make_agent(tmp_path, monkeypatch, environment=env)
    await agent.initialize()
    try:
        assert "CFG PERSONA" in agent.system_prompt
        result = await agent.reload()
        assert result.ok
        assert not result.prompt_changed
        assert "CFG PERSONA" in agent.system_prompt
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_reuses_explicit_global_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reload replays an explicitly supplied global dir for appends."""
    _isolate_home(tmp_path, monkeypatch)
    gdir = tmp_path / "custom-global"
    gdir.mkdir()
    (gdir / "APPEND_SYSTEM.md").write_text("GLOBAL APPEND MARKER", encoding="utf-8")
    # The agent-scope SYSTEM.md must exist before resolving, mirroring
    # _make_agent, so initial and reloaded discovery see the same files.
    cfg = _config_dir(tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "SYSTEM.md").write_text(BASE_PERSONA, encoding="utf-8")
    env = MvgeEnvironment.resolve(
        agent_name="reload-test",
        global_dir=gdir,
        allow_unknown_agent=True,
    )
    agent = _make_agent(tmp_path, monkeypatch, environment=env)
    await agent.initialize()
    try:
        assert "GLOBAL APPEND MARKER" in agent.system_prompt
        result = await agent.reload()
        assert result.ok
        assert not result.prompt_changed
        assert "GLOBAL APPEND MARKER" in agent.system_prompt
    finally:
        await agent.close()


def test_provider_snapshot_restore() -> None:
    """Snapshot/restore round-trips add/overwrite/merge mutations."""
    reg = RealmRegistry()
    reg.register_provider("keep", {"a": "1"})
    factory = lambda *a: None  # noqa: E731
    reg.register_realm_factory("keepf", factory)
    snap = _snapshot_provider_registrations(reg)
    # Candidate-style mutations.
    reg.register_provider("new", {"b": "2"})
    reg.register_provider("keep", {"c": "3"})
    reg.register_realm_factory("keepf", lambda *a: None)
    reg.register_realm_factory("newf", lambda *a: None)
    _restore_provider_registrations(reg, snap)
    assert reg.get_provider_config("new") is None
    assert "new" not in reg.get_registered_providers()
    assert reg.get_provider_config("keep") == {"a": "1"}
    assert reg.get_realm_factory("keepf") is factory
    assert reg.get_realm_factory("newf") is None


@pytest.mark.asyncio
async def test_failed_reload_rolls_back_provider_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed reload leaves no provider trace from the candidate."""
    name_file = tmp_path / "provider_name.txt"
    name_file.write_text("probe-prov", encoding="utf-8")
    monkeypatch.setenv("PROBE_PROVIDER_NAME", str(name_file))
    _write_probe_rune(tmp_path, body=_PROVIDER_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe-prov" in agent.registered_providers
        # The candidate build will register a NEW provider name...
        name_file.write_text("probe-prov-2", encoding="utf-8")
        (_config_dir(tmp_path) / "SYSTEM.md").write_text(
            "# PERSONA_NEW\nYou are New.\n", encoding="utf-8"
        )

        def _boom_builder() -> Any:
            raise RuntimeError("candidate build exploded")

        assert agent._harness is not None
        monkeypatch.setattr(agent._harness, "_reload_builder", _boom_builder)
        result = await agent.reload()
        assert not result.ok
        # ...but the failed build rolled it back: the live provider
        # remains, the candidate's registration never happened.
        assert "probe-prov" in agent.registered_providers
        assert "probe-prov-2" not in agent.registered_providers
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_lifecycle_watches_reload_trigger_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File events in rune, spell, and config dirs all fire the trigger."""
    _isolate_home(tmp_path, monkeypatch)
    runes_dir = tmp_path / "runes"
    runes_dir.mkdir(parents=True, exist_ok=True)
    spells_dir = tmp_path / "caller" / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    config_dir = _config_dir(tmp_path)
    config_dir.mkdir(parents=True, exist_ok=True)

    calls: list[Path] = []

    async def trigger() -> None:
        calls.append(Path("triggered"))

    lifecycle = RuneLifecycle(
        agent_name="reload-test",
        api_key="test-key",
        rune_layers=[ResolvedLayer(Scope.PROJECT, runes_dir)],
        reload_callback=trigger,
        extra_watch_dirs=[str(spells_dir), str(config_dir)],
    )
    await lifecycle.start()
    try:
        for target in (runes_dir, spells_dir, config_dir):
            calls.clear()
            (target / "probe.txt").write_text("x", encoding="utf-8")
            for _ in range(100):
                if calls:
                    break
                await asyncio.sleep(0.05)
            assert calls, f"no reload trigger from {target}"
    finally:
        await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_reload_command_failure_is_error_action() -> None:
    agent = MagicMock(spec=MvgeAgent)
    agent.reload = AsyncMock(
        return_value=ReloadResult(ok=False, message="Reload failed: boom")
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/reload")
    assert outcome.action == CommandAction.ERROR
    assert outcome.data["ok"] is False
    assert "boom" in outcome.message


@pytest.mark.asyncio
async def test_reload_command_queued_is_reload_queued_action() -> None:
    """A queued reload is reported as queued, not as completed."""
    agent = MagicMock(spec=MvgeAgent)
    agent.reload = AsyncMock(
        return_value=ReloadResult(ok=True, queued=True, message="queued")
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/reload")
    assert outcome.action == CommandAction.RELOAD_QUEUED
    assert outcome.data["queued"] is True


@pytest.mark.asyncio
async def test_reload_command_completed_is_reloaded_action() -> None:
    """An executed reload is still reported as RELOADED."""
    agent = MagicMock(spec=MvgeAgent)
    agent.reload = AsyncMock(
        return_value=ReloadResult(ok=True, queued=False, message="done")
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/reload")
    assert outcome.action == CommandAction.RELOADED
    assert outcome.data["queued"] is False


@pytest.mark.asyncio
async def test_reload_command_dispatches_to_agent() -> None:
    agent = MagicMock(spec=MvgeAgent)
    agent.reload = AsyncMock(
        return_value=ReloadResult(ok=True, message="Reload complete.")
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/reload")
    assert outcome.action == CommandAction.RELOADED
    assert outcome.command == "/reload"
    agent.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_command_without_agent_reload_errors() -> None:
    agent = MagicMock(spec=MvgeAgent)
    del agent.reload  # simulate an agent predating the reload API
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/reload")
    assert outcome.action == CommandAction.ERROR
    assert "reload" in outcome.message.lower()


@pytest.mark.asyncio
async def test_engine_reload_wins_over_rune_command_named_reload() -> None:
    """No Rune command may masquerade as the engine-owned /reload."""
    agent = MagicMock(spec=MvgeAgent)
    agent.reload = AsyncMock(
        return_value=ReloadResult(ok=True, message="Reload complete.")
    )
    imposter = MagicMock()
    imposter.name = "reload"
    imposter.description = "rune imposter"
    agent.get_registered_commands = MagicMock(return_value=[imposter])
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/reload")
    assert outcome.action == CommandAction.RELOADED
    agent.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_reloads_do_not_interleave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent reload() calls serialize: the second waits for the
    in-progress execution instead of interleaving its build over the
    shared provider registry."""
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        assert agent._harness is not None
        original_build = agent._harness._reload_builder

        async def _gated_build() -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            assert original_build is not None
            return await original_build()

        monkeypatch.setattr(agent._harness, "_reload_builder", _gated_build)
        first = asyncio.create_task(agent.reload())
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(agent.reload())
        # Give the second call a chance to start its build while the
        # first is still gated: serialized, it must not.
        await asyncio.sleep(0.2)
        assert calls == 1
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result.ok
        assert second_result.ok
        # The second caller ran its own execution afterwards and got its
        # own truthful outcome — one execution per caller, never
        # interleaved.
        assert calls == 2
    finally:
        await agent.close()


_PROVIDER_KEYS_RUNE_PY = """\
import json
import os
from pathlib import Path


def _probe_factory(**kwargs):
    raise AssertionError("probe factory must not be invoked in these tests")


def rune_factory(api):
    keys = json.loads(
        Path(os.environ["PROBE_PROVIDER_KEYS"]).read_text(encoding="utf-8")
    )
    api.register_provider("probe-prov", keys)
    api.register_realm_factory("probe:", _probe_factory)
"""


@pytest.mark.asyncio
async def test_reload_replaces_provider_config_not_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful reload replaces rune-owned provider configs: keys the
    rune dropped must not survive through register_provider()'s
    dict.update merge."""
    keys_file = tmp_path / "provider_keys.json"
    keys_file.write_text(json.dumps({"keep": "1", "drop": "2"}), encoding="utf-8")
    monkeypatch.setenv("PROBE_PROVIDER_KEYS", str(keys_file))
    _write_probe_rune(tmp_path, body=_PROVIDER_KEYS_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert agent._provider_registry.get_provider_config("probe-prov") == {
            "keep": "1",
            "drop": "2",
        }
        keys_file.write_text(json.dumps({"keep": "1"}), encoding="utf-8")
        result = await agent.reload()
        assert result.ok
        assert agent._provider_registry.get_provider_config("probe-prov") == {
            "keep": "1"
        }
        # The rune's realm factory is re-registered from the candidate.
        assert agent._provider_registry.get_realm_factory("probe:") is not None
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reload_removes_deleted_rune_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rune deleted from disk takes its provider registration and realm
    factory with it on the next successful reload."""
    keys_file = tmp_path / "provider_keys.json"
    keys_file.write_text(json.dumps({"keep": "1"}), encoding="utf-8")
    monkeypatch.setenv("PROBE_PROVIDER_KEYS", str(keys_file))
    _write_probe_rune(tmp_path, body=_PROVIDER_KEYS_RUNE_PY)
    agent = _make_agent(tmp_path, monkeypatch)
    await agent.initialize()
    try:
        assert "probe-prov" in agent.registered_providers
        assert agent._provider_registry.get_realm_factory("probe:") is not None
        shutil.rmtree(tmp_path / "runes" / "probe-rune")
        result = await agent.reload()
        assert result.ok
        assert "probe-prov" not in agent.registered_providers
        assert agent._provider_registry.get_realm_factory("probe:") is None
    finally:
        await agent.close()
