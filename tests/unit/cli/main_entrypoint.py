"""Unit tests for the ``python -m mvge.cli`` entry point."""

from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.agent.installer import ProfileInstallResult
from mvge.cli.main import _maybe_bootstrap_profile, _run_agent, main


def test_main_module_calls_main() -> None:
    """Running the package as __main__ delegates to main()."""
    import mvge
    import mvge.cli

    p1 = str(Path(mvge.__file__).parent / "__main__.py")
    p2 = str(Path(mvge.cli.__file__).parent / "__main__.py")

    with patch("mvge.cli.main.main", return_value=0) as mock_main:
        runpy.run_path(p1, run_name="__main__")
        mock_main.assert_called_once_with()

    with patch("mvge.cli.main.main", return_value=0) as mock_main_cli:
        runpy.run_path(p2, run_name="__main__")
        mock_main_cli.assert_called_once_with()


def test_default_spells_from_config() -> None:
    from mvge.agent.config_manager import ConfigLayer, ConfigValue
    from mvge.cli.main import _default_spells_from_config

    assert (
        _default_spells_from_config(
            {"spells_enabled": ConfigValue(["a", "b"], ConfigLayer.DEFAULTS)}
        )
        == "a,b"
    )
    assert (
        _default_spells_from_config(
            {"spells_enabled": ConfigValue("all", ConfigLayer.DEFAULTS)}
        )
        == "all"
    )


def test_maybe_bootstrap_profile_already_installed() -> None:
    with patch(
        "mvge.agent.agent_name.installed_agent_names",
        return_value=["coding_mvge"],
    ):
        assert _maybe_bootstrap_profile(bootstrap=None) is False
        assert _maybe_bootstrap_profile(bootstrap=True) is False


def test_maybe_bootstrap_profile_explicit_false() -> None:
    with patch("mvge.agent.agent_name.installed_agent_names", return_value=[]):
        assert _maybe_bootstrap_profile(bootstrap=False, is_interactive=True) is False


def test_maybe_bootstrap_profile_explicit_true() -> None:
    mock_res = ProfileInstallResult("coding", ["coding_mvge"], ["seeker"], [], [])
    with (
        patch("mvge.agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdout.isatty", return_value=False),
        patch(
            "mvge.agent.installer.install_profile", return_value=mock_res
        ) as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=True) is True
        mock_install.assert_called_once_with("coding", confirm_python_deps=True)


def test_maybe_bootstrap_profile_explicit_true_terminal_keeps_prompt_gate() -> None:
    """Explicit --bootstrap at a terminal still gates deps on prompts."""
    mock_res = ProfileInstallResult("coding", ["coding_mvge"], ["seeker"], [], [])
    with (
        patch("mvge.agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch(
            "mvge.agent.installer.install_profile", return_value=mock_res
        ) as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=True) is True
        mock_install.assert_called_once_with("coding", confirm_python_deps=None)


def test_maybe_bootstrap_profile_interactive_auto_installs() -> None:
    """Interactive first run installs without a prompt to skip."""
    mock_res = ProfileInstallResult("coding", ["coding_mvge"], ["seeker"], [], [])
    with (
        patch("mvge.agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch(
            "mvge.agent.installer.install_profile", return_value=mock_res
        ) as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=None, is_interactive=True) is True
        mock_install.assert_called_once_with("coding", confirm_python_deps=None)


def test_maybe_bootstrap_profile_non_terminal_installs_nothing() -> None:
    with (
        patch("mvge.agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdout.isatty", return_value=True),
        patch("mvge.agent.installer.install_profile") as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=None, is_interactive=True) is False
        mock_install.assert_not_called()


def test_maybe_bootstrap_profile_non_interactive_installs_nothing() -> None:
    with (
        patch("mvge.agent.agent_name.installed_agent_names", return_value=[]),
        patch("mvge.agent.installer.install_profile") as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=None, is_interactive=False) is False
        mock_install.assert_not_called()


def test_maybe_bootstrap_profile_failure_returns_false() -> None:
    with (
        patch("mvge.agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch(
            "mvge.agent.installer.install_profile",
            side_effect=RuntimeError("marketplace unreachable"),
        ) as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=None, is_interactive=True) is False
        mock_install.assert_called_once_with("coding", confirm_python_deps=None)


@pytest.mark.asyncio
async def test_run_agent_rpc_mode() -> None:
    with (
        patch("mvge.cli.main.run_rpc", new_callable=AsyncMock) as mock_rpc,
        patch("mvge.cli.main._create_agent", new_callable=AsyncMock) as mock_create,
    ):
        mock_rpc.return_value = 0
        code = await _run_agent(
            incantation=None,
            model_id="test/model",
            api_key="sk-or-v1-dummy",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            mode="rpc",
        )
        assert code == 0
        mock_create.assert_called_once()
        mock_rpc.assert_called_once()


