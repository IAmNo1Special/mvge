from __future__ import annotations

import asyncio
import contextlib
import io
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import render as render_markup
from rich.text import Text

from mvge.agent.protocol import AgentFactory, MvgeAgent
from mvge.cli.agent_factory import create_agent, validate_api_key
from mvge.cli.approval_binding import (
    NO_SLOT_WARNING,
    bind_approval_presenter,
    resolve_approval_mode,
    unbind_approval_presenter,
)
from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.cli.commands.dispatcher import CliCommandDispatcher
from mvge.cli.commands.repl import (
    NoConsoleScreenBufferError,
    SlashCompleter,
    StreamRenderer,
    console,
)
from mvge.cli.commands.setup import install_missing_deps
from mvge.cli.console import format_error
from mvge.cli.formatting import (
    check_and_warn_load_failures,
    check_and_warn_missing_deps,
    fit_footer,
    format_tome_info,
    get_git_branch,
    render_live_rate_limit,
)
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.errors import RateLimitError
from mvge.provider.model_registry import ModelRegistry


@dataclass
class _Entry:
    kind: Literal["text", "md"]
    text: Text | None = None
    bg: str | None = None
    md: str | None = None


class TuiSink:
    """Full-screen sink: appends rich lines to a shared transcript."""

    def __init__(self) -> None:
        self._entries: list[_Entry] = []
        self._pending_md: str | None = None
        self._line_dirty = False
        self._version = 0
        self._redraw_cb: Callable[[], None] | None = None

    @property
    def line_dirty(self) -> bool:
        return self._line_dirty

    @property
    def version(self) -> int:
        return self._version

    def set_redraw_cb(self, cb: Callable[[], None]) -> None:
        self._redraw_cb = cb

    def _schedule(self) -> None:
        if self._redraw_cb is not None:
            self._redraw_cb()

    def write(self, text: str) -> None:
        if text == "\n":
            self._entries.append(_Entry("text", Text("")))
            self._line_dirty = False
            self._version += 1
            self._schedule()
            return
        lines = text.split("\n")
        for part in lines:
            if part:
                if self._line_dirty and self._entries:
                    last = self._entries[-1]
                    if last.kind == "text" and last.text is not None:
                        last.text = Text(last.text.plain + part, style=last.text.style)
                    else:
                        self._entries.append(_Entry("text", Text(part)))
                else:
                    self._entries.append(_Entry("text", Text(part)))
        self._line_dirty = not text.endswith("\n")
        self._version += 1
        self._schedule()

    def write_card_line(self, line: Text, bg: str | None = None) -> None:
        self._entries.append(_Entry("text", line.copy(), bg))
        self._line_dirty = False
        self._version += 1
        self._schedule()

    def stream_narration(self, chunk: str, full: str) -> None:
        self._pending_md = full
        self._line_dirty = False
        self._version += 1
        self._schedule()

    def finalize_narration(self) -> None:
        if self._pending_md is not None:
            self._entries.append(_Entry("md", md=self._pending_md))
            self._pending_md = None
            self._version += 1
            self._schedule()

    def render_to_ansi(self, width: int) -> list[str]:
        buf = io.StringIO()
        encoder = Console(
            file=buf, force_terminal=True, color_system="truecolor", width=width
        )
        render_console = Console(width=width, file=io.StringIO())
        md_options = render_console.options.update(width=width, height=1_000_000)
        lines: list[str] = []

        def encode(text: Text) -> str:
            buf.seek(0)
            buf.truncate(0)
            encoder.print(text, end="", soft_wrap=True, highlight=False)
            return buf.getvalue()

        entries_to_render = list(self._entries)
        if self._pending_md is not None:
            entries_to_render.append(_Entry("md", md=self._pending_md))

        for entry in entries_to_render:
            if entry.kind == "md":
                md_lines: list[str] = []
                for seg_line in render_console.render_lines(
                    Markdown(entry.md or ""), md_options
                ):
                    segs = [s for s in seg_line if s.text.strip()]
                    if not segs:
                        md_lines.append("")
                        continue
                    text = Text()
                    for seg in segs:
                        text.append(seg.text, style=seg.style or "")
                    md_lines.append(encode(text))
                while md_lines and md_lines[-1] == "":
                    md_lines.pop()
                lines.extend(md_lines)
            else:
                text = (entry.text or Text()).copy()
                if entry.bg:
                    pad = width - len(text)
                    if pad > 0:
                        style = text.style if text.style else f"on {entry.bg}"
                        text.append(" " * pad, style=style)
                    elif pad < 0:
                        text.truncate(width, overflow="ellipsis")
                encoded = encode(text)
                for split_line in encoded.split("\n"):
                    lines.append(split_line)
        return lines

    def print_transcript(self) -> None:
        for entry in self._entries:
            if entry.kind == "md":
                console.print(Markdown(entry.md or ""))
                continue
            text = (entry.text or Text()).copy()
            if entry.bg:
                width = console.width
                pad = width - len(text)
                if pad > 0:
                    style = text.style if text.style else f"on {entry.bg}"
                    text.append(" " * pad, style=style)
                elif pad < 0:
                    text.truncate(width, overflow="ellipsis")
            console.print(text)


