"""Unit tests for the Application Settings modal component."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.components import settings_modal as settings_modal_module
from mvgeos_gui.components.settings_modal import render_app_settings_modal
from mvgeos_gui.services.agent_service import AgentService
from mvgeos_gui.services.config_service import AppSettings, ConfigService
from mvgeos_gui.state import AppState, ServerState


def _make_state(tmp_path: Path, settings: AppSettings | None = None) -> AppState:
    service = ConfigService(config_dir=tmp_path)
    if settings is not None:
        service.save_app_settings(settings)
    state = AppState()
    state._config_service = service
    state._show_app_settings = True
    return state


@pytest.mark.asyncio
async def test_app_settings_modal_shows_api_key_field(
    user: User, tmp_path: Path
) -> None:
    settings = AppSettings(api_key="sk-existing")
    state = _make_state(tmp_path, settings)

    @ui.page("/test_settings_modal")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_modal")
    await user.should_see("sk-existing")


@pytest.mark.asyncio
async def test_app_settings_modal_shows_model_selector(
    user: User, tmp_path: Path
) -> None:
    state = _make_state(tmp_path)

    @ui.page("/test_settings_model")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_model")
    await user.should_see("Default Model")


@pytest.mark.asyncio
async def test_app_settings_modal_shows_mana_limit(user: User, tmp_path: Path) -> None:
    settings = AppSettings(mana_limit=8192)
    state = _make_state(tmp_path, settings)

    @ui.page("/test_settings_mana")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_mana")
    await user.should_see("8192")


@pytest.mark.asyncio
async def test_app_settings_modal_shows_theme_selector(
    user: User, tmp_path: Path
) -> None:
    state = _make_state(tmp_path)

    @ui.page("/test_settings_theme")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_theme")
    await user.should_see("Theme")


@pytest.mark.asyncio
async def test_app_settings_modal_has_save_button(user: User, tmp_path: Path) -> None:
    state = _make_state(tmp_path)

    @ui.page("/test_settings_save")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_save")
    await user.should_see("Save")


@pytest.mark.asyncio
async def test_app_settings_modal_has_cancel_button(user: User, tmp_path: Path) -> None:
    state = _make_state(tmp_path)

    @ui.page("/test_settings_cancel")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_cancel")
    await user.should_see("Cancel")


@pytest.mark.asyncio
async def test_app_settings_modal_save_success(user: User, tmp_path: Path) -> None:
    """Clicking Save should persist settings and hide modal."""
    state = _make_state(tmp_path)

    @ui.page("/test_settings_save_action")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_save_action")
    api_input = next(iter(user.find(marker="api_key_input").elements))
    api_input.set_value("sk-new-key")

    user.find("Save").click()
    assert state._show_app_settings is False
    saved = state._config_service.load_app_settings()
    assert saved.api_key == "sk-new-key"


@pytest.mark.asyncio
async def test_app_settings_modal_save_switches_model(
    user: User, tmp_path: Path
) -> None:
    """If default_model changes, switch_model should be invoked."""
    state = _make_state(tmp_path)
    state.selected_model = "different-model"
    state.switch_model = MagicMock()

    @ui.page("/test_settings_switch_model")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_switch_model")
    user.find("Save").click()
    state.switch_model.assert_called_once()


@pytest.mark.asyncio
async def test_app_settings_modal_save_invalid_mana_limit(
    user: User, tmp_path: Path
) -> None:
    """Invalid mana limit should show notification and not save."""
    state = _make_state(tmp_path)

    @ui.page("/test_settings_invalid_mana")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_invalid_mana")
    mana_input = next(iter(user.find(marker="mana_limit_input").elements))
    mana_input.set_value("-500")

    user.find("Save").click()
    assert state._show_app_settings is True
    await user.should_see("Invalid mana limit: must be a positive integer")


@pytest.mark.asyncio
async def test_app_settings_modal_save_invalid_temperature(
    user: User, tmp_path: Path
) -> None:
    """Temperature outside 0.0-2.0 should show notification and not save."""
    state = _make_state(tmp_path)

    @ui.page("/test_settings_invalid_temp")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_invalid_temp")
    temp_input = next(iter(user.find(marker="temperature_input").elements))
    temp_input.set_value("3.5")

    user.find("Save").click()
    assert state._show_app_settings is True
    await user.should_see("Invalid temperature: must be between 0.0 and 2.0")


@pytest.mark.asyncio
async def test_app_settings_modal_cancel_action(user: User, tmp_path: Path) -> None:
    """Closing the dialog should invoke _on_close and hide modal."""
    state = _make_state(tmp_path)

    @ui.page("/test_settings_cancel_action")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_cancel_action")
    dialog = next(iter(user.find(ui.dialog).elements))
    listener_id = next(
        k for k, v in dialog._event_listeners.items() if v.type == "close"
    )
    dialog._handle_event({"listener_id": listener_id, "args": None})
    assert state._show_app_settings is False


@pytest.mark.asyncio
async def test_app_settings_modal_does_not_render_when_hidden(
    user: User, tmp_path: Path
) -> None:
    """When _show_app_settings is False, modal should not render."""
    state = _make_state(tmp_path)
    state._show_app_settings = False

    @ui.page("/test_settings_hidden")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_hidden")
    await user.should_not_see("Application Settings")


@pytest.mark.asyncio
async def test_app_settings_modal_api_key_is_masked_with_toggle(
    user: User, tmp_path: Path
) -> None:
    """C8: API key must be a masked password input with a show/hide toggle."""
    state = _make_state(tmp_path, AppSettings(api_key="sk-secret"))

    @ui.page("/test_settings_api_key_masked")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_api_key_masked")
    api_input = next(iter(user.find(marker="api_key_input").elements))
    assert api_input._props.get("type") == "password"
    toggle_icons = [
        el
        for el in user.find(ui.icon).elements
        if el._props.get("name") in ("visibility", "visibility_off")
    ]
    assert toggle_icons, "expected a show/hide toggle on the API key input"


@pytest.mark.asyncio
async def test_app_settings_modal_save_reports_all_validation_errors(
    user: User, tmp_path: Path
) -> None:
    """C9: mana -5 + temperature 999 must report BOTH errors in one pass."""
    state = _make_state(tmp_path)

    @ui.page("/test_settings_all_errors")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_all_errors")
    mana_input = next(iter(user.find(marker="mana_limit_input").elements))
    mana_input.set_value("-5")
    temp_input = next(iter(user.find(marker="temperature_input").elements))
    temp_input.set_value("999")

    user.find("Save").click()
    assert state._show_app_settings is True
    await user.should_see("Invalid mana limit")
    await user.should_see("Invalid temperature")


@pytest.mark.asyncio
async def test_app_settings_modal_traps_focus(user: User, tmp_path: Path) -> None:
    """C26: a Tab keydown trap must be registered on the settings dialog."""
    state = _make_state(tmp_path)

    @ui.page("/test_settings_focus_trap")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_focus_trap")
    dialog = next(iter(user.find(ui.dialog).elements))
    trap_listeners = [
        listener
        for listener in dialog._event_listeners.values()
        if listener.type == "keydown.tab"
        and listener.js_handler
        and "focus" in listener.js_handler
    ]
    assert trap_listeners, "expected a Tab focus trap on the settings dialog"


@pytest.mark.asyncio
async def test_saving_new_theme_applies_it_live(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving a changed theme in Settings switches the live page theme."""
    service = ConfigService(config_dir=tmp_path)
    service.save_app_settings(AppSettings(theme="dark"))
    state = AppState()
    state._config_service = service
    state._show_app_settings = True

    applied: list[tuple[str, object]] = []
    monkeypatch.setattr(
        settings_modal_module,
        "apply_theme",
        lambda theme, dark_mode: applied.append((theme, dark_mode)),
    )

    @ui.page("/test_settings_theme_apply")
    def page() -> None:
        from mvgeos_gui.styles import inject_theme

        state._dark_mode = inject_theme("dark")
        render_app_settings_modal(state)

    await user.open("/test_settings_theme_apply")
    await user.should_see("Theme")

    select = next(iter(user.find(marker="theme_select").elements))
    select.set_value("light")
    user.find("Save", kind=ui.button).click()

    assert service.load_app_settings().theme == "light"
    assert applied == [("light", state._dark_mode)]


