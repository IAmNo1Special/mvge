from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.agent.function_spell import (
    PEP723ScriptSpell,
    discover_spells_from_dir,
    parse_pep723_metadata,
)
from mvge.core.abort import AbortController, AbortError
from mvge.core.spells import SpellResult, SpellStatus


def test_parse_pep723_metadata_valid() -> None:
    source = """# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx",
#     "rich>=10.0",
# ]
# ///

import httpx
print("done")
"""
    meta = parse_pep723_metadata(source)
    assert meta is not None
    assert meta.requires_python == ">=3.11"
    assert meta.dependencies == ["httpx", "rich>=10.0"]
    assert "dependencies" in meta.raw_toml


def test_parse_pep723_metadata_with_custom_fields() -> None:
    source = """# /// script
# dependencies = ["requests"]
# description = "Fetch data from URL"
# [parameters]
# url = { type = "string", description = "The URL to fetch" }
# ///
"""
    meta = parse_pep723_metadata(source)
    assert meta is not None
    assert meta.dependencies == ["requests"]
    assert meta.raw_toml.get("description") == "Fetch data from URL"
    assert "url" in meta.raw_toml.get("parameters", {})


def test_parse_pep723_metadata_various_comment_styles() -> None:
    source = """# /// script
#dependencies = ["aiohttp"]
#
# requires-python = ">=3.12"
# ///
"""
    meta = parse_pep723_metadata(source)
    assert meta is not None
    assert meta.dependencies == ["aiohttp"]
    assert meta.requires_python == ">=3.12"


def test_parse_pep723_metadata_missing() -> None:
    source = """
def regular_function():
    return 42
"""
    meta = parse_pep723_metadata(source)
    assert meta is None


def test_parse_pep723_metadata_other_type() -> None:
    source = """# /// tool
# dependencies = ["ruff"]
# ///
"""
    meta = parse_pep723_metadata(source)
    assert meta is None


def test_parse_pep723_metadata_invalid_toml() -> None:
    source = """# /// script
# this is [not valid toml :::
# ///
"""
    with pytest.raises(ValueError, match="Invalid PEP 723 TOML"):
        parse_pep723_metadata(source)


def test_pep723_script_spell_metadata_inference(tmp_path: Path) -> None:
    script = tmp_path / "fetch_url.py"
    script.write_text(
        '"""Fetch a remote resource via HTTP."""\n'
        "# /// script\n"
        '# dependencies = ["httpx"]\n'
        "# [parameters]\n"
        '# url = { type = "string", description = "Target URL" }\n'
        "# ///\n"
        "import httpx\n",
        encoding="utf-8",
    )

    spell = PEP723ScriptSpell(script)
    assert spell.name == "fetch_url"
    assert "Fetch a remote resource" in spell.description
    assert "url" in spell.parameters.get("properties", {})


@pytest.mark.asyncio
async def test_pep723_script_spell_execute_success(tmp_path: Path) -> None:
    script = tmp_path / "echo.py"
    script.write_text(
        "# /// script\n# dependencies = []\n# ///\nprint('hello world')\n",
        encoding="utf-8",
    )
    spell = PEP723ScriptSpell(script)

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"hello world\n", b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await spell.execute("cast_1", {"arg": "val"})
        assert result == "hello world"
        mock_exec.assert_called_once()
        args, kwargs = mock_exec.call_args
        assert args[0] == "uv"
        assert args[1] == "run"
        assert args[2] == "--script"
        assert str(script) == args[3]


@pytest.mark.asyncio
async def test_pep723_script_spell_execute_json_spell_result(tmp_path: Path) -> None:
    script = tmp_path / "json_spell.py"
    script.write_text(
        "# /// script\n# dependencies = []\n# ///\n",
        encoding="utf-8",
    )
    spell = PEP723ScriptSpell(script)

    json_payload = (
        '{"status": "success", "content": "all good", "details": {"code": 200}}'
    )
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (json_payload.encode("utf-8"), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await spell.execute("cast_2", {})
        assert isinstance(result, SpellResult)
        assert result.status == SpellStatus.SUCCESS
        assert result.content == "all good"
        assert result.details == {"code": 200}


@pytest.mark.asyncio
async def test_pep723_script_spell_execute_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "failing.py"
    script.write_text(
        "# /// script\n# dependencies = []\n# ///\n",
        encoding="utf-8",
    )
    spell = PEP723ScriptSpell(script)

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"ZeroDivisionError: division by zero\n")
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await spell.execute("cast_3", {})
        assert isinstance(result, SpellResult)
        assert result.status == SpellStatus.ERROR
        assert "ZeroDivisionError" in (result.error_message or "")


