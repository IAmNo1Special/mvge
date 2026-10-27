from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.cli.main import _run_agent
from mvge.core.channel import Model
from mvge.provider.base import Realm
from mvge.provider.registry import RealmRegistry


class FakeRouterRealm(Realm):
    @property
    def is_router(self) -> bool:
        return True


class FakeDirectRealm(Realm):
    @property
    def is_router(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_router_realm_requires_provider_in_non_interactive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    router_realm = FakeRouterRealm()
    mock_reg = MagicMock(spec=RealmRegistry)
    fake_model = Model(
        id="generic-model",
        name="Generic Model",
        realm="openrouter",
        base_url="",
        api_key="",
    )
    mock_reg.resolve.return_value = (fake_model, router_realm)
    mock_reg.get_providers_for_realm.return_value = ["openai", "anthropic", "google"]

    with patch("mvge.cli.main.get_default_realm_registry", return_value=mock_reg):
        code = await _run_agent(
            incantation="hello",
            model_id="generic-model",
            api_key="sk-or-test",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
        )
    assert code == 1
    captured = capsys.readouterr()
    assert "Provider selection required for router realm" in captured.out


@pytest.mark.asyncio
async def test_router_realm_accepts_provider_flag() -> None:
    router_realm = FakeRouterRealm()
    mock_reg = MagicMock(spec=RealmRegistry)
    fake_model = Model(
        id="generic-model",
        name="Generic Model",
        realm="openrouter",
        base_url="",
        api_key="",
    )
    mock_reg.resolve.return_value = (fake_model, router_realm)
    mock_reg.get_providers_for_realm.return_value = ["openai", "anthropic"]

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.close = AsyncMock()

    with (
        patch("mvge.cli.main.get_default_realm_registry", return_value=mock_reg),
        patch("mvge.cli.main._create_agent", new_callable=AsyncMock) as mock_ca,
    ):
        mock_ca.return_value = mock_agent
        code = await _run_agent(
            incantation="hello",
            model_id="generic-model",
            api_key="sk-or-test",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name="openai",
            tome_dir=None,
            tui=False,
        )
    assert code == 0


@pytest.mark.asyncio
async def test_direct_realm_bypasses_provider_selection() -> None:
    direct_realm = FakeDirectRealm()
    mock_reg = MagicMock(spec=RealmRegistry)
    fake_model = Model(
        id="ollama-model",
        name="Ollama Model",
        realm="ollama",
        base_url="",
        api_key="",
    )
    mock_reg.resolve.return_value = (fake_model, direct_realm)
    mock_reg.get_providers_for_realm.return_value = []

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.close = AsyncMock()

    with (
        patch("mvge.cli.main.get_default_realm_registry", return_value=mock_reg),
        patch("mvge.cli.main._create_agent", new_callable=AsyncMock) as mock_ca,
    ):
        mock_ca.return_value = mock_agent
        code = await _run_agent(
            incantation="hello",
            model_id="ollama-model",
            api_key="sk-or-test",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
        )
    assert code == 0
