"""Git diff parsing and workspace VCS inspection for MvgeOS GUI."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from mvgeos_gui.models import ChangedFile, DiffHunk, DiffLine, DiffView

__all__ = [
    "ChangedFile",
    "DiffHunk",
    "DiffLine",
    "DiffView",
    "get_changed_files",
    "get_diff_for_file",
    "parse_git_diff",
    "resolve_git_branch",
]

_DIFF_HEADER_RE = re.compile(
    r"^diff --git (?:\"?[a-zA-Z]/(.+?)\"? \"?[a-zA-Z]/(.+?)\"?|(.+?) (.+))$"
)
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<src_start>\d+)(?:,(?P<src_len>\d+))? "
    r"\+(?P<tgt_start>\d+)(?:,(?P<tgt_len>\d+))? @@"
)


def _parse_hunk_header(line: str) -> tuple[int, int, int, int] | None:
    match = _HUNK_HEADER_RE.match(line)
    if not match:
        return None
    src_start = int(match.group("src_start"))
    src_len = int(match.group("src_len") or "1")
    tgt_start = int(match.group("tgt_start"))
    tgt_len = int(match.group("tgt_len") or "1")
    return src_start, src_len, tgt_start, tgt_len


def parse_git_diff(diff_text: str) -> list[DiffView]:
    """Parse unified diff output into structured DiffView objects."""
    if not diff_text.strip():
        return []

    views: list[DiffView] = []
    current_view: DiffView | None = None
    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            if current_view is not None:
                views.append(current_view)
            match = _DIFF_HEADER_RE.match(raw_line)
            file_path = (
                (match.group(2) or match.group(4) or raw_line) if match else raw_line
            )
            current_view = DiffView(file_path=file_path)
            current_hunk = None
            old_line = 0
            new_line = 0
        elif raw_line.startswith("new file mode ") and current_view is not None:
            current_view.status = "added"
        elif raw_line.startswith("deleted file mode ") and current_view is not None:
            current_view.status = "deleted"
        elif raw_line.startswith(("index ", "--- ", "+++ ")):
            pass
        elif raw_line.startswith("@@"):
            if current_view is None:
                continue
            parsed = _parse_hunk_header(raw_line)
            if parsed is None:
                continue
            src_start, src_len, tgt_start, tgt_len = parsed
            old_line = src_start
            new_line = tgt_start
            current_hunk = DiffHunk(
                source_start=src_start,
                source_length=src_len,
                target_start=tgt_start,
                target_length=tgt_len,
            )
            current_view.hunks.append(current_hunk)
        elif current_hunk is not None and current_view is not None:
            if raw_line.startswith("+"):
                current_hunk.lines.append(
                    DiffLine(
                        content=raw_line[1:],
                        line_type="addition",
                        old_line_number=None,
                        new_line_number=new_line,
                    )
                )
                current_view.additions += 1
                new_line += 1
            elif raw_line.startswith("-"):
                current_hunk.lines.append(
                    DiffLine(
                        content=raw_line[1:],
                        line_type="deletion",
                        old_line_number=old_line,
                        new_line_number=None,
                    )
                )
                current_view.deletions += 1
                old_line += 1
            elif raw_line.startswith(" "):
                current_hunk.lines.append(
                    DiffLine(
                        content=raw_line[1:],
                        line_type="context",
                        old_line_number=old_line,
                        new_line_number=new_line,
                    )
                )
                old_line += 1
                new_line += 1

    if current_view is not None:
        views.append(current_view)

    return views


def _run_git(project_path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return result.stdout
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
        OSError,
    ):
        return ""


def get_changed_files(project_path: Path) -> list[ChangedFile]:
    """Return modified, added, and deleted files with +N / -N counts."""
    output = _run_git(project_path, "diff", "--numstat", "--no-ext-diff")
    if not output:
        return []
    files: list[ChangedFile] = []
    for line in output.splitlines():
        parts = line.split("	", 2)
        if len(parts) < 3:
            continue
        add_str, del_str, path = parts
        try:
            additions = int(add_str) if add_str != "-" else 0
            deletions = int(del_str) if del_str != "-" else 0
        except ValueError:
            additions = 0
            deletions = 0
        status = "modified"
        if additions > 0 and deletions == 0:
            status = "added"
        elif deletions > 0 and additions == 0:
            status = "deleted"
        files.append(
            ChangedFile(
                path=path,
                status=status,
                additions=additions,
                deletions=deletions,
            )
        )
    return files


def get_diff_for_file(project_path: Path, file_path: str) -> DiffView | None:
    """Return parsed diff for a single file, or None if unchanged."""
    output = _run_git(
        project_path,
        "diff",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--",
        file_path,
    )
    if not output.strip():
        return None
    views = parse_git_diff(output)
    return views[0] if views else None


def resolve_git_branch(cwd: str | Path) -> str | None:
    """Resolve the current git branch for a working directory.

    Returns the branch name, or None if the directory is not a git repo
    or git is unavailable.
    """
    cwd_path = Path(cwd).resolve()
    toplevel_output = _run_git(cwd_path, "rev-parse", "--show-toplevel").strip()
    if not toplevel_output:
        return None
    try:
        toplevel = Path(toplevel_output).resolve()
    except Exception:
        return None
    if toplevel != cwd_path:
        return None

    branch = _run_git(cwd_path, "branch", "--show-current").strip()
    return branch or None
