from __future__ import annotations

import asyncio
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.markup import render as render_markup

from mvge.cli.commands.repl import StreamRenderer
from mvge.cli.commands.tui import TranscriptControl, TuiSink
from mvge.core.events import (
    MvgeEvent,
    MvgeEventType,
)


def _message_event(text: str) -> MvgeEvent:
    return MvgeEvent(type=MvgeEventType.MESSAGE_UPDATE, data={"text": text})


def _tool_start_event(name: str, args: dict[str, Any]) -> MvgeEvent:
    return MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={"spellName": name, "arguments": args},
    )


def _tool_end_event(result: Any = None, error: str | None = None) -> MvgeEvent:
    data: dict[str, Any] = {}
    if result is not None:
        data["result"] = result
    if error is not None:
        data["error"] = error
    return MvgeEvent(type=MvgeEventType.SPELL_CASTING_END, data=data)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestTuiSink:
    def test_write_accumulates_partial_lines(self) -> None:
        sink = TuiSink()
        sink.write("hello")
        assert sink.line_dirty is True
        sink.write(" world\n")
        assert sink.line_dirty is False
        assert sink._entries[0].text is not None
        assert sink._entries[0].text.plain == "hello world"

    def test_write_newline_adds_blank_separator(self) -> None:
        sink = TuiSink()
        sink.write("\n")
        assert sink._entries[0].text is not None
        assert sink._entries[0].text.plain == ""

    def test_narration_buffered_until_finalize(self) -> None:
        sink = TuiSink()
        sink.stream_narration("one", "one")
        assert sink._pending_md == "one"
        assert sink._entries == []
        sink.finalize_narration()
        assert sink._pending_md is None
        assert len(sink._entries) == 1
        assert sink._entries[0].kind == "md"
        assert sink._entries[0].md == "one"

    def test_finalize_narration_noop_without_pending(self) -> None:
        sink = TuiSink()
        sink.finalize_narration()
        assert sink._entries == []

    def test_renderer_events_build_transcript(self) -> None:
        sink = TuiSink()
        renderer = StreamRenderer(sink)
        renderer.on_message_update(_message_event("Let me **check**.\n"))
        renderer.on_tool_start(_tool_start_event("bash", {"command": "ls"}))
        renderer.on_tool_end(_tool_end_event(result="a.txt"))
        renderer.finish()
        kinds = [e.kind for e in sink._entries]
        assert "md" in kinds
        text_kinds = [e for e in sink._entries if e.kind == "text"]
        assert any(
            e.text is not None and e.text.plain.startswith("  [bash]")
            for e in text_kinds
        )
        assert any(e.text is not None and e.text.plain == "  a.txt" for e in text_kinds)
        assert any(e.text is not None and "Took" in e.text.plain for e in text_kinds)

    def test_render_to_ansi_pads_cards_and_bolds_markdown(self) -> None:
        sink = TuiSink()
        renderer = StreamRenderer(sink)
        renderer.on_message_update(_message_event("Hello **world**\n"))
        renderer.on_tool_start(_tool_start_event("bash", {"command": "ls"}))
        renderer.on_tool_end(_tool_end_event(result="a.txt"))
        renderer.finish()
        lines = sink.render_to_ansi(30)
        joined = "\n".join(lines)
        assert "\x1b[1mworld\x1b[0m" in joined
        assert "\x1b[1;48;2;40;40;50m[bash]\x1b[0m" in joined
        card_lines = [ln for ln in lines if "[bash]" in ln or "a.txt" in ln]
        assert card_lines
        for ln in card_lines:
            assert len(_strip_ansi(ln)) == 30

    def test_render_to_ansi_truncates_long_lines(self) -> None:
        sink = TuiSink()
        sink.write_card_line(__import__("rich").text.Text("x" * 50), "#282832")
        lines = sink.render_to_ansi(20)
        assert len(_strip_ansi(lines[0])) == 20
        assert _strip_ansi(lines[0]).endswith("…")

    def test_version_bumps_on_mutation(self) -> None:
        sink = TuiSink()
        v0 = sink.version
        sink.write("a\n")
        assert sink.version > v0

    def test_print_transcript(self, capsys: pytest.CaptureFixture[str]) -> None:
        sink = TuiSink()
        renderer = StreamRenderer(sink)
        renderer.on_message_update(_message_event("note **bold**\n"))
        renderer.on_tool_start(_tool_start_event("list", {"cwd": "."}))
        renderer.on_tool_end(_tool_end_event(result="b.txt"))
        renderer.finish()
        sink.print_transcript()
        out = capsys.readouterr().out
        assert "note" in out
        assert "[list]" in out
        assert "b.txt" in out


