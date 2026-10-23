"""Unit tests for git workspace VCS inspection and diff parsing."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from mvgeos_gui.git_workspace import (
    get_changed_files,
    get_diff_for_file,
    parse_git_diff,
    resolve_git_branch,
)

SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc1234..def5678 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,5 +1,6 @@
 # Module header
+import os

 def main():
-    print("hello")
+    print("hello world")
     return True

diff --git a/README.md b/README.md
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/README.md
@@ -0,0 +1,3 @@
+# Project
+
+This is a project.

"""

EMPTY_DIFF = """\
"""

DELETED_DIFF = """\
diff --git a/old.txt b/old.txt
deleted file mode 100644
index abc1234..0000000
--- a/old.txt
+++ /dev/null
@@ -1,3 +0,0 @@
-line1
-line2
-line3
"""


def test_parse_git_diff_returns_diff_views() -> None:
    """Verify parse_git_diff returns a DiffView per changed file."""
    views = parse_git_diff(SAMPLE_DIFF)
    assert len(views) == 2
    assert views[0].file_path == "src/main.py"
    assert views[0].status == "modified"
    assert views[1].file_path == "README.md"
    assert views[1].status == "added"


def test_parse_git_diff_calculates_additions_and_deletions() -> None:
    """Verify each hunk reports correct addition/deletion line counts."""
    views = parse_git_diff(SAMPLE_DIFF)
    main_view = views[0]
    assert main_view.additions == 2
    assert main_view.deletions == 1


def test_parse_git_diff_hunks_have_line_numbers() -> None:
    """Verify hunks preserve source and target line numbers."""
    views = parse_git_diff(SAMPLE_DIFF)
    hunk = views[0].hunks[0]
    assert hunk.source_start == 1
    assert hunk.source_length == 5
    assert hunk.target_start == 1
    assert hunk.target_length == 6


def test_parse_git_diff_hunk_lines_classified() -> None:
    """Verify each line in a hunk is marked context, addition, or deletion."""
    views = parse_git_diff(SAMPLE_DIFF)
    lines = views[0].hunks[0].lines
    types = [line.line_type for line in lines]
    assert "addition" in types
    assert "deletion" in types
    assert "context" in types


def test_parse_git_diff_new_file_status() -> None:
    """Verify new files have status 'added'."""
    views = parse_git_diff(SAMPLE_DIFF)
    readme_view = views[1]
    assert readme_view.status == "added"
    assert readme_view.additions == 3
    assert readme_view.deletions == 0


def test_parse_git_diff_empty_input_returns_empty_list() -> None:
    """Verify empty diff string produces empty list."""
    assert parse_git_diff("") == []


def test_parse_git_diff_no_diff_marker_returns_empty() -> None:
    """Verify non-diff text returns empty list."""
    assert parse_git_diff("no diff here") == []


def test_parse_git_diff_zero_changes() -> None:
    """Verify empty diff produces empty list."""
    views = parse_git_diff(EMPTY_DIFF)
    assert views == []


def test_parse_git_diff_deleted_file() -> None:
    """Verify deleted files are parsed with status 'deleted'."""
    views = parse_git_diff(DELETED_DIFF)
    assert len(views) == 1
    assert views[0].file_path == "old.txt"
    assert views[0].status == "deleted"
    assert views[0].deletions == 3
    assert views[0].additions == 0


def test_parse_git_diff_mnemonic_prefixes() -> None:
    """Verify git mnemonic prefixes (i/ and w/) parse clean file paths."""
    mnemonic_diff = """\
diff --git i/src/calc.py w/src/calc.py
index abc1234..def5678 100644
--- i/src/calc.py
+++ w/src/calc.py
@@ -1,2 +1,3 @@
 def add(a, b):
+    return a + b
"""
    views = parse_git_diff(mnemonic_diff)
    assert len(views) == 1
    assert views[0].file_path == "src/calc.py"
    assert views[0].additions == 1


def test_parse_git_diff_quoted_paths_with_spaces() -> None:
    """Verify quoted file paths with spaces parse cleanly."""
    quoted_diff = """\
diff --git "a/my module/calc.py" "b/my module/calc.py"
index abc1234..def5678 100644
--- "a/my module/calc.py"
+++ "b/my module/calc.py"
@@ -1,2 +1,3 @@
 def add(a, b):
+    return a + b
"""
    views = parse_git_diff(quoted_diff)
    assert len(views) == 1
    assert views[0].file_path == "my module/calc.py"
    assert views[0].additions == 1


