from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from mvge.agent.protocol import MvgeAgent
from mvge.core.errors import AuthenticationError, RateLimitError
from mvge.core.spells import SpellResultMessage
from mvge.core.worklog import LandedWork, landed_work

_console = Console()


def _reset_at_suffix(reset_at: float | None) -> str:
    """Render an absolute reset time as a trailing clause, or nothing."""
    if not reset_at:
        return ""
    dt = datetime.fromtimestamp(reset_at, UTC)
    return f" Resets at {dt.strftime('%H:%M UTC')}."


def format_error(exc: Exception | str) -> str:
    """Format an exception or error string into consistent Rich markup."""
    if isinstance(exc, RateLimitError):
        if exc.limit_source == "openrouter_free_tier_daily":
            limit_str = (
                f" ({exc.quota_limit}/{exc.quota_limit} requests)"
                if exc.quota_limit
                else ""
            )
            reset_str = _reset_at_suffix(exc.reset_at)
            hint = f"\n[dim]Hint: {exc.remedy_hint}[/dim]" if exc.remedy_hint else ""
            return (
                f"[yellow]Daily free-model quota exhausted{limit_str}."
                f"{reset_str}[/yellow]{hint}"
            )
        if (
            exc.limit_source == "upstream_rate_limit"
            or "provider returned error" in str(exc).lower()
        ):
            return (
                f"[yellow]Upstream provider overloaded: {exc}"
                f"{_reset_at_suffix(exc.reset_at)}[/yellow]"
            )

        hint = ""
        if exc.retry_after is not None and not exc.reset_at:
            hint = f" Try again in {exc.retry_after:.0f}s."
        remedy = f"\n[dim]Hint: {exc.remedy_hint}[/dim]" if exc.remedy_hint else ""
        if str(exc) and str(exc).lower() not in (
            "rate limited",
            "rate limit exceeded",
            "you are being rate limited",
        ):
            return (
                f"[yellow]Rate limited by the provider: {exc}.{hint}"
                f"{_reset_at_suffix(exc.reset_at)}[/yellow]{remedy}"
            )
        return (
            f"[yellow]Rate limited by the provider.{hint}"
            f"{_reset_at_suffix(exc.reset_at)}[/yellow]{remedy}"
        )
    if isinstance(exc, AuthenticationError):
        return (
            "[red]Authentication failed (401). "
            "Check your OPENROUTER_API_KEY or --api-key.[/red]"
        )
    msg = str(exc)
    if msg.startswith("[red]") or msg.startswith("[yellow]"):
        return msg
    if msg.startswith("Error: "):
        return f"[red]{msg}[/red]"
    return f"[red]Error: {msg}[/red]"


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}" if value == 1 else f"{value} {noun}s"


def format_partial_work(work: LandedWork) -> list[str]:
    """Report the work a failed run already left behind, as Rich markup lines.

    Printed only when a run exits non-zero. A clean run gets no summary, because
    a Summoner who did not ask for one learns nothing new from it.

    A run that cast nothing says so plainly. Silence there would leave the
    Summoner guessing whether the work landed, which is the exact ambiguity
    that makes a blind retry dangerous.
    """
    if work.spells_run == 0:
        return ["[dim]No spells ran before the failure, so nothing was written.[/dim]"]

    lines = ["[bold yellow]Work already done before the failure:[/bold yellow]"]
    lines.append(
        f"  [yellow]{_count(work.spells_succeeded, 'spell')} completed[/yellow]"
    )
    if work.spells_failed:
        lines.append(f"  [yellow]{_count(work.spells_failed, 'spell')} failed[/yellow]")
    if work.mutated_paths:
        lines.append("  [yellow]Files already on disk:[/yellow]")
        lines.extend(f"    [yellow]{path}[/yellow]" for path in work.mutated_paths)
        lines.append(
            "[dim]Check these before retrying; a retry can overwrite them.[/dim]"
        )
    else:
        lines.append("[dim]None of them wrote a file the engine can name.[/dim]")
    return lines


def transcript_length(agent: MvgeAgent) -> int:
    """How many invocations the agent's transcript holds, or 0 when unreadable.

    Snapshot before a turn so the report after a failure covers that turn only.
    """
    invocations = getattr(getattr(agent, "_state", None), "invocations", None)
    return len(invocations) if isinstance(invocations, list) else 0


def landed_work_of(agent: MvgeAgent, since: int = 0) -> LandedWork | None:
    """Read the landed work off an agent's transcript, from ``since`` onward.

    The transcript survives the failure: the harness reduces every
    ``MESSAGE_END`` into the state as the run goes, so what the spells wrote is
    already recorded by the time an upstream error raises.

    ``since`` scopes the read to the invocations one turn appended. A resumed
    Tome starts with a full transcript, and reporting those casts as work the
    failing turn just completed would be wrong.

    Returns ``None`` when the agent exposes no readable transcript. A custom
    ``agent_factory`` may return an agent without the engine's accessors, and
    that is a missing slot to skip, not a crash on the error path.
    """
    state = getattr(agent, "_state", None)
    invocations = getattr(state, "invocations", None)
    if not isinstance(invocations, list):
        return None
    spells = getattr(state, "spells", None)
    return landed_work(
        invocations[since:],
        spells if isinstance(spells, list) else [],
    )


