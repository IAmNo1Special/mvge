"""Tests that the Python embedded in workflow heredocs is valid Python.

This exists because of a measured failure. On 2026-10-10 #212 shipped a `bump`
job that could not run:

```
File "<stdin>", line 12
    updated, count = pattern.subn(...)
IndentationError: unexpected indent
```

Three things made that reach main. The edit was made inside a GitHub Actions
`run:` block, so it was YAML-valid. `release-tooling` runs `tests/`, and
nothing compiled the Python that YAML is only a container for. And the job that
died from it ran only on pushes to main, so the first place the failure was
visible was a red run on main, after the merge.

The lesson generalises past heredocs: a workflow file is not just YAML, it is
YAML with programs inside it, and only the YAML half was being checked. These
tests read the workflows as data and compile the other half.

Note on extraction. A `run: |` block is a YAML literal scalar, so `yaml` hands
back the script with the block's indentation already removed -- which is exactly
what `bash` receives. A heredoc opened with `<<'PY'` therefore closes on a line
that is `PY` at column zero of that returned string. Getting this wrong is how
the first draft of this file "passed" while checking nothing: it kept prose
from the surrounding script and compiled a body that never existed on the
runner.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

#: Heredoc openings that mean "the next lines are Python". `<<-` would strip
#: leading tabs, which no workflow here uses; it is left out on purpose so a
#: future tab-heredoc fails to be found rather than compile tab-mangled.
HEREDOC_OPENINGS = ("<<'PY'", '<<"PY"', "<<PY")


def _run_blocks() -> list[tuple[pathlib.Path, str, str, str]]:
    """Every `run` block as (workflow, job name, step name, script)."""
    found: list[tuple[pathlib.Path, str, str, str]] = []
    for workflow in WORKFLOWS:
        document = yaml.safe_load(workflow.read_text())
        for job_name, job in (document.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                script = step.get("run")
                if not isinstance(script, str):
                    continue
                found.append(
                    (
                        workflow,
                        job_name,
                        step.get("name") or f"step {index}",
                        script,
                    )
                )
    return found


def _heredocs(script: str) -> list[tuple[int, str, str]]:
    """Every Python heredoc in a script as (line number, body, terminator line).

    The body is what the runner feeds on stdin. The terminator line is returned
    so a caller can check *where* it closed -- an indented one never closes at
    all, because `<<'PY'` is not `<<-'PY'`.
    """
    found: list[tuple[int, str, str]] = []
    lines = script.split("\n")
    for index, line in enumerate(lines):
        if not any(opening in line for opening in HEREDOC_OPENINGS):
            continue
        body: list[str] = []
        terminator = ""
        for candidate in lines[index + 1 :]:
            if candidate == "PY":
                terminator = candidate
                break
            body.append(candidate)
        found.append((index + 1, "\n".join(body), terminator))
    return found


def _all_heredocs() -> list[tuple[pathlib.Path, str, str, int, str, str]]:
    """Every Python heredoc as (workflow, job, step, opening line, body, terminator)."""
    found: list[tuple[pathlib.Path, str, str, int, str, str]] = []
    for workflow, job_name, step_name, script in _run_blocks():
        for opening_line, body, terminator in _heredocs(script):
            found.append(
                (workflow, job_name, step_name, opening_line, body, terminator)
            )
    return found


def test_the_workflow_files_are_where_this_test_expects() -> None:
    """A glob that matches nothing passes every other test here vacuously."""
    names = {workflow.name for workflow in WORKFLOWS}
    assert names == {"ci.yml", "publish.yml", "release.yml", "scorecard.yml"}


def test_at_least_one_python_heredoc_was_found() -> None:
    """The glob and the openers both have to catch something to be worth running."""
    if not _all_heredocs():
        pytest.skip(
            "no python heredoc found in any workflow; all scripts are standalone files"
        )


@pytest.mark.parametrize(
    ("workflow", "job_name", "step_name", "opening_line", "body", "terminator"),
    _all_heredocs(),
    ids=lambda value: value.name if isinstance(value, pathlib.Path) else "",
)
def test_every_python_heredoc_is_terminated_at_column_zero(
    workflow: pathlib.Path,
    job_name: str,
    step_name: str,
    opening_line: int,
    body: str,
    terminator: str,
) -> None:
    """An unterminated heredoc does not fail loudly; it consumes the script.

    `<<'PY'` closes only on a line that is `PY` with nothing before it. Indent
    the terminator -- easy to do while re-indenting a step -- and the body
    swallows the rest of the script, `python3` reads a truncated program from
    stdin, and the shell then runs whatever came after as shell.
    """
    assert terminator == "PY", (
        f"{workflow.name}: job {job_name!r}, step {step_name!r} opens a python "
        f"heredoc at line {opening_line} that never terminates at column zero. "
        "The heredoc swallows the rest of the script. `<<'PY'` is not `<<-'PY'`, "
        "so an indented terminator does not close it."
    )


@pytest.mark.parametrize(
    ("workflow", "job_name", "step_name", "opening_line", "body", "terminator"),
    _all_heredocs(),
    ids=lambda value: value.name if isinstance(value, pathlib.Path) else "",
)
def test_every_python_heredoc_compiles(
    workflow: pathlib.Path,
    job_name: str,
    step_name: str,
    opening_line: int,
    body: str,
    terminator: str,
) -> None:
    """The Python inside a workflow is a program, so it has to parse.

    `ast.parse` rather than `py_compile`, because the point is syntax: an
    IndentationError inside a `run:` block is invisible to `yaml.safe_load`, to
    `bash -n`, and to a suite that only runs files under `tests/`.
    """
    source_name = f"{workflow.name}:{job_name}:{opening_line}"
    try:
        ast.parse(body, filename=source_name)
    except SyntaxError as error:
        raise AssertionError(
            f"{workflow.name}: job {job_name!r}, step {step_name!r} contains a "
            f"python heredoc that does not parse: {error.msg} at line "
            f"{error.lineno}. This is YAML-valid and shell-valid, and it fails "
            "only when the job runs on a push to main."
        ) from error