@pytest.mark.asyncio
async def test_saving_unchanged_theme_does_not_reapply(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving Settings without touching the theme leaves the page alone."""
    service = ConfigService(config_dir=tmp_path)
    service.save_app_settings(AppSettings(theme="dark"))
    state = AppState()
    state._config_service = service
    state._show_app_settings = True

    applied: list[tuple[str, object]] = []
    monkeypatch.setattr(
        settings_modal_module,
        "apply_theme",
        lambda theme, dark_mode: applied.append((theme, dark_mode)),
    )

    @ui.page("/test_settings_theme_noop")
    def page() -> None:
        from mvgeos_gui.styles import inject_theme

        state._dark_mode = inject_theme("dark")
        render_app_settings_modal(state)

    await user.open("/test_settings_theme_noop")
    await user.should_see("Theme")

    user.find("Save", kind=ui.button).click()

    assert applied == []


# ---------------------------------------------------------------------------
# API-key runtime propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_settings_modal_save_propagates_api_key_to_runtime(
    user: User, tmp_path: Path
) -> None:
    """Saving a new key must update state.api_key and the live AgentService."""
    state = _make_state(tmp_path)

    seen_keys: list[Any] = []
    closed: list[Any] = []

    def factory(**kwargs: Any) -> Any:
        seen_keys.append(kwargs.get("api_key"))
        agent = MagicMock()

        async def _close() -> None:
            closed.append(agent)

        agent.close = _close
        return agent

    service = AgentService(
        project_path=tmp_path, api_key="sk-old-key", agent_factory=factory
    )
    state.agent_service = service
    old_agent = service.get_or_create_agent(state)
    assert seen_keys == ["sk-old-key"]

    @ui.page("/test_settings_save_runtime_key")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_save_runtime_key")
    api_input = next(iter(user.find(marker="api_key_input").elements))
    api_input.set_value("sk-new-key")

    user.find("Save").click()
    assert state.api_key == "sk-new-key"

    for _ in range(100):
        if closed:
            break
        await asyncio.sleep(0.01)
    assert closed == [old_agent]

    # The next agent construction resolves the new key.
    new_agent = service.get_or_create_agent(state)
    assert new_agent is not old_agent
    assert seen_keys == ["sk-old-key", "sk-new-key"]


@pytest.mark.asyncio
async def test_app_settings_modal_save_rekeys_every_connected_client(
    user: User, tmp_path: Path
) -> None:
    """All connected clients' agent services are re-keyed (dedup safe)."""
    server = ServerState()
    config = ConfigService(config_dir=tmp_path)
    closed: list[Any] = []
    seen_keys: list[Any] = []
    services: list[AgentService] = []
    olds: list[Any] = []

    def factory(**kwargs: Any) -> Any:
        seen_keys.append(kwargs.get("api_key"))
        agent = MagicMock()

        async def _close() -> None:
            closed.append(agent)

        agent.close = _close
        return agent

    clients = []
    for _ in range(2):
        client = server.new_client_state()
        client._config_service = config
        service = AgentService(
            project_path=tmp_path, api_key="sk-old-key", agent_factory=factory
        )
        client.agent_service = service
        olds.append(service.get_or_create_agent(client))
        services.append(service)
        clients.append(client)

    state = server.client_states[0]
    state._show_app_settings = True

    @ui.page("/test_settings_save_multi_client")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_save_multi_client")
    api_input = next(iter(user.find(marker="api_key_input").elements))
    api_input.set_value("sk-new-key")

    user.find("Save").click()
    assert state.api_key == "sk-new-key"

    for _ in range(100):
        if len(closed) == 2:
            break
        await asyncio.sleep(0.01)
    assert closed == olds
    # Both clients' next agents resolve the new key (each exactly once:
    # the dedup in _save must not re-key any client twice).
    for service, client in zip(services, clients, strict=True):
        assert service.get_or_create_agent(client) is not None
    assert seen_keys == ["sk-old-key", "sk-old-key", "sk-new-key", "sk-new-key"]


@pytest.mark.asyncio
async def test_settings_modal_tabs_switching(user: User, tmp_path: Path) -> None:
    """Settings modal contains Application and Workspace tabs."""
    state = _make_state(tmp_path)
    state.project_path = tmp_path

    @ui.page("/test_settings_tabs")
    def page() -> None:
        render_app_settings_modal(state)

    await user.open("/test_settings_tabs")
    assert user.find(marker="settings_tab_app")
    assert user.find(marker="settings_tab_workspace")

    user.find(marker="settings_tab_workspace").click()
    await user.should_see("Project Name")
    assert user.find(marker="project_name_input")