class TestTranscriptControl:
    def _render(self, sink: TuiSink) -> TranscriptControl:
        return TranscriptControl(sink)

    def test_follow_pins_to_end(self) -> None:
        sink = TuiSink()
        for i in range(10):
            sink.write(f"line {i}\n")
        ctrl = self._render(sink)
        top = ctrl._update_scroll(10, 4)
        assert ctrl.following is True
        assert top == 6

    def test_scroll_up_disables_follow(self) -> None:
        sink = TuiSink()
        for i in range(10):
            sink.write(f"line {i}\n")
        ctrl = self._render(sink)
        ctrl._update_scroll(10, 4)
        ctrl.scroll_up(3)
        assert ctrl.following is False
        assert ctrl.scroll_top == 3
        ctrl._update_scroll(10, 4)
        assert ctrl.scroll_top == 3

    def test_scroll_down_to_end_reenables_follow(self) -> None:
        sink = TuiSink()
        for i in range(10):
            sink.write(f"line {i}\n")
        ctrl = self._render(sink)
        ctrl._update_scroll(10, 4)
        ctrl.scroll_up(9)
        ctrl._update_scroll(10, 4)
        assert ctrl.following is False
        ctrl.scroll_to_bottom()
        assert ctrl.following is True

    def test_scroll_to_top(self) -> None:
        sink = TuiSink()
        for i in range(10):
            sink.write(f"line {i}\n")
        ctrl = self._render(sink)
        ctrl._update_scroll(10, 4)
        ctrl.scroll_to_top()
        assert ctrl.following is False
        assert ctrl.scroll_top == 0

    def test_create_content_shows_visible_window(self) -> None:
        sink = TuiSink()
        for i in range(10):
            sink.write(f"line {i}\n")
        ctrl = self._render(sink)
        content = ctrl.create_content(40, 3)
        assert content.line_count == 3
        visible = [
            _strip_ansi(tuple(frags) and "".join(t for _, t in frags))
            for frags in [content.get_line(i) for i in range(3)]
        ]
        assert "line 9" in "".join(visible)

    def test_content_caches_by_version_and_width(self) -> None:
        sink = TuiSink()
        sink.write("hello\n")
        ctrl = self._render(sink)
        a = ctrl._get_lines(20)
        b = ctrl._get_lines(20)
        assert a is b
        c = ctrl._get_lines(30)
        assert c is not a


class TestCommandDispatcherOut:
    @pytest.mark.asyncio
    async def test_out_routes_output(self) -> None:
        from mvge.agent import Mvge
        from mvge.cli.commands.dispatcher import CliCommandDispatcher

        agent = Mvge(api_key="test-key")
        captured: list[str] = []
        dispatcher = CliCommandDispatcher(agent, MagicMock(), out=captured.append)
        should_exit = await dispatcher.dispatch("/help")
        assert should_exit is False
        assert any("/quit" in line for line in captured)


class FakeAgent:
    def __init__(self, tome_id: str = "abcd1234efgh") -> None:
        self.tome_id = tome_id
        self._model_id = "nvidia/nemotron-3-ultra-550b-a55b:free"
        self._contemplation_level = "medium"
        self.queue_mode = "one-at-a-time"
        self._initialized = False
        self.closed = False
        self.initialize_calls = 0
        self.run_calls: list[str] = []
        self.error: Exception | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def contemplation_level(self) -> str:
        return self._contemplation_level

    @property
    def enabled_spells(self) -> list[str]:
        return ["bash", "read", "write"]

    @property
    def available_spells(self) -> list[str]:
        return ["bash", "read", "write"]

    @property
    def registered_providers(self) -> list[str]:
        return ["openrouter"]

    def set_enabled_spells(self, spell_names: Any) -> None:
        pass

    async def reset_session(self, *, resume_tome_id: str | None = None) -> None:
        await self.close()
        self._initialized = False
        await self.initialize()

    def on(self, name: str, cb: Any) -> Any:
        return lambda: None

    def steer(self, text: str) -> None:
        self.run_calls.append(f"steer:{text}")

    def follow_up(self, text: str) -> None:
        self.run_calls.append(f"follow_up:{text}")

    async def run(self, text: str) -> None:
        self.run_calls.append(text)
        if self.error is not None:
            raise self.error

    async def close(self) -> None:
        self.closed = True

    async def initialize(self) -> None:
        self._initialized = True
        self.initialize_calls += 1

    async def switch_model(self, model: str) -> None:
        self._model_id = model


