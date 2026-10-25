#!/usr/bin/env python3
"""Re-measure the published MvgeOS scorecard so a sceptic can falsify it.

Scorecard v1 was measured by hand. This script is the measurement code. Every
number it prints is produced by one of the seven measures below, and every measure
states the definition it uses so the reader can disagree with the definition
rather than with an unverifiable claim.

Run it with no arguments::

    uv run python scripts/scorecard.py

That measures every project and prints a table, exiting non-zero if a gate
regresses. Each project is a shallow clone (`--depth 1`, default branch)
cached under a temporary directory, with one exception: MvgeOS's own row is
measured from the checkout this script is run in, because a scorecard's own
row has to describe the code the reader is standing in. Pass `--clone-self` to
measure our published clone instead. Point any row at a local checkout with
`--project KEY=PATH`; every check except measure 5 runs without network.

Every row is stamped with the commit it was measured at, and every stamp carries
whether a remote ref contains that commit. `--update-baseline` refuses to write
when it does not: the snapshot is a permanent reference point, so a sha that
resolves only in the clone that measured it would make the published number
uncheckable for good. Run the re-record from a tree that is on a pushed ref.

THE SEVEN MEASURES AND THEIR EXACT DEFINITIONS

1. Source and test LOC
   A counted file is any `.py`, `.ts` or `.tsx` file in the checkout. Every
   language is counted, for every project, and the two totals are summed.

   THIS CHANGES THE MEASURE, AND IT CHANGES IT IN OUR FAVOUR, SO READ THIS.

   Scorecard v1 counted one language per project. v1's hermes-agent row is its
   900,282 Python lines and ignores its 624,949 lines of TypeScript; v1's
   deepseek-harness row is its 478,874 TypeScript lines and ignores its 8,244
   lines of Python. Counting all languages therefore moves hermes-agent's ratio
   from 1.43 to 1.07, and with hermes-agent below us rather than above us,
   MvgeOS moves from fourth to third of seven on test density. Exactly three
   ratios are above ours either way: adk-python 1.71 and openclaw 1.51, plus
   hermes-agent at 1.43 under v1's rule and not at all under this one. An
   earlier draft of this comment said fifth to third, which was wrong; the
   ranks above are recomputed from the same snapshot they are published with.

   We count all languages anyway, for two reasons, and the reader should hold
   both against the paragraph above:

   a) It is the stated definition. The measure is specified as "any `.py`,
      `.ts` or `.tsx` file". Narrowing it to one language so the numbers match
      a hand-measured document makes the script a rubber stamp for that
      document, which is the opposite of what a reproducible benchmark is for.
      The correction goes in the document, not in the script.
   b) The one-language rule has no stated basis and is not self-consistent. It
      counts hermes-agent's Python and deepseek-harness's TypeScript, which is
      "whatever language the project's own tooling is written in", an inference
      a reader cannot check from the output. It also silently drops a third of
      hermes-agent's source lines from a measure about source lines.

   A reader who thinks the mixed ratio is the wrong measure should still have
   the information to disagree: the per-language breakdown of both totals is in
   the baseline snapshot, and a project that is not single-language has its
   split printed under the table. MvgeOS's own row is single-language, so this
   decision does not move our own ratio in either direction -- it moves our
   RANK, by pushing hermes-agent below us.

   A counted file is EXCLUDED when any directory component is one of
   `node_modules`, `dist`, `build`, `vendor`, `out`, `coverage`, `.next`,
   `target`, `__pycache__`, `.git`, `.venv`, `venv`, `.tox`, `.eggs` or
   `site-packages`. The last five are not in v1's list and have to be: the
   documented command creates `.venv/` in this repository before the script
   runs, and counting it reports MvgeOS as a million-line project.

   A counted file is TEST code when any directory component is one of `test`,
   `tests`, `__tests__`, `spec`, or the file name matches one of
   `test_*`, `*_test.py`, `*.test.ts`, `*.test.tsx`, `*.spec.ts`,
   `*.spec.tsx`. Everything else is SOURCE code.

   `scripts/` is counted as SOURCE and is deliberately NOT in
   `EXCLUDED_DIRS`. It was a candidate: `scripts/pypi_new_project_window.py`
   landed in v0.6.8 with 181 source lines and no tests, which put MvgeOS's
   `test_ratio` below its baseline and turned the gate red, and adding
   `scripts` to the list below would have turned it green at once.

   It was not done, because the exclusion is far larger than the 181 lines
   that provoked it. Measured with `measure_loc` on main at d31cbfc:

       as measured       src=41321 test=57692 ratio=1.396191
       `scripts/` source = 2,315 lines, of which this file is 1,929
       excluded          src=39006 test=57692 ratio=1.479055   (+0.082864)

   The gate would have been cleared by removing this file -- the thing doing
   the measuring -- from its own denominator. A published number moved that
   way is not a measurement, so the helper was tested instead, in
   `scripts/tests/quota_window.py`, over the quota arithmetic rather than the
   HTTP call. Re-derive either figure with `measure_loc`; neither is meant to
   be taken on trust.

   Lines are `len(text.splitlines())` over the whole file: blank lines and
   comment lines included. This is the definition v1 used and it is what makes
   the mvgeos row reproduce exactly (38,971 / 57,545). A "physical lines"
   figure is not a statement about how much code exists; it is a reproducible
   one, which is the only thing a benchmark needs.

2. Type strictness
   For a Python project: `[tool.mypy] strict` from any `pyproject.toml` in
   the checkout. For a TypeScript project: `compilerOptions.strict` in the
   root `tsconfig.json`, following the `extends` chain until a config that
   sets it is found. A project's nearest ancestor wins, matching how `tsc`
   merges configs. Reported as `true`, `false` or `unset`. Never
   inferred, never carried over from a sibling config that does not set it.

   This measure exists because v1 got it wrong by reading each tsconfig in
   isolation: pi and deepseek-harness both set `strict: true` in a
   `tsconfig.base.json` that the root config extends. Following the chain is
   not a refinement, it is the definition.

3. Enforced coverage floor
   The `fail_under` or `fail-under` key in `pyproject.toml` (under
   `[tool.coverage.report]`, `[tool.coverage]`, or `[coverage:report]`),
   `setup.cfg`, `.coveragerc` or `tox.ini`. The script then looks for a CI
   job that runs a coverage reporter without disabling the floor, and reports
   that job's name. `none` means no floor was found.

   Known blind spot, stated rather than hidden: a project that enforces coverage
   through Codecov's `target:` or a vitest/jest threshold instead of a
   `fail_under` key is reported as `none`. None of the seven projects does
   this today.

4. Cross-extension runtime imports  (the gate)
   Only for projects that declare an extension root. Each direct subdirectory of
   that root is one extension. An extension owns the importable name of any
   `mvge.runes_*` directory it contains, or else its own directory name.

   Every runtime source file under the extension root is parsed with `ast`
   -- never with grep, because grep cannot tell an import from a string that
   mentions one, and cannot tell a runtime import from a comment. Test files
   and any directory component in the exclusion list are skipped. An absolute
   import whose first dotted segment is owned by a DIFFERENT extension is
   counted as a cross-extension runtime import.

   A file that will not parse is reported as a parse failure with its path, and
   a parse failure is a FAILURE. It is never reported as a passing zero. This
   is the failure mode that would let the gate lie: a Rune saved with a syntax
   error under a Python version this interpreter cannot parse would silently
   contribute zero imports and look like a clean boundary.

   THE GATE: for a gated project, any cross-extension runtime import or any
   parse failure exits non-zero. Only mvgeos is gated. hermes-agent has a
   cross-plugin import today and that is a measurement, not our regression; a
   peer's code cannot fail our build.

5. Installability  (needs network, `--online`)
   One HTTP GET per package name against the package's index:
   `https://pypi.org/pypi/<name>/json` or
   `https://registry.npmjs.org/<name>`. The HTTP status code is reported.
   404 means a stranger cannot install it. Without `--online` this column
   reads `skipped` and the exit code is unaffected.

6. Annotation coverage
   Per Python source file, over every `def` and `async def` in the file
   (methods included), ignoring `self` and `cls`:

   fully annotated (1.0)
       every function in the file has an annotation on every parameter and on
       its return;
   partially annotated (0.5)
       not fully, but at least one parameter or return annotation appears
       somewhere in the file;
   nothing (0.0)
       no annotations at all, or the file defines no functions.

   A file that defines no functions scores 0, which is the only non-absurd
   choice: scoring it 1 would report mvgeos at 100%, and excluding it from the
   denominator would do the same. This is a count of annotations present in
   source text. It is NOT a type-checker result and it is not a substitute for
   one. mypy strict passing is a different and stronger claim, reported
   separately by measure 2.

7. Release automation
   The number of workflow FILES directly in `<checkout>/.github/workflows`,
   counting `*.yml` and `*.yaml`. Not recursive: a file in a subdirectory of
   `.github/workflows` is not counted. One file is one workflow, whatever it is
   called -- `ci.yml`, `ci.yaml` and `release-checks.yml` each count once, and a
   reusable workflow called via `workflow_call` counts the same as any other.

   A project with no `.github/workflows` directory reports 0, not null and not an
   error: no workflows is a measurement, and a missing column would read like a
   measurement that never ran.

   This is the weakest axis on the scorecard and is reported as a count for that
   reason. A count of files is not a measure of whether releases are automated,
   of how good they are, or of whether they succeed; it is the axis the published
   document had hand-counted, and this makes the count reproducible. It is never
   treated as a regression (see REGRESSION_DIRECTION), because openclaw's 111 is
   not eleven times more released than a project's 10.

EXIT CODES
==========
0   no gate violation
1   a gate regressed: cross-extension import, parse failure, or a regression in
    a gated project's own row against the baseline snapshot
2   usage or environment error
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

# --------------------------------------------------------------------------
# Definitions. Every constant here is echoed in the module docstring above and
# in --help. Change one and you must change the other.
# --------------------------------------------------------------------------

#: File extensions counted by measure 1, per project language.
#:
#: Measure 1 counts every extension in this table for every project. The
#: per-language grouping is still needed by measures 2 and 6, which are
#: language-specific by nature: a tsconfig says nothing about Python, and an
#: annotation count over a `.tsx` file is not a thing.
LANGUAGE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "Python": (".py",),
    "TypeScript": (".ts", ".tsx"),
}

#: Measure 1 counts all of these, for every project, in one pass. See the
#: measure 1 section of the docstring for why a project is not narrowed to
#: one language, and for the disclosure of what that choice costs us.
COUNTED_SUFFIXES: tuple[str, ...] = tuple(
    sorted({suffix for suffixes in LANGUAGE_SUFFIXES.values() for suffix in suffixes})
)

#: A counted file under any of these directories is not counted at all.
#:
#: The virtual-environment and egg entries are not in scorecard v1's list and
#: they have to be: `uv run python scripts/scorecard.py` -- the documented
#: command -- creates `.venv/` in this repository before the script runs, and
#: without these entries MvgeOS's own row measures 1,014,561 source lines
#: instead of 40,545, because it is counting every installed dependency.
EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        "dist",
        "build",
        "vendor",
        "out",
        "coverage",
        ".next",
        "target",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".tox",
        ".eggs",
        "site-packages",
    }
)

#: A counted file under any of these directories is test code.
TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec"})

#: A counted file matching any of these globs is test code.
TEST_FILENAME_PATTERNS = (
    "test_*",
    "*_test.py",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.ts",
    "*.spec.tsx",
)

#: Measure 7 counts files in `.github/workflows` with one of these suffixes.
#: Spelled out rather than borrowed from the `*.y*ml` glob measure 3 uses: that
#: glob also matches extensions GitHub does not run (`.yxml`), and two measures
#: with one stated convention each must not silently share a third.
WORKFLOW_SUFFIXES = (".yml", ".yaml")

PYPI_URL = "https://pypi.org/pypi/{name}/json"
NPM_URL = "https://registry.npmjs.org/{name}"

#: The repository this script lives in. MvgeOS's own row is measured from here
#: rather than from a fresh clone of the published repository, because the
#: scorecard's own row has to describe the code the reader is standing in.
#: Cloning ourselves would mean the number we are judged on changes the moment
#: a change merges, with no commit to point at, and the gate would stay blind
#: to every regression until it landed.
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CACHE_DIRNAME = "mvgeos-scorecard-clones"
DEFAULT_BASELINE = Path("docs/scorecard-baseline.json")
BASELINE_SCHEMA = 1

#: The docstring heading that opens the measures list, and the epilog --help
#: prints verbatim. Spelling the count in one constant keeps the two copies of
#: the heading from disagreeing, which is the only thing this constant is for.
MEASURES_HEADING = "THE SEVEN MEASURES"


# --------------------------------------------------------------------------
# Project table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Extension:
    """Where a project's independently installable extensions live.

    ``slug`` is a separate repository because for MvgeOS the Runes are not in
    this repository at all. ``package_prefix`` names the importable package
    family a Rune owns; ``""`` means an extension owns only its directory
    name, which is the case for hermes-agent's plugins.
    """

    slug: str
    root: str
    package_prefix: str


@dataclass(frozen=True)
class Project:
    """One row of the scorecard."""

    key: str
    slug: str
    language: str
    #: (index, package name) pairs probed by measure 5.
    packages: tuple[tuple[str, str], ...] = ()
    #: Extension boundary to measure, or None when the project has none.
    extension: Extension | None = None
    #: A gated project can fail the run. See measure 4 in the docstring.
    gated: bool = False


PROJECTS: tuple[Project, ...] = (
    Project(
        key="mvge",
        slug="IAmNo1Special/mvge",
        language="Python",
        packages=(("pypi", "mvge"),),
        # The Runes ship in the marketplace repository, not here. AGENTS.md is
        # explicit that Rune capabilities live in mvgeos-marketplace.
        extension=Extension(
            slug="IAmNo1Special/mvgeos-marketplace",
            root="runes",
            package_prefix="mvge.runes_",
        ),
        gated=True,
    ),
    Project(
        key="adk-python",
        slug="google/adk-python",
        language="Python",
        packages=(("pypi", "google-adk"),),
    ),
    Project(
        key="smolagents",
        slug="huggingface/smolagents",
        language="Python",
        packages=(("pypi", "smolagents"),),
    ),
    Project(
        key="hermes-agent",
        slug="nousresearch/hermes-agent",
        language="Python",
        packages=(("pypi", "hermes-agent"),),
        extension=Extension(
            slug="nousresearch/hermes-agent",
            root="plugins",
            package_prefix="",
        ),
    ),
    Project(
        key="openclaw",
        slug="openclaw/openclaw",
        language="TypeScript",
        packages=(("npm", "openclaw"),),
    ),
    Project(
        key="deepseek-harness",
        slug="deepseek-ai/deepseek-harness",
        language="TypeScript",
        packages=(("npm", "@deepseek-ai/dsh"),),
    ),
    Project(
        key="pi",
        slug="earendil-works/pi",
        language="TypeScript",
        packages=(("npm", "@earendil-works/pi-coding-agent"),),
    ),
)

PROJECTS_BY_KEY = {project.key: project for project in PROJECTS}

#: The project whose row defaults to the checkout this script is run in.
SELF_PROJECT_KEY = "mvgeos"


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


class ParseFailure(NamedTuple):
    """A source file that could not be parsed. Always a failure, never a zero."""

    path: str
    error: str


class CrossImport(NamedTuple):
    """One import of a sibling extension's package from runtime source."""

    path: str
    lineno: int
    module: str
    owner: str


