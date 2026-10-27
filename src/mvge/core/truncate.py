from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 51_200
GREP_MAX_LINE_LENGTH = 500

MAX_LIST_ENTRIES = 2000
MAX_FIND_ENTRIES = 2000
MAX_GREP_MATCHES = 100
MAX_LIST_RESULTS = 500
MAX_SPELL_RESULT_BYTES = 100_000
MAX_BASH_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class TruncationResult:
    """Bounded view of spell output with enough metadata to continue."""

    text: str
    truncated: bool
    strategy: Literal["head", "tail"] | None
    total_lines: int
    shown_start: int | None
    shown_end: int | None
    total_bytes: int
    version: int = 1


def format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable size."""
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / (1024 * 1024):.1f}MB"


def _split_lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _line_bytes(line: str) -> int:
    return len(line.encode("utf-8"))


def truncate_head(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the first lines/bytes; cut at line boundaries, UTF-8 safe."""
    total_bytes = len(content.encode("utf-8"))
    lines = _split_lines(content)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            text=content,
            truncated=False,
            strategy=None,
            total_lines=total_lines,
            shown_start=None,
            shown_end=None,
            total_bytes=total_bytes,
        )
    kept: list[str] = []
    used = 0
    for line in lines:
        if len(kept) >= max_lines:
            break
        cost = _line_bytes(line) + (1 if kept else 0)
        if used + cost > max_bytes:
            break
        kept.append(line)
        used += cost
    return TruncationResult(
        text="\n".join(kept),
        truncated=True,
        strategy="head",
        total_lines=total_lines,
        shown_start=1 if kept else None,
        shown_end=len(kept) if kept else None,
        total_bytes=total_bytes,
    )


def _tail_suffix(line: str, max_bytes: int) -> str:
    chars = list(line)
    used = 0
    keep = 0
    for char in reversed(chars):
        cost = len(char.encode("utf-8"))
        if used + cost > max_bytes:
            break
        used += cost
        keep += 1
    return "".join(chars[len(chars) - keep :]) if keep else ""


def truncate_tail(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the last lines/bytes; a lone huge line keeps its end, UTF-8 safe."""
    total_bytes = len(content.encode("utf-8"))
    lines = _split_lines(content)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            text=content,
            truncated=False,
            strategy=None,
            total_lines=total_lines,
            shown_start=None,
            shown_end=None,
            total_bytes=total_bytes,
        )
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if len(kept) >= max_lines:
            break
        cost = _line_bytes(line) + (1 if kept else 0)
        if used + cost > max_bytes:
            if not kept:
                suffix = _tail_suffix(line, max_bytes)
                if suffix:
                    kept.append(suffix)
            break
        kept.append(line)
        used += cost
    kept.reverse()
    shown_end = total_lines
    shown_start = total_lines - len(kept) + 1 if kept else None
    return TruncationResult(
        text="\n".join(kept),
        truncated=True,
        strategy="tail",
        total_lines=total_lines,
        shown_start=shown_start,
        shown_end=shown_end if kept else None,
        total_bytes=total_bytes,
    )


def truncate_line_around_match(
    line: str,
    matches: list[tuple[int, int]],
    budget: int = GREP_MAX_LINE_LENGTH,
) -> str:
    """Cap a long line while keeping match(es) visible with ellipsis markers."""
    if len(line) <= budget:
        return line
    if not matches:
        return f"{line[:budget]}..."
    first_start, first_end = matches[0]
    _, last_end = matches[-1]
    if last_end - first_start <= budget:
        spare = budget - (last_end - first_start)
        start = max(0, min(first_start - spare // 2, len(line) - budget))
        window = line[start : start + budget]
        prefix = "..." if start > 0 else ""
        suffix = "..." if start + budget < len(line) else ""
        return f"{prefix}{window}{suffix}"
    half_before = (budget - (first_end - first_start)) // 2
    start = max(0, first_start - half_before)
    window = line[start : start + budget]
    prefix = "..." if start > 0 else ""
    suffix = "..." if start + budget < len(line) else ""
    return f"{prefix}{window}{suffix}"


def truncation_details(
    result: TruncationResult,
    full_output_path: str | None = None,
    backstop_applied: bool = False,
) -> dict[str, Any]:
    """Build the versioned truncation details contract for spell results."""
    return {
        "truncation": {
            "version": 1,
            "truncated": result.truncated,
            "strategy": result.strategy,
            "total_lines": result.total_lines,
            "shown_start": result.shown_start,
            "shown_end": result.shown_end,
            "total_bytes": result.total_bytes,
            "full_output_path": full_output_path,
            "backstop_applied": backstop_applied,
        }
    }


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "MAX_BASH_BYTES",
    "MAX_FIND_ENTRIES",
    "MAX_GREP_MATCHES",
    "MAX_LIST_ENTRIES",
    "MAX_LIST_RESULTS",
    "MAX_SPELL_RESULT_BYTES",
    "TruncationResult",
    "format_size",
    "truncate_head",
    "truncate_line_around_match",
    "truncate_tail",
    "truncation_details",
]
