from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.cli.commands.repl import (
    NoConsoleScreenBufferError,
    StreamRenderer,
    _read_fallback_prompt,
    _StreamFilter,
    run_repl,
)
from mvge.core.events import MvgeEvent, MvgeEventType


class TestChannelTagStripping:
    def test_channel_tag_stripped_from_content(self) -> None:
        f = _StreamFilter()
        assert f.feed("Let me list <channel|list>\n") == "Let me list\n"

    def test_channel_tag_alone_yields_empty(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|list>\n") == ""

    def test_empty_channel_tag_stripped(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|>\n") == ""

    def test_channel_tag_stripped_before_newline(self) -> None:
        f = _StreamFilter()
        assert f.feed("text<channel|commentary>\n") == "text\n"

    def test_channel_tag_after_content(self) -> None:
        f = _StreamFilter()
        assert f.feed("done <channel|>\n") == "done\n"

    def test_channel_tag_partial_across_chunks(self) -> None:
        f = _StreamFilter()
        assert f.feed("see <channel|") == "see "
        assert f.feed("list>\n") == ""
        assert f.feed("Done\n") == "Done\n"

    def test_multiple_channel_tags_on_one_line(self) -> None:
        f = _StreamFilter()
        result = f.feed("start <channel|x> middle <channel|y> end\n")
        assert result == "start  middle  end\n"

    def test_channel_tag_case_insensitive(self) -> None:
        f = _StreamFilter()
        assert f.feed("text <CHANNEL|LIST>\n") == "text\n"


class TestReasoningChannelSuppression:
    def test_reasoning_channel_enters_suppression(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("secret thought\n") == ""
        assert f.feed("<channel|list>\n") == ""
        assert f.feed("Here is the answer\n") == "Here is the answer\n"

    def test_thinking_channel_enters_suppression(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|thinking>\n") == ""
        assert f.feed("thinking hard\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("result\n") == "result\n"

    def test_thought_channel_enters_suppression(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|thought>\n") == ""
        assert f.feed("pondering\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("result\n") == "result\n"

    def test_commentary_channel_exits_reasoning(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("hidden\n") == ""
        assert f.feed("<channel|commentary>\n") == ""
        assert f.feed("visible\n") == "visible\n"

    def test_empty_channel_exits_reasoning(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("hidden\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("visible\n") == "visible\n"

    def test_list_channel_exits_reasoning(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|thinking>\n") == ""
        assert f.feed("hidden\n") == ""
        assert f.feed("<channel|list>\n") == ""
        assert f.feed("visible\n") == "visible\n"

    def test_reasoning_channel_with_inline_text_suppressed(self) -> None:
        f = _StreamFilter()
        assert f.feed("prefix <channel|reasoning>body\n") == "prefix\n"
        assert f.feed("hidden\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("after\n") == "after\n"

    def test_open_and_close_on_same_line_not_stuck(self) -> None:
        f = _StreamFilter()
        assert f.feed("x<channel|reasoning>y<channel|list>z\n") == "xz\n"
        assert f.feed("leak check\n") == "leak check\n"

    def test_close_tag_inline_suppresses_reasoning_prefix(self) -> None:
        f = _StreamFilter()
        f.feed("<channel|reasoning>\n")
        assert f.feed("secret <channel|list>visible\n") == "visible\n"
        assert f.feed("more after close\n") == "more after close\n"

    def test_close_tag_only_line_does_not_leak(self) -> None:
        f = _StreamFilter()
        f.feed("<channel|reasoning>\n")
        assert f.feed("<channel|list>\n") == ""
        assert f.feed("visible now\n") == "visible now\n"


class TestMarkdownHeadingPreservation:
    def test_thinking_heading_lowercase_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("# thinking\n") == "# thinking\n"

    def test_thought_heading_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("# Thought\n") == "# Thought\n"

    def test_reasoning_heading_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("# Reasoning\n") == "# Reasoning\n"

    def test_thinking_heading_no_space_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("#thinking\n") == "#thinking\n"

    def test_markdown_heading_after_reasoning_channel_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("reasoning content\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("# Summary\n") == "# Summary\n"

    def test_markdown_heading_inside_reasoning_suppressed(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("# Summary\n") == ""
        assert f.feed("after heading\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("visible\n") == "visible\n"


class TestStandaloneWordPreservation:
    def test_standalone_thought_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("thought\n") == "thought\n"

    def test_standalone_thinking_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("thinking\n") == "thinking\n"

    def test_standalone_reasoning_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("reasoning\n") == "reasoning\n"


class TestNormalContent:
    def test_normal_markdown_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("# Title\n") == "# Title\n"
        assert f.feed("body\n") == "body\n"

    def test_empty_lines_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("a\n\nb\n") == "a\n\nb\n"

    def test_multiline_content(self) -> None:
        f = _StreamFilter()
        text = "Line1\nLine2\nLine3\n"
        assert f.feed(text) == text

    def test_inline_channel_tag_in_markdown(self) -> None:
        f = _StreamFilter()
        result = f.feed("Some text <channel|list> more text\n")
        assert result == "Some text  more text\n"


class TestFlush:
    def test_flush_pending(self) -> None:
        f = _StreamFilter()
        assert f.feed("Hello") == "Hello"
        assert f.flush() == ""

    def test_flush_suppresses_reasoning(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("suppressed text") == ""
        assert f.flush() == ""

    def test_flush_clean_line(self) -> None:
        f = _StreamFilter()
        assert f.feed("see <channel|list>") == "see "
        assert f.flush() == ""

    def test_flush_incomplete_channel_tag(self) -> None:
        f = _StreamFilter()
        f.feed("before <channel|")
        result = f.flush()
        assert result == "<channel|"

    def test_flush_whitespace_leftover(self) -> None:
        f = _StreamFilter()
        f.feed("   ")
        assert f.flush() == ""

    def test_flush_markdown_preserved(self) -> None:
        f = _StreamFilter()
        assert f.feed("# thinking") == "# thinking"
        assert f.flush() == ""


class TestReset:
    def test_reset_clears_reasoning_state(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        f.reset()
        assert f.feed("clean\n") == "clean\n"

    def test_reset_clears_pending(self) -> None:
        f = _StreamFilter()
        f.feed("partial")
        f.reset()
        assert f.feed("rest\n") == "rest\n"


class TestStreamingEdgeCases:
    def test_thinking_heading_then_real_channel(self) -> None:
        f = _StreamFilter()
        assert f.feed("# thinking\n") == "# thinking\n"
        assert f.feed("This is real content\n") == "This is real content\n"
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("suppressed\n") == ""
        assert f.feed("<channel|list>\n") == ""
        assert f.feed("back to normal\n") == "back to normal\n"

    def test_channel_tag_escaped_angle_brackets(self) -> None:
        f = _StreamFilter()
        assert f.feed("x < y and z > w\n") == "x < y and z > w\n"

    def test_nested_looking_tags_not_treated_as_channels(self) -> None:
        f = _StreamFilter()
        assert f.feed("<div>not a channel</div>\n") == "<div>not a channel</div>\n"

    def test_reasoning_channel_case_insensitive(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|REASONING>\n") == ""
        assert f.feed("hidden\n") == ""
        assert f.feed("<channel|LIST>\n") == ""
        assert f.feed("visible\n") == "visible\n"

    def test_nested_reasoning_channel_keeps_suppression(self) -> None:
        f = _StreamFilter()
        assert f.feed("<channel|reasoning>\n") == ""
        assert f.feed("<channel|thinking>\n") == ""
        assert f.feed("still hidden\n") == ""
        assert f.feed("<channel|list>\n") == ""
        assert f.feed("visible\n") == "visible\n"

    def test_reasoning_tag_in_unterminated_chunk_keeps_prefix(self) -> None:
        f = _StreamFilter()
        assert f.feed("see <channel|reasoning>body") == "see "
        assert f.feed("hidden\n") == ""
        assert f.feed("<channel|>\n") == ""
        assert f.feed("after\n") == "after\n"


class TestNoConsoleBufferFallback:
    @pytest.mark.asyncio
    async def test_read_fallback_prompt_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "hello world")
        result = await _read_fallback_prompt()
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_read_fallback_prompt_keyboard_interrupt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_ki(prompt: str) -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_ki)
        result = await _read_fallback_prompt()
        assert result is None

    @pytest.mark.asyncio
    async def test_read_fallback_prompt_eof_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_eof(prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        result = await _read_fallback_prompt()
        assert result is None

    @pytest.mark.asyncio
    @patch("mvge.cli.commands.repl._create_agent")
    @patch("mvge.cli.commands.repl.PromptSession")
    async def test_run_repl_instantiation_no_console_buffer(
        self,
        mock_session_cls: MagicMock,
        mock_create_agent: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_agent = AsyncMock()
        mock_agent.on = MagicMock(return_value=lambda: None)
        mock_agent.tome_id = "test-tome-id"
        mock_agent._model_id = "nvidia/nemotron"
        mock_create_agent.return_value = mock_agent

        # PromptSession creation raises NoConsoleScreenBufferError
        mock_session_cls.side_effect = NoConsoleScreenBufferError()

        inputs = ["/help", "/quit"]
        input_iter = iter(inputs)

        def mock_input(prompt: str = "") -> str:
            return next(input_iter)

        monkeypatch.setattr("builtins.input", mock_input)

        await run_repl(api_key="sk-or-test-key")

        mock_agent.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("mvge.cli.commands.repl._create_agent")
    @patch("mvge.cli.commands.repl._read_initial_prompt")
    @patch("mvge.cli.commands.repl.PromptSession")
    async def test_run_repl_lazy_prompt_no_console_buffer(
        self,
        mock_session_cls: MagicMock,
        mock_read_initial: MagicMock,
        mock_create_agent: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_agent = AsyncMock()
        mock_agent.on = MagicMock(return_value=lambda: None)
        mock_agent.tome_id = "test-tome-id"
        mock_agent._model_id = "nvidia/nemotron"
        mock_create_agent.return_value = mock_agent

        mock_session_cls.return_value = MagicMock()
        # _read_initial_prompt raises NoConsoleScreenBufferError on first prompt call
        mock_read_initial.side_effect = NoConsoleScreenBufferError()

        inputs = ["/quit"]
        input_iter = iter(inputs)

        def mock_input(prompt: str = "") -> str:
            return next(input_iter)

        monkeypatch.setattr("builtins.input", mock_input)

        await run_repl(api_key="sk-or-test-key")

        mock_agent.close.assert_awaited_once()


class TestStreamRendererFinishError:
    def _renderer_with_contemplation(self) -> tuple[MagicMock, StreamRenderer]:
        sink = MagicMock()
        sink.line_dirty = False
        renderer = StreamRenderer(sink=sink)
        renderer.on_message_update(
            MvgeEvent(
                type=MvgeEventType.MESSAGE_UPDATE,
                data={"kind": "contemplation", "text": "Thinking probe"},
            )
        )
        return sink, renderer

    def test_finish_error_finalizes_narration(self) -> None:
        sink, renderer = self._renderer_with_contemplation()
        renderer.finish(error=True)
        assert sink.finalize_narration.call_count == 1

    def test_finish_error_streams_nothing_new(self) -> None:
        sink, renderer = self._renderer_with_contemplation()
        streamed = sink.stream_narration.call_count
        renderer.finish(error=True)
        assert sink.stream_narration.call_count == streamed

    def test_finish_error_then_reset_finalizes_once(self) -> None:
        sink, renderer = self._renderer_with_contemplation()
        renderer.finish(error=True)
        renderer.reset()
        assert sink.finalize_narration.call_count == 1

    def test_finish_success_still_finalizes(self) -> None:
        sink, renderer = self._renderer_with_contemplation()
        renderer.finish()
        assert sink.finalize_narration.call_count == 1


class TestStreamRendererSpellMethods:
    def test_spell_cast_lifecycle(self) -> None:
        sink = MagicMock()
        sink.line_dirty = False
        renderer = StreamRenderer(sink=sink)

        start_event = MvgeEvent(
            type="spell_casting_start",
            data={"spellName": "read_file", "arguments": {"path": "foo.py"}},
        )
        renderer.on_spell_start(start_event)
        assert renderer._spell_cast_line_open is True
        assert renderer._spell_cast_start_time is not None

        end_event = MvgeEvent(
            type="spell_casting_end",
            data={"spellName": "read_file", "result": "file contents"},
        )
        renderer.on_spell_end(end_event)
        assert renderer._spell_cast_line_open is False
        assert renderer._spell_cast_start_time is None
        assert renderer._tool_line_open is False


class TestCtrlCAbortsBeforeCancelling:
    """Ctrl-C on a running turn must fire the abort signal before
    cancelling the task (the RPC precedent): cancelling alone leaves a
    wait:false background child running to completion."""

    @pytest.mark.asyncio
    @patch("mvge.cli.commands.repl._create_agent")
    @patch("mvge.cli.commands.repl.PromptSession")
    async def test_sigint_aborts_agent_before_cancelling_task(
        self,
        mock_session_cls: MagicMock,
        mock_create_agent: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        class FakeSignal:
            SIGINT = 2

            def signal(self, sig: int, handler: object) -> None:
                captured["handler"] = handler

        order: list[str] = []
        run_started = asyncio.Event()

        async def fake_run(text: str) -> None:
            run_started.set()
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                order.append("cancelled")
                raise

        mock_agent = AsyncMock()
        mock_agent.on = MagicMock(return_value=lambda: None)
        mock_agent.tome_id = "test-tome-id"
        mock_agent._model_id = "fake/parent"
        mock_agent.environment = MagicMock()
        mock_agent.environment.diagnostics = []
        mock_agent.runner = None
        mock_agent.run = AsyncMock(side_effect=fake_run)
        mock_agent.close = AsyncMock()
        mock_agent.abort = MagicMock(side_effect=lambda: order.append("abort"))
        mock_create_agent.return_value = mock_agent
        mock_session_cls.side_effect = NoConsoleScreenBufferError()

        inputs = iter(["hello", "/quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

        with patch("mvge.cli.commands.repl.signal", FakeSignal()):
            repl_task = asyncio.create_task(run_repl(api_key="test-key"))
            await asyncio.wait_for(run_started.wait(), timeout=10.0)
            assert "handler" in captured, "SIGINT handler was not installed"
            handler = captured["handler"]
            assert callable(handler)
            handler(2, None)
            await asyncio.wait_for(repl_task, timeout=10.0)

        assert order == ["abort", "cancelled"]
        mock_agent.abort.assert_called_once()