@dataclass
class Result:
    """Everything measured for one project."""

    key: str
    source: str
    #: Short commit SHA the row was measured at, when the source is a git
    #: checkout. A number without the commit it was measured at cannot be
    #: checked, and a shallow clone of a moving peer is only as good as its age.
    commit: str | None = None
    #: Whether ``commit`` is reachable from a ref fetched from the remote, so
    #: that a reader can resolve it in their own clone. ``False`` means the sha
    #: exists but is not published, which is worse than ``None``: ``None`` says
    #: "no commit", ``False`` says "here is a commit you cannot check". See
    #: ``commit_is_published``.
    commit_published: bool | None = None
    #: Set when the project could not be obtained or read at all. The row is
    #: still printed. A missing row that looks like a low score is worse than
    #: an error.
    error: str | None = None
    source_loc: int | None = None
    test_loc: int | None = None
    source_files: int | None = None
    #: Per-language ``(source_loc, test_loc)`` breakdown of the two totals above.
    #: Measure 1 counts every language; this says what was in each one, so a
    #: reader is never asked to take a blended ratio on trust.
    language_split: dict[str, tuple[int, int]] = field(default_factory=dict)
    type_strictness: str | None = None
    type_strictness_detail: str | None = None
    coverage_fail_under: float | None = None
    coverage_job: str | None = None
    coverage_detail: str | None = None
    extension_count: int | None = None
    cross_imports: int | None = None
    cross_import_list: list[CrossImport] = field(default_factory=list)
    parse_failures: list[ParseFailure] = field(default_factory=list)
    annotation_files: int | None = None
    annotation_fully: int | None = None
    annotation_partial: int | None = None
    annotation_weighted: float | None = None
    #: Measure 7. Always an int once a checkout was read, including 0 for a
    #: project with no `.github/workflows` directory. None only when the project
    #: could not be obtained at all, which is already reported in `error`.
    workflow_files: int | None = None
    install: dict[str, str] = field(default_factory=dict)

    @property
    def test_ratio(self) -> float | None:
        if not self.source_loc:
            return None
        return (self.test_loc or 0) / self.source_loc

    @property
    def coverage_text(self) -> str:
        if self.coverage_fail_under is None:
            return "none"
        job = self.coverage_job or "not enforced in CI"
        return f"{self.coverage_fail_under:g} ({job})"