@pytest.mark.asyncio
async def test_pep723_script_spell_execute_timeout(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text(
        "# /// script\n# dependencies = []\n# ///\n",
        encoding="utf-8",
    )
    spell = PEP723ScriptSpell(script, timeout=0.1)

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = asyncio.TimeoutError
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await spell.execute("cast_4", {})
        assert isinstance(result, SpellResult)
        assert result.status == SpellStatus.ERROR
        assert "timed out" in (result.error_message or "").lower()
        mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_pep723_script_spell_execute_abort_signal(tmp_path: Path) -> None:
    script = tmp_path / "long.py"
    script.write_text(
        "# /// script\n# dependencies = []\n# ///\n",
        encoding="utf-8",
    )
    spell = PEP723ScriptSpell(script)

    # Pre-aborted signal
    controller = AbortController()
    controller.abort()
    with pytest.raises(AbortError):
        await spell.execute("cast_5", {}, signal=controller.signal)

    # In-flight abort
    controller2 = AbortController()
    mock_proc = AsyncMock()

    async def delayed_communicate(*args, **kwargs):
        controller2.abort()
        await asyncio.sleep(0.01)
        return (b"", b"")

    mock_proc.communicate.side_effect = delayed_communicate
    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(AbortError):
            await spell.execute("cast_6", {}, signal=controller2.signal)
        mock_proc.terminate.assert_called()


def test_discover_spells_from_dir_with_pep723(tmp_path: Path) -> None:
    spells_dir = tmp_path / "spells"
    spells_dir.mkdir()

    # Regular spell
    (spells_dir / "regular.py").write_text(
        "def regular(x: int) -> int:\n    '''Doc'''\n    return x\n",
        encoding="utf-8",
    )

    # PEP 723 spell that imports a non-existent package
    (spells_dir / "pep723_script.py").write_text(
        '"""A script with inline metadata."""\n'
        "# /// script\n"
        '# dependencies = ["definitely-not-installed-pkg-xyz"]\n'
        "# ///\n"
        "import definitely_not_installed_pkg_xyz\n",
        encoding="utf-8",
    )

    discovered = discover_spells_from_dir(spells_dir)
    assert len(discovered) == 2
    names = [s.name for s in discovered]
    assert "regular" in names
    assert "pep723_script" in names

    pep_spell = next(s for s in discovered if s.name == "pep723_script")
    assert isinstance(pep_spell, PEP723ScriptSpell)
    assert "A script with inline metadata." in pep_spell.description


@pytest.mark.asyncio
async def test_pep723_script_spell_cancellation(tmp_path: Path) -> None:
    script = tmp_path / "cancel.py"
    script.write_text("# /// script\n# dependencies = []\n# ///\n", encoding="utf-8")
    spell = PEP723ScriptSpell(script)

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = asyncio.CancelledError()
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(asyncio.CancelledError):
            await spell.execute("cast_cancel", {})
        mock_proc.kill.assert_called()


def test_pep723_script_spell_explicit_arguments(tmp_path: Path) -> None:
    script = tmp_path / "explicit.py"
    script.write_text("print(1)\n", encoding="utf-8")
    from mvge.agent.function_spell import PEP723Metadata

    meta = PEP723Metadata(dependencies=["dep1"], requires_python=">=3.12")
    spell = PEP723ScriptSpell(
        script,
        name="explicit_name",
        description="explicit_desc",
        parameters={"my_param": {"type": "integer"}},
        metadata=meta,
    )
    assert spell.name == "explicit_name"
    assert spell.description == "explicit_desc"
    assert "my_param" in spell.parameters
    assert spell.metadata == meta


def test_pep723_script_spell_toml_description_and_parameters_schema(
    tmp_path: Path,
) -> None:
    script = tmp_path / "custom_schema.py"
    script.write_text(
        "# /// script\n"
        "# dependencies = []\n"
        '# description = "TOML desc"\n'
        "# [parameters]\n"
        '# type = "object"\n'
        '# properties = { val = { type = "string" } }\n'
        "# ///\n",
        encoding="utf-8",
    )
    spell = PEP723ScriptSpell(script)
    assert spell.description == "TOML desc"
    assert spell.parameters.get("type") == "object"


def test_pep723_script_spell_nonexistent_file() -> None:
    spell = PEP723ScriptSpell(Path("/nonexistent/fake_script.py"))
    assert spell.name == "fake_script"
    assert "Execute standalone script fake_script via uv run." in spell.description


@pytest.mark.asyncio
async def test_pep723_script_spell_missing_uv_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No uv anywhere means an ERROR result, not a FileNotFoundError.

    uv installed per-user lives in ``~/.local/bin``, which is not on PATH
    in every shell. Pointing HOME at an empty directory (and hiding any
    PATH uv) must still produce the spell's normal error shape.
    """
    script = tmp_path / "needs_uv.py"
    script.write_text(
        "# /// script\\n# dependencies = []\\n# ///\\nprint('hi')\\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    spell = PEP723ScriptSpell(script)

    with patch("mvge.agent.function_spell.shutil.which", return_value=None):
        result = await spell.execute("cast_no_uv", {})

    assert isinstance(result, SpellResult)
    assert result.status == SpellStatus.ERROR
    assert "uv" in (result.error_message or "")
