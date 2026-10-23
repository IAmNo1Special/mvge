"""Unit tests for collapsible execution step cards."""

import pytest
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.components.step_cards import (
    render_commands_card,
    render_contemplation_card,
    render_files_card,
    render_step_card,
    render_summoning_card,
    render_worked_card,
)
from mvgeos_gui.models import (
    CommandExecution,
    ExecutionStep,
    FileExploration,
    StepType,
)
from mvgeos_gui.state import AppState


@pytest.mark.asyncio
async def test_render_worked_card(user: User) -> None:
    """Verify 'Worked for Xs' step card renders correctly."""
    step = ExecutionStep(
        step_type=StepType.WORKED,
        title="Worked for 2.5s",
        details=["Analyzed project files", "Generated code patch"],
        is_complete=True,
    )

    @ui.page("/test_worked_card")
    def page() -> None:
        render_worked_card(step)

    await user.open("/test_worked_card")
    await user.should_see("Worked for 2.5s")
    await user.should_see("Analyzed project files")


@pytest.mark.asyncio
async def test_render_files_card(user: User) -> None:
    """Verify 'Explored N files' step card renders files and line ranges."""
    step = ExecutionStep(
        step_type=StepType.FILES,
        title="Explored 2 files",
        files=[
            FileExploration(path="mvgeos_gui/app.py", lines="L1-50", operation="read"),
            FileExploration(
                path="mvgeos_gui/state.py", lines="120 lines", operation="grep"
            ),
        ],
    )

    @ui.page("/test_files_card")
    def page() -> None:
        render_files_card(step)

    await user.open("/test_files_card")
    await user.should_see("Explored 2 files")
    await user.should_see("mvgeos_gui/app.py")
    await user.should_see("L1-50")
    await user.should_see("READ")
    await user.should_see("GREP")


@pytest.mark.asyncio
async def test_render_commands_card(user: User) -> None:
    """Verify 'Ran N commands' card renders terminal prompt and output."""
    step = ExecutionStep(
        step_type=StepType.COMMANDS,
        title="Ran 1 command",
        commands=[
            CommandExecution(
                command="uv run pytest",
                output="75 passed in 2.1s",
                duration_seconds=2.1,
            )
        ],
    )

    @ui.page("/test_commands_card")
    def page() -> None:
        render_commands_card(step)

    await user.open("/test_commands_card")
    await user.should_see("Ran 1 command")
    await user.should_see("uv run pytest")
    await user.should_see("75 passed in 2.1s")


@pytest.mark.asyncio
async def test_render_step_card_dispatcher(user: User) -> None:
    """Verify render_step_card returns the correct card or None."""
    worked = ExecutionStep(step_type=StepType.WORKED, title="Worked step")
    files = ExecutionStep(step_type=StepType.FILES, title="Files step")
    commands = ExecutionStep(step_type=StepType.COMMANDS, title="Commands step")

    @ui.page("/test_dispatcher")
    def page() -> None:
        render_step_card(worked)
        render_step_card(files)
        render_step_card(commands)

    await user.open("/test_dispatcher")
    await user.should_see("Worked step")
    await user.should_see("Files step")
    await user.should_see("Commands step")


@pytest.mark.asyncio
async def test_render_worked_card_with_spell_name_and_result(user: User) -> None:
    """Verify WORKED card renders spell_name as title and result in code block."""
    step = ExecutionStep(
        step_type=StepType.WORKED,
        title="Worked for 2.5s",
        spell_name="edit",
        result="Saved edits to config.py",
        is_complete=True,
    )

    @ui.page("/test_worked_card_with_result")
    def page() -> None:
        render_worked_card(step)

    await user.open("/test_worked_card_with_result")
    await user.should_see("edit")
    await user.should_see("Saved edits to config.py")


@pytest.mark.asyncio
async def test_render_worked_card_with_params(user: User) -> None:
    """Verify WORKED card renders params when present."""
    step = ExecutionStep(
        step_type=StepType.WORKED,
        title="Worked for 1.0s",
        spell_name="edit",
        params={"path": "config.py", "content": "hello"},
        result="Saved edits to config.py",
        is_complete=True,
    )

    @ui.page("/test_worked_card_with_params")
    def page() -> None:
        render_worked_card(step)

    await user.open("/test_worked_card_with_params")
    await user.should_see("edit")
    await user.should_see("Parameters")
    await user.should_see("'path': 'config.py'")


@pytest.mark.asyncio
async def test_render_contemplation_card(user: User) -> None:
    """Verify 'Thought' contemplation card renders reasoning text."""
    thought_text = "The user said 'hey' - I should respond politely."

    @ui.page("/test_thought_card")
    def page() -> None:
        render_contemplation_card(thought_text, is_streaming=False)

    await user.open("/test_thought_card")
    await user.should_see("Contemplation")
    await user.should_see("The user said 'hey' - I should respond politely.")