# --------------------------------------------------------------------------
# Measure 1: source and test LOC
# --------------------------------------------------------------------------


def is_test_file(relative: Path) -> bool:
    """True when a counted file is test code. See measure 1 in the docstring."""
    if TEST_DIRS.intersection(relative.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(relative.name, p) for p in TEST_FILENAME_PATTERNS)


def is_excluded(relative: Path) -> bool:
    """True when a file must not be counted at all."""
    return bool(EXCLUDED_DIRS.intersection(relative.parts))


def iter_counted_files(root: Path, language: str | None = None) -> list[Path]:
    """Every countable source file under ``root``, sorted for reproducibility.

    ``language`` restricts the walk to one language's extensions. ``None`` means
    every extension measure 1 counts, which is what measure 1 asks for.
    """
    suffixes = COUNTED_SUFFIXES if language is None else LANGUAGE_SUFFIXES[language]
    found: list[Path] = []
    for suffix in suffixes:
        for path in root.rglob(f"*{suffix}"):
            relative = path.relative_to(root)
            if is_excluded(relative):
                continue
            found.append(path)
    return sorted(found)


def short_commit(checkout: Path) -> str | None:
    """Short SHA of a checkout, or None when it is not a git working tree."""
    if not (checkout / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def commit_is_published(checkout: Path, sha: str) -> bool:
    """True when ``sha`` is reachable from a ref fetched from the remote.

    A baseline is a permanent reference point, so a sha that resolves only in
    the clone that measured it is not provenance, it is a rumour. ``short_commit``
    cannot tell the difference: it reports whatever ``HEAD`` happens to be, which
    includes a local merge nobody pushed and which ``git gc`` is free to delete.
    A stamp like that looks exactly like a checkable one until the day somebody
    tries to check it and it is gone.

    Reachability is asked of the remote-tracking refs, so this answers "would a
    fresh clone of this checkout contain this commit", which is the only
    question a reader of the published baseline can act on. A checkout with no
    remote cannot vouch for anything: ``branch --remotes`` prints nothing, which
    is a false here rather than an error, because an unverifiable stamp must
    never be mistaken for a verified one.
    """
    if not (checkout / ".git").exists():
        return False
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), "branch", "--remotes", "--contains", sha],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


def parse_python(path: Path) -> ast.Module:
    """Parse a Python file, or raise SyntaxError.

    Parsing a peer repository must not print that peer's SyntaxWarnings into
    the middle of our table: hermes-agent has a docstring with an invalid
    escape sequence, and a benchmark whose output is interleaved with another
    project's warnings is harder to read and harder to diff.
    """
    text = path.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(text, filename=str(path))


def physical_lines(path: Path) -> int:
    """Lines in a file, blank and comment lines included."""
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def measure_loc(root: Path) -> tuple[int, int, int, int, dict[str, tuple[int, int]]]:
    """Return ``(source_loc, test_loc, source_files, test_files, per_language)``.

    ``per_language`` maps each language that contributed lines to its own
    ``(source_loc, test_loc)`` pair. Measure 1 counts every language, so the
    pair is a breakdown of the total rather than a filter on it. It is carried
    into the baseline and printed for any project that is not single-language,
    because a ratio over two languages is only interpretable if the reader can
    see what was in the other one.
    """
    source_loc = test_loc = source_files = test_files = 0
    per_language: dict[str, tuple[int, int]] = {}
    for path in iter_counted_files(root):
        language = _language_of(path)
        lines = physical_lines(path)
        is_test = is_test_file(path.relative_to(root))
        if is_test:
            test_loc += lines
            test_files += 1
        else:
            source_loc += lines
            source_files += 1
        lang_source, lang_test = per_language.get(language, (0, 0))
        if is_test:
            lang_test += lines
        else:
            lang_source += lines
        per_language[language] = (lang_source, lang_test)
    return source_loc, test_loc, source_files, test_files, per_language


def _language_of(path: Path) -> str:
    """The project language a file belongs to, by its extension."""
    if path.suffix == ".py":
        return "Python"
    return "TypeScript"


# --------------------------------------------------------------------------
# Measure 2: type strictness
# --------------------------------------------------------------------------