class TranscriptControl(UIControl):
    def __init__(self, sink: TuiSink) -> None:
        self.sink = sink
        self.scroll_top = 0
        self.following = True
        self._cache_version = -1
        self._cache_width = -1
        self._cache: list[str] = []

    def _get_lines(self, width: int) -> list[str]:
        if self._cache_version != self.sink.version or self._cache_width != width:
            self._cache = self.sink.render_to_ansi(width)
            self._cache_version = self.sink.version
            self._cache_width = width
        return self._cache

    def _update_scroll(self, total: int, height: int) -> int:
        max_top = max(0, total - height)
        if self.following:
            self.scroll_top = max_top
        else:
            self.scroll_top = min(self.scroll_top, max_top)
            if self.scroll_top >= max_top:
                self.following = True
        return self.scroll_top

    def preferred_width(self, max_available_width: int) -> int | None:
        return None

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool,
        get_line_prefix: Any,
    ) -> int | None:
        return None

    def create_content(
        self,
        width: int,
        height: int,
        preview_content: Any = None,
        focus_element: Any = None,
    ) -> UIContent:
        lines = self._get_lines(width)
        top = self._update_scroll(len(lines), height)
        visible = lines[top : top + height]

        def get_line(i: int) -> FormattedText:
            if i < len(visible) and visible[i]:
                return to_formatted_text(ANSI(visible[i]))
            return FormattedText([("", " ")])

        return UIContent(
            get_line=get_line,
            line_count=max(len(visible), height),
            show_cursor=False,
        )

    def scroll_up(self, lines: int = 3) -> None:
        self.following = False
        self.scroll_top = max(0, self.scroll_top - lines)

    def scroll_down(self, lines: int = 3) -> None:
        self.following = False
        self.scroll_top += lines

    def scroll_to_top(self) -> None:
        self.following = False
        self.scroll_top = 0

    def scroll_to_bottom(self) -> None:
        self.following = True