@pytest.mark.asyncio
async def test_render_contemplation_card_with_state_persistence(
    user: User,
) -> None:
    """Verify thought card uses state expansion and updates state when toggled."""
    from mvgeos_gui.state import AppState

    state = AppState()
    state.set_card_expansion("thought_test_1", True)

    @ui.page("/test_thought_state_card")
    def page() -> None:
        render_contemplation_card(
            "Analyzing issue...",
            is_streaming=False,
            card_id="thought_test_1",
            state=state,
        )

    await user.open("/test_thought_state_card")
    await user.should_see("Analyzing issue...")
    assert state.is_card_expanded("thought_test_1") is True


@pytest.mark.asyncio
async def test_render_step_card_with_state_persistence(user: User) -> None:
    """Verify step card dispatcher passes card_id and state."""
    from mvgeos_gui.state import AppState

    state = AppState()
    state.set_card_expansion("step_test_1", True)
    step = ExecutionStep(
        step_type=StepType.WORKED,
        title="Worked for 1.5s",
        details=["Processed task"],
    )

    @ui.page("/test_step_state_card")
    def page() -> None:
        render_step_card(step, card_id="step_test_1", state=state)

    await user.open("/test_step_state_card")
    await user.should_see("Worked for 1.5s")
    assert state.is_card_expanded("step_test_1") is True


@pytest.mark.asyncio
async def test_render_worked_card_incomplete_and_params(user: User) -> None:
    state = AppState()
    step = ExecutionStep(
        step_type=StepType.WORKED,
        spell_name="custom_spell",
        is_complete=False,
        details=[],
        params={"key": "val"},
    )

    @ui.page("/test_worked_incomplete")
    def page() -> None:
        render_worked_card(step, card_id="c1", state=state)

    await user.open("/test_worked_incomplete")
    await user.should_see("custom_spell")
    await user.should_see("Execution completed.")
    await user.should_see("Parameters")


@pytest.mark.asyncio
async def test_render_files_card_with_long_details(user: User) -> None:
    state = AppState()
    long_details = "\n".join(f"line {i}" for i in range(30))
    step = ExecutionStep(
        step_type=StepType.FILES,
        title="",
        files=[
            FileExploration(
                path="foo.py", details=long_details, lines="L1", operation="edit"
            )
        ],
    )

    @ui.page("/test_files_long")
    def page() -> None:
        render_files_card(step, card_id="f1", state=state)

    await user.open("/test_files_long")
    await user.should_see("Explored 1 file")
    await user.should_see("more lines, expand to view")


@pytest.mark.asyncio
async def test_render_summoning_card(user: User) -> None:
    """Verify Sub-Mvge summoning card renders name, status, spell, and actions."""
    from unittest.mock import MagicMock

    state = AppState()
    state.inspect_sub_tome = MagicMock()  # type: ignore[attr-defined]
    state.switch_to_tome = MagicMock()  # type: ignore[method-assign]

    step = ExecutionStep(
        step_type=StepType.SUMMONING,
        title="Codebase Researcher",
        sub_mvge_id="researcher_1",
        child_tome_id="child_tome_123",
        spell_name="search_code",
        mana_spent=420,
        is_complete=False,
    )

    @ui.page("/test_summoning_card")
    def page() -> None:
        render_summoning_card(step, card_id="summon_1", state=state)

    await user.open("/test_summoning_card")
    await user.should_see("Codebase Researcher")
    await user.should_see("search_code")
    await user.should_see("420 Mana")
    assert user.find(marker="inspect_sub_mvge_btn")
    assert user.find(marker="open_full_tome_btn")

    user.find(marker="inspect_sub_mvge_btn").click()
    state.inspect_sub_tome.assert_called_once_with(
        "child_tome_123", "Codebase Researcher"
    )

    user.find(marker="open_full_tome_btn").click()
    state.switch_to_tome.assert_called_once_with("child_tome_123")


@pytest.mark.asyncio
async def test_render_step_card_dispatches_summoning(user: User) -> None:
    """Verify render_step_card dispatches StepType.SUMMONING to summoning card."""
    step = ExecutionStep(
        step_type=StepType.SUMMONING,
        title="Database Specialist",
        sub_mvge_id="db_spec_1",
    )

    @ui.page("/test_dispatch_summoning")
    def page() -> None:
        render_step_card(step)

    await user.open("/test_dispatch_summoning")
    await user.should_see("Database Specialist")
