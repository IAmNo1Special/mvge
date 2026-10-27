"""Interactive terminal presenter for the Approval Rune.

The host (CLI) binds one of these to the engine-owned runner's approval
slot at startup and unbinds it on shutdown. The rune owns policy matching,
repeated-denial suppression, persistence, and audit; this module only
presents the request and returns the Summoner's decision.

Fail-closed behavior:

* EOF on stdin, Ctrl-C, non-TTY, closed presenter, or automation ``deny``
  mode all resolve as ``deny`` -- never a crash, never an allow.
* On a real terminal the prompt reads stdin through the event loop
  (non-blocking; the terminal stays in canonical mode) so the loop stays
  responsive: the gate's abort race and close()/unbind can resolve an
  in-flight prompt as denied instead of hanging for an answer. During the
  prompt SIGINT is handled on the loop (a raising handler would escape the
  loop); the previous handler is always restored afterwards. The blocking
  fallback for injected streams claims SIGINT with a raising handler
  instead, for the same deny-on-Ctrl-C guarantee.
* Interactive prompts wait until the Summoner decides or the run is
  cancelled. Cancellation (``asyncio.CancelledError``) is never swallowed:
  it propagates so the gate's abort race can deny the cast.
* Every model-controlled string renders as inert plain text. No markup is
  interpreted; the output path never touches rich.
"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
import sys
import threading
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from types import FrameType
from typing import Any, TextIO

from mvge.cli.approval_types import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
    PersistentGrantInfo,
    allow,
    deny,
)

__all__ = ["CliApprovalPresenter"]

_VALID_MODES: tuple[ApprovalMode, ...] = ("deny", "prompt", "allow-all")


class CliApprovalPresenter:
    """TTY approval prompt implementing the spec's Interactive CLI surface.

    Implements the engine's ``ApprovalPresenter`` contract
    (``mvge.core.approval``): the engine invokes the presenter as
    ``await presenter(request)``. The host binds one of these to the
    engine-owned runner's approval slot at startup and unbinds it on
    shutdown.

    Options: allow once, deny, always-allow spell (second confirmation),
    approve-all-this-session, approve-all-in-this-project (second
    confirmation). Persistent deny ("always deny this spell") lives under
    the secondary "more options" action to prevent accidental lockout.
    """

    def __init__(
        self,
        mode: ApprovalMode = "prompt",
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"Unknown approval mode: {mode!r}")
        self._mode = mode
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._closed = False
        # Evented-prompt state (real terminal only; touched on the loop thread).
        self._pending_input = b""
        self._line_future: asyncio.Future[bytes | None] | None = None
        self._sigint_pending = False
        self._prompt_task: asyncio.Task[Any] | None = None

    def close(self) -> None:
        """Deny all further requests; resolve an in-flight prompt as denied.

        Called on unbind/shutdown. Idempotent. If a prompt is in flight its
        task is cancelled -- the gate turns a cancelled presenter future
        into a deny, never an allow. New requests after close deny
        immediately.
        """
        self._closed = True
        task = self._prompt_task
        if task is not None and not task.done():
            try:
                task.get_loop().call_soon_threadsafe(task.cancel)
            except RuntimeError:
                task.cancel()

    @property
    def closed(self) -> bool:
        """Whether the presenter has been closed (unbound)."""
        return self._closed

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        """The engine's presenter contract: ``await presenter(request)``."""
        return await self.request_approval(request)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Return the Summoner's decision, failing closed on every edge.

        Task cancellation (the gate's abort race, or close() during a
        prompt) is never swallowed: it propagates so the gate can deny the
        cast.
        """
        if self._closed:
            return self._deny(request, "once")
        if self._mode == "deny":
            return self._deny(request, "once")
        if self._mode == "allow-all":
            return self._allow(request, "once")
        if not self._is_tty():
            return self._deny(request, "once")
        if self._use_evented_io():
            return await self._request_evented(request)
        return await self._request_blocking(request)

    def _use_evented_io(self) -> bool:
        """Whether the real terminal can be read without blocking the loop.

        Needs the actual stdin file descriptor on the main thread with a
        selector loop (no add_reader on Windows). Anything else -- injected
        test streams, worker threads -- falls back to blocking reads.
        """
        if self._stdin is not sys.stdin:
            return False
        if threading.current_thread() is not threading.main_thread():
            return False
        if platform.system() == "Windows":
            return False
        try:
            self._stdin.fileno()
        except (OSError, ValueError):
            return False
        return True

    async def _request_evented(self, request: ApprovalRequest) -> ApprovalDecision:
        """Prompt on the real terminal without blocking the event loop.

        stdin is read via the loop's reader in non-blocking mode; the
        terminal itself stays in canonical mode, so kernel echo and line
        editing behave exactly as with a blocking read. The loop stays
        responsive, so the gate's abort race and close() resolve an
        in-flight prompt as denied instead of hanging for an answer.
        """
        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        was_blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        previous_sigint = signal.getsignal(signal.SIGINT)
        loop.add_signal_handler(signal.SIGINT, self._sigint_during_prompt)
        self._prompt_task = asyncio.current_task()
        self._sigint_pending = False
        self._pending_input = b""
        try:
            try:
                return await self._prompt_dialog(request, self._read_line_from_tty)
            except KeyboardInterrupt:
                self._line("Denied (interrupted).")
                return self._deny(request, "once")
            except EOFError:
                return self._deny(request, "once")
        finally:
            self._prompt_task = None
            loop.remove_signal_handler(signal.SIGINT)
            self._restore_sigint(previous_sigint)
            os.set_blocking(fd, was_blocking)

    async def _request_blocking(self, request: ApprovalRequest) -> ApprovalDecision:
        """Prompt via blocking reads.

        Fallback for injected (non-real) streams, e.g. unit tests, where no
        file descriptor is available for the evented reader. Ctrl-C still
        denies via the claimed SIGINT handler; task cancellation pends
        until the read returns, and the gate still turns it into a deny.
        """
        self._prompt_task = asyncio.current_task()
        previous_sigint = self._claim_sigint()
        try:
            try:
                return await self._prompt_dialog(request, self._read_line_from_stream)
            except KeyboardInterrupt:
                self._line("Denied (interrupted).")
                return self._deny(request, "once")
            except EOFError:
                return self._deny(request, "once")
        finally:
            self._restore_sigint(previous_sigint)
            self._prompt_task = None

    def _claim_sigint(self) -> Any:
        """Install a SIGINT handler that raises KeyboardInterrupt for the
        prompt duration. Returns the previous handler, or None when signal
        handling is unavailable (not on the main thread).

        The previous handler is restored by _restore_sigint in a finally,
        so Ctrl-C outside a prompt keeps its ambient meaning.
        """
        if threading.current_thread() is not threading.main_thread():
            return None

        def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:
            raise KeyboardInterrupt

        try:
            return signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
        except (ValueError, OSError):
            return None

    def _restore_sigint(self, previous: Any) -> None:
        """Restore the SIGINT handler saved by _claim_sigint. No-op when the
        claim was unavailable."""
        if previous is None:
            return
        with suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, previous)

    def _is_tty(self) -> bool:
        return self._stdin.isatty() and self._stdout.isatty()

    @staticmethod
    def _deny(request: ApprovalRequest, scope: ApprovalScope | str) -> ApprovalDecision:
        return deny(request, ApprovalReasonCode.USER, scope)

    @staticmethod
    def _allow(
        request: ApprovalRequest, scope: ApprovalScope | str
    ) -> ApprovalDecision:
        return allow(request, ApprovalReasonCode.USER, scope)

    async def _prompt_dialog(
        self,
        request: ApprovalRequest,
        read_line: Callable[[str], Awaitable[str]],
    ) -> ApprovalDecision:
        """Run the approval menu. ``read_line`` raises EOFError on end of
        input and KeyboardInterrupt on Ctrl-C; both propagate to the caller,
        which turns them into deny. Task cancellation also propagates, so
        the gate's abort race can deny the cast."""
        self._render_request(request)
        while True:
            self._render_menu()
            raw = await read_line("Choice [1/2/3/4/5/m]: ")
            selected = raw.strip().lower()
            if selected == "1":
                return self._allow(request, "once")
            if selected == "2":
                return self._deny(request, "once")
            if selected == "3":
                if await self._confirm_always_allow(request, read_line):
                    return self._allow(request, "spell")
                continue
            if selected == "4":
                self._line(
                    "Session approval active: all non-denied casts are allowed "
                    "until the tome or project changes, or this run exits."
                )
                return self._allow(request, "session")
            if selected == "5":
                if await self._confirm_approve_project(request, read_line):
                    return self._allow(request, "project")
                continue
            if selected == "m":
                decision = await self._more_options_dialog(request, read_line)
                if decision is not None:
                    return decision
                continue
            # Invalid entries re-prompt forever: the prompt waits until the
            # Summoner decides or the run is cancelled. It never auto-denies.
            self._line(f"Unknown choice {raw.strip()!r}. Try again.")

    async def _more_options_dialog(
        self,
        request: ApprovalRequest,
        read_line: Callable[[str], Awaitable[str]],
    ) -> ApprovalDecision | None:
        """Secondary actions. Returns a decision, or None to go back."""
        self._line("")
        self._line("More options:")
        self._line("  a) Always deny this spell... (persistent, asks to confirm)")
        self._line("  b) Back")
        while True:
            raw = await read_line("Choice [a/b]: ")
            selected = raw.strip().lower()
            if selected == "a":
                if await self._confirm_always_deny(request, read_line):
                    return self._deny(request, "spell")
                return None
            if selected == "b":
                return None
            self._line(f"Unknown choice {raw.strip()!r}. Try again.")

    def _spell_source(self, request: ApprovalRequest) -> str:
        """Engine-derived spell attribution for display.

        Never trusted for policy (the rune owns that); shown so the
        Summoner sees what identity the grant would attach to.
        """
        identity = request.spell_identity
        source_kind = identity.get("source_kind", "")
        source_id = identity.get("source_id", "")
        if source_kind == "rune" and source_id:
            return f" (rune: {source_id})"
        if source_kind:
            return f" ({source_kind})"
        return ""

    def _grant_info_for_spell(self, request: ApprovalRequest) -> PersistentGrantInfo:
        """Describe the persistent "always allow this spell" grant.

        The engine's request carries no grant metadata (the rune owns
        policy), so the presenter describes the grant from the
        engine-derived identity: global scope per the spec default, and the
        unconstrained warning for mutating spells, where the grant would
        authorize every future argument for this identity.
        """
        read_only = request.spell_identity.get("read_only", "") == "true"
        return PersistentGrantInfo(
            kind="spell",
            scope_label="global (all projects)",
            unconstrained_warning=not read_only,
        )

    async def _confirm_always_allow(
        self,
        request: ApprovalRequest,
        read_line: Callable[[str], Awaitable[str]],
    ) -> bool:
        grant = self._grant_info_for_spell(request)
        lines = [
            "",
            "Confirm persistent grant:",
            f'  Always allow spell "{request.spell_name}"?',
            f"  Scope: {grant.scope_label}",
        ]
        lines.extend(self._constraint_lines(grant))
        lines.append("  This cast:")
        lines.extend(self._argument_lines(request))
        return await self._confirm("allow", lines, request, read_line)

    async def _confirm_approve_project(
        self,
        request: ApprovalRequest,
        read_line: Callable[[str], Awaitable[str]],
    ) -> bool:
        project = request.project_root or "the current project"
        lines = [
            "",
            "Confirm persistent grant:",
            f"  Approve ALL mutating spells while project {project} is active?",
            "  This is not a sandbox: a shell spell could still reach paths",
            "  outside the project unless its arguments are constrained.",
        ]
        return await self._confirm("approve", lines, request, read_line)

    async def _confirm_always_deny(
        self,
        request: ApprovalRequest,
        read_line: Callable[[str], Awaitable[str]],
    ) -> bool:
        lines = [
            "",
            "Confirm persistent rule:",
            f'  Always deny spell "{request.spell_name}"?',
            "  A never-allow rule overrides every approval scope.",
        ]
        return await self._confirm("deny", lines, request, read_line)

    @staticmethod
    def _constraint_lines(grant: PersistentGrantInfo) -> list[str]:
        if grant.constraint_lines:
            lines = ["  Argument constraints:"]
            lines.extend(f"    - {line}" for line in grant.constraint_lines)
            return lines
        lines = ["  Argument constraints: none"]
        if grant.unconstrained_warning:
            lines.append(
                "  WARNING: an unconstrained grant for a shell or file-writing"
            )
            lines.append("  spell authorizes EVERY future argument for this spell.")
            lines.append("  Only confirm if you mean full trust.")
        return lines

    async def _confirm(
        self,
        word: str,
        lines: list[str],
        request: ApprovalRequest,
        read_line: Callable[[str], Awaitable[str]],
    ) -> bool:
        """Ask the user to type ``word``. True = confirmed, False = wrong
        word (back to menu). EOF and Ctrl-C propagate to the caller."""
        for line in lines:
            self._line(line)
        answer = await read_line(
            f"Type {word.upper()} to confirm, or anything else to go back: "
        )
        if answer.strip().lower() == word:
            self._recorded_notice(word, request)
            return True
        self._line("Confirmation did not match. No persistent grant created.")
        return False

    def _recorded_notice(self, word: str, request: ApprovalRequest) -> None:
        name = request.spell_name
        if word == "allow":
            scope = self._grant_info_for_spell(request).scope_label
            self._line(f'Recorded: always allow spell "{name}" ({scope}).')
        elif word == "approve":
            project = request.project_root or "the current project"
            self._line(f"Recorded: approve all for project {project}.")
        elif word == "deny":
            self._line(f'Recorded: always deny spell "{name}".')

    @staticmethod
    def _format_value(value: Any) -> str:
        """Render a frozen argument value as inert plain text."""
        if isinstance(value, Mapping):
            inner = ", ".join(
                f"{key}: {CliApprovalPresenter._format_value(item)}"
                for key, item in value.items()
            )
            text = "{" + inner + "}"
        elif isinstance(value, (tuple, list)):
            inner = ", ".join(
                CliApprovalPresenter._format_value(item) for item in value
            )
            text = "[" + inner + "]"
        else:
            text = str(value)
        if len(text) > 500:
            text = text[:497] + "..."
        return text

    def _argument_lines(self, request: ApprovalRequest) -> list[str]:
        """Render the cast's frozen arguments as inert ``key: value`` lines."""
        lines = []
        for key, value in request.arguments.items():
            lines.append(f"    {key}: {self._format_value(value)}")
        if not lines:
            lines.append("    (no arguments)")
        return lines

    def _render_request(self, request: ApprovalRequest) -> None:
        self._line("")
        self._line("Approve spell cast?")
        self._line(f"  Spell:     {request.spell_name}{self._spell_source(request)}")
        self._line(f"  Cast:      {request.cast_id}")
        if not request.schema_validated:
            self._line(
                "  Warning:   unvalidated arguments - this spell has no "
                "parameter schema, so no persistent rule can cover it; "
                "every cast prompts."
            )
        if request.project_root:
            self._line(f"  Project:   {request.project_root}")
        if request.tome_id:
            self._line(f"  Tome:      {request.tome_id}")
        for line in self._argument_lines(request):
            self._line(line)

    def _render_menu(self) -> None:
        self._line("")
        self._line("  1) Allow once (this cast only)")
        self._line("  2) Deny")
        self._line("  3) Always allow this spell... (persistent, asks to confirm)")
        self._line(
            "  4) Approve all for this session (clears on tome/project change or exit)"
        )
        self._line("  5) Approve all in this project... (persistent, asks to confirm)")
        self._line("  m) More options")

    def _sigint_during_prompt(self) -> None:
        """Loop-thread SIGINT callback for the evented prompt.

        Flags the interrupt and wakes any pending line read. The reader
        turns the flag into KeyboardInterrupt, so the prompt denies with the
        usual "interrupted" message instead of the exception escaping the
        event loop (which a raising signal handler would do while the loop
        -- not a blocking read -- owns the thread).
        """
        self._sigint_pending = True
        future = self._line_future
        if future is not None and not future.done():
            future.set_result(None)

    def _take_sigint(self) -> bool:
        """Consume a pending SIGINT flag.

        The flag is set by the loop-thread callback while this coroutine is
        suspended; reading it through a helper keeps mypy from narrowing the
        attribute to False after the first check.
        """
        pending, self._sigint_pending = self._sigint_pending, False
        return pending

    async def _read_line_from_tty(self, prompt_text: str) -> str:
        """Await one terminal line.

        Raises KeyboardInterrupt on Ctrl-C and EOFError on end of input.
        Task cancellation propagates untouched so the gate's abort race can
        deny the cast.
        """
        self._stdout.write(prompt_text)
        self._stdout.flush()
        if self._take_sigint():
            raise KeyboardInterrupt
        while True:
            if b"\n" in self._pending_input:
                line, _, self._pending_input = self._pending_input.partition(b"\n")
                return line.decode("utf-8", "replace")
            chunk = await self._read_available()
            if self._take_sigint():
                raise KeyboardInterrupt
            if chunk is None:
                # EOF (Ctrl-D); a signal wake-up is handled by the flag above.
                if self._pending_input:
                    line = bytes(self._pending_input)
                    self._pending_input = b""
                    return line.decode("utf-8", "replace")
                raise EOFError
            self._pending_input += chunk

    async def _read_available(self) -> bytes | None:
        """Await the next stdin chunk. None on EOF or signal wake-up."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes | None] = loop.create_future()
        self._line_future = future
        fd = sys.stdin.fileno()

        def _on_readable() -> None:
            if future.done():
                return
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                return
            except OSError:
                future.set_result(None)
                return
            # b"" from a terminal means EOF (Ctrl-D); normalize to None so
            # the reader is not re-armed into a busy loop.
            future.set_result(chunk if chunk != b"" else None)

        loop.add_reader(fd, _on_readable)
        try:
            return await future
        finally:
            loop.remove_reader(fd)
            self._line_future = None
            if not future.done():
                future.cancel()

    async def _read_line_from_stream(self, prompt_text: str) -> str:
        """Blocking line read for injected streams.

        Raises EOFError on end of input. Ctrl-C raises KeyboardInterrupt via
        the claimed SIGINT handler; task cancellation pends until the read
        returns.
        """
        self._stdout.write(prompt_text)
        self._stdout.flush()
        line = self._stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\n")

    def _line(self, text: str = "") -> None:
        print(text, file=self._stdout, flush=True)
