"""Tests for the partial-publish resume helper.

`scripts/published_files.py` decides which built files still need uploading after
a release failed partway. That decision is load-bearing in the worst possible
direction: a file reported as missing when it is already published is uploaded
into an immutable version and fails, and a file reported as present when it is
missing is silently skipped, leaving the index half-written and the run green.

The HTTP call is mocked. What is under test is the decision made from the
answer, including the answer that could not be obtained -- the case that most
easily becomes a silent skip.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import published_files

DIST = "dist/mvge"
WHEEL = "mvge-0.6.17-py3-none-any.whl"
SDIST = "mvge-0.6.17.tar.gz"


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _serving(*filenames: str) -> object:
    """A urlopen that answers with a version holding ``filenames``."""

    def fake_urlopen(url: str, timeout: int = 0) -> _Response:
        body = json.dumps({"urls": [{"filename": name} for name in filenames]}).encode()
        return _Response(body)

    return fake_urlopen


def _missing_404(url: str, timeout: int = 0) -> _Response:
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def _published(*filenames: str, monkeypatch) -> None:
    monkeypatch.setattr(published_files.urllib.request, "urlopen", _serving(*filenames))


def test_a_version_with_nothing_on_it_reports_every_file(monkeypatch) -> None:
    monkeypatch.setattr(published_files.urllib.request, "urlopen", _missing_404)

    missing = published_files.missing_files("mvge", "0.6.17", [WHEEL, SDIST])

    assert missing == [WHEEL, SDIST]


def test_a_fully_published_version_reports_nothing(monkeypatch) -> None:
    _published(WHEEL, SDIST, monkeypatch=monkeypatch)

    missing = published_files.missing_files("mvge", "0.6.17", [WHEEL, SDIST])

    assert missing == []


def test_a_half_uploaded_version_reports_only_what_is_absent(monkeypatch) -> None:
    """An upload can die between two files, so this is the interesting case."""
    _published(WHEEL, monkeypatch=monkeypatch)

    missing = published_files.missing_files("mvge", "0.6.17", [WHEEL, SDIST])

    assert missing == [SDIST]


def test_an_index_that_cannot_be_reached_is_not_a_skip(monkeypatch) -> None:
    """The failure that matters: unreachable index must never read as "present".

    Reporting nothing missing here would skip every upload and leave the release
    half-done with a green run, which is the exact state this issue exists to
    stop.
    """

    def refused(url: str, timeout: int = 0) -> _Response:
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(published_files.urllib.request, "urlopen", refused)

    assert published_files.missing_files("mvge", "0.6.17", [WHEEL]) is None


def test_a_server_error_is_not_a_skip(monkeypatch) -> None:
    """Only 404 means "nothing there". 500 means "we do not know"."""

    def failing(url: str, timeout: int = 0) -> _Response:
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)

    monkeypatch.setattr(published_files.urllib.request, "urlopen", failing)

    assert published_files.missing_files("mvge", "0.6.17", [WHEEL]) is None


def test_a_response_that_is_not_the_expected_shape_is_not_a_skip(
    monkeypatch,
) -> None:
    """A truncated or changed response must not read as an empty index.

    An empty `urls` is a real answer -- a version with no files, which does not
    happen in practice. A missing or wrongly typed one is not.
    """

    def malformed(url: str, timeout: int = 0) -> _Response:
        return _Response(json.dumps({"info": {"name": "mvge"}}).encode())

    monkeypatch.setattr(published_files.urllib.request, "urlopen", malformed)

    assert published_files.missing_files("mvge", "0.6.17", [WHEEL]) is None


def test_a_build_directory_gitignore_is_not_a_distribution(
    tmp_path, monkeypatch, capsys
) -> None:
    """`uv build --out-dir` leaves a `.gitignore` beside the distributions.

    Counting it makes every already-published package look incomplete, and the
    run then tries to upload it. Measured against PyPI before this filter
    existed: all six landed packages reported one missing file.
    """
    dist = tmp_path / "mvge"
    dist.mkdir()
    (dist / ".gitignore").write_text("*\n", encoding="utf-8")
    (dist / WHEEL).write_bytes(b"wheel")
    (dist / SDIST).write_bytes(b"sdist")
    _published(WHEEL, SDIST, monkeypatch=monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "published_files.py",
            "--package",
            "mvge",
            "--version",
            "0.6.17",
            "--dist-dir",
            ".",
        ],
    )

    assert published_files.main() == 0
    assert capsys.readouterr().out == ""
