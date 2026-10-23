"""Execution-related models: steps, commands, file exploration, background tasks."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StepType(StrEnum):
    """Categorized type of an intermediate agent execution step."""

    WORKED = "worked"
    FILES = "files"
    COMMANDS = "commands"
    SUMMONING = "summoning"


class TaskStatus(StrEnum):
    """Lifecycle status of a background task tracked by the inspector."""

    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class BackgroundTask:
    """A long-running background task (spell or subagent) tracked in state."""

    id: str
    name: str
    status: TaskStatus = TaskStatus.RUNNING
    progress: float = 0.0
    parent_id: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    result: str = ""
    error: str | None = None


@dataclass
class CommandExecution:
    """Execution details of a shell command invocation."""

    command: str
    output: str = ""
    exit_code: int = 0
    is_error: bool = False
    duration_seconds: float = 0.0


@dataclass
class FileExploration:
    """Exploration details of a file or directory operation."""

    path: str
    operation: str = "read"
    lines: str | None = None
    details: str | None = None
    is_error: bool = False


@dataclass
class ExecutionStep:
    """A collapsible execution card grouping internal operations."""

    step_type: StepType
    title: str = ""
    duration_seconds: float = 0.0
    commands: list[CommandExecution] = field(default_factory=list)
    files: list[FileExploration] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    is_complete: bool = False
    spell_name: str = ""
    result: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sub_mvge_id: str | None = None
    child_tome_id: str | None = None
    status: str = "running"
    mana_spent: int = 0
