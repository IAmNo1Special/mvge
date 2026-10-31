from __future__ import annotations

from unittest.mock import MagicMock

from prompt_toolkit.output import DummyOutput

from mvge.cli.commands.tui import TuiApp, TuiSink


def _app(agent: MagicMock) -> TuiApp:
    return TuiApp(agent, TuiSink(), MagicMock(), MagicMock(), output=DummyOutput())


def test_handle_interrupt_aborts_before_cancelling_turn() -> None:
    """Ctrl-C on a running turn must fire the abort signal before
    cancelling the task (the RPC precedent): cancelling alone leaves a
    wait:false background child running to completion."""
    order: list[str] = []
    agent = MagicMock()
    agent.abort = MagicMock(side_effect=lambda: order.append("abort"))
    task = MagicMock()
    task.done.return_value = False
    task.cancel = MagicMock(side_effect=lambda: order.append("cancelled"))

    app = _app(agent)
    app._task = task
    event = MagicMock()
    app._handle_interrupt(event)

    assert order == ["abort", "cancelled"]
    agent.abort.assert_called_once()
    task.cancel.assert_called_once()
    event.app.exit.assert_not_called()


def test_handle_interrupt_idle_exits_without_abort() -> None:
    agent = MagicMock()
    app = _app(agent)
    app._task = None
    app._slash_task = None
    event = MagicMock()
    app._handle_interrupt(event)

    event.app.exit.assert_called_once()
    agent.abort.assert_not_called()


def test_handle_interrupt_slash_task_cancels_without_abort() -> None:
    """A slash command is not an agent turn: interrupting one cancels it
    only and must not fire the agent abort signal."""
    agent = MagicMock()
    app = _app(agent)
    app._task = None
    slash_task = MagicMock()
    slash_task.done.return_value = False
    app._slash_task = slash_task
    event = MagicMock()
    app._handle_interrupt(event)

    slash_task.cancel.assert_called_once()
    agent.abort.assert_not_called()
    event.app.exit.assert_not_called()
