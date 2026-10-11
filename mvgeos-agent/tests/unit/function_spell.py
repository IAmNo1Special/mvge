from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mvgeos_core import MissingApiKeyError
from mvgeos_core.constants import DEFAULT_MODEL
from mvgeos_core.spells import (
    MvgeSpell,
    SpellResult,
    SpellStatus,
)
from mvgeos_provider.base import Realm
from mvgeos_provider.realms import realm_for_model_id
from mvgeos_provider.registry import RealmRegistry
from pydantic import BaseModel

from mvgeos_agent import (
    FunctionSpell,
    Mvge,
    MvgeEnvironment,
    coerce_spell,
)


def sample_sync_func(query: str, limit: int = 10) -> str:
    """Search for items matching the query.

    Args:
        query: The search string.
        limit: Max results.
    """
    return f"found {limit} results for {query}"


async def sample_async_func(x: int, y: int) -> int:
    """Add two numbers together asynchronously."""
    await asyncio.sleep(0.001)
    return x + y


class SampleOutputModel(BaseModel):
    status: str
    count: int


def model_returning_func() -> SampleOutputModel:
    """Return a pydantic model."""
    return SampleOutputModel(status="ok", count=42)


def spell_result_func(success: bool) -> SpellResult:
    """Return a SpellResult."""
    if success:
        return SpellResult(
            spell_name="custom", status=SpellStatus.SUCCESS, content="yay"
        )
    return SpellResult(
        spell_name="custom", status=SpellStatus.ERROR, error_message="nay"
    )


class TestFunctionSpell:
    def test_sync_function_metadata(self) -> None:
        spell = FunctionSpell(sample_sync_func)
        assert spell.name == "sample_sync_func"
        assert "Search for items" in spell.description
        assert "query" in spell.parameters.get("properties", {})
        assert "limit" in spell.parameters.get("properties", {})
        assert spell.parameters.get("required") == ["query"]

    def test_async_function_metadata(self) -> None:
        spell = FunctionSpell(
            sample_async_func, name="custom_adder", description="Adds nums"
        )
        assert spell.name == "custom_adder"
        assert spell.description == "Adds nums"
        assert "x" in spell.parameters.get("properties", {})
        assert "y" in spell.parameters.get("properties", {})

    @pytest.mark.asyncio
    async def test_execute_sync_function(self) -> None:
        spell = FunctionSpell(sample_sync_func)
        result = await spell.execute("call-1", {"query": "python", "limit": 5})
        assert result == "found 5 results for python"

    @pytest.mark.asyncio
    async def test_execute_async_function(self) -> None:
        spell = FunctionSpell(sample_async_func)
        result = await spell.execute("call-2", {"x": 10, "y": 20})
        assert result == "30"

    @pytest.mark.asyncio
    async def test_execute_returning_pydantic_model(self) -> None:
        spell = FunctionSpell(model_returning_func)
        result = await spell.execute("call-3", {})
        assert '"status":"ok"' in result or '"status": "ok"' in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_execute_returning_spell_result(self) -> None:
        spell = FunctionSpell(spell_result_func)
        res_ok = await spell.execute("call-4", {"success": True})
        assert isinstance(res_ok, SpellResult)
        assert res_ok.content == "yay"
        assert res_ok.status == SpellStatus.SUCCESS

        res_err = await spell.execute("call-5", {"success": False})
        assert isinstance(res_err, SpellResult)
        assert res_err.status == SpellStatus.ERROR
        assert res_err.error_message == "nay"

    @pytest.mark.asyncio
    async def test_execute_preserves_spell_result_details(self) -> None:
        def detailed() -> SpellResult:
            """Return a detailed SpellResult."""
            return SpellResult(
                spell_name="detailed",
                status=SpellStatus.SUCCESS,
                content="done",
                details={"truncation": {"version": 1, "truncated": False}},
            )

        spell = FunctionSpell(detailed)
        result = await spell.execute("call-7", {})
        assert isinstance(result, SpellResult)
        assert result.details["truncation"]["version"] == 1

    @pytest.mark.asyncio
    async def test_execute_invalid_arguments_raises_error_format(self) -> None:
        spell = FunctionSpell(sample_sync_func)
        with pytest.raises(ValueError, match="Invalid arguments"):
            await spell.execute("call-6", {"limit": "not_an_int"})