def test_parse_git_diff_no_prefix() -> None:
    """Verify git diff without prefixes parses correctly."""
    no_prefix_diff = """\
diff --git calc.py calc.py
index abc1234..def5678 100644
--- calc.py
+++ calc.py
@@ -1,2 +1,3 @@
 def add(a, b):
+    return a + b
"""
    views = parse_git_diff(no_prefix_diff)
    assert len(views) == 1
    assert views[0].file_path == "calc.py"
    assert views[0].additions == 1


class TestGetChangedFiles:
    def test_parses_numstat_output(self, tmp_path: Path) -> None:
        """Verify get_changed_files parses numstat output correctly."""
        mock_output = "2\t1\tsrc/main.py\n3\t0\tREADME.md\n"
        mock_result = MagicMock(stdout=mock_output, returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            files = get_changed_files(tmp_path)
        assert len(files) == 2
        assert files[0].path == "src/main.py"
        assert files[0].status == "modified"
        assert files[0].additions == 2
        assert files[0].deletions == 1
        assert files[1].path == "README.md"
        assert files[1].status == "added"
        assert files[1].additions == 3
        assert files[1].deletions == 0

    def test_added_file_status(self, tmp_path: Path) -> None:
        """Verify a file with only additions gets status 'added'."""
        mock_output = "1\t0\tsrc/main.py\n"
        mock_result = MagicMock(stdout=mock_output, returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            files = get_changed_files(tmp_path)
        assert files[0].path == "src/main.py"
        assert files[0].status == "added"
        assert files[0].additions == 1
        assert files[0].deletions == 0

    def test_empty_repo_returns_empty_list(self, tmp_path: Path) -> None:
        """Verify get_changed_files returns empty list when no changes."""
        mock_result = MagicMock(stdout="", returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            files = get_changed_files(tmp_path)
        assert files == []

    def test_subprocess_failure_returns_empty_list(self, tmp_path: Path) -> None:
        """Verify get_changed_files returns empty list on subprocess error."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            files = get_changed_files(tmp_path)
        assert files == []


class TestGetDiffForFile:
    def test_returns_diff_view(self, tmp_path: Path) -> None:
        """Verify get_diff_for_file returns a DiffView for the requested file."""
        diff_text = """diff --git a/src/main.py b/src/main.py
index abc1234..def5678 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,3 @@
 # header
+import os
 print('hello')
"""
        mock_result = MagicMock(stdout=diff_text, returncode=0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            view = get_diff_for_file(tmp_path, "src/main.py")
        assert view is not None
        assert view.file_path == "src/main.py"
        assert view.status == "modified"
        assert view.additions == 1
        assert view.deletions == 0
        args = mock_run.call_args[0][0]
        assert "--src-prefix=a/" in args
        assert "--dst-prefix=b/" in args

    def test_returns_none_for_unchanged_file(self, tmp_path: Path) -> None:
        """Verify get_diff_for_file returns None when file has no diff."""
        mock_result = MagicMock(stdout="", returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            view = get_diff_for_file(tmp_path, "src/main.py")
        assert view is None

    def test_returns_none_on_subprocess_error(self, tmp_path: Path) -> None:
        """Verify get_diff_for_file returns None on subprocess error."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            view = get_diff_for_file(tmp_path, "src/main.py")
        assert view is None


class TestResolveGitBranch:
    def test_resolves_current_branch(self, tmp_path: Path) -> None:
        """Verify resolve_git_branch returns the branch name when git succeeds."""

        def mock_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(stdout=str(tmp_path) + "\n", returncode=0)
            if "branch" in cmd:
                return MagicMock(stdout="main\n", returncode=0)
            return MagicMock(stdout="", returncode=1)

        with patch("subprocess.run", side_effect=mock_run):
            branch = resolve_git_branch(tmp_path)
        assert branch == "main"

    def test_returns_none_for_non_git_directory(self, tmp_path: Path) -> None:
        """Verify resolve_git_branch returns None when directory is not a git repo."""
        mock_result = MagicMock(stdout="", returncode=128)
        with patch("subprocess.run", return_value=mock_result):
            branch = resolve_git_branch(tmp_path)
        assert branch is None

    def test_returns_none_when_parent_is_toplevel(self, tmp_path: Path) -> None:
        """Verify resolve_git_branch returns None when toplevel is a parent
        directory.
        """
        subdir = tmp_path / "sub"
        subdir.mkdir()
        mock_result = MagicMock(stdout=str(tmp_path) + "\n", returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            branch = resolve_git_branch(subdir)
        assert branch is None

    def test_handles_subprocess_exception(self, tmp_path: Path) -> None:
        """Verify resolve_git_branch safely handles exceptions."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            branch = resolve_git_branch(tmp_path)
        assert branch is None
