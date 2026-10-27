from dataclasses import FrozenInstanceError, replace

import pytest

from mvge.runes.types import BeforeMvgeStartData, RuneContext


class TestRuneContext:
    def test_default_values(self) -> None:
        ctx = RuneContext()
        assert ctx.cwd == ""
        assert ctx.mode == "cli"
        assert ctx.has_ui is False
        assert ctx.agent_name == ""
        assert ctx.api_key == ""

    def test_custom_values(self) -> None:
        ctx = RuneContext(
            cwd="/workspace",
            mode="gui",
            has_ui=True,
            agent_name="tester",
            api_key="secret-key",
        )
        assert ctx.cwd == "/workspace"
        assert ctx.mode == "gui"
        assert ctx.has_ui is True
        assert ctx.agent_name == "tester"
        assert ctx.api_key == "secret-key"

    def test_rune_context_is_frozen(self) -> None:
        ctx = RuneContext(cwd="/workspace", mode="cli", agent_name="tester")
        with pytest.raises(FrozenInstanceError):
            ctx.cwd = "/new-workspace"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            ctx.mode = "gui"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            ctx.has_ui = True  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            ctx.agent_name = "other"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            ctx.api_key = "other-key"  # type: ignore[misc]

    def test_replace_creates_new_instance(self) -> None:
        original = RuneContext(cwd="/workspace", mode="cli", agent_name="tester")
        updated = replace(original, mode="gui", has_ui=True)

        assert original.mode == "cli"
        assert original.has_ui is False
        assert updated.cwd == "/workspace"
        assert updated.mode == "gui"
        assert updated.has_ui is True
        assert updated.agent_name == "tester"
        assert updated != original

    def test_equality_and_hash(self) -> None:
        ctx1 = RuneContext(cwd="/workspace", mode="cli")
        ctx2 = RuneContext(cwd="/workspace", mode="cli")
        ctx3 = RuneContext(cwd="/workspace", mode="gui")

        assert ctx1 == ctx2
        assert ctx1 != ctx3
        assert hash(ctx1) == hash(ctx2)


class TestBeforeMvgeStartData:
    def _minimal(self) -> "BeforeMvgeStartData":
        return BeforeMvgeStartData(
            base_prompt="Be helpful.",
            spell_names=["bash", "read"],
            config_dir="/cfg",
            custom_prompt="Custom.",
            agent_name="tester",
            cwd="/work",
        )

    def test_new_fields_default_to_unset(self) -> None:
        data = self._minimal()
        assert data.spells_dir is None
        assert data.system_path is None
        assert data.runes_paths == []

    def test_new_fields_accept_values(self) -> None:
        data = BeforeMvgeStartData(
            base_prompt="Be helpful.",
            spell_names=["bash"],
            config_dir="/cfg",
            custom_prompt="",
            agent_name="tester",
            cwd="/work",
            spells_dir="/cfg/spells",
            system_path="/cfg/SYSTEM.md",
            runes_paths=["/ext"],
        )
        assert data.spells_dir == "/cfg/spells"
        assert data.system_path == "/cfg/SYSTEM.md"
        assert data.runes_paths == ["/ext"]

    def test_existing_fields_unchanged(self) -> None:
        data = self._minimal()
        assert data.base_prompt == "Be helpful."
        assert data.spell_names == ["bash", "read"]
        assert data.config_dir == "/cfg"
        assert data.custom_prompt == "Custom."
        assert data.agent_name == "tester"
        assert data.cwd == "/work"