class TestCoerceSpell:
    def test_coerce_raw_callable(self) -> None:
        res = coerce_spell(sample_sync_func)
        assert isinstance(res, FunctionSpell)
        assert res.name == "sample_sync_func"

    def test_coerce_existing_function_spell(self) -> None:
        orig = FunctionSpell(sample_async_func)
        res = coerce_spell(orig)
        assert res is orig

    def test_coerce_custom_mvge_spell(self) -> None:
        class CustomSpell(MvgeSpell):
            pass

        orig = CustomSpell(name="custom", description="", parameters={})
        res = coerce_spell(orig)
        assert res is orig

    def test_coerce_invalid_type_raises(self) -> None:
        with pytest.raises(
            TypeError, match="Expected Callable, MvgeSpell, or SpellDefinition"
        ):
            coerce_spell("not_a_spell")  # type: ignore[arg-type]


class TestMvgeApiKeyAndEnvLoading:
    #: Every variable ``_resolve_api_key`` consults, in fallback order. A
    #: test that wants no key must clear all of them: clearing only the
    #: Realm's own leaves another Realm's variable (or a ``.env`` another test
    #: loaded) to satisfy the lookup, and the missing-key path silently stops
    #: being exercised.
    API_KEY_ENV_VARS = (
        "OPENCODE_API_KEY",
        "OPENROUTER_API_KEY",
        "MVGEOS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    )

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_error_on_initialize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in self.API_KEY_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        agent = Mvge(
            environment=MvgeEnvironment.resolve(
                overrides={"model": "google/gemini-2.5-flash"}
            )
        )
        agent._api_key = ""

        with pytest.raises(MissingApiKeyError, match="API key not found"):
            await agent.initialize()

    @pytest.mark.asyncio
    async def test_a_realm_that_needs_no_credential_initializes_without_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shipped default must start on a machine with no credentials.

        ``opencode/space-bunny-free`` is the model MvgeOS boots on, and Zen's
        free tier answers a request with no ``Authorization`` header at all
        (ADR-0015). Requiring a key for it is a dead end -- there is nothing to
        go set, so the Summoner is told to run a setup step that cannot help.

        A stub Realm is registered so the assertion is on reaching the end of
        ``initialize`` rather than on which error stopped the way. That matters:
        whether a Realm factory exists for ``opencode`` is process-wide state
        another test sets, so an assertion naming the successor error would be
        green or red depending on what ran first.
        """
        for var in self.API_KEY_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        realm = realm_for_model_id(DEFAULT_MODEL)
        registry = RealmRegistry()
        registry.register_realm_factory(realm, lambda **kwargs: MagicMock(spec=Realm))

        environment = MvgeEnvironment.resolve(
            config_dir=tmp_path,
            overrides={"model": DEFAULT_MODEL},
        )
        agent = Mvge(
            api_key="",
            environment=environment,
            tome_dir=tmp_path / "tomes",
            runes_paths=[str(tmp_path / "runes")],
            provider_registry=registry,
        )

        await agent.initialize()

        assert agent._model is not None

    @pytest.mark.asyncio
    async def test_a_paid_model_on_zen_still_raises_and_names_its_own_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exemption is per *model*, not per Realm -- at the library gate.

        Zen's free tier needs no key (ADR-0015); its paid models are keyed. A
        gate that asked only "is this Realm exempt?" would attempt a paid model
        anonymously and fail at the host with a message about the model rather
        than about the missing credential.

        The neighbouring test uses ``google/``, a *different* Realm, so it
        cannot tell a per-model answer from a per-Realm one. This is the
        library-side half of the pair, and it is what keeps
        ``Mvge.initialize`` from being widened without a test going red.
        """
        for var in self.API_KEY_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        environment = MvgeEnvironment.resolve(
            config_dir=tmp_path,
            overrides={"model": "opencode/glm-5"},
        )
        agent = Mvge(api_key="", environment=environment)

        with pytest.raises(MissingApiKeyError) as caught:
            await agent.initialize()

        message = str(caught.value)
        assert "OPENCODE_API_KEY" in message

    @pytest.mark.asyncio
    async def test_missing_api_key_error_names_the_realm_being_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message names this Realm's variable, not OpenRouter's.

        ``opencode/glm-5`` is the discriminating case. It does need a credential,
        and it is not ``openrouter``, so a message that hardcodes
        ``OPENROUTER_API_KEY`` still passes every assertion written against a
        routed slug -- which is what a test here used to do, reading
        ``google/gemini-2.5-flash`` as a Realm. It is not one: no ``google``
        Realm ships, and the catalog serves every ``google/*`` model as
        ``openrouter``, so that slug's correct variable *is*
        ``OPENROUTER_API_KEY`` and it cannot tell the two answers apart.
        """
        for var in self.API_KEY_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        environment = MvgeEnvironment.resolve(
            config_dir=tmp_path,
            overrides={"model": "opencode/glm-5"},
        )
        agent = Mvge(environment=environment)

        with pytest.raises(MissingApiKeyError) as caught:
            await agent.initialize()

        message = str(caught.value)
        assert "opencode" in message
        assert "OPENCODE_API_KEY" in message
        assert "OPENROUTER_API_KEY" not in message

    def test_dot_env_auto_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        monkeypatch.delenv("MVGEOS_API_KEY", raising=False)

        # The default model is served by the `opencode` Realm, so that is the
        # Realm whose variable is read. What is under test is that a `.env`
        # beside the caller is loaded at all -- an OpenRouter key here would
        # only have been honoured by falling back across Realms.
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENCODE_API_KEY=sk-zen-test-from-env-file\n", encoding="utf-8"
        )

        fake_caller_file = tmp_path / "caller.py"
        fake_caller_file.touch()

        with patch("inspect.stack") as mock_stack:
            frame_mock = type("Frame", (), {"filename": str(fake_caller_file)})()
            mock_stack.return_value = [None, frame_mock]

            agent = Mvge()
            assert agent._api_key == "sk-zen-test-from-env-file"


class TestSpellDirectoryDiscovery:
    def test_nonexistent_directory_returns_empty_list(self, tmp_path: Path) -> None:
        from mvgeos_agent import discover_spells_from_dir

        res = discover_spells_from_dir(tmp_path / "nonexistent")
        assert res == []

    def test_discovery_via_init_all(self, tmp_path: Path) -> None:
        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()

        init_file = spells_dir / "__init__.py"
        init_file.write_text(
            "def spell_one(x: int) -> int:\n"
            "    '''Doc 1'''\n"
            "    return x + 1\n\n"
            "def spell_two(y: str) -> str:\n"
            "    '''Doc 2'''\n"
            "    return y\n\n"
            "__all__ = ['spell_one', 'spell_two']\n",
            encoding="utf-8",
        )

        discovered = discover_spells_from_dir(spells_dir)
        assert len(discovered) == 2
        names = [s.name for s in discovered]
        assert "spell_one" in names
        assert "spell_two" in names

    def test_discovery_via_filename_matching(self, tmp_path: Path) -> None:
        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()

        # spell 1: bash.py -> def bash()
        (spells_dir / "bash.py").write_text(
            "def bash(command: str) -> str:\n"
            "    '''Run command.'''\n"
            "    return command\n",
            encoding="utf-8",
        )
        # spell 2: read.py -> def read()
        (spells_dir / "read.py").write_text(
            "def read(path: str) -> str:\n    '''Read file.'''\n    return path\n",
            encoding="utf-8",
        )
        # helper file starting with _ is skipped
        (spells_dir / "_internal.py").write_text(
            "def helper(): pass\n", encoding="utf-8"
        )

        discovered = discover_spells_from_dir(spells_dir)
        assert len(discovered) == 2
        names = [s.name for s in discovered]
        assert "bash" in names
        assert "read" in names

    def test_discovery_fallback_to_single_public_function(self, tmp_path: Path) -> None:
        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()

        # custom_tool.py -> def execute_tool()
        (spells_dir / "custom_tool.py").write_text(
            "def execute_tool(param: str) -> str:\n"
            "    '''Tool doc.'''\n"
            "    return param\n",
            encoding="utf-8",
        )

        discovered = discover_spells_from_dir(spells_dir)
        assert len(discovered) == 1
        assert discovered[0].name == "execute_tool"

    def test_discovery_ambiguous_raises_spell_discovery_error(
        self, tmp_path: Path
    ) -> None:
        from mvgeos_core import SpellDiscoveryError

        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()

        # ambiguous.py defines multiple functions without one matching
        # the filename and no __all__
        (spells_dir / "ambiguous.py").write_text(
            "def func_a(): pass\ndef func_b(): pass\n",
            encoding="utf-8",
        )

        with pytest.raises(
            SpellDiscoveryError, match="Could not discover spell in 'ambiguous.py'"
        ):
            discover_spells_from_dir(spells_dir)

    def test_discovery_on_file_error_skips_broken_file(self, tmp_path: Path) -> None:
        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()
        (spells_dir / "good.py").write_text(
            "def good(param: str) -> str:\n    '''Good.'''\n    return param\n",
            encoding="utf-8",
        )
        (spells_dir / "broken.py").write_text(
            "def broken(:\n    this is not python\n",
            encoding="utf-8",
        )

        errors: list[tuple[Path, Exception]] = []
        discovered = discover_spells_from_dir(
            spells_dir, on_file_error=lambda p, e: errors.append((p, e))
        )

        assert [s.name for s in discovered] == ["good"]
        assert len(errors) == 1
        assert errors[0][0] == spells_dir / "broken.py"
        assert isinstance(errors[0][1], Exception)

    def test_discovery_on_file_error_skips_broken_init(self, tmp_path: Path) -> None:
        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()
        # __init__.py names a spell that does not exist in the module.
        (spells_dir / "__init__.py").write_text(
            "__all__ = ['missing']\n",
            encoding="utf-8",
        )
        (spells_dir / "good.py").write_text(
            "def good(param: str) -> str:\n    '''Good.'''\n    return param\n",
            encoding="utf-8",
        )

        errors: list[tuple[Path, Exception]] = []
        discovered = discover_spells_from_dir(
            spells_dir, on_file_error=lambda p, e: errors.append((p, e))
        )

        assert discovered == []
        assert len(errors) == 1
        assert errors[0][0] == spells_dir / "__init__.py"
        assert isinstance(errors[0][1], Exception)

    def test_discovery_broken_init_raises_without_handler(self, tmp_path: Path) -> None:
        from mvgeos_core import SpellDiscoveryError

        from mvgeos_agent import discover_spells_from_dir

        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()
        (spells_dir / "__init__.py").write_text(
            "__all__ = ['missing']\n",
            encoding="utf-8",
        )

        with pytest.raises(SpellDiscoveryError, match="was not found in the module"):
            discover_spells_from_dir(spells_dir)

    @pytest.mark.asyncio
    async def test_execute_injects_signal_when_supported(self) -> None:
        from mvgeos_core.abort import AbortController

        from mvgeos_agent.function_spell import FunctionSpell

        received_signal = None

        def my_spell(query: str, signal=None) -> str:
            nonlocal received_signal
            received_signal = signal
            return f"searched {query}"

        spell = FunctionSpell(func=my_spell)
        controller = AbortController()
        res = await spell.execute("cast_1", {"query": "test"}, signal=controller.signal)
        assert res == "searched test"
        assert received_signal is controller.signal
