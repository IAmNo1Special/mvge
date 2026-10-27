from __future__ import annotations

from pathlib import Path

import pytest

from mvge.agent import PEP723ScriptSpell
from mvge.core.spells import SpellResult, SpellStatus


@pytest.mark.asyncio
async def test_pep723_real_uv_run_execution(tmp_path: Path) -> None:
    script_file = tmp_path / "real_script.py"
    script_file.write_text(
        '"""A real PEP 723 script."""\n'
        "# /// script\n"
        '# requires-python = ">=3.11"\n'
        "# dependencies = []\n"
        "# ///\n"
        "import sys\n"
        "import json\n"
        "input_data = json.load(sys.stdin)\n"
        'msg = input_data.get("name", "world")\n'
        'output = {"status": "success", "content": f"Hello, {msg}!"}\n'
        "print(json.dumps(output))\n",
        encoding="utf-8",
    )

    spell = PEP723ScriptSpell(script_file)
    assert spell.name == "real_script"
    assert "A real PEP 723 script." in spell.description

    result = await spell.execute("cast_real_1", {"name": "Mvge"})
    assert isinstance(result, SpellResult)
    assert result.status == SpellStatus.SUCCESS
    assert result.content == "Hello, Mvge!"


@pytest.mark.asyncio
async def test_pep723_real_uv_run_plain_text(tmp_path: Path) -> None:
    script_file = tmp_path / "plain_script.py"
    script_file.write_text(
        "# /// script\n# dependencies = []\n# ///\nprint('simple text output')\n",
        encoding="utf-8",
    )

    spell = PEP723ScriptSpell(script_file)
    result = await spell.execute("cast_real_2", {})
    assert result == "simple text output"