class TuiApp:
    def __init__(
        self,
        agent: MvgeAgent,
        sink: TuiSink,
        registry: ModelRegistry,
        renderer: StreamRenderer,
        input: Any = None,
        output: Any = None,
    ) -> None:
        self._agent = agent
        self.sink = sink
        self._registry = registry
        self.renderer = renderer
        self.dispatcher = CliCommandDispatcher(agent, registry, out=self._out)
        self._busy = False
        self._task: asyncio.Task[Any] | None = None
        # Slash commands run on their own handle: sharing _task with the
        # turn let a slash typed during a busy turn hijack Ctrl-C and let
        # the turn's finally clobber the slash handle.
        self._slash_task: asyncio.Task[Any] | None = None
        self._branch = get_git_branch()
        self.transcript = TranscriptControl(sink)

        app_kb = KeyBindings()
        self._bind_app_keys(app_kb)

        input_kb = KeyBindings()

        @input_kb.add("c-d")
        def _quit(event: Any) -> None:
            if not event.app.current_buffer.text.strip():
                event.app.exit()

        self._buffer = Buffer(
            completer=SlashCompleter(),
            multiline=False,
            complete_while_typing=True,
            accept_handler=self._on_accept,
        )
        input_bindings = merge_key_bindings([load_key_bindings(), input_kb])
        input_window = Window(
            BufferControl(
                buffer=self._buffer,
                key_bindings=input_bindings,
                focus_on_click=True,
            ),
            height=Dimension.exact(1),
        )
        footer_window = Window(
            FormattedTextControl(self._footer_text),
            height=Dimension.exact(1),
            style="class:toolbar",
        )
        transcript_window = Window(
            self.transcript,
            height=Dimension(min=1, weight=1),
            always_hide_cursor=True,
        )
        self._root = FloatContainer(
            HSplit([transcript_window, input_window, footer_window]),
            floats=[Float(CompletionsMenu(max_height=3))],
        )

        self.application: Application[Any] = Application(
            layout=Layout(self._root),
            key_bindings=app_kb,
            full_screen=True,
            mouse_support=True,
            input=input,
            output=output,
        )

    @property
    def agent(self) -> MvgeAgent:
        return self._agent

    @agent.setter
    def agent(self, value: MvgeAgent) -> None:
        self._agent = value
        if hasattr(self, "dispatcher"):
            self.dispatcher.agent = value

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    @registry.setter
    def registry(self, value: ModelRegistry) -> None:
        self._registry = value
        if hasattr(self, "dispatcher"):
            self.dispatcher.registry = value

    def _bind_app_keys(self, kb: KeyBindings) -> None:
        @kb.add("c-c")
        def _interrupt(event: Any) -> None:
            if self._task is not None and not self._task.done():
                self._task.cancel()
            elif self._slash_task is not None and not self._slash_task.done():
                self._slash_task.cancel()
            else:
                event.app.exit()

        @kb.add("s-pageup")
        def _page_up(event: Any) -> None:
            self.transcript.scroll_up(3)

        @kb.add("s-pagedown")
        def _page_down(event: Any) -> None:
            self.transcript.scroll_down(3)

        @kb.add("c-home")
        def _home(event: Any) -> None:
            self.transcript.scroll_to_top()

        @kb.add("c-end")
        def _end(event: Any) -> None:
            self.transcript.scroll_to_bottom()

    def _footer_text(self) -> FormattedText:
        items = list(format_tome_info(self.agent, self._branch, fit=False))
        mode = getattr(self.agent, "queue_mode", "one-at-a-time")
        if self._busy:
            items.append(("", f"  (working • mode: {mode})"))
        else:
            items.append(("", f"  (mode: {mode})"))
        width = self.application.output.get_size().columns
        return FormattedText(fit_footer(items, width))

    def _out(self, text: str) -> None:
        self.sink.write_card_line(render_markup(text), None)
        self.application.invalidate()

    def _on_accept(self, buffer: Buffer) -> bool:
        text = buffer.text.strip()
        buffer.text = ""
        if not text:
            return False
        if text.startswith("/"):
            self._slash_task = asyncio.get_event_loop().create_task(
                self._run_slash(text)
            )
            return True
        self.sink.write(f"> {text}\n")
        self.application.invalidate()
        if self._busy:
            self.agent.steer(text)
            return True
        self._task = asyncio.get_event_loop().create_task(self._run_turn(text))
        return True

    async def _run_turn(self, text: str) -> None:
        self._busy = True
        self.renderer.reset()
        self.application.invalidate()
        try:
            await self.agent.run(text)
        except asyncio.CancelledError:
            self.renderer.finish(error=True)
            self._out("\n[yellow]Interrupted.[/yellow]")
        except Exception as exc:
            self.renderer.finish(error=True)
            if isinstance(exc, RateLimitError):
                await render_live_rate_limit(
                    exc, out=self._out, invalidate=self.application.invalidate
                )
            else:
                markup = format_error(exc)
                self._out(f"\n{markup}")
        else:
            self.renderer.finish()
        finally:
            self._busy = False
            # Only clear the handle this turn set: a newer turn's task must
            # never be clobbered by a stale finally.
            if self._task is asyncio.current_task():
                self._task = None
            self.application.invalidate()

    async def _run_slash(self, text: str) -> None:
        """Run a slash command, releasing only this slash's handle."""
        task = asyncio.current_task()
        try:
            await self._handle_slash(text)
        finally:
            if self._slash_task is task:
                self._slash_task = None

    async def _handle_slash(self, text: str) -> None:
        should_exit = await self.dispatcher.dispatch(text, out=self._out)
        if should_exit:
            self.application.exit()
            return
        self.application.invalidate()

    async def run(self) -> None:
        await self.application.run_async()


