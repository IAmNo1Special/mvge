from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvgeos_provider import NoRealmRegisteredError
from mvgeos_provider.registry import no_realm_registered
from typer.testing import CliRunner

from mvgeos_cli.main import _run_agent, app

runner = CliRunner()


@contextmanager
def _at_a_terminal() -> Iterator[None]:
    """Report both streams as a terminal for the duration of the block.

    Whether the CLI may ask a question is asked of the streams, and pytest
    captures both, so a test that means to exercise the interactive branch has
    to say so rather than inherit it from the runner.
    """
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch.object(sys.stdout, "isatty", return_value=True),
    ):
        yield


def test_rune_help() -> None:
    result = runner.invoke(app, ["rune", "--help"])
    assert result.exit_code == 0
    assert "Extension rune management" in result.output
    assert "install" in result.output


def test_rune_install_success() -> None:
    with patch(
        "mvgeos_cli.commands.rune.install_rune",
        return_value=Path("/tmp/extensions/sample-rune"),
    ) as mock_install:
        result = runner.invoke(app, ["rune", "install", "sample-rune"])
        assert result.exit_code == 0
        assert "Successfully installed rune 'sample-rune'" in result.output
        # Declared deps are opt-in: the manifest is authored by whoever
        # published the rune, so installing its packages is not implied by
        # installing the rune.
        mock_install.assert_called_once_with("sample-rune", confirm_python_deps=False)


def test_rune_install_confirms_python_deps_when_asked() -> None:
    with patch(
        "mvgeos_cli.commands.rune.install_rune",
        return_value=Path("/tmp/extensions/sample-rune"),
    ) as mock_install:
        result = runner.invoke(
            app, ["rune", "install", "sample-rune", "--confirm-python-deps"]
        )
        assert result.exit_code == 0
        mock_install.assert_called_once_with("sample-rune", confirm_python_deps=True)


def test_rune_install_failure() -> None:
    with patch(
        "mvgeos_cli.commands.rune.install_rune",
        side_effect=ValueError("Rune 'bad-rune' not found in marketplace."),
    ):
        result = runner.invoke(app, ["rune", "install", "bad-rune"])
        assert result.exit_code == 1
        assert "Rune 'bad-rune' not found in marketplace." in result.output


def _no_realm(model_id: str) -> NoRealmRegisteredError:
    """The error the registry actually raises for an uninstalled Realm.

    Constructed here rather than as a literal because ``rune_name`` is what the
    prompt now reads, and only the registry's constructor fills it in. A
    hand-written message would have left the attribute empty and the test would
    have been asserting the fallback rather than the behaviour.
    """
    return no_realm_registered(model_id)


def _agent() -> MagicMock:
    """An agent with the one slot the CLI reaches for after a retry succeeds.

    ``close`` is async because ``_run_agent`` awaits it on the way out, and a
    plain ``MagicMock`` there is the "coroutine never awaited" trap in its
    silent form -- it fails as an await, not as a warning.
    """
    agent = MagicMock()
    agent.close = AsyncMock(return_value=None)
    return agent