def turn_error_reports(
    agent: MvgeAgent, since: int = 0, final_text: str = ""
) -> list[str]:
    """Text of this turn's unrecovered spell errors, minus quoted ones.

    The transcript slice from ``since`` onward holds the turn's invocations.
    An error counts as unrecovered when no successful spell result follows it
    in the slice: a later success means the turn recovered and the error is
    history, not the outcome. An error the final message already quotes is
    skipped, so print mode never prints the same failure twice.

    Returns [] when the agent exposes no readable transcript. A custom
    ``agent_factory`` may return an agent without the engine's accessors, and
    that is a missing slot to skip, not a crash on the display path.
    """
    state = getattr(agent, "_state", None)
    invocations = getattr(state, "invocations", None)
    if not isinstance(invocations, list):
        return []
    turn = invocations[since:]
    reports: list[str] = []
    seen: set[str] = set()
    for index, invocation in enumerate(turn):
        if not isinstance(invocation, SpellResultMessage):
            continue
        if not invocation.is_error:
            continue
        if any(
            isinstance(later, SpellResultMessage) and not later.is_error
            for later in turn[index + 1 :]
        ):
            continue
        for item in invocation.content or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = str(item.get("text", ""))
            if not text or text in seen or text in final_text:
                continue
            seen.add(text)
            reports.append(text)
    return reports


def turn_unrecovered_error_count(agent: MvgeAgent, since: int = 0) -> int:
    """Count this turn's unrecovered spell errors, quoted or not.

    The same classification as ``turn_error_reports`` — an error counts
    as unrecovered when no successful spell result follows it in the
    slice from ``since`` onward, distinct error texts only — but without
    the ``final_text`` quote filter, so an unrecovered error the final
    message already quotes still counts. The recovered-note gate uses
    this count, not the printed reports: a quoted unrecovered error
    prints no report, yet the turn still ended in failure.

    Returns 0 when the agent exposes no readable transcript.

    Note: an unrecovered error with empty text produces no report today,
    and this helper, mirroring the distinct-text scan, inherits that hole.
    """
    state = getattr(agent, "_state", None)
    invocations = getattr(state, "invocations", None)
    if not isinstance(invocations, list):
        return 0
    turn = invocations[since:]
    seen: set[str] = set()
    count = 0
    for index, invocation in enumerate(turn):
        if not isinstance(invocation, SpellResultMessage):
            continue
        if not invocation.is_error:
            continue
        if any(
            isinstance(later, SpellResultMessage) and not later.is_error
            for later in turn[index + 1 :]
        ):
            continue
        for item in invocation.content or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = str(item.get("text", ""))
            if not text or text in seen:
                continue
            seen.add(text)
            count += 1
    return count


def turn_recovered_error_count(
    agent: MvgeAgent, since: int = 0, final_text: str = ""
) -> int:
    """Count this turn's recovered spell errors, minus quoted ones.

    The mirror of ``turn_error_reports`` for the recovered half: an error
    counts as recovered when a successful spell result follows it in the
    slice from ``since`` onward. Distinct error texts only, mirroring the
    ``seen`` deduplication there, and an error the final message already
    quotes is skipped, so a model that already explained the recovery
    does not earn a redundant note.

    Call only when the turn has no unrecovered error; the count alone
    cannot tell a pure-recovered turn from a mixed one.

    Returns 0 when the agent exposes no readable transcript. The count
    carries no error text and no cause: it is true for a validation
    failure, an execution error, and a denial-shaped error alike, and
    the caller must not attach a cause claim to it.
    """
    state = getattr(agent, "_state", None)
    invocations = getattr(state, "invocations", None)
    if not isinstance(invocations, list):
        return 0
    turn = invocations[since:]
    seen: set[str] = set()
    count = 0
    for index, invocation in enumerate(turn):
        if not isinstance(invocation, SpellResultMessage):
            continue
        if not invocation.is_error:
            continue
        if not any(
            isinstance(later, SpellResultMessage) and not later.is_error
            for later in turn[index + 1 :]
        ):
            continue
        for item in invocation.content or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = str(item.get("text", ""))
            if not text or text in seen or text in final_text:
                continue
            seen.add(text)
            count += 1
    return count


def format_cwd() -> str:
    """Return formatted current working directory relative to user home."""
    home = Path.home()
    try:
        rel = Path.cwd().relative_to(home)
    except ValueError:
        return str(Path.cwd())
    if str(rel) == ".":
        return "~"
    return f"~/{rel.as_posix()}"


