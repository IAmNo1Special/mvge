from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from mvgeos_cli.agent_factory import create_agent
from mvgeos_cli.main import _run_agent, app

runner = CliRunner()

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (typer styles option names with embedded
    color codes, which breaks plain substring checks on colored output)."""
    return _ANSI_ESCAPE_RE.sub("", text)


def test_main_is_callable() -> None:
    from mvgeos_cli.main import main

    assert callable(main)


def test_app_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "MvgeOS" in result.output
    assert "REPL" in result.output
    assert "Session tome management" in result.output


def test_app_config_help() -> None:
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "config" in result.output.lower()


def test_app_tome_help() -> None:
    result = runner.invoke(app, ["tome", "--help"])
    assert result.exit_code == 0
    assert "tome" in result.output.lower()


def test_app_build_help() -> None:
    result = runner.invoke(app, ["build", "--help"])
    assert result.exit_code == 0
    assert "manifest" in result.output.lower()


def test_app_info_help() -> None:
    result = runner.invoke(app, ["info", "--help"])
    assert result.exit_code == 0
    assert "snapshot" in result.output.lower()


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_repl_callback_no_incantation_runs_repl(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_repl_callback_with_incantation(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["--incantation", "hello world"])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["incantation"] == "hello world"


# This invocation names `--model test/model`, so the Realm being served is
# `test` and that is the Realm whose credential has to be present. A key for
# any other Realm is no longer accepted on its behalf.
@patch.dict(os.environ, {"TEST_API_KEY": "sk-test-v1-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_repl_callback_with_options(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(
        app,
        [
            "--model",
            "test/model",
            "--temperature",
            "0.5",
            "--max-tokens",
            "2048",
            "--contemplation",
            "high",
            "--spells",
            "bash,read",
            "--extension-dir",
            "/tmp/ext",
            "--resume",
            "/tmp/session.jsonl",
            "--provider",
            "test-provider",
            "--tome-dir",
            "/tmp/sessions",
            "--tui",
            "--incantation",
            "test prompt",
        ],
    )
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["model_id"] == "test/model"
    assert call_kwargs["temperature"] == 0.5
    assert call_kwargs["max_tokens"] == 2048
    assert call_kwargs["contemplation_level"] == "high"
    assert call_kwargs["spells_enabled"] == ["bash", "read"]
    assert call_kwargs["extension_dir"] == "/tmp/ext"
    assert call_kwargs["resume"] == "/tmp/session.jsonl"
    assert call_kwargs["provider_name"] == "test-provider"
    assert call_kwargs["tome_dir"] == "/tmp/sessions"
    assert call_kwargs["tui"] is True


def test_repl_callback_missing_api_key() -> None:
    """A Realm that does need one still refuses, naming its own variable.

    ``opencode/glm-5`` is the discriminating case: it needs a credential and it
    is not ``openrouter``, so a message naming OpenRouter's variable would pass
    every assertion written against a routed slug -- which is what a test here
    used to do, reading ``google/gemini-2.5-flash`` as a Realm. It is not one:
    no ``google`` Realm ships, and the catalog serves every ``google/*`` model as
    ``openrouter``, so that slug's correct variable *is* ``OPENROUTER_API_KEY``
    and it can no longer discriminate between the two answers.
    """
    env = dict(os.environ)
    for name in (
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        env.pop(name, None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(
            app, ["--model", "opencode/glm-5", "--incantation", "test"]
        )
        assert result.exit_code == 1
        assert "API key required" in result.output
        assert "OPENCODE_API_KEY" in result.output
        mock_run.assert_not_called()


def test_a_paid_model_on_zen_still_refuses_and_names_its_own_variable() -> None:
    """The exemption is per *model*, not per Realm -- at the CLI gate.

    Zen's free tier needs no key (ADR-0015); its paid models are keyed. A gate
    that asked only "is this Realm exempt?" would let a Summoner spend a paid
    model anonymously and then fail at the host with a message about the model
    rather than about the missing credential.

    This is the only test at this gate that can catch that: the neighbouring
    ones use ``google/``, a *different* Realm, so they cannot tell a per-model
    answer from a per-Realm one.
    """
    env = dict(os.environ)
    for name in (
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MVGEOS_API_KEY",
    ):
        env.pop(name, None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(app, ["--model", "opencode/glm-5", "hi"])
        assert result.exit_code == 1
        assert "API key required" in _strip_ansi(result.output)
        assert "OPENCODE_API_KEY" in _strip_ansi(result.output)
        mock_run.assert_not_called()


def test_the_default_model_runs_with_no_credential_at_all() -> None:
    """The shipped default must start on a machine with no credentials.

    ``DEFAULT_MODEL`` is ``opencode/space-bunny-free`` and Zen's free tier
    answers a request carrying no ``Authorization`` header (ADR-0015). The
    refusal this replaces was a pure loss: the Summoner was told to run a setup
    step for a credential that could not change the outcome.

    Asserted as the CLI *reaching* the agent rather than exiting, so it covers
    the gate rather than whatever comes after it. ``_run_agent`` is mocked
    because the next real step is resolving a Realm, which needs a Rune
    installed -- a separate concern with its own test.
    """
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("OPENCODE_API_KEY", None)
    env.pop("MVGEOS_API_KEY", None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(app, ["--model", "opencode/space-bunny-free", "hi"])
        assert result.exit_code == 0
        assert "API key required" not in _strip_ansi(result.output)
        mock_run.assert_called_once()
        # An empty credential, not the string "None" and not another Realm's.
        assert mock_run.call_args.kwargs["api_key"] == ""


def test_a_google_key_is_not_handed_to_the_realm_that_serves_a_google_slug() -> None:
    """A provider prefix must not decide whose credential is forwarded.

    Every ``google/*`` model in the shipped catalog is served by ``openrouter`` --
    there is no ``google`` Realm and no Rune for one -- so a ``GEMINI_API_KEY``
    picked up from a ``google/`` prefix was forwarded to openrouter.ai and the
    run proceeded as if a credential had been found. That is the cross-Realm
    credential exposure ADR-0015 records as fixed, reached by a different route:
    the prefix was read as if it named a Realm.

    Asserted on what the CLI asks for. The Realm that serves this slug requires
    a credential, so the correct outcome with none set is a refusal naming
    *its* variable -- and a Summoner holding only a Google key is told exactly
    what to set, rather than being handed a key for the wrong host.
    """
    env = dict(os.environ)
    env["GEMINI_API_KEY"] = "AIza-google-key-for-the-wrong-realm"
    for name in ("OPENROUTER_API_KEY", "OPENCODE_API_KEY", "GOOGLE_API_KEY"):
        env.pop(name, None)
    env.pop("MVGEOS_API_KEY", None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(app, ["--model", "google/gemini-2.5-flash", "hi"])
        assert result.exit_code == 1
        assert "OPENROUTER_API_KEY" in _strip_ansi(result.output)
        assert "AIza-google-key-for-the-wrong-realm" not in _strip_ansi(result.output)
        mock_run.assert_not_called()


def test_openrouter_key_is_not_handed_to_another_realm() -> None:
    """The CLI must not answer one Realm's credential with another's.

    ``OPENROUTER_API_KEY`` used to be a fallback for every Realm, which sent it
    to whichever host the model named. With ``opencode`` as the default Realm
    that was the common case.

    An OpenRouter key on the ``opencode`` path is now simply unused: that Realm
    is exempt from the credential requirement, so the Summoner has nothing to
    do. What this pins is that their key is never forwarded -- on a Realm that
    *does* require one, only that Realm's own variable is read.
    """
    env = dict(os.environ)
    env["OPENROUTER_API_KEY"] = "sk-or-v1-openrouter-secret"
    env.pop("OPENCODE_API_KEY", None)
    env.pop("MVGEOS_API_KEY", None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(app, ["--model", "opencode/space-bunny-free", "hi"])
        assert result.exit_code == 0
        assert "sk-or-v1-openrouter-secret" not in _strip_ansi(result.output)
        assert mock_run.call_args.kwargs["api_key"] == ""

    # And on a Realm that does need one, the OpenRouter key is not the answer.
    #
    # ``opencode/glm-5`` rather than a routed ``google/*`` slug: no ``google``
    # Realm ships, so the catalog serves those as ``openrouter`` and
    # ``OPENROUTER_API_KEY`` is the *right* answer there -- that slug cannot
    # discriminate. This one is served by ``opencode``, so only
    # ``OPENCODE_API_KEY`` would do, and the two wrong-realm keys below must go
    # unread. The slug is deliberately not a shipped catalog entry: that is what
    # makes it a *paid* model on a Realm whose free tier is anonymous, which is
    # the case the per-model contract exists to refuse.
    env["GOOGLE_API_KEY"] = "AIza-google-key-for-the-wrong-realm"
    env.pop("OPENCODE_API_KEY", None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(app, ["--model", "opencode/glm-5", "hi"])
        assert result.exit_code == 1
        assert "OPENCODE_API_KEY" in _strip_ansi(result.output)
        assert "sk-or-v1-openrouter-secret" not in _strip_ansi(result.output)
        assert "AIza-google-key-for-the-wrong-realm" not in _strip_ansi(result.output)
        mock_run.assert_not_called()


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_one_shot_merges_incantation_and_positionals(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["--incantation", "p0", "p1", "p2"])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["incantation"] == "p0"
    assert call_kwargs["prompts"] == ["p1", "p2"]


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_one_shot_positional_only(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["list", "files"])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["incantation"] is None
    assert call_kwargs["prompts"] == ["list", "files"]


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_subcommands_still_route(mock_run_agent: AsyncMock) -> None:
    result = runner.invoke(app, ["tome", "list"])
    assert result.exit_code == 0
    mock_run_agent.assert_not_called()


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_leaf_subcommand_with_extra_args_routes_to_prompts(
    mock_run_agent: AsyncMock,
) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["info", "about", "python"])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["incantation"] is None
    assert call_kwargs["prompts"] == ["info", "about", "python"]

    mock_run_agent.reset_mock()
    result_build = runner.invoke(app, ["build", "a", "snake", "game"])
    assert result_build.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs_build = mock_run_agent.call_args[1]
    assert call_kwargs_build["incantation"] is None
    assert call_kwargs_build["prompts"] == ["build", "a", "snake", "game"]


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_options_after_positionals_are_prompts(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["p1", "--model", "x"])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["prompts"] == ["p1", "--model", "x"]


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_error_exit_code_propagates(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 1
    result = runner.invoke(app, ["p1"])
    assert result.exit_code == 1


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_success_exit_code_zero(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["p1"])
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_agent_closes_agent_on_error() -> None:
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=RuntimeError("Agent failure"))
    mock_agent.close = AsyncMock()

    with patch(
        "mvgeos_cli.main._create_agent",
        new_callable=AsyncMock,
        return_value=mock_agent,
    ):
        code = await _run_agent(
            incantation="test",
            model_id=None,
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
        )

    assert code == 1
    mock_agent.close.assert_called_once()


@pytest.mark.asyncio
async def test_bug4_single_registry_and_rune_providers(tmp_path: Path) -> None:
    rune_dir = tmp_path / "test_rune"
    rune_dir.mkdir()
    manifest = {
        "name": "test_rune",
        "version": "0.1.0",
        "description": "Test rune registering provider",
        "entry_point": "index.py",
    }
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    index_code = (
        "from unittest.mock import AsyncMock\n"
        "def rune_factory(api):\n"
        "    api.register_provider('custom_rune_prov', {'base_url': 'http://localhost'})\n"
        "    api.register_realm_factory('openrouter', lambda **kw: AsyncMock())\n"
    )
    (rune_dir / "index.py").write_text(index_code, encoding="utf-8")

    tome_dir = tmp_path / "tomes"

    with (
        patch("mvgeos_runes.watcher.RuneWatcher.start", new_callable=AsyncMock),
        patch("mvgeos_runes.watcher.RuneWatcher.stop", new_callable=AsyncMock),
    ):
        agent = await create_agent(
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            api_key="sk-or-v1-test-key",
            spells="bash,read",
            extension_dir=str(tmp_path),
            tome_dir=str(tome_dir),
            resume=None,
            provider=None,
            temperature=0.7,
            max_tokens=4096,
            contemplation="medium",
        )
        try:
            assert "custom_rune_prov" in agent.registered_providers
            assert agent.harness is not None
            assert agent.harness.state.rune_runner is not None
        finally:
            await agent.close()


def test_app_help_lists_approval_mode() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--approval-mode" in _strip_ansi(result.output)


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_repl_callback_passes_approval_mode(mock_run_agent: AsyncMock) -> None:
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["--approval-mode", "allow-all"])
    assert result.exit_code == 0
    mock_run_agent.assert_called_once()
    call_kwargs = mock_run_agent.call_args[1]
    assert call_kwargs["approval_mode"] == "allow-all"


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-v1-test-key"})
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_repl_callback_rejects_bad_approval_mode(
    mock_run_agent: AsyncMock,
) -> None:
    result = runner.invoke(app, ["--approval-mode", "sometimes"])
    assert result.exit_code != 0
    mock_run_agent.assert_not_called()


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-test-key"})
@patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None)
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_a_realm_that_needs_a_key_reads_its_own_api_key(
    mock_run_agent: AsyncMock, mock_auth: MagicMock
) -> None:
    """A Realm that requires a credential must read its own, not another's.

    Handing one Realm's credential to another authenticates at the wrong host
    and then fails every call with a message naming the model, so the Summoner is
    sent to look at the wrong thing.

    Not the default model, because a free model on ``opencode`` is exempt from the
    credential requirement: a Summoner who has no key is not stopped there, so a
    test on that path cannot show the lookup happening.

    ``opencode/glm-5`` rather than a ``google/*`` slug, because no ``google``
    Realm ships and the catalog serves those as ``openrouter`` -- so OpenRouter's
    variable is the *right* answer for them and reading ``GOOGLE_API_KEY`` was
    forwarding a key to a host that never asked for it. Deliberately uncatalogued:
    that is what makes it a paid model on a Realm whose free tier is anonymous.
    """
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["--model", "opencode/glm-5", "--incantation", "hello"])

    assert result.exit_code == 0
    assert mock_run_agent.await_args.kwargs["api_key"] == "sk-zen-test-key"


@patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-zen-test-key"})
@patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None)
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_a_key_is_still_used_by_the_realm_that_does_not_require_one(
    mock_run_agent: AsyncMock, mock_auth: MagicMock
) -> None:
    """Exempting a Realm must not discard a key the Summoner did set.

    The exemption removes the *requirement*. A Summoner holding an
    ``OPENCODE_API_KEY`` -- paid Zen, or an endpoint that wants one -- must still
    have it forwarded, or the fix would have cost them working access in order to
    make the free tier work.
    """
    mock_run_agent.return_value = 0
    result = runner.invoke(app, ["--incantation", "hello"])

    assert result.exit_code == 0
    assert mock_run_agent.await_args.kwargs["api_key"] == "sk-zen-test-key"


@patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key"})
@patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None)
@patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock)
def test_an_explicit_model_reads_its_own_realms_key(
    mock_run_agent: AsyncMock, mock_auth: MagicMock
) -> None:
    """Choosing an openrouter model reads OpenRouter's key, not the default's."""
    mock_run_agent.return_value = 0
    result = runner.invoke(
        app, ["--model", "openrouter/free", "--incantation", "hello"]
    )

    assert result.exit_code == 0
    assert mock_run_agent.await_args.kwargs["api_key"] == "sk-or-test-key"


@patch.dict(os.environ, {}, clear=True)
def test_a_routed_slug_reads_the_credential_of_the_realm_that_serves_it() -> None:
    """A provider prefix must not decide whose credential file is read.

    ``nvidia/nemotron-3-ultra-550b-a55b:free`` is routed: ``nvidia`` is the
    *provider* and the shipped catalog serves it as ``openrouter``. Reading the
    Realm off the slug made the CLI ask for ``NVIDIA_API_KEY`` and read
    ``~/.agents/auth/nvidia.json``, so the credential ``mvgeos export`` actually
    writes -- ``openrouter.json`` -- did nothing, and the Summoner was told to
    set a variable that Realm never reads.

    Asserted on the Realm the lookup is asked for rather than on the key that
    comes back, because the credential file is resolved by that argument and it
    is the argument that was wrong.
    """
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None) as mock_auth,
        patch("mvgeos_cli.main._run_agent", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = 0
        result = runner.invoke(
            app,
            [
                "--model",
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "--incantation",
                "hello",
            ],
        )

    assert result.exit_code == 1
    # The message names the Realm the request will be sent to.
    assert "OPENROUTER_API_KEY" in _strip_ansi(result.output)
    assert "NVIDIA_API_KEY" not in _strip_ansi(result.output)
    # And the credential file it consults is that Realm's.
    mock_auth.assert_called_once_with("openrouter")


@patch.dict(os.environ, {}, clear=True)
def test_the_missing_key_message_names_the_models_own_variable() -> None:
    """The remedy has to name the variable that would actually fix it.

    ``opencode/glm-5`` rather than the default model, which is exempt: its Realm
    serves without a credential, so no message is printed there and naming its
    variable would be advice for a key it never reads. Not a ``google/`` slug
    either -- no ``google`` Realm ships, so that one's answer *is*
    ``OPENROUTER_API_KEY`` and it cannot discriminate.
    """
    env = dict(os.environ)
    for name in ("OPENROUTER_API_KEY", "OPENCODE_API_KEY", "GOOGLE_API_KEY"):
        env.pop(name, None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("mvgeos_cli.main.load_api_key_for_realm", return_value=None),
    ):
        result = runner.invoke(app, ["--model", "opencode/glm-5"])
        assert result.exit_code == 1
        assert "OPENCODE_API_KEY" in result.output
        assert "OPENROUTER_API_KEY" not in result.output