@pytest.mark.asyncio
async def test_run_agent_off_terminal_self_heals_missing_realm() -> None:
    """Off-terminal, a missing Realm installs on the prompt's default
    answer instead of failing the run: the documented first run would
    otherwise be a dead end (the NoRealmRegisteredError handler's
    comment records this intent). This locks the F1 behavior the
    end-user test flagged: the profile rule (non-terminal installs no
    profile) is separate from the single Realm rune needed to serve
    the model the user explicitly requested."""
    from mvge.provider.base import NoRealmRegisteredError

    mock_agent = MagicMock()
    mock_agent.runner = None
    mock_agent.close = AsyncMock()
    err = NoRealmRegisteredError(
        "no realm", realm="pollinations", rune_name="pollinations-realm"
    )
    with (
        patch("mvge.cli.main._create_agent", new_callable=AsyncMock) as mock_create,
        patch("mvge.cli.main.validate_api_key"),
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdout.isatty", return_value=False),
        patch("mvge.cli.main.install_rune") as mock_install,
        patch(
            "mvge.cli.main._run_print_mode",
            new_callable=AsyncMock,
            return_value=0,
        ) as mock_print,
    ):
        mock_create.side_effect = [err, mock_agent]
        code = await _run_agent(
            incantation="say hi",
            model_id="pollinations/openai",
            api_key=None,
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            approval_mode=None,
        )
    assert code == 0
    mock_install.assert_called_once_with("pollinations-realm")
    mock_print.assert_called_once()


@pytest.mark.asyncio
async def test_run_print_mode_warns_on_untrusted_workspace() -> None:
    """Print mode restores the user-visible half of the trust check:
    the status is warned to the user after the run, never steered into
    the model as a turn (Pi's shape for the same problem)."""
    from io import StringIO

    from rich.console import Console

    from mvge.cli.main import _run_print_mode
    from mvge.runes.trust import WorkspaceTrustStatus

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=MagicMock())
    mock_agent.workspace_trust_status = WorkspaceTrustStatus.DECLINED
    out = StringIO()
    with patch("mvge.cli.main.console", Console(file=out, force_terminal=False)):
        code = await _run_print_mode(mock_agent, ["say hi"])
    assert code == 0
    assert "declined" in out.getvalue()
    assert "project-level runes" in out.getvalue()

    mock_agent.workspace_trust_status = WorkspaceTrustStatus.TRUSTED
    out_trusted = StringIO()
    with patch(
        "mvge.cli.main.console", Console(file=out_trusted, force_terminal=False)
    ):
        code = await _run_print_mode(mock_agent, ["say hi"])
    assert code == 0
    assert "project-level runes" not in out_trusted.getvalue()


def test_main_sanitizes_bracketed_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI startup strips bracketed NO_PROXY tokens before any realm
    builds an httpx client (the runtime half of the test-suite
    sanitizer)."""
    import os

    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    with patch("mvge.cli.main.app") as mock_app:
        main()
    mock_app.assert_called_once_with()
    assert os.environ["NO_PROXY"] == "localhost"


@pytest.mark.asyncio
async def test_run_agent_tui_mode() -> None:
    with patch("mvge.cli.main.run_tui", new_callable=AsyncMock) as mock_tui:
        mock_tui.return_value = None
        code = await _run_agent(
            incantation=None,
            model_id="test/model",
            api_key="sk-or-v1-dummy",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=True,
            mode="tui",
        )
        assert code == 0
        mock_tui.assert_called_once()


def test_repl_callback_prompts_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock

    from mvge.cli.main import _repl_callback

    ctx = MagicMock()
    ctx.invoked_subcommand = None
    ctx.meta = {}

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(
        "mvge.cli.main.prompt_api_key", lambda console, realm=None: "sk-or-v1-prompted"
    )
    monkeypatch.setattr(
        "mvge.cli.main.save_api_key_to_auth",
        lambda key, realm: str(tmp_path / "auth.json"),
    )
    monkeypatch.setattr("mvge.cli.main.load_api_key_for_realm", lambda realm: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    with patch("mvge.cli.main._run_agent", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = 0
        _repl_callback(
            ctx,
            incantation="hi",
            model="openrouter/auto",
            api_key=None,
            temperature=None,
            max_tokens=None,
            contemplation=None,
            spells=None,
            extension_dir=None,
            resume=None,
            provider=None,
            tome_dir=None,
            tui=False,
            agent_name="coding_mvge",
            approval_mode=None,
            mode="auto",
            bootstrap=False,
        )
        assert mock_run.call_args[1]["api_key"] == "sk-or-v1-prompted"


def test_cli_main_function() -> None:
    with (
        patch("mvge.cli.main.configure_streams") as mock_conf,
        patch("mvge.cli.main.app") as mock_app,
    ):
        main()
        mock_conf.assert_called_once()
        mock_app.assert_called_once()
