from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Generator
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from mvge.agent.protocol import AgentFactory
from mvge.cli.agent_factory import create_agent, validate_api_key
from mvge.cli.approval_binding import (
    NO_SLOT_WARNING,
    bind_approval_presenter,
    resolve_approval_mode,
    unbind_approval_presenter,
)
from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.cli.commands.dispatcher import SLASH_COMMANDS, CliCommandDispatcher
from mvge.cli.commands.setup import install_missing_deps
from mvge.cli.console import format_error
from mvge.cli.formatting import (
    check_and_warn_load_failures,
    check_and_warn_missing_deps,
    format_tome_info,
    get_git_branch,
    render_live_rate_limit,
)
from mvge.core.channel import MvgeResponse
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.errors import RateLimitError
from mvge.core.events import MvgeEvent
from mvge.core.layers import history_file
from mvge.core.spells import SpellResultMessage
from mvge.provider.model_registry import ModelRegistry


class NoConsoleScreenBufferError(Exception):
    """Fallback when prompt_toolkit's win32 variant is unavailable."""

    pass


if sys.platform == "win32":
    try:
        from prompt_toolkit.output.win32 import (
            NoConsoleScreenBufferError as _NoConsoleScreenBufferError,
        )

        NoConsoleScreenBufferError = _NoConsoleScreenBufferError  # type: ignore[misc,assignment]
    except Exception:
        pass


logger = logging.getLogger(__name__)

console = Console()


REPL_STYLE = Style.from_dict(
    {
        "prompt": "bold green",
        "toolbar": "bg:#333333 #aaaaaa",
        "slash": "bold cyan",
    }
)


class SlashCompleter(Completer):
    def get_completions(
        self, document: Document, complete_event: Any
    ) -> Generator[Completion]:
        text = document.text_before_cursor
        if text.startswith("/"):
            for cmd, desc in SLASH_COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )


def _make_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("c-c")
    def _(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    @bindings.add("c-d")
    def _(event: Any) -> None:
        text = event.app.current_buffer.text
        if not text.strip():
            event.app.exit(exception=EOFError)

    return bindings


def _get_history_path() -> Path:
    path = history_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _trim_history_file(history_path: Path, max_entries: int = 100) -> None:
    """Trim the prompt-toolkit FileHistory file on disk.

    Keeps only the last max_entries lines.
    """
    try:
        if not history_path.exists():
            return
        lines = history_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_entries:
            trimmed = lines[-max_entries:]
            history_path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except OSError:
        pass


def _display_response(result: Any) -> str:
    """Render a turn's final result, returning the text it displayed.

    Callers use the returned text to decide whether a failure the turn
    already carried still needs reporting, without re-reading the render.
    """
    displayed: list[str] = []
    if isinstance(result, MvgeResponse):
        for item in result.content:
            if item.get("type") == "text":
                text = str(item.get("text", ""))
                displayed.append(text)
                md = Markdown(text)
                console.print(md)
        if result.stop_reason:
            reason = getattr(result.stop_reason, "value", result.stop_reason)
            console.print(f"[dim]Stop reason: {reason}[/dim]")
    elif isinstance(result, SpellResultMessage):
        # Print mode's turn result is the last invocation, and a turn
        # that ends on a terminating spell -- codeact's final_answer
        # -- ends on a SpellResultMessage, not an MvgeResponse.
        # Dropping it leaves the user with no output at all for the
        # whole turn, even though the turn concluded with a value.
        for item in result.content:
            if item.get("type") == "text":
                text = str(item.get("text", ""))
                displayed.append(text)
                md = Markdown(text)
                console.print(md)
    return "\n".join(displayed)


async def _read_initial_prompt(
    session: PromptSession[Any],
    toolbar: Any,
) -> str | None:
    try:
        result: str = await session.prompt_async(
            "> ",
            bottom_toolbar=toolbar,
            multiline=False,
        )
        return result
    except (KeyboardInterrupt, EOFError):
        return None


async def _read_fallback_prompt() -> str | None:
    try:
        return await asyncio.to_thread(input, "> ")
    except (KeyboardInterrupt, EOFError):
        return None


class _StreamFilter:
    """Strips pi-style channel markers and reasoning blocks from streamed text.

    Models emit reasoning content in dedicated channels delimited by
    ``<channel|name>`` tags (e.g. ``<channel|reasoning>``). Content inside
    a reasoning channel is suppressed from the REPL display; all channel
    tags are stripped.  Standard markdown headings such as ``# Thinking``
    are **not** treated as reasoning artifacts and are preserved verbatim.
    """

    _CHANNEL_TAG = re.compile(r"<\s*channel\s*\|[^>]*>", re.I)
    _REASONING_CHANNEL = re.compile(
        r"<\s*channel\s*\|\s*(?:reasoning|thinking|thought)\s*>", re.I
    )

    def __init__(self) -> None:
        self._pending = ""
        self._in_reasoning = False

    def feed(self, text: str) -> str:
        self._pending += text
        out: list[str] = []
        while "\n" in self._pending:
            idx = self._pending.find("\n")
            line = self._pending[:idx]
            self._pending = self._pending[idx + 1 :]
            filtered = self._process_line(line)
            if filtered is not None:
                out.append(filtered)
                out.append("\n")

        if self._pending and not self._in_reasoning:
            last_lt = self._pending.rfind("<")
            if last_lt >= 0 and ">" not in self._pending[last_lt:]:
                emit_now = self._pending[:last_lt]
                self._pending = self._pending[last_lt:]
                if emit_now:
                    out.append(_StreamFilter._CHANNEL_TAG.sub("", emit_now))
            elif _StreamFilter._REASONING_CHANNEL.search(self._pending):
                match = _StreamFilter._REASONING_CHANNEL.search(self._pending)
                assert match is not None
                prefix = self._pending[: match.start()]
                if prefix:
                    out.append(_StreamFilter._CHANNEL_TAG.sub("", prefix))
                self._in_reasoning = True
                self._pending = ""
            else:
                emit_now = _StreamFilter._CHANNEL_TAG.sub("", self._pending)
                self._pending = ""
                if emit_now:
                    out.append(emit_now)

        return "".join(out)

    def flush(self) -> str:
        leftover = self._pending
        self._pending = ""
        if not leftover:
            return ""
        if self._in_reasoning:
            return ""
        cleaned = _StreamFilter._clean_line(leftover)
        if cleaned is None:
            return ""
        return cleaned

    def reset(self) -> None:
        self._pending = ""
        self._in_reasoning = False

    def _process_line(self, line: str) -> str | None:
        """Return cleaned line content, or None to suppress the line.

        Every channel tag on the line is honoured in order: a reasoning
        channel (``<channel|reasoning>``, ``<channel|thinking>``,
        ``<channel|thought>``) toggles suppression on, any other channel tag
        toggles it off.  Content before an open tag or after a close tag
        stays visible; content inside a reasoning block is stripped.
        """
        in_reasoning = self._in_reasoning
        out: list[str] = []
        saw_tag = False
        last_end = 0
        for tag_match in _StreamFilter._CHANNEL_TAG.finditer(line):
            saw_tag = True
            start, end = tag_match.span()
            if not in_reasoning:
                out.append(line[last_end:start])
            in_reasoning = (
                _StreamFilter._REASONING_CHANNEL.fullmatch(tag_match.group())
                is not None
            )
            last_end = end
        if not in_reasoning:
            out.append(line[last_end:])
        self._in_reasoning = in_reasoning
        visible = "".join(out)
        if visible.strip():
            return visible.rstrip()
        if not saw_tag and not in_reasoning:
            return ""
        return None

    @staticmethod
    def _clean_line(line: str) -> str | None:
        if not line.strip():
            return ""
        cleaned = _StreamFilter._CHANNEL_TAG.sub("", line).rstrip()
        if not cleaned:
            return None
        return cleaned


_MAX_RESPONSE_LINES = 20
_MAX_LINE_CHARS = 200

_CARD_BG_PENDING = "#282832"
_CARD_BG_SUCCESS = "#283228"
_CARD_BG_ERROR = "#3c2828"


def _build_card(text: str, bg: str, extra: str = "") -> Text:
    style = f"on {bg}" + (f" {extra}" if extra else "")
    return Text(text, style=style)


def _build_card_title(name: str, args: Any, bg: str) -> Text:
    title = Text("  ", style=f"on {bg}")
    title.append(f"[{name}]", style=f"bold on {bg}")
    if name == "bash":
        cmd = (args or {}).get("command", "") if isinstance(args, dict) else ""
        title.append(f"  $ {cmd}", style=f"bold on {bg}")
        if isinstance(args, dict) and args.get("timeout"):
            title.append(f" (timeout {args['timeout']}s)", style=f"on {bg}")
    elif args:
        title.append(
            f"  {json.dumps(args, separators=(',', ':'))}",
            style=f"yellow on {bg}",
        )
    return title


class Sink(Protocol):
    """Presentation target for streamed agent output."""

    @property
    def line_dirty(self) -> bool: ...

    def write(self, text: str) -> None: ...

    def write_card_line(self, line: Text, bg: str | None = None) -> None: ...

    def stream_narration(self, chunk: str, full: str) -> None: ...

    def finalize_narration(self) -> None: ...


class ConsoleSink:
    """Terminal-scrollback sink: cards padded to width, narration via Live."""

    def __init__(self) -> None:
        self._line_dirty = False
        self._live: Live | None = None
        self._live_broken = False

    @property
    def line_dirty(self) -> bool:
        return self._line_dirty

    @property
    def _live_enabled(self) -> bool:
        return console.is_terminal and not self._live_broken

    def write(self, text: str) -> None:
        console.print(Text(text), end="", soft_wrap=True, highlight=False)
        self._line_dirty = not text.endswith("\n")

    def write_card_line(self, line: Text, bg: str | None = None) -> None:
        width = console.width
        pad = width - len(line)
        if pad > 0 and bg is not None:
            line = line.copy()
            style = line.style if line.style else f"on {bg}"
            line.append(" " * pad, style=style)
        console.print(line, end="\n", soft_wrap=True, highlight=False)
        self._line_dirty = False

    def stream_narration(self, chunk: str, full: str) -> None:
        if not self._live_enabled:
            self.write(chunk)
            return
        try:
            if self._live is None:
                self._live = Live(
                    Markdown(full),
                    console=console,
                    refresh_per_second=6,
                    transient=False,
                    vertical_overflow="ellipsis",
                )
                self._live.start()
            self._live.update(Markdown(full))
        except Exception:
            self._live_broken = True
            if self._live is not None:
                with suppress(Exception):
                    self._live.stop()
                self._live = None
            self.write(chunk)

    def finalize_narration(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()
            finally:
                self._live = None
                self._line_dirty = False


class StreamRenderer:
    def __init__(self, sink: Sink | None = None) -> None:
        self._sink = sink or ConsoleSink()
        self._text_parts: list[str] = []
        self._markdown_buffer = ""
        self._contemplation_buffer = ""
        self._started = False
        self._filter = _StreamFilter()
        self._spell_cast_line_open = False
        self._spell_cast_start_time: float | None = None
        self._narration_finalized = False

    @property
    def _tool_line_open(self) -> bool:
        return self._spell_cast_line_open

    @_tool_line_open.setter
    def _tool_line_open(self, value: bool) -> None:
        self._spell_cast_line_open = value

    @property
    def _tool_start_time(self) -> float | None:
        return self._spell_cast_start_time

    @_tool_start_time.setter
    def _tool_start_time(self, value: float | None) -> None:
        self._spell_cast_start_time = value

    def _ensure_started(self) -> None:
        if not self._started:
            self._sink.write("\n")
            self._started = True

    def _write_card_line(self, text: str, bg: str, extra: str = "") -> None:
        self._sink.write_card_line(_build_card(text, bg, extra), bg)

    def _write_card_title(self, name: str, args: Any, bg: str) -> None:
        self._sink.write_card_line(_build_card_title(name, args, bg), bg)

    def _write_spell_cast_line(self, name: str, args: Any) -> None:
        self._ensure_started()
        if self._sink.line_dirty:
            self._sink.write("\n")
        self._write_card_title(name, args, _CARD_BG_PENDING)
        self._spell_cast_line_open = True
        self._spell_cast_start_time = time.monotonic()

    def _write_tool_call_line(self, name: str, args: Any) -> None:
        self._write_spell_cast_line(name, args)

    def _write_response_block(self, text: str, bg: str) -> None:
        lines = text.splitlines()
        more = 0
        if len(lines) > _MAX_RESPONSE_LINES:
            more = len(lines) - _MAX_RESPONSE_LINES
            lines = lines[:_MAX_RESPONSE_LINES]
        if not lines:
            self._write_card_line("  (no output)", bg, extra="dim")
            return
        truncated_chars = False
        for i, line in enumerate(lines):
            if len(line) > _MAX_LINE_CHARS:
                lines[i] = line[:_MAX_LINE_CHARS] + "..."
                truncated_chars = True
        for line in lines:
            self._write_card_line(f"  {line}", bg)
        if more or truncated_chars:
            detail = []
            if more:
                detail.append(f"{more} more lines")
            if truncated_chars:
                detail.append("long lines capped")
            self._write_card_line(
                f"  ... (truncated: {', '.join(detail)})", bg, extra="dim"
            )

    @staticmethod
    def _result_to_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    def _get_full_md(self) -> str:
        if self._contemplation_buffer.strip():
            thinking = f"> *Thinking: {self._contemplation_buffer.strip()}*"
            if self._markdown_buffer:
                return f"{thinking}\n\n{self._markdown_buffer}"
            return thinking
        return self._markdown_buffer

    def on_message_update(self, event: MvgeEvent) -> None:
        kind = event.data.get("kind")
        text = str(event.data.get("text", ""))
        if not text:
            return
        if kind == "contemplation":
            self._contemplation_buffer += text
            self._ensure_started()
            self._sink.stream_narration(text, self._get_full_md())
            self._narration_finalized = False
            return

        clean = self._filter.feed(text)
        if not clean:
            return
        self._markdown_buffer += clean
        self._text_parts.append(clean)
        self._ensure_started()
        self._sink.stream_narration(clean, self._get_full_md())
        self._narration_finalized = False

    def on_spell_start(self, event: MvgeEvent) -> None:
        pending = self._filter.flush()
        if pending:
            self._markdown_buffer += pending
            self._ensure_started()
            self._sink.stream_narration(pending, self._get_full_md())
        self._sink.finalize_narration()
        self._narration_finalized = True
        name = str(event.data.get("spellName", "?"))
        args = event.data.get("arguments", {})
        self._write_spell_cast_line(name, args)

    def on_tool_start(self, event: MvgeEvent) -> None:
        self.on_spell_start(event)

    def on_spell_end(self, event: MvgeEvent) -> None:
        had_start = self._spell_cast_start_time is not None
        if not self._spell_cast_line_open and event.data.get("spellName"):
            self._write_spell_cast_line(
                str(event.data["spellName"]), event.data.get("arguments", {})
            )
        elapsed = 0.0
        if self._spell_cast_start_time is not None:
            elapsed = time.monotonic() - self._spell_cast_start_time
        self._spell_cast_line_open = False
        self._spell_cast_start_time = None
        bg = _CARD_BG_ERROR if "error" in event.data else _CARD_BG_SUCCESS
        if "error" in event.data:
            msg = event.data.get("message") or event.data.get("error") or "spell failed"
            self._write_card_line(f"  [error] {msg}", bg, extra="bold red")
        elif "result" in event.data:
            self._write_response_block(self._result_to_text(event.data["result"]), bg)
        if had_start:
            self._write_card_line(f"  Took {elapsed:.1f}s", bg, extra="dim")

    def on_tool_end(self, event: MvgeEvent) -> None:
        self.on_spell_end(event)

    def finish(self, *, error: bool = False) -> None:
        if self._spell_cast_line_open:
            self._write_card_line("  (interrupted)", _CARD_BG_PENDING, extra="dim")
            self._spell_cast_line_open = False
            self._spell_cast_start_time = None
        if not error:
            pending = self._filter.flush()
            if pending:
                self._markdown_buffer += pending
                self._ensure_started()
                self._sink.stream_narration(pending, self._get_full_md())
        if not self._narration_finalized:
            self._sink.finalize_narration()
            self._narration_finalized = True
        if self._started:
            self._sink.write("\n")
        self._started = False

    def reset(self) -> None:
        if not self._narration_finalized:
            self._sink.finalize_narration()
        self._markdown_buffer = ""
        self._contemplation_buffer = ""
        self._text_parts.clear()
        self._started = False
        self._filter.reset()
        self._spell_cast_line_open = False
        self._spell_cast_start_time = None
        self._narration_finalized = False

    def on_turn_start(self, event: MvgeEvent) -> None:
        self.reset()

    def on_turn_end(self, event: MvgeEvent) -> None:
        pending = self._filter.flush()
        if pending:
            self._markdown_buffer += pending
            self._ensure_started()
            self._sink.stream_narration(pending, self._get_full_md())
        if not self._narration_finalized:
            self._sink.finalize_narration()
            self._narration_finalized = True


_validate_api_key = validate_api_key
_create_agent = create_agent


async def run_repl(
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

    mode = resolve_approval_mode(approval_mode)
    presenter = CliApprovalPresenter(mode=mode)
    # getattr: a custom agent_factory may return an agent without the engine
    # runner accessor; that is a missing slot (warn, fail closed), not a crash.
    runner = getattr(agent, "runner", None)
    if not bind_approval_presenter(runner, presenter):
        console.print(f"[yellow]{NO_SLOT_WARNING}[/yellow]")

    unsubs: list[object] = []
    try:
        registry = ModelRegistry()
        registry.load_cache()
        if registry.needs_refresh():
            with contextlib.suppress(Exception):
                await registry.auto_refresh()

        dispatcher = CliCommandDispatcher(agent, registry, out=console.print)

        console.print("[green]Mvge REPL[/green]")
        console.print(f"[dim]Model: {model}[/dim]")
        levels = registry.get_supported_contemplation_levels(model)
        if levels:
            levels_str = ", ".join(levels)
            console.print(
                f"[dim]Contemplation: {contemplation} (supported: {levels_str})[/dim]"
            )
        if agent.tome_id:
            console.print(f"[dim]Tome: {agent.tome_id}[/dim]")
        console.print(
            "[dim]Type /help for commands, Ctrl+C to interrupt, Ctrl+D to quit[/dim]"
        )
        console.print()

        check_and_warn_load_failures(agent.environment.diagnostics)
        check_and_warn_missing_deps(
            agent.environment.diagnostics,
            out=console.print,
            prompt=input if sys.stdin.isatty() else None,
            install=lambda: install_missing_deps(
                agent_name=agent_name,
                extension_dir=extension_dir,
                yes=True,
            ),
        )

        history = FileHistory(str(_get_history_path()))
        session: PromptSession[Any] | None = None
        use_fallback = False
        try:
            session = PromptSession(
                history=history,
                completer=SlashCompleter(),
                key_bindings=_make_bindings(),
                style=REPL_STYLE,
            )
        except NoConsoleScreenBufferError:
            use_fallback = True
            console.print(
                "[dim]Console screen buffer unavailable. "
                "Falling back to standard line reader.[/dim]\n"
            )

        branch = get_git_branch()

        def get_toolbar() -> list[tuple[str, str]]:
            return format_tome_info(agent, branch)

        renderer = StreamRenderer()
        unsubs = [
            agent.on("message_update", renderer.on_message_update),
            agent.on("spell_casting_start", renderer.on_spell_start),
            agent.on("spell_casting_end", renderer.on_spell_end),
            agent.on("turn_start", renderer.on_turn_start),
            agent.on("turn_end", renderer.on_turn_end),
        ]

        agent_task: asyncio.Task[Any] | None = None

        def _on_sigint(sig: int, frame: Any) -> None:
            if agent_task is not None and not agent_task.done():
                agent_task.cancel()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, _on_sigint)

        while True:
            if use_fallback or session is None:
                text = await _read_fallback_prompt()
            else:
                try:
                    text = await _read_initial_prompt(session, get_toolbar)
                except NoConsoleScreenBufferError:
                    use_fallback = True
                    console.print(
                        "[dim]Console screen buffer unavailable. "
                        "Falling back to standard line reader.[/dim]\n"
                    )
                    text = await _read_fallback_prompt()

            if text is None:
                console.print("\n[dim]Goodbye.[/dim]")
                break

            text = text.strip()
            if not text:
                continue

            if text.startswith("/"):
                should_exit = await dispatcher.dispatch(text)
                if should_exit:
                    console.print("[dim]Goodbye.[/dim]")
                    break
                continue

            renderer.reset()
            agent_task = asyncio.create_task(agent.run(text))
            try:
                await agent_task
            except asyncio.CancelledError:
                renderer.finish(error=True)
                console.print("\n[yellow]Interrupted.[/yellow]")
            except Exception as exc:
                renderer.finish(error=True)
                if isinstance(exc, RateLimitError):
                    await render_live_rate_limit(exc, out=console.print)
                else:
                    markup = format_error(exc)
                    console.print(f"\n{markup}")
            else:
                renderer.finish()
            finally:
                agent_task = None

    finally:
        for unsub in unsubs:
            if callable(unsub):
                unsub()
        unbind_approval_presenter(runner, presenter)
        await agent.close()
    _trim_history_file(_get_history_path())