@pytest.mark.asyncio
async def test_no_realm_registered_repl_prompt_accepted() -> None:
    call_count = 0

    async def fake_repl(**kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _no_realm("opencode/space-bunny-free")
        return 0

    with (
        _at_a_terminal(),
        patch("mvgeos_cli.main.run_repl", side_effect=fake_repl),
        patch("builtins.input", return_value="y") as mock_input,
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation=None,
            model_id="opencode/space-bunny-free",
            api_key="sk-test",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=[],
        )

        assert code == 0
        assert call_count == 2
        mock_input.assert_called_once()
        mock_install.assert_called_once_with("opencode-realm")


@pytest.mark.asyncio
async def test_no_realm_registered_repl_prompt_rejected() -> None:
    async def fake_repl(**kwargs: object) -> int:
        raise _no_realm("opencode/space-bunny-free")

    with (
        _at_a_terminal(),
        patch("mvgeos_cli.main.run_repl", side_effect=fake_repl),
        patch("builtins.input", return_value="n") as mock_input,
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation=None,
            model_id="opencode/space-bunny-free",
            api_key="sk-test",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=[],
        )

        assert code == 1
        mock_input.assert_called_once()
        mock_install.assert_not_called()


@pytest.mark.asyncio
async def test_no_realm_registered_print_mode_no_prompt() -> None:
    with (
        _at_a_terminal(),
        patch(
            "mvgeos_cli.main._create_agent",
            side_effect=_no_realm("opencode/space-bunny-free"),
        ),
        patch("builtins.input") as mock_input,
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation="test incantation",
            model_id="openai/gpt-4o",
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
            prompts=["hello"],
        )

        assert code == 1
        mock_input.assert_not_called()
        # A Summoner at a terminal is told the command to run and left to run
        # it: one-shot mode has never installed anything on its own, and a
        # silent install is not a thing to add to it.
        mock_install.assert_not_called()


@pytest.mark.asyncio
async def test_no_realm_registered_no_tty_installs_without_asking() -> None:
    """A run with nobody at the terminal installs the Rune instead of giving up.

    This is the documented first run: `mvgeos --agent-name coding_mvge "task"`.
    There is no TTY to ask on, `input()` raises EOFError, and the old code read
    that as "no" and exited 1 with no next step. The remedy is the same one a
    Summoner typing "y" would have chosen.
    """
    call_count = 0

    async def fake_create_agent(**kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _no_realm("opencode/space-bunny-free")
        return _agent()

    with (
        patch("mvgeos_cli.main._create_agent", side_effect=fake_create_agent),
        patch(
            "mvgeos_cli.main._run_print_mode", new_callable=AsyncMock, return_value=0
        ) as mock_run,
        patch("builtins.input") as mock_input,
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation="Create a file named hello.txt",
            model_id="opencode/space-bunny-free",
            api_key="sk-or-v1-test-key",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=["Create a file named hello.txt"],
        )

        assert code == 0
        assert call_count == 2
        mock_input.assert_not_called()
        mock_install.assert_called_once_with("opencode-realm")
        mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_realm_registered_no_tty_repl_installs_without_asking() -> None:
    """The REPL without a terminal is the same dead end, and the same remedy."""
    call_count = 0

    async def fake_repl(**kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _no_realm("opencode/space-bunny-free")
        return 0

    with (
        patch("mvgeos_cli.main.run_repl", side_effect=fake_repl),
        patch("builtins.input") as mock_input,
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation=None,
            model_id="opencode/space-bunny-free",
            api_key="",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=[],
        )

        assert code == 0
        assert call_count == 2
        mock_input.assert_not_called()
        mock_install.assert_called_once_with("opencode-realm")


@pytest.mark.asyncio
async def test_no_realm_registered_retries_once_then_reports() -> None:
    """An install that does not register a Realm is reported, not repeated.

    The self-heal works by installing the Rune and running the request again,
    and a Rune only registers its factory once it has loaded. If it does not --
    a manifest that names the wrong entry point, a marketplace entry pointing at
    the wrong directory -- the retry raises the same error, and an unbounded
    retry reinstalls on every frame until the stack runs out. Two attempts, then
    the error is the honest answer: the same Rune installed twice is the same
    Rune.
    """
    call_count = 0

    async def fake_create_agent(**kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        raise _no_realm("opencode/space-bunny-free")

    with (
        patch("mvgeos_cli.main._create_agent", side_effect=fake_create_agent),
        patch("builtins.input"),
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation="Create a file named hello.txt",
            model_id="opencode/space-bunny-free",
            api_key="sk-or-v1-test-key",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=["Create a file named hello.txt"],
        )

        assert code == 1
        assert call_count == 2
        assert mock_install.call_count == 1


@pytest.mark.asyncio
async def test_no_realm_registered_repl_retries_once_then_reports() -> None:
    """The same bound on the REPL path, where the prompt used to absorb it."""

    async def fake_repl(**kwargs: object) -> int:
        raise _no_realm("opencode/space-bunny-free")

    with (
        patch("mvgeos_cli.main.run_repl", side_effect=fake_repl),
        patch("builtins.input"),
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation=None,
            model_id="opencode/space-bunny-free",
            api_key="",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=[],
        )

        assert code == 1
        assert mock_install.call_count == 1


@pytest.mark.asyncio
async def test_no_realm_registered_no_tty_reports_install_failure() -> None:
    """A marketplace that cannot be reached is named, not swallowed."""

    async def fake_repl(**kwargs: object) -> int:
        raise _no_realm("opencode/space-bunny-free")

    with (
        patch("mvgeos_cli.main.run_repl", side_effect=fake_repl),
        patch("builtins.input"),
        patch(
            "mvgeos_cli.main.install_rune",
            side_effect=ValueError(
                "Failed to fetch marketplace index from "
                "'https://raw.githubusercontent.com/': timed out"
            ),
        ),
    ):
        code = await _run_agent(
            incantation=None,
            model_id="opencode/space-bunny-free",
            api_key="",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=[],
        )

        assert code == 1


@pytest.mark.asyncio
async def test_no_realm_registered_unknown_realm_reports_the_error() -> None:
    """No Rune to offer means no install to attempt, asked or otherwise.

    `rune_for_realm("")` returns "" rather than "-realm", so a model whose Realm
    could not be determined has nothing to install and must not have a guess
    made for it -- on a terminal or off one.
    """

    async def fake_repl(**kwargs: object) -> int:
        raise NoRealmRegisteredError(
            "No Realm factory registered for model 'bare-model'.",
            realm="",
            rune_name="",
        )

    with (
        patch("mvgeos_cli.main.run_repl", side_effect=fake_repl),
        patch("builtins.input") as mock_input,
        patch("mvgeos_cli.main.install_rune") as mock_install,
    ):
        code = await _run_agent(
            incantation=None,
            model_id="bare-model",
            api_key="",
            temperature=None,
            max_tokens=None,
            contemplation_level=None,
            spells_enabled=None,
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            prompts=[],
        )

        assert code == 1
        mock_input.assert_not_called()
        mock_install.assert_not_called()
