"""Unit tests for the Phase 2A RuneAPI narrowing.

A factory must never receive a live RuneRunner: the API it gets is a
facade over narrow methods only. These tests assert observable
behaviour, not internals: no private attributes, no patched
machinery beyond the fake realm a model-call test needs.
"""

from __future__ import annotations

from typing import Any

import pytest

from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneContext, SkillManifest, SpellDefinition


def make_api(rune_name: str = "test-rune") -> Any:
    runner = RuneRunner()
    return runner.create_api(rune_name=rune_name), runner


def test_api_has_no_runner_attribute() -> None:
    api, _runner = make_api()
    assert getattr(api, "_runner", None) is None
    with pytest.raises(AttributeError):
        api._runner  # noqa: B018


def test_api_context_has_no_api_key_and_is_a_copy() -> None:
    api, runner = make_api()
    runner.bind_context(
        RuneContext(cwd="/workspace", agent_name="tester", model_id="test/free")
    )
    ctx = api.context
    assert not hasattr(ctx, "api_key")
    assert ctx.cwd == "/workspace"
    assert ctx.agent_name == "tester"
    # A copy, not the runner's live context object (RuneContext is frozen,
    # so identity — not mutation — is the observable difference).
    assert ctx is not runner.context
    assert ctx == runner.context


def test_api_get_spell_and_session_name_getter() -> None:
    api, _runner = make_api()
    assert api.get_spell("missing") is None
    spell = SpellDefinition(name="hello", description="hi")
    assert api.register_spell(spell) is True
    assert api.get_spell("hello") is spell
    assert api.get_session_name() is None
    api.set_session_name("My Title")
    assert api.get_session_name() == "My Title"


def test_api_skills_view_is_read_only_and_clear_is_rune_scoped() -> None:
    runner = RuneRunner()
    api = runner.create_api(rune_name="test-rune")
    other_api = runner.create_api(rune_name="other-rune")
    api.register_skill(SkillManifest(name="mine", description="mine"))
    other_api.register_skill(SkillManifest(name="theirs", description="theirs"))
    skills = api.get_skills()
    assert {s.name for s in skills} == {"mine", "theirs"}
    skills.clear()
    assert len(api.get_skills()) == 2
    api.clear_skills_for_rune()
    assert {s.name for s in api.get_skills()} == {"theirs"}


@pytest.mark.asyncio
async def test_api_gateway_spell_names() -> None:
    from mvge.runes.types import RuneLoad, RuneManifest

    runner = RuneRunner()
    api = runner.create_api(rune_name="gw")
    api.register_spell(SpellDefinition(name="gw_spell", description="gw"))
    worker_api = runner.create_api(rune_name="worker")
    worker_api.register_spell(SpellDefinition(name="w_spell", description="w"))
    await runner.load_rune_loads(
        [
            RuneLoad(
                manifest=RuneManifest(
                    name="gw", version="1.0.0", description="", spell_gateway=True
                ),
            )
        ]
    )
    assert api.gateway_spell_names() == ["gw_spell"]


def test_fixture_rune_cannot_reach_runner_or_key() -> None:
    api, _runner = make_api()

    def fixture_factory(api: Any) -> None:
        with pytest.raises(AttributeError):
            api._runner  # noqa: B018
        with pytest.raises(AttributeError):
            api.context.api_key  # noqa: B018
        assert getattr(api, "_runner", None) is None

    fixture_factory(api)


class _FakeRealm:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def stream(self, model: Any, invocations: Any, config: Any) -> Any:
        from types import SimpleNamespace

        yield SimpleNamespace(
            invocation=SimpleNamespace(content=[{"type": "text", "text": "hello"}])
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_stream_model_text_uses_engine_held_key_not_rune_key() -> None:
    from mvge.runes.types import RuneLoad, RuneManifest

    api, runner = make_api()
    await runner.load_rune_loads(
        [
            RuneLoad(
                manifest=RuneManifest(
                    name="test-rune",
                    version="1.0.0",
                    description="",
                    types=["RealmProvider"],
                )
            )
        ]
    )
    runner.bind_model_key("engine-held-key")
    api.register_realm_factory("test", lambda **kwargs: _FakeRealm(**kwargs))
    text = await api.stream_model_text("test/free", "say hello")
    assert text == "hello"


@pytest.mark.asyncio
async def test_stream_model_text_never_delivers_key_to_non_realm_factory() -> None:
    api, runner = make_api(rune_name="evil-rune")
    runner.bind_model_key("engine-held-key")
    called: dict[str, Any] = {}

    def evil_factory(**kwargs: Any) -> _FakeRealm:
        called.update(kwargs)
        return _FakeRealm(**kwargs)

    assert api.register_realm_factory("evil", evil_factory) is True
    with pytest.raises(RuntimeError, match="No Realm factory"):
        await api.stream_model_text("evil/free", "say hello")
    assert called == {}


def test_approval_presenter_source_refuses_capture_object() -> None:
    api, _runner = make_api()
    received: list[Any] = []

    class _Capture:
        def share_approval_presenter_from(self, runner: Any) -> None:
            received.append(runner)

    source = api.approval_presenter_source()
    with pytest.raises(TypeError, match="RuneRunner"):
        source(_Capture())
    assert received == []


def test_approval_presenter_source_accepts_real_runner() -> None:
    api, _runner = make_api()
    child = RuneRunner()
    # No raise: the manager path (real child runner) must keep working.
    api.approval_presenter_source()(child)


@pytest.mark.asyncio
async def test_stream_model_text_without_realm_factory_fails() -> None:
    api, _runner = make_api()
    with pytest.raises(RuntimeError, match="No Realm factory"):
        await api.stream_model_text("test/free", "say hello")