_validate_api_key = validate_api_key
_create_agent = create_agent


async def run_tui(
    model: str = "",
    api_key: str | None = None,
    spells: str = "bash,read,write,edit,find,list,grep",
    extension_dir: str | None = None,
    resume: str | None = None,
    provider: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    contemplation: str = "medium",
    tome_dir: str | None = None,
    agent_name: str = DEFAULT_AGENT_NAME,
    agent_factory: AgentFactory | None = None,
    approval_mode: str | None = None,
) -> None:
    if api_key is None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key is None:
        console.print(
            format_error("API key required for this model. Set the key for its Realm.")
        )
        return

    try:
        agent = await _create_agent(
            model=model,
            api_key=api_key,
            spells=spells,
            extension_dir=extension_dir,
            tome_dir=tome_dir,
            resume=resume,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            contemplation=contemplation,
            agent_name=agent_name,
            agent_factory=agent_factory,
        )
    except ValueError as e:
        console.print(format_error(e))
        return

    registry = ModelRegistry()
    registry.load_cache()
    if registry.needs_refresh():
        with contextlib.suppress(Exception):
            await registry.auto_refresh()

    sink = TuiSink()
    renderer = StreamRenderer(sink)
    app = TuiApp(agent, sink, registry, renderer)
    sink.set_redraw_cb(app.application.invalidate)
    # Approval: same host presenter slot as the REPL, but only for modes
    # that never prompt. A prompt-mode CliApprovalPresenter reads stdin
    # and writes to stdout, which would fight the full-screen app, so
    # prompt mode stays unbound and the Approval Rune gate denies, exactly
    # as the CLI note says. allow-all and deny return immediately without
    # any terminal I/O, so they are safe to bind here. The rune's policy
    # still runs first: explicit deny/allow rules decide before the
    # presenter is ever consulted, and every decision is still audited
    # by the gate.
    # Binding, diagnostics, and subscriptions all live inside the try so
    # a failure anywhere before app.run still unbinds (and closes) the
    # presenter and closes the agent, instead of leaking a bound slot.
    presenter: CliApprovalPresenter | None = None
    runner: Any | None = None
    presenter_bound = False
    unsubs: list[object] = []
    try:
        mode = resolve_approval_mode(approval_mode)
        if mode != "prompt":
            presenter = CliApprovalPresenter(mode=mode)
            runner = getattr(agent, "runner", None)
            presenter_bound = bind_approval_presenter(runner, presenter)
            if not presenter_bound:
                app._out(f"[yellow]{NO_SLOT_WARNING}[/yellow]")
                # Never bound, so unbind will never close it: close it
                # here so a failed bind leaves no live presenter behind.
                presenter.close()
        check_and_warn_load_failures(agent.environment.diagnostics, out=app._out)
        check_and_warn_missing_deps(
            agent.environment.diagnostics,
            out=app._out,
            install=lambda: install_missing_deps(
                agent_name=agent_name,
                extension_dir=extension_dir,
                yes=True,
            ),
        )

        unsubs = [
            agent.on("message_update", renderer.on_message_update),
            agent.on("spell_casting_start", renderer.on_spell_start),
            agent.on("spell_casting_end", renderer.on_spell_end),
            agent.on("turn_start", renderer.on_turn_start),
            agent.on("turn_end", renderer.on_turn_end),
        ]
        await app.run()
    except NoConsoleScreenBufferError:
        console.print(
            format_error(
                "TUI mode requires a Win32 console screen buffer. "
                "Run in a terminal or use standard REPL."
            )
        )
    finally:
        for unsub in unsubs:
            if callable(unsub):
                unsub()
        if presenter_bound:
            unbind_approval_presenter(runner, presenter)
        await agent.close()
        sink.print_transcript()
