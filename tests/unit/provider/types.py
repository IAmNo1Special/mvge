import pytest

from mvge.core.channel import ChannelConfig, Model, RealmResponse
from mvge.provider.base import Realm


def test_realm_protocol_has_stream_method() -> None:
    assert hasattr(Realm, "stream")


def test_model_has_required_fields() -> None:
    model = Model(
        id="openrouter/test-model",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        max_completion_mana=1000,
        context_window=128000,
        max_tokens=4096,
    )
    assert model.id == "openrouter/test-model"
    assert model.realm == "openrouter"
    assert model.max_completion_mana == 1000
    assert model.supported_parameters == []


def test_model_supported_parameters() -> None:
    model = Model(
        id="openrouter/openai/o1",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        supported_parameters=["reasoning", "temperature"],
    )
    assert "reasoning" in model.supported_parameters
    assert "temperature" in model.supported_parameters


def test_channel_config_system_prompt() -> None:
    model = Model(
        id="openrouter/test-model",
        name="Test",
        realm="openrouter",
        base_url="",
        api_key="",
    )
    default_config = ChannelConfig(model=model)
    assert default_config.system_prompt == ""

    custom_config = ChannelConfig(model=model, system_prompt="You are a Mvge.")
    assert custom_config.system_prompt == "You are a Mvge."


def test_channel_config_spells_and_tools_sync() -> None:
    model = Model(
        id="openrouter/test-model",
        name="Test",
        realm="openrouter",
        base_url="",
        api_key="",
    )
    spell_payload = [{"type": "function", "function": {"name": "cast_light"}}]

    # Initializing with spells populates tools
    cfg_spells = ChannelConfig(model=model, spells=spell_payload)
    assert cfg_spells.spells == spell_payload
    assert cfg_spells.tools == spell_payload

    # Initializing with tools populates spells
    cfg_tools = ChannelConfig(model=model, tools=spell_payload)
    assert cfg_tools.spells == spell_payload
    assert cfg_tools.tools == spell_payload


def test_realm_response_has_diagnostic_fields() -> None:

    model = Model(
        id="openrouter/test-model",
        name="Test",
        realm="openrouter",
        base_url="",
        api_key="",
    )
    resp = RealmResponse(
        model=model,
        error_message="Rate limit exceeded",
        error_code="rate_limited",
        retry_after=15.0,
        limit_source="openrouter_free_tier_daily",
        remedy_hint="Add credits to unlock 1000 requests",
        reset_at=1788566400.0,
        quota_limit=50,
        quota_remaining=0,
    )
    assert resp.retry_after == 15.0
    assert resp.limit_source == "openrouter_free_tier_daily"
    assert resp.remedy_hint == "Add credits to unlock 1000 requests"
    assert resp.reset_at == 1788566400.0
    assert resp.quota_limit == 50
    assert resp.quota_remaining == 0


@pytest.mark.asyncio
async def test_abort_controller_and_signal() -> None:
    import asyncio

    from mvge.core.abort import (
        AbortController,
        AbortError,
    )

    controller = AbortController()
    signal = controller.signal
    assert not signal.aborted

    called: list[int] = []
    signal.on_abort(lambda: called.append(1))

    # Callback that raises should be suppressed cleanly
    def broken_callback() -> None:
        raise ValueError("boom")

    signal.on_abort(broken_callback)

    signal.raise_if_aborted()

    async def waiter() -> None:
        await signal.wait()

    task1 = asyncio.create_task(waiter())
    task2 = asyncio.create_task(waiter())

    await asyncio.sleep(0.01)
    assert not signal.aborted
    controller.abort()
    assert signal.aborted
    assert 1 in called

    await asyncio.gather(task1, task2)

    # Calling abort again is a no-op
    controller.abort()

    # on_abort when already aborted invokes callback immediately
    already_called: list[int] = []
    signal.on_abort(lambda: already_called.append(2))
    assert 2 in already_called

    with pytest.raises(AbortError):
        signal.raise_if_aborted()

    # wait() when already aborted returns immediately
    await signal.wait()


@pytest.mark.asyncio
async def test_realm_base_protocol() -> None:
    from mvge.core.channel import ChannelConfig
    from mvge.provider.base import Realm

    realm = Realm()
    model = Model(
        id="test",
        name="test",
        realm="test",
        base_url="",
        api_key="",
    )
    config = ChannelConfig(model=model)

    with pytest.raises(NotImplementedError):
        realm.stream(model, [], config)

    with pytest.raises(NotImplementedError):
        await realm.complete(model, [], config)

    await realm.close()