def strip_jsonc(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments from JSON-with-comments.

    tsconfig files are JSONC and routinely carry comments. A regex cannot do
    this safely because a URL in a string value contains ``//``. This walks the
    text once and tracks whether it is inside a string, which is the only
    correct way to tell ``"https://x"`` from a comment.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length:
            following = text[index + 1]
            if following == "/":
                while index < length and text[index] != "\n":
                    index += 1
                continue
            if following == "*":
                index += 2
                while index + 1 < length and text[index : index + 2] != "*/":
                    index += 1
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def load_jsonc(path: Path) -> dict[str, Any] | None:
    """Parse a JSONC file, or return None if it cannot be read as an object."""
    try:
        raw = strip_jsonc(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    # A trailing comma before a closing brace or bracket is legal in JSONC and
    # fatal to json.loads.
    cleaned = re.sub(r",(\s*[}\]])", r"\1", raw)
    try:
        loaded = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def python_type_strictness(root: Path) -> tuple[str, str]:
    """Read ``[tool.mypy] strict``. Returns ``(state, detail)``."""
    found: dict[str, str] = {}
    for pyproject in sorted(root.rglob("pyproject.toml")):
        relative = pyproject.relative_to(root)
        if is_excluded(relative) or EXCLUDED_DIRS.intersection(relative.parts[:-1]):
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        mypy = data.get("tool", {}).get("mypy", {})
        if isinstance(mypy, dict) and "strict" in mypy:
            value = mypy["strict"]
            if isinstance(value, bool):
                found[str(relative)] = "true" if value else "false"
    if not found:
        return "unset", "no [tool.mypy] strict key in any pyproject.toml"
    if any(state == "true" for state in found.values()):
        setting = next(p for p, s in found.items() if s == "true")
        return "true", f"{setting} sets strict = true"
    setting = next(iter(found))
    return "false", f"{setting} sets strict = false"


def _extends_target(config: dict[str, Any]) -> str | None:
    """The last ``extends`` entry, which is the one that wins in TypeScript."""
    extends = config.get("extends")
    if isinstance(extends, str):
        return extends
    if isinstance(extends, list) and extends:
        last = extends[-1]
        return last if isinstance(last, str) else None
    return None


def typescript_type_strictness(root: Path) -> tuple[str, str]:
    """Resolve ``compilerOptions.strict`` for the root tsconfig.

    Follows the ``extends`` chain because that is how ``tsc`` resolves it.
    The nearest config that sets ``strict`` wins; a config that only inherits
    one is not ``unset``, and a config that is never reached by the root is
    not evidence of anything.
    """
    seen: list[Path] = []
    config_path = root / "tsconfig.json"
    # tsconfig extends chains are shallow in practice; the cap stops a cycle.
    for _ in range(16):
        if config_path in seen or not config_path.is_file():
            break
        seen.append(config_path)
        config = load_jsonc(config_path)
        if config is None:
            return "unset", f"{config_path.name} could not be parsed as JSONC"
        options = config.get("compilerOptions", {})
        if isinstance(options, dict) and isinstance(options.get("strict"), bool):
            value = "true" if options["strict"] else "false"
            chain = " -> ".join(p.name for p in seen)
            return value, f"{chain} (strict set in {config_path.name})"
        target = _extends_target(config)
        if not target:
            break
        # A package specifier (a shared config from node_modules) is not
        # resolvable from a shallow clone without an install.
        if not target.startswith("."):
            return "unset", f"root tsconfig extends non-relative '{target}'"
        config_path = (config_path.parent / target).resolve()
        if not config_path.suffix:
            config_path = config_path.with_suffix(".json")
    chain = " -> ".join(p.name for p in seen) or "tsconfig.json"
    return "unset", f"no compilerOptions.strict in {chain}"


def measure_type_strictness(root: Path, language: str) -> tuple[str, str]:
    """Return ``(state, detail)`` for measure 2."""
    if language == "Python":
        return python_type_strictness(root)
    return typescript_type_strictness(root)


# --------------------------------------------------------------------------
# Measure 3: enforced coverage floor
# --------------------------------------------------------------------------

#: Keys that hold a coverage floor, and how to read them out of each file type.
_FAIL_UNDER_RE = re.compile(r"fail[_-]under\s*[:=]\s*[\"']?(\d+(?:\.\d+)?)", re.I)
#: A coverage reporter being invoked in a CI step.
_COVERAGE_RUN_RE = re.compile(
    r"coverage\s+(?:report|combine|html|xml)|pytest[^\n]*--cov|--cov[=\s]|check-coverage",
    re.I,
)
#: An explicit opt-out of the floor, which makes the job not enforce it.
_COVERAGE_OPT_OUT_RE = re.compile(
    r"--cov-fail-under=0|--no-cov-fail-under|--cov-fail-under[= ]0\b", re.I
)


def _coverage_fail_under(root: Path) -> tuple[float | None, str]:
    """Find a declared coverage floor. Returns ``(value, detail)``."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            data = {}
        coverage = data.get("tool", {}).get("coverage", {})
        if isinstance(coverage, dict):
            report = coverage.get("report", {})
            candidates = [coverage.get("fail_under")]
            if isinstance(report, dict):
                candidates.append(report.get("fail_under"))
            for candidate in candidates:
                if isinstance(candidate, (int, float)) and not isinstance(
                    candidate, bool
                ):
                    return float(candidate), "pyproject.toml [tool.coverage.report]"
    for name in (".coveragerc", "setup.cfg", "tox.ini"):
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = _FAIL_UNDER_RE.search(text)
        if found:
            return float(found.group(1)), f"{name}"
    return None, "no fail_under key found"


def _enforcing_workflow_job(root: Path) -> str | None:
    """Name a CI job that runs a coverage reporter without disabling the floor.

    A floor nothing runs is a comment. This looks for the job that would
    actually fail, and refuses to count a job that passes
    ``--cov-fail-under=0`` or is marked ``continue-on-error``.
    """
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return None
    for workflow in sorted(workflows.glob("*.y*ml")):
        try:
            text = workflow.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Start at `jobs:`. The `on:` block above it is also two-space
        # indented, and `on: { pull_request: }` would otherwise read as a job
        # whose body is the entire rest of the file.
        jobs_at = re.search(r"^jobs:\s*$", text, re.M)
        if jobs_at is None:
            continue
        text = text[jobs_at.end() :]
        # Job headers are two-space indented keys under `jobs:`.
        for match in re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", text, re.M):
            job = match.group(1)
            if job in {"name", "on", "permissions", "defaults", "concurrency"}:
                continue
            body = text[match.end() :]
            next_job = re.search(r"^  [A-Za-z0-9_-]+:\s*$", body, re.M)
            block = body[: next_job.start()] if next_job else body
            if not _COVERAGE_RUN_RE.search(block):
                continue
            if _COVERAGE_OPT_OUT_RE.search(block):
                continue
            return f"{workflow.name}:{job}"
    return None


def measure_coverage_floor(root: Path) -> tuple[float | None, str | None, str]:
    """Return ``(fail_under, enforcing_job, detail)`` for measure 3."""
    value, detail = _coverage_fail_under(root)
    if value is None:
        return None, None, detail
    job = _enforcing_workflow_job(root)
    if job is None:
        return value, None, f"{detail}; no CI job runs a coverage reporter"
    return value, job, f"{detail}; enforced by {job}"


# --------------------------------------------------------------------------
# Measure 4: cross-extension runtime imports (the gate)
# --------------------------------------------------------------------------


def extension_identities(root: Path, spec: Extension) -> dict[str, str]:
    """Map each extension directory to the importable name it owns."""
    root_path = root / spec.root
    identities: dict[str, str] = {}
    if not root_path.is_dir():
        return identities
    for child in sorted(p for p in root_path.iterdir() if p.is_dir()):
        owned = child.name
        if spec.package_prefix:
            for candidate in sorted(child.iterdir()):
                if (
                    candidate.is_dir()
                    and candidate.name.startswith(spec.package_prefix)
                    and (candidate / "__init__.py").is_file()
                ):
                    owned = candidate.name
                    break
        identities[child.name] = owned
    return identities


def _runtime_sources(extension_dir: Path) -> list[Path]:
    """Runtime source files of one extension, tests and build output excluded."""
    found: list[Path] = []
    for path in sorted(extension_dir.rglob("*.py")):
        relative = path.relative_to(extension_dir)
        if TEST_DIRS.intersection(relative.parts[:-1]):
            continue
        if is_test_file(relative):
            continue
        if EXCLUDED_DIRS.intersection(relative.parts):
            continue
        found.append(path)
    return found


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """Every absolute module named by an import, with its line number.

    Walks the whole tree, so imports inside ``try`` blocks and under
    ``TYPE_CHECKING`` are counted too. Relative imports are skipped: they
    cannot name a sibling extension.
    """
    modules: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            modules.append((node.module or "", node.lineno))
    return modules


def _names_extension(module: str, owned: str, root_name: str) -> bool:
    """True when a dotted module path names the extension that owns `owned`.

    Two layouts occur in this peer set and both have to work:

    * an extension installed as its own distribution, imported as
      `mvge.runes_x` (MvgeOS Runes);
    * an extension living inside the extension root package, imported as
      `plugins.web` (hermes-agent plugins).

    So the root's directory name is stripped before matching, because whether
    it appears in an import path is a packaging detail, not a boundary.
    """
    candidates = {module}
    if module.startswith(f"{root_name}."):
        candidates.add(module[len(root_name) + 1 :])
    return any(
        candidate == owned or candidate.startswith(f"{owned}.")
        for candidate in candidates
    )


def measure_cross_extension(
    root: Path, spec: Extension, base: Path
) -> tuple[int, list[CrossImport], list[ParseFailure], str]:
    """Measure 4. Returns ``(count, imports, parse_failures, detail)``."""
    root_path = root / spec.root
    if not root_path.is_dir():
        return (
            0,
            [],
            [],
            f"no {spec.root}/ directory at {base / spec.root}; "
            "extension isolation not measured",
        )
    identities = extension_identities(root, spec)
    if not identities:
        return 0, [], [], f"{spec.root}/ contains no extension directories"
    hits: list[CrossImport] = []
    failures: list[ParseFailure] = []
    for name in sorted(identities):
        siblings = {owned for other, owned in identities.items() if other != name}
        for source in _runtime_sources(root_path / name):
            try:
                tree = parse_python(source)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                # A file that will not parse is a failure. Reporting it as a
                # passing zero would let the gate lie about the boundary.
                failures.append(ParseFailure(str(source.relative_to(base)), str(exc)))
                continue
            for module, lineno in _imported_modules(tree):
                if any(
                    _names_extension(module, owned, root_path.name)
                    for owned in siblings
                ):
                    hits.append(
                        CrossImport(
                            path=str(source.relative_to(base)),
                            lineno=lineno,
                            module=module,
                            owner=name,
                        )
                    )
    return len(hits), hits, failures, f"{len(identities)} extensions"


# --------------------------------------------------------------------------
# Measure 5: installability (network)
# --------------------------------------------------------------------------


def probe_package(index: str, name: str, timeout: float) -> str:
    """One HTTP GET against a package index. Returns a status code or an error."""
    template = PYPI_URL if index == "pypi" else NPM_URL
    request = urllib.request.Request(
        template.format(name=name),
        headers={"User-Agent": "mvgeos-scorecard/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(response.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"error: {exc}"


def measure_installability(project: Project, timeout: float) -> dict[str, str]:
    """Measure 5. One GET per package name; empty when not run."""
    return {
        f"{index}:{name}": probe_package(index, name, timeout)
        for index, name in project.packages
    }


# --------------------------------------------------------------------------
# Measure 6: annotation coverage
# --------------------------------------------------------------------------


def _annotated_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    """Parameters needing an annotation: everything but ``self`` and ``cls``."""
    arguments = function.args
    params = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if arguments.vararg is not None:
        params.append(arguments.vararg)
    if arguments.kwarg is not None:
        params.append(arguments.kwarg)
    return [param for param in params if param.arg not in ("self", "cls")]


def _function_definitions(
    tree: ast.AST,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def file_annotation_score(tree: ast.AST) -> float:
    """1.0 fully annotated, 0.5 partially, 0.0 nothing. See measure 6."""
    functions = _function_definitions(tree)
    if not functions:
        return 0.0
    all_fully = True
    any_annotated = False
    for function in functions:
        params = _annotated_parameters(function)
        fully = function.returns is not None and all(
            param.annotation is not None for param in params
        )
        annotated = function.returns is not None or any(
            param.annotation is not None for param in params
        )
        all_fully = all_fully and fully
        any_annotated = any_annotated or annotated
    if all_fully:
        return 1.0
    return 0.5 if any_annotated else 0.0


def measure_annotation_coverage(
    root: Path, language: str
) -> tuple[int, int, int, float, list[ParseFailure]]:
    """Measure 6. Returns files, fully, partial, weighted fraction, failures."""
    if language != "Python":
        return 0, 0, 0, 0.0, []
    # ^ 0.0 is a real zero for a Python project with no source files; the
    # renderer shows "n/a" for a project that is not Python at all.
    files = fully = partial = 0
    weighted = 0.0
    failures: list[ParseFailure] = []
    for path in iter_counted_files(root, language):
        if is_test_file(path.relative_to(root)):
            continue
        try:
            tree = parse_python(path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            failures.append(ParseFailure(str(path.relative_to(root)), str(exc)))
            continue
        files += 1
        score = file_annotation_score(tree)
        weighted += score
        if score == 1.0:
            fully += 1
        elif score == 0.5:
            partial += 1
    return files, fully, partial, (weighted / files if files else 0.0), failures


# --------------------------------------------------------------------------
# Measure 7: release automation
# --------------------------------------------------------------------------


def measure_release_automation(root: Path) -> int:
    """Measure 7. Workflow files directly in ``.github/workflows``. See the
    measure 7 section of the docstring for the convention.

    ``iterdir`` rather than ``glob``: the directory is not walked into, so a
    subdirectory of ``.github/workflows`` contributes nothing, and no directory
    matches a ``*.yml`` suffix in the first place. A missing directory is 0,
    not None, because "this project ships no workflows" is an answer rather than
    a measurement that failed to run.

    An unreadable directory is not caught and is not reported as 0. A zero that
    means "we could not look" is the failure mode this whole axis was measured
    to remove, so it is left to raise, the way an unreadable source file does in
    measure 1.
    """
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return 0
    return sum(
        1
        for entry in workflows.iterdir()
        if entry.is_file() and entry.suffix in WORKFLOW_SUFFIXES
    )


# --------------------------------------------------------------------------
# Obtaining sources
# --------------------------------------------------------------------------


def default_cache_dir() -> Path:
    """Shallow clones live here so a second run costs nothing."""
    return Path(tempfile.gettempdir()) / DEFAULT_CACHE_DIRNAME


def resolve_source(
    spec: str | Path, cache_dir: Path, timeout: float, refresh: bool
) -> tuple[Path | None, str]:
    """Return ``(checkout, note)`` for a path or a ``owner/name`` slug.

    A path is used as-is. A slug is shallow-cloned into the cache and reused
    on the next run. ``--depth 1`` is not an optimisation: a full clone brings
    vendored directories and history that change every count in the scorecard.
    """
    candidate = Path(spec)
    if candidate.is_dir():
        return candidate.resolve(), f"local checkout {candidate}"
    if candidate.exists():
        return None, f"{candidate} exists but is not a directory"
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(spec)):
        return None, f"{spec!r} is neither a directory nor an owner/name slug"
    destination = cache_dir / str(spec).replace("/", "__")
    if (destination / ".git").is_dir() and not refresh:
        return destination, f"cached shallow clone of {spec}"
    if refresh and destination.exists():
        shutil.rmtree(destination)
    cache_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--quiet",
        f"https://github.com/{spec}.git",
        str(destination),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"clone of {spec} failed: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit {completed.returncode}"
        return None, f"clone of {spec} failed: {message}"
    return destination, f"shallow clone of {spec}"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def measure_project(
    project: Project,
    checkout: Path,
    extensions_checkout: Path | None,
    online: bool,
    timeout: float,
) -> Result:
    """Run every measure for one project."""
    result = Result(key=project.key, source=project.slug)
    (
        source_loc,
        test_loc,
        source_files,
        _,
        language_split,
    ) = measure_loc(checkout)
    result.source_loc = source_loc
    result.test_loc = test_loc
    result.source_files = source_files
    result.language_split = language_split

    state, detail = measure_type_strictness(checkout, project.language)
    result.type_strictness = state
    result.type_strictness_detail = detail

    floor, job, floor_detail = measure_coverage_floor(checkout)
    result.coverage_fail_under = floor
    result.coverage_job = job
    result.coverage_detail = floor_detail

    if project.extension is not None:
        if extensions_checkout is None:
            result.extension_count = None
            result.cross_imports = None
        else:
            count, hits, failures, ext_detail = measure_cross_extension(
                extensions_checkout, project.extension, extensions_checkout
            )
            result.extension_count = _count_extensions(
                extensions_checkout, project.extension
            )
            result.cross_imports = count
            result.cross_import_list = hits
            result.parse_failures = failures
            result.type_strictness_detail = f"{detail}; {ext_detail}"

    files, fully, partial, weighted, ann_failures = measure_annotation_coverage(
        checkout, project.language
    )
    result.annotation_files = files
    result.annotation_fully = fully
    result.annotation_partial = partial
    result.annotation_weighted = weighted if project.language == "Python" else None
    for failure in ann_failures:
        result.parse_failures.append(failure)

    result.workflow_files = measure_release_automation(checkout)

    if online:
        result.install = measure_installability(project, timeout)
    return result


def _count_extensions(root: Path, spec: Extension) -> int:
    return len(extension_identities(root, spec))


#: Measure name -> is a rise a regression? Used by the baseline diff. ``None``
#: means the measure is reported but never treated as a regression, because a
#: project's line count going up or down says nothing about our quality.
#:
#: Read the True/False as "more is worse" / "more is better", not as good/bad:
#: a rising test-to-source ratio and a rising annotation coverage are both
#: improvements, and the first version of this table had them backwards.
REGRESSION_DIRECTION: dict[str, bool | None] = {
    "cross_extension_imports": True,
    "parse_failures": True,
    "annotation_weighted": False,
    "test_ratio": False,
    "source_loc": None,
    "test_loc": None,
    "source_files": None,
    "annotation_files": None,
    "annotation_fully": None,
    "annotation_partial": None,
    # Measure 7 is a count of files, and a count of files is not a measure of
    # release quality in either direction. More is not worse -- openclaw's 111
    # workflows are not a better release process than ours -- and less is not
    # gatable here either, because the six repositories this axis counts are
    # not ours and a peer deleting a workflow must never fail our build. It is
    # recorded and diffed as a measurement and never as a gate. See measure 7.
    "workflow_files": None,
}

#: Measures compared by a rule of their own rather than by direction.
_SPECIAL_MEASURES = frozenset({"type_strictness", "coverage_fail_under"})

#: Recorded in the snapshot but never diffed. `install` is present or absent
#: depending on whether `--online` was passed, so diffing it reports the flag
#: rather than the project. `commit` changes every time a peer lands anything,
#: which is not a regression in any measure, and `commit_published` is a
#: property of the clone rather than of the project.
_NEVER_DIFFED = frozenset({"install", "commit", "commit_published"})

#: Ordering used when a string measure gets worse. Lower index is worse.
_STRICTNESS_ORDER: dict[str, int] = {"true": 0, "false": 1, "unset": 2}


def baseline_document(
    results: list[Result], measured_at: str | None = None
) -> dict[str, Any]:
    """The checked-in snapshot: one flat dict of comparable measures.

    ``measured_at`` and each row's ``commit`` are provenance, not measures. They
    are recorded so that a claim like "the whole table was re-measured on one
    date" is checkable against the snapshot instead of being an assertion in a
    commit message. Nothing here is ever diffed: a peer's commit moving is not a
    regression, and today's date is never worse than the baseline's.

    ``commit_published`` is recorded beside the sha it qualifies, because a
    snapshot that stores a bare sha cannot distinguish a commit a reader can
    resolve from one that only ever existed in the clone that measured it. The
    refusal lives in ``main`` where the snapshot is written, so this stays the
    one place that decides what a row looks like: the row says whether its stamp
    is checkable, and the writer refuses to publish one that is not.
    """
    if measured_at is None:
        measured_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    projects: dict[str, Any] = {}
    for result in results:
        projects[result.key] = {
            "commit": result.commit,
            "commit_published": result.commit_published,
            "source_loc": result.source_loc,
            "test_loc": result.test_loc,
            "source_files": result.source_files,
            "test_ratio": (
                round(result.test_ratio, 6) if result.test_ratio is not None else None
            ),
            "language_split": {
                language: {"source_loc": source, "test_loc": test}
                for language, (source, test) in sorted(result.language_split.items())
            },
            "type_strictness": result.type_strictness,
            "coverage_fail_under": result.coverage_fail_under,
            "coverage_job": result.coverage_job,
            "extension_count": result.extension_count,
            "cross_extension_imports": result.cross_imports,
            "parse_failures": len(result.parse_failures),
            "annotation_files": result.annotation_files,
            "annotation_fully": result.annotation_fully,
            "annotation_partial": result.annotation_partial,
            "annotation_weighted": (
                round(result.annotation_weighted, 6)
                if result.annotation_weighted is not None
                else None
            ),
            "workflow_files": result.workflow_files,
            "install": result.install or None,
        }
    return {
        "schema": BASELINE_SCHEMA,
        "generated_by": "scripts/scorecard.py",
        "measured_at": measured_at,
        "projects": projects,
    }


def _is_regression(measure: str, before: Any, after: Any) -> bool | None:
    """True when ``after`` is worse than ``before``, None when incomparable.

    A regression only matters for a gated project, so this answers "did this get
    worse", not "should the build fail".
    """
    if measure not in REGRESSION_DIRECTION and measure not in _SPECIAL_MEASURES:
        return None
    if before is None or after is None:
        # Losing a coverage floor is a regression in its own right: a floor that
        # stopped existing is worse than any floor that did.
        if measure == "coverage_fail_under":
            return before is not None and after is None
        return None
    if measure == "type_strictness":
        if before not in _STRICTNESS_ORDER or after not in _STRICTNESS_ORDER:
            return None
        return _STRICTNESS_ORDER[str(after)] > _STRICTNESS_ORDER[str(before)]
    if isinstance(before, bool) or isinstance(after, bool):
        return None
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    left, right = float(before), float(after)
    if measure == "coverage_fail_under":
        return right < left
    direction = REGRESSION_DIRECTION.get(measure)
    if direction is None:
        return None
    return right > left if direction else right < left


def diff_against_baseline(
    results: list[Result], baseline: dict[str, Any]
) -> list[tuple[str, str, Any, Any, bool | None]]:
    """Return ``(project, measure, before, after, is_regression)`` for changes."""
    known = baseline.get("projects", {})
    changes: list[tuple[str, str, Any, Any, bool | None]] = []
    for result in results:
        recorded = known.get(result.key)
        if not isinstance(recorded, dict):
            continue
        current = baseline_document([result])["projects"][result.key]
        for measure, before in recorded.items():
            if measure not in current or measure in _NEVER_DIFFED:
                continue
            after = current[measure]
            if before == after:
                continue
            regression = _is_regression(measure, before, after)
            changes.append((result.key, measure, before, after, regression))
    return changes


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_CELL_WIDTH = 22


def _clip(text: str, width: int = 78) -> str:
    """Shorten a long path from the middle, so both ends stay readable."""
    if len(text) <= width:
        return text
    keep = width - 1
    return f"{text[: keep // 2]}...{text[-(keep - keep // 2) :]}"


def _cell(value: str, width: int = _CELL_WIDTH) -> str:
    text = str(value)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.ljust(width)


def _workflow_text(result: Result) -> str:
    """Measure 7 as a cell: a count, or an explicit dash when unmeasured.

    A project whose checkout could not be read has ``workflow_files`` unset and
    an ``error`` already printed beside its key. Printing `0` there would be a
    number this run did not measure, so it prints `-`.
    """
    if result.workflow_files is None:
        return "-"
    return str(result.workflow_files)


def render_table(results: list[Result], online: bool) -> str:
    """The scorecard table."""
    lines: list[str] = []
    header = (
        _cell("Project", 18)
        + _cell("Source LOC")
        + _cell("Test LOC")
        + _cell("Test/source")
        + _cell("Type strict")
        + _cell("Coverage floor")
        + _cell("Annot. coverage")
    )
    lines.append(header)
    lines.append("-" * len(header))
    for result in results:
        if result.error:
            lines.append(
                _cell(result.key, 18)
                + _cell("ERROR")
                + _cell("-")
                + _cell("-")
                + _cell("-")
                + _cell("-")
                + _cell("-")
            )
            lines.append(f"    {result.key}: {result.error}")
            continue
        ratio = result.test_ratio
        annotation = (
            f"{result.annotation_weighted * 100:.1f}%"
            if result.annotation_weighted is not None
            else "n/a (not Python)"
        )
        lines.append(
            _cell(result.key, 18)
            + _cell(f"{result.source_loc:,}")
            + _cell(f"{result.test_loc:,}")
            + _cell(f"{ratio:.2f}" if ratio is not None else "-")
            + _cell(result.type_strictness or "-")
            + _cell(result.coverage_text)
            + _cell(annotation)
        )
    lines.append("")
    lines.append("Language split of the LOC columns (measure 1 counts all of them)")
    lines.append("-" * len(header))
    lines.append(
        _cell("Project", 18)
        + _cell("Language")
        + _cell("Source LOC")
        + _cell("Test LOC")
        + _cell("Share of source")
    )
    lines.append("-" * len(header))
    for result in results:
        if result.error or not result.language_split or not result.source_loc:
            continue
        for language, (source, test) in sorted(result.language_split.items()):
            lines.append(
                _cell(result.key, 18)
                + _cell(language, 14)
                + _cell(f"{source:,}")
                + _cell(f"{test:,}")
                + _cell(f"{source / result.source_loc:.0%}")
            )
    lines.append("")
    lines.append("Extension isolation (measure 4, the gate)")
    lines.append("-" * len(header))
    ext_header = (
        _cell("Project", 18)
        + _cell("Extensions")
        + _cell("Cross-ext. imports")
        + _cell("Parse failures")
        + _cell("Gated")
    )
    lines.append(ext_header)
    lines.append("-" * len(ext_header))
    for result in results:
        if result.extension_count is None:
            lines.append(_cell(result.key, 18) + _cell("not measured"))
            continue
        if result.error:
            lines.append(_cell(result.key, 18) + _cell("ERROR"))
            continue
        lines.append(
            _cell(result.key, 18)
            + _cell(str(result.extension_count))
            + _cell(
                str(result.cross_imports)
                if result.cross_imports is not None
                else "not measured"
            )
            + _cell(str(len(result.parse_failures)))
            + _cell("yes" if PROJECTS_BY_KEY[result.key].gated else "no")
        )
    if online:
        lines.append("")
        lines.append("Installability (measure 5, needs network)")
        lines.append("-" * 30)
        for result in results:
            if not result.install:
                continue
            for name, status in sorted(result.install.items()):
                lines.append(f"{_cell(result.key, 18)}{_cell(name, 30)}{status}")
    lines.append("")
    lines.append("Release automation (measure 7: workflow files in .github/workflows)")
    lines.append("-" * len(header))
    for result in results:
        if result.error:
            lines.append(_cell(result.key, 18) + _cell("ERROR"))
            continue
        lines.append(_cell(result.key, 18) + _cell(_workflow_text(result)))
    lines.append("")
    lines.append("Provenance")
    lines.append("-" * len(header))
    for result in results:
        commit = result.commit or "not a git checkout"
        if result.commit and result.commit_published is False:
            commit = f"{commit}!"
        lines.append(
            f"{_cell(result.key, 18)}{_cell(commit, 10)}{_clip(result.source)}"
        )
    unverifiable = [result for result in results if result.commit_published is False]
    if unverifiable:
        lines.append("")
        lines.append(
            "! marks a commit that no remote ref contains, so it cannot be "
            "resolved in a fresh clone:"
        )
        for result in unverifiable:
            lines.append(f"  {result.key}: {result.commit} is only in {result.source}")
    return "\n".join(lines)


def render_markdown(results: list[Result], online: bool) -> str:
    """GitHub-flavoured markdown, for a CI job summary."""
    lines = [
        "## MvgeOS scorecard",
        "",
        "Reproduce locally: `uv run python scripts/scorecard.py`",
        "",
        "| Project | Source LOC | Test LOC | Test/source | Type strictness "
        "| Coverage floor | Annotation coverage |",
        "| --- | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for result in results:
        if result.error:
            lines.append(f"| **{result.key}** | ERROR | | | | | |")
            continue
        ratio = result.test_ratio
        annotation = (
            f"{result.annotation_weighted * 100:.1f}%"
            if result.annotation_weighted is not None
            else "n/a"
        )
        lines.append(
            f"| **{result.key}** | {result.source_loc:,} | {result.test_loc:,} "
            f"| {ratio:.2f} | {result.type_strictness} | {result.coverage_text} "
            f"| {annotation} |"
        )
    lines += [
        "",
        "| Project | Extensions | Cross-extension runtime imports | Parse failures "
        "| Gated |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        if result.extension_count is None:
            lines.append(f"| {result.key} | not measured | | | |")
            continue
        if result.error:
            lines.append(f"| {result.key} | ERROR | | | |")
            continue
        gated = "yes" if PROJECTS_BY_KEY[result.key].gated else "no"
        lines.append(
            f"| {result.key} | {result.extension_count} | {result.cross_imports} "
            f"| {len(result.parse_failures)} | {gated} |"
        )
    if online:
        lines += ["", "| Project | Package | HTTP |", "| --- | --- | ---: |"]
        for result in results:
            for name, status in sorted(result.install.items()):
                lines.append(f"| {result.key} | `{name}` | {status} |")
    lines += [
        "",
        "| Project | Workflow files in `.github/workflows` |",
        "| --- | ---: |",
    ]
    for result in results:
        if result.error:
            lines.append(f"| {result.key} | ERROR |")
            continue
        lines.append(f"| {result.key} | {_workflow_text(result)} |")
    lines += [
        "",
        "A number without the commit it was measured at cannot be checked.",
        "",
        "| Project | Commit | Published | Source |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        commit = result.commit or "n/a"
        published = _published_text(result)
        lines.append(f"| {result.key} | `{commit}` | {published} | {result.source} |")
    return "\n".join(lines)


def _published_text(result: Result) -> str:
    """How a row's stamp reads in the published-provenance column.

    ``no`` is spelled out rather than shown as a flag because this column is the
    one a reader skims, and "no" next to a sha is the whole warning.
    """
    if result.commit is None:
        return "n/a"
    return "yes" if result.commit_published else "**no**"


def render_failures(results: list[Result]) -> str:
    """Every cross-extension import and every parse failure, located."""
    lines: list[str] = []
    for result in results:
        for hit in result.cross_import_list:
            lines.append(
                f"CROSS-EXTENSION {hit.path}:{hit.lineno}: extension "
                f"{hit.owner!r} imports sibling {hit.module!r}"
            )
        for failure in result.parse_failures:
            lines.append(f"PARSE FAILURE {failure.path}: {failure.error}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_assignment(value: str, flag: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"{flag} expects KEY=VALUE, got {value!r}")
    key, _, raw = value.partition("=")
    if not key or not raw:
        raise argparse.ArgumentTypeError(f"{flag} expects KEY=VALUE, got {value!r}")
    return key, raw


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. The epilog carries every definition."""
    epilog = __doc__.split(MEASURES_HEADING, 1)[1]
    parser = argparse.ArgumentParser(
        prog="scorecard.py",
        description=(
            "Re-measure the published MvgeOS scorecard. With no arguments this "
            "shallow-clones each project, measures it, prints a table, and "
            "exits non-zero if a gate regresses."
        ),
        epilog=MEASURES_HEADING + epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="KEY=SOURCE",
        help=(
            "override a project's source with a local path or an owner/name "
            "slug to clone. Repeatable. Keys: " + ", ".join(PROJECTS_BY_KEY)
        ),
    )
    parser.add_argument(
        "--extensions",
        action="append",
        default=[],
        metavar="KEY=SOURCE",
        help=(
            "override the checkout holding a project's extensions, for the "
            "projects that have one (mvgeos, hermes-agent). A path or slug."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="KEY",
        help="measure only these projects. Repeatable.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="where shallow clones are cached (default: a directory under TMPDIR)",
    )
    parser.add_argument(
        "--clone-self",
        action="store_true",
        help=(
            "measure MvgeOS's own row from a shallow clone of the published "
            "repository instead of from the checkout this script is run in"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-clone instead of reusing the cached shallow clone",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help=(
            "NEEDS NETWORK. Also run measure 5, one HTTP GET per package "
            "name against PyPI or npm."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds per git clone and per HTTP request (default: 180)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "diff every measure against a JSON snapshot and print what moved. "
            "Defaults to "
            f"{DEFAULT_BASELINE} when that file exists; a regression in a "
            "gated project exits non-zero."
        ),
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="ignore the checked-in baseline even if it exists",
    )
    parser.add_argument(
        "--update-baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "write the current measurements to PATH as the new snapshot, "
            "stamped with the UTC time of the run and each row's commit. "
            "Those two are provenance, never diffed: a peer's commit moving "
            "is not a regression, and a date is not a measure. Refuses to "
            "write when any row's commit is not reachable from a remote ref, "
            "because an unresolvable sha is not a reference point."
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="PATH",
        help="also write the full result document to PATH",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="print a GitHub-flavoured markdown table instead of the plain one",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the scorecard. See the module docstring for every definition."""
    args = build_parser().parse_args(argv)

    # _parse_assignment raises ArgumentTypeError, which argparse only converts
    # into a usage error when it is used as a `type=`. It is called directly
    # here, so the error is turned into exit 2 explicitly rather than escaping
    # as a traceback with exit 1, which a CI job would read as a gate failure.
    try:
        parsed_project = [_parse_assignment(v, "--project") for v in args.project]
        parsed_extensions = [
            _parse_assignment(v, "--extensions") for v in args.extensions
        ]
    except argparse.ArgumentTypeError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2

    overrides: dict[str, str] = {}
    for key, source in parsed_project:
        if key not in PROJECTS_BY_KEY:
            print(  # noqa: T201
                f"error: unknown project {key!r}; known: {', '.join(PROJECTS_BY_KEY)}",
                file=sys.stderr,
            )
            return 2
        overrides[key] = source
    extension_overrides: dict[str, str] = {}
    for key, source in parsed_extensions:
        if PROJECTS_BY_KEY[key].extension is None:
            print(  # noqa: T201
                f"error: {key} declares no extension root to override",
                file=sys.stderr,
            )
            return 2
        extension_overrides[key] = source

    selected = list(PROJECTS_BY_KEY)
    for key in args.only:
        if key not in PROJECTS_BY_KEY:
            print(  # noqa: T201
                f"error: unknown project {key!r}; known: {', '.join(PROJECTS_BY_KEY)}",
                file=sys.stderr,
            )
            return 2
    if args.only:
        selected = [key for key in selected if key in set(args.only)]

    if shutil.which("git") is None:
        print("error: git is not on PATH; cannot obtain a checkout", file=sys.stderr)  # noqa: T201
        return 2

    cache_dir = args.cache_dir or default_cache_dir()
    results: list[Result] = []

    for key in selected:
        project = PROJECTS_BY_KEY[key]
        if key in overrides:
            source_spec: str | Path = overrides[key]
        elif key == SELF_PROJECT_KEY and not args.clone_self:
            source_spec = REPO_ROOT
        else:
            source_spec = project.slug
        checkout, note = resolve_source(
            source_spec, cache_dir, args.timeout, args.refresh
        )
        if checkout is None:
            results.append(Result(key=key, source=str(source_spec), error=note))
            continue

        extensions_checkout: Path | None = None
        if project.extension is not None:
            extension_spec = extension_overrides.get(key, project.extension.slug)
            extensions_checkout, extension_note = resolve_source(
                extension_spec, cache_dir, args.timeout, args.refresh
            )
            if extensions_checkout is None:
                results.append(
                    Result(
                        key=key,
                        source=str(source_spec),
                        error=f"{note}; extensions unavailable: {extension_note}",
                    )
                )
                continue
            note = f"{note}; extensions: {extension_note}"

        result = measure_project(
            project, checkout, extensions_checkout, args.online, args.timeout
        )
        result.source = note
        result.commit = short_commit(checkout)
        result.commit_published = (
            commit_is_published(checkout, result.commit) if result.commit else None
        )
        results.append(result)

    printer = render_markdown if args.markdown else render_table
    print(printer(results, args.online))  # noqa: T201

    failures = render_failures(results)
    if failures:
        print("\nFailures")  # noqa: T201
        print("--------")  # noqa: T201
        print(failures)  # noqa: T201

    document = baseline_document(results)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    if args.update_baseline:
        # The baseline is the permanent reference point, so a row stamped with a
        # commit nobody can resolve is not a rough draft of a number, it is a
        # permanent unfalsifiable claim. Refuse the whole write rather than
        # downgrade the row: a baseline with one unverifiable row is a baseline
        # whose reader cannot tell which rows are real, and the whole table is
        # then cited on trust.
        #
        # --json is not gated the same way on purpose. That file is the dated
        # measurement artifact from a CI run on a detached checkout, it carries
        # `commit_published` so the false is visible in the record, and
        # refusing it would throw away a measurement that was otherwise sound.
        unverifiable = [
            result for result in results if result.commit_published is False
        ]
        if unverifiable:
            print(  # noqa: T201
                "\nerror: refusing to write a baseline stamped with a commit no "
                "remote ref contains.",
                file=sys.stderr,
            )
            for result in unverifiable:
                print(  # noqa: T201
                    f"  {result.key}: {result.commit} ({result.source})",
                    file=sys.stderr,
                )
            print(  # noqa: T201
                "  A sha that resolves only in this clone is not provenance: it "
                "is queued for `git gc`, and once it is gone the published "
                "number cannot be checked by anybody, ever.\n"
                "  Push the commit, fetch the remote refs, and re-run; or "
                "measure from a checkout that is already on a remote ref "
                "(`git fetch --all` first).",
                file=sys.stderr,
            )
            return 2
        args.update_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.update_baseline.write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nbaseline written to {args.update_baseline}")  # noqa: T201

    baseline_path = args.baseline
    if baseline_path is None and not args.no_baseline and DEFAULT_BASELINE.is_file():
        baseline_path = DEFAULT_BASELINE
    baseline: dict[str, Any] | None = None
    if baseline_path is not None:
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(  # noqa: T201
                f"error: could not read baseline {baseline_path}: {exc}",
                file=sys.stderr,
            )
            return 2

    gate_failures: list[str] = []
    if baseline is not None:
        changes = diff_against_baseline(results, baseline)
        if changes:
            print(f"\nBaseline diff against {baseline_path}")  # noqa: T201
            print("-" * 40)  # noqa: T201
            for project_key, measure, before, after, regression in changes:
                mark = ""
                if regression is True:
                    mark = "  REGRESSION"
                elif regression is False:
                    mark = "  improved"
                print(  # noqa: T201
                    f"{project_key:18} {measure:26} "
                    f"{before!r:>16} -> {after!r:<16}{mark}"
                )
        for project_key, measure, _before, _after, regression in changes:
            if regression is not True:
                continue
            if PROJECTS_BY_KEY[project_key].gated:
                gate_failures.append(f"{project_key}: {measure} regressed")

    for result in results:
        project = PROJECTS_BY_KEY[result.key]
        if not project.gated:
            continue
        if result.error:
            gate_failures.append(f"{result.key}: {result.error}")
            continue
        if result.parse_failures:
            gate_failures.append(
                f"{result.key}: {len(result.parse_failures)} parse failure(s); "
                "a file that will not parse cannot be counted as a clean boundary"
            )
        if (result.cross_imports or 0) > 0:
            gate_failures.append(
                f"{result.key}: {result.cross_imports} cross-extension runtime "
                "import(s)"
            )

    if gate_failures:
        print("\nGATE FAILED")  # noqa: T201
        print("----------")  # noqa: T201
        for failure in gate_failures:
            print(f"- {failure}")  # noqa: T201
        return 1
    print("\nGate OK: no cross-extension runtime imports, no parse failures.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