def get_git_branch() -> str | None:
    """Return active git branch or None if not a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def mana_context(agent: MvgeAgent) -> tuple[str, str]:
    """Return mana usage styling and text tuple."""
    used = getattr(agent, "mana_used", None)
    if used is None:
        state = getattr(agent, "_state", None)
        if state is not None:
            used = getattr(state, "mana_used", None)
    if used is None:
        return "", "mana ?"
    return "", f"mana {used}"


def fit_footer(items: list[tuple[str, str]], width: int) -> list[tuple[str, str]]:
    """Fit footer items to terminal width, truncating excess."""
    if width <= 0:
        return []
    items = list(items)
    while items:
        total = sum(len(text) for _, text in items)
        if total <= width:
            return items

        target_idx = -1
        if len(items) >= 2 and items[-1][1].lstrip().startswith("("):
            target_idx = -2

        n = len(items)
        norm_target = (target_idx + n) % n
        other_total = sum(
            len(text) for i, (_, text) in enumerate(items) if i != norm_target
        )
        room = width - other_total
        if room >= 1:
            style, text = items[norm_target]
            items[norm_target] = (style, text[: room - 1] + "…")
            return items
        items.pop(norm_target)

    return items


def format_tome_info(
    agent: MvgeAgent,
    branch: str | None = None,
    fit: bool = True,
    console_width: int | None = None,
) -> list[tuple[str, str]]:
    """Format footer status items for the active tome."""
    cwd = format_cwd()
    if branch:
        cwd = f"{cwd} ({branch})"
    items: list[tuple[str, str]] = [("bold", f" {cwd}")]
    tid = agent.tome_id
    if tid:
        items.append(("dim", f"  tome {tid[:8]}"))
    mana_style, mana_text = mana_context(agent)
    items.append((mana_style, f"  {mana_text}"))
    right = getattr(agent, "model_id", getattr(agent, "_model_id", ""))
    thinking = getattr(
        agent, "contemplation_level", getattr(agent, "_contemplation_level", None)
    )
    if thinking:
        right = f"{right} • {thinking}"
    items.append(("", f"  {right}"))
    if not fit:
        return items
    width = console_width if console_width is not None else _console.width
    return fit_footer(items, width)


async def render_live_rate_limit(
    exc: RateLimitError,
    out: Callable[[str], None] = _console.print,
    invalidate: Callable[[], None] | None = None,
    sleep_fn: Any = asyncio.sleep,
) -> None:
    """Render a live rate-limit countdown or static exhaustion message.

    An absolute `reset_at` means the window is measured in hours, not
    seconds, so the static message is printed once. Counting down
    `retry_after` in that case tells the Summoner to watch a clock that ends
    long before the limit actually clears.
    """
    if (
        exc.limit_source == "openrouter_free_tier_daily"
        or (exc.retry_after is None and exc.limit_source)
        or exc.reset_at
    ):
        markup = format_error(exc)
        out(markup)
        if invalidate is not None:
            invalidate()
        return

    seconds = int(exc.retry_after or 60)
    for sec in range(seconds, 0, -1):
        msg = f"[yellow]Rate limited by the provider. Retry in {sec}s...[/yellow]"
        out(msg)
        if invalidate is not None:
            invalidate()
        await sleep_fn(1)


def _diag_kind(diag: Any) -> str:
    kind = getattr(diag, "kind", None)
    if kind is None:
        return ""
    return str(getattr(kind, "value", kind)).lower()


def check_and_warn_load_failures(
    diagnostics: list[Any],
    out: Callable[[str], None] = _console.print,
) -> None:
    """Scan diagnostics for load failures and print a prominent warning."""
    load_failures: list[Any] = []
    for diag in diagnostics:
        if _diag_kind(diag) == "load_failure":
            load_failures.append(diag)

    if not load_failures:
        return

    count = len(load_failures)
    out(f"[bold yellow]Warning: Failed to load {count} rune(s):[/bold yellow]")
    for diag in load_failures:
        name = (
            getattr(diag, "rune_name", None)
            or getattr(diag, "skill_name", None)
            or getattr(diag, "name", "unknown")
        )
        msg = getattr(diag, "message", str(diag))
        out(f"  [yellow]- {name}: {msg}[/yellow]")
    out("[dim]Run 'mvge info' for detailed diagnostic information.[/dim]\n")


def check_and_warn_missing_deps(
    diagnostics: list[Any],
    out: Callable[[str], None] = _console.print,
    prompt: Callable[[str], str] | None = None,
    install: Callable[[], Any] | None = None,
) -> bool:
    """Scan diagnostics for MISSING_DEP and print a recovery alert."""
    missing: list[Any] = []
    for diag in diagnostics:
        if _diag_kind(diag) == "missing_dep":
            missing.append(diag)

    if not missing:
        return False

    out("[bold yellow]Missing rune dependencies detected:[/bold yellow]")
    for diag in missing:
        name = getattr(diag, "rune_name", None) or getattr(diag, "name", "unknown")
        msg = getattr(diag, "message", str(diag))
        out(f"  [yellow]- {name}: {msg}[/yellow]")
    out("[dim]Run 'mvge setup install' to install the missing dependencies.[/dim]")

    if install is not None and prompt is not None:
        response = (
            prompt("Auto-install missing dependencies now? [y/N]: ").strip().lower()
        )
        if response == "y":
            install()
    out("")
    return True