class FakeRegistry:
    async def refresh(self, force_refresh: bool = False) -> int:
        return 0

    def get(self, model_id: str) -> object | None:
        return object() if model_id else None

    def list_all(self) -> list[Any]:
        return []

    def get_supported_contemplation_levels(self, model_id: str) -> list[str]:
        return []


class TestTuiApp:
    def _app(
        self, agent: FakeAgent, sink: TuiSink | None = None, output: Any = None
    ) -> Any:
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from mvge.cli.commands.tui import TuiApp

        sink = sink or TuiSink()
        out = output if output is not None else DummyOutput()
        with create_pipe_input() as pin:
            return TuiApp(
                agent,
                sink,
                FakeRegistry(),
                StreamRenderer(sink),
                input=pin,
                output=out,
            )

    def test_app_constructs(self) -> None:
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from mvge.cli.commands.tui import TuiApp

        sink = TuiSink()
        agent = FakeAgent()
        with create_pipe_input() as pin:
            app = TuiApp(
                agent,
                sink,
                FakeRegistry(),
                StreamRenderer(sink),
                input=pin,
                output=DummyOutput(),
            )
        assert app.application is not None
        with patch(
            "mvge.cli.commands.tui.fit_footer",
            side_effect=lambda items, width: items,
        ):
            footer = app._footer_text()
            assert any("nvidia" in text for _, text in footer)

    def test_footer_shows_working_while_busy(self) -> None:
        app = self._app(FakeAgent())
        assert not any("working" in t for _, t in app._footer_text())
        app._busy = True
        assert any("working" in t for _, t in app._footer_text())

    def test_out_writes_card_line(self) -> None:
        sink = TuiSink()
        app = self._app(FakeAgent(), sink)
        app._out("[red]boom[/red]")
        assert len(sink._entries) == 1
        entry = sink._entries[0]
        assert entry.kind == "text"
        assert "boom" in (entry.text or "").plain

    def test_accept_rejects_empty(self) -> None:
        app = self._app(FakeAgent())
        app._buffer.text = "   "
        assert app._on_accept(app._buffer) is False
        assert app._task is None

    def test_accept_busy_queues_input(self) -> None:
        sink = TuiSink()
        agent = FakeAgent()
        app = self._app(agent, sink)
        app._busy = True
        app._buffer.text = "hello"
        assert app._on_accept(app._buffer) is True
        assert app._task is None
        assert any("hello" in (e.text or "").plain for e in sink._entries)

    @pytest.mark.asyncio
    async def test_accept_runs_turn(self) -> None:
        agent = FakeAgent()
        app = self._app(agent)
        app._buffer.text = "hi"
        assert app._on_accept(app._buffer) is True
        assert app._task is not None
        await app._task
        assert agent.run_calls == ["hi"]
        assert app._busy is False
        assert app._task is None

    @pytest.mark.asyncio
    async def test_run_turn_error_renders_notice(self) -> None:
        agent = FakeAgent()
        agent.error = RuntimeError("nope")
        sink = TuiSink()
        app = self._app(agent, sink)
        app._buffer.text = "hi"
        app._on_accept(app._buffer)
        assert app._task is not None
        await app._task
        plain = "\n".join((e.text or "").plain for e in sink._entries)
        assert "Error: nope" in plain

    @pytest.mark.asyncio
    async def test_run_turn_cancelled_renders_notice(self) -> None:
        class CancelAgent(FakeAgent):
            async def run(self, text: str) -> None:
                raise asyncio.CancelledError

        sink = TuiSink()
        app = self._app(CancelAgent(), sink)
        app._buffer.text = "hi"
        app._on_accept(app._buffer)
        assert app._task is not None
        await app._task
        plain = "\n".join((e.text or "").plain for e in sink._entries)
        assert "Interrupted" in plain

    @pytest.mark.asyncio
    async def test_slash_new_session(self) -> None:
        agent = FakeAgent()
        app = self._app(agent)
        app._buffer.text = "/new"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        assert agent.closed is True
        assert agent.initialize_calls == 1
        plain = "\n".join((e.text or "").plain for e in app.sink._entries)
        assert "New tome" in plain

    @pytest.mark.asyncio
    async def test_slash_model_switch(self) -> None:
        agent = FakeAgent()
        app = self._app(agent)
        app._buffer.text = "/model nvidia/nemotron-3-ultra-550b-a55b:free"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        assert agent._model_id == "nvidia/nemotron-3-ultra-550b-a55b:free"
        plain = "\n".join((e.text or "").plain for e in app.sink._entries)
        assert "Model switched" in plain

    @pytest.mark.asyncio
    async def test_slash_unknown_model_renders_error(self) -> None:
        class EmptyRegistry(FakeRegistry):
            def get(self, model_id: str) -> object | None:
                return None

        agent = FakeAgent()
        app = self._app(agent)
        app.registry = EmptyRegistry()
        app._buffer.text = "/model bogus"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        assert agent._model_id == "nvidia/nemotron-3-ultra-550b-a55b:free"
        plain = "\n".join((e.text or "").plain for e in app.sink._entries)
        assert "Unknown model" in plain

    @pytest.mark.asyncio
    async def test_slash_switch_model_error_renders(self) -> None:
        class FailSwitch(FakeAgent):
            async def switch_model(self, model: str) -> None:
                raise ValueError("bad model")

        agent = FailSwitch()
        app = self._app(agent)
        app._buffer.text = "/model nvidia/nemotron-3-ultra-550b-a55b:free"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        plain = "\n".join((e.text or "").plain for e in app.sink._entries)
        assert "bad model" in plain

    @pytest.mark.asyncio
    async def test_slash_refresh_models(self) -> None:
        agent = FakeAgent()
        app = self._app(agent)
        app._buffer.text = "/refresh-models"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        plain = "\n".join((e.text or "").plain for e in app.sink._entries)
        assert "Models refreshed" in plain

    @pytest.mark.asyncio
    async def test_slash_exit(self) -> None:
        app = self._app(FakeAgent())
        exits: list[Any] = []
        app.application.exit = lambda *a, **k: exits.append(1)  # type: ignore[method-assign]
        app._buffer.text = "/quit"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        assert exits == [1]

    @pytest.mark.asyncio
    async def test_slash_new_session_initialize_error(self) -> None:
        class FailInit(FakeAgent):
            async def initialize(self) -> None:
                raise ValueError("no api key")

        agent = FailInit()
        app = self._app(agent)
        app._buffer.text = "/new"
        app._on_accept(app._buffer)
        assert app._slash_task is not None
        await app._slash_task
        plain = "\n".join((e.text or "").plain for e in app.sink._entries)
        assert "no api key" in plain

    def test_print_transcript_truncates_bg_card(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from rich.text import Text

        sink = TuiSink()
        sink.write_card_line(Text("x" * 200), "#282832")
        sink.print_transcript()
        out = _strip_ansi(capsys.readouterr().out).strip()
        assert len(out) < 200
        assert out.endswith("…")

    def test_redraw_callback_invoked_on_write(self) -> None:
        sink = TuiSink()
        calls: list[int] = []
        sink.set_redraw_cb(lambda: calls.append(1))
        sink.write("a\n")
        sink.write_card_line(render_markup("x"), None)
        sink.stream_narration("m", "m")
        sink.finalize_narration()
        assert len(calls) >= 3

    def test_write_merges_partials(self) -> None:
        sink = TuiSink()
        sink.write("pre")
        sink.write("suffix\n")
        assert any(
            e.text is not None and e.text.plain == "presuffix" for e in sink._entries
        )

    def test_card_line_resets_partial_state(self) -> None:
        sink = TuiSink()
        sink.write("pre")
        sink.write_card_line(render_markup("card"), None)
        sink.write("suffix\n")
        plains = [(e.text or "").plain for e in sink._entries]
        assert plains == ["pre", "card", "suffix"]

    def test_render_to_ansi_splits_newlines_in_card_line(self) -> None:
        sink = TuiSink()
        sink.write_card_line(render_markup("Rate limited\nby the provider."), None)
        lines = sink.render_to_ansi(80)
        assert "Rate limited" in _strip_ansi(lines[0])
        assert "by the provider." in _strip_ansi(lines[1])
        for line in lines:
            assert "\n" not in _strip_ansi(line)

    def test_write_after_md_entry_starts_new_text(self) -> None:
        sink = TuiSink()
        sink.write("partial")
        sink.stream_narration("**md**", "**md**")
        sink.finalize_narration()
        sink.write("tail\n")
        kinds = [e.kind for e in sink._entries]
        assert kinds == ["text", "md", "text"]

    def test_scroll_down_clamp_reenables_follow(self) -> None:
        sink = TuiSink()
        for i in range(10):
            sink.write(f"line {i}\n")
        ctrl = TranscriptControl(sink)
        ctrl._update_scroll(10, 4)
        ctrl.scroll_up(9)
        ctrl._update_scroll(10, 4)
        assert ctrl.following is False
        assert ctrl.scroll_top == 0
        ctrl.scroll_down(6)
        assert ctrl.scroll_top == 6
        assert ctrl.following is False
        ctrl._update_scroll(10, 4)
        assert ctrl.following is True

    @pytest.mark.asyncio
    async def test_tui_on_accept_queues_message_when_busy(self) -> None:
        from prompt_toolkit.output import DummyOutput

        from mvge.cli.commands.tui import TuiApp

        mock_agent = MagicMock()
        sink = TuiSink()
        registry = MagicMock()
        renderer = MagicMock()
        app = TuiApp(mock_agent, sink, registry, renderer, output=DummyOutput())
        app._busy = True

        app._buffer.text = "follow up prompt"
        handled = app._on_accept(app._buffer)

        assert handled is True
        mock_agent.steer.assert_called_once_with("follow up prompt")
        assert any(
            "follow up prompt" in (e.text.plain if e.text else "")
            for e in sink._entries
        )

    def test_footer_text_appends_mode_before_fit_footer_and_uses_terminal_width(
        self,
    ) -> None:
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.output import DummyOutput

        agent = FakeAgent()
        agent.tome_id = "1234567890"
        agent._model_id = "very-long-model-provider-name/model-name-extra-long:latest"
        sink = TuiSink()

        class CustomOutput(DummyOutput):
            def get_size(self) -> Size:
                return Size(rows=24, columns=50)

        app = self._app(agent, sink, output=CustomOutput())
        footer = app._footer_text()
        plain_text = "".join(text for _, text in footer)
        assert len(plain_text) <= 50

    def test_footer_text_working_status_truncation(self) -> None:
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.output import DummyOutput

        agent = FakeAgent()
        agent.tome_id = "1234567890"
        agent._model_id = "very-long-model-provider-name/model-name-extra-long:latest"
        sink = TuiSink()

        class CustomOutput(DummyOutput):
            def get_size(self) -> Size:
                return Size(rows=24, columns=60)

        app = self._app(agent, sink, output=CustomOutput())
        app._busy = True
        footer = app._footer_text()
        plain_text = "".join(text for _, text in footer)
        assert len(plain_text) <= 60

    def test_fit_footer_dynamic_truncation_multiple_items_narrow_width(self) -> None:
        from mvge.cli.formatting import fit_footer

        items = [
            ("bold", " ~/mvge (main)"),
            ("dim", "  session 12345678"),
            ("", "  mana 0"),
            ("", "  nvidia/nemotron-3-ultra-550b-a55b:free • medium"),
            ("", "  (working • mode: one-at-a-time)"),
        ]
        # Total length of items is 119
        fitted_50 = fit_footer(items, 50)
        assert sum(len(text) for _, text in fitted_50) <= 50

        fitted_30 = fit_footer(items, 30)
        assert sum(len(text) for _, text in fitted_30) <= 30

        fitted_15 = fit_footer(items, 15)
        assert sum(len(text) for _, text in fitted_15) <= 15


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
async def test_run_tui_no_console_buffer(
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mvge.cli.commands.repl import NoConsoleScreenBufferError
    from mvge.cli.commands.tui import run_tui

    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_create_agent.return_value = mock_agent

    mock_app = MagicMock()
    mock_app.run = AsyncMock(side_effect=NoConsoleScreenBufferError())
    mock_tui_app_cls.return_value = mock_app

    await run_tui(api_key="sk-or-test-key")

    captured = capsys.readouterr()
    assert "TUI mode requires a Win32 console screen buffer" in captured.out
    mock_agent.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_slash_during_busy_does_not_hijack_turn_task(self) -> None:
        """A slash typed during a busy turn keeps its own handle.

        The turn's task handle must stay intact so Ctrl-C still cancels the
        running turn instead of the slash command.
        """
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowAgent(FakeAgent):
            async def run(self, text: str) -> None:
                entered.set()
                await release.wait()

        app = self._app(SlowAgent())
        app.application.exit = lambda *a, **k: None  # type: ignore[method-assign]
        app._buffer.text = "hi"
        assert app._on_accept(app._buffer) is True
        await asyncio.wait_for(entered.wait(), timeout=5)
        turn_task = app._task
        assert turn_task is not None

        app._buffer.text = "/quit"
        assert app._on_accept(app._buffer) is True
        assert app._slash_task is not None
        assert app._task is turn_task

        release.set()
        await asyncio.wait_for(asyncio.gather(turn_task, app._slash_task), timeout=5)
        assert app._busy is False
