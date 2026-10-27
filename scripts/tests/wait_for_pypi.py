"""Tests for the release-readiness wait.

`scripts/wait_for_pypi.py` stands between an upload and the jobs that claim the
release is installable. What is under test is every judgement it makes from what
the index said, because each of those can be wrong in a direction that produces
a confident wrong answer:

* a version that is not on the index reported as present, which is the vacuous
  check that ran on v0.6.19 -- `/pypi/mvge//json` is 200 for every package
  that has ever existed;
* an index that could not be reached reported as absent, which sends the reader
  hunting for an upload problem that does not exist;
* a wait that spins forever rather than failing, which occupies the runner until
  the job timeout and reports nothing;
* a `uvx` command that differs from the one a stranger types, so the wait proves
  something other than installability.

The HTTP call and the `uvx` call are mocked. Nothing here talks to PyPI.
"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error

import wait_for_pypi

PROJECT = "https://pypi.org/pypi/mvge/json"


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _document(*versions: str) -> bytes:
    """A project document whose releases are exactly ``versions``."""
    return json.dumps(
        {
            "info": {"name": "mvge", "version": "0.6.25"},
            "releases": {
                name: [{"filename": f"mvge-{name}-py3-none-any.whl"}]
                for name in versions
            },
        }
    ).encode()


def _serving(*versions: str):
    def fake_urlopen(url: str, timeout: float = 0) -> _Response:
        return _Response(_document(*versions))

    return fake_urlopen


def _refusing(url: str, timeout: float = 0) -> _Response:
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def _unreachable(url: str, timeout: float = 0) -> _Response:
    raise urllib.error.URLError("name resolution failed")


def _failing(url: str, timeout: float = 0) -> _Response:
    raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)


def test_a_released_version_is_present(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.25"))

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.PRESENT


def test_a_version_that_never_landed_is_absent(monkeypatch) -> None:
    """The half-landed release: the project exists, the version does not."""
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.24"))

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.ABSENT


def test_a_version_with_no_files_is_absent(monkeypatch) -> None:
    """A release with nothing in it cannot be installed, so it has not landed."""

    def empty_version(url: str, timeout: float = 0) -> _Response:
        return _Response(json.dumps({"info": {}, "releases": {"0.6.25": []}}).encode())

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", empty_version)

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.ABSENT


def test_a_document_that_changed_shape_is_unknown_not_absent(
    monkeypatch,
) -> None:
    """The failure that matters: a document this script cannot read is not a
    release that did not land. Reporting absent here would name the wrong
    defect and send the reader to the upload log.
    """

    def malformed(url: str, timeout: float = 0) -> _Response:
        return _Response(json.dumps({"info": {"name": "mvge"}}).encode())

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", malformed)

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.UNKNOWN


def test_an_unreachable_index_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _unreachable)

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.UNKNOWN


def test_a_server_error_is_unknown(monkeypatch) -> None:
    """Only a 404 means "not there". 500 means "we do not know"."""
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _failing)

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.UNKNOWN


def test_a_project_that_does_not_exist_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _refusing)

    assert wait_for_pypi.release_state("mvge", "0.6.25") == wait_for_pypi.ABSENT


def test_the_wait_stops_as_soon_as_the_version_arrives(monkeypatch) -> None:
    """Propagation is the whole point: the wait must end when the index catches
    up, not run out its timeout on a release that already landed.
    """
    asked: list[float] = []

    def late(remaining: list[int]) -> object:
        def fake_urlopen(url: str, timeout: float = 0) -> _Response:
            if remaining[0]:
                remaining[0] -= 1
                return _Response(_document("0.6.24"))
            return _Response(_document("0.6.24", "0.6.25"))

        return fake_urlopen

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", late([2]))
    monkeypatch.setattr(
        wait_for_pypi.time, "sleep", lambda seconds: asked.append(seconds)
    )

    states = wait_for_pypi.wait_for_release(["mvge"], "0.6.25", 30, 5)

    assert states == {"mvge": wait_for_pypi.PRESENT}
    assert asked == [5, 5]


def test_a_release_that_never_arrives_gives_up_at_the_timeout(monkeypatch) -> None:
    """A wait that never ends occupies the runner and reports nothing."""
    asked: list[str] = []

    def every_round(url: str, timeout: float = 0) -> _Response:
        asked.append(url)
        return _Response(_document("0.6.24"))

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", every_round)

    states = wait_for_pypi.wait_for_release(
        ["mvge", "mvge-desktop"], "0.6.25", 0.2, 0.01
    )

    assert states == {
        "mvge": wait_for_pypi.ABSENT,
        "mvge-desktop": wait_for_pypi.ABSENT,
    }
    # Asked more than once: the wait polled until its deadline rather than
    # asking a single question and concluding.
    assert len(asked) > 2


def test_every_package_is_asked_in_one_round(monkeypatch) -> None:
    """Eight distributions wait once, not eight times.

    Waiting per package would make a half-landed release take eight timeouts to
    diagnose, which in the `verify` job is a ten-minute budget spent on the
    diagnosis instead of on the report.
    """
    asked: list[str] = []

    def every_round(url: str, timeout: float = 0) -> _Response:
        asked.append(url)
        return _Response(_document("0.6.24"))

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", every_round)

    wait_for_pypi.wait_for_release(
        ["mvge", "mvge-desktop", "mvge-cli"], "0.6.25", 0.05, 0.01
    )

    assert asked[:3] == [
        "https://pypi.org/pypi/mvge/json",
        "https://pypi.org/pypi/mvge-desktop/json",
        "https://pypi.org/pypi/mvge-cli/json",
    ]


def test_a_tag_style_version_is_the_version_the_index_records(monkeypatch) -> None:
    assert wait_for_pypi.normalized_version("v0.6.25") == "0.6.25"
    assert wait_for_pypi.normalized_version("0.6.25") == "0.6.25"


def test_a_version_that_cannot_be_named_is_refused() -> None:
    """The v0.6.19 gate's defect, in one place.

    An empty version asks the index `/pypi/mvge//json`, which is 200 for every
    package that has ever existed, so the gate reports eight green over a
    release that is not there. Refusing is the only answer that cannot be read
    as success.
    """
    assert wait_for_pypi.normalized_version("") == ""
    assert wait_for_pypi.normalized_version("main") == ""
    assert wait_for_pypi.normalized_version("   ") == ""


def test_main_refuses_a_version_that_is_not_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv", ["wait_for_pypi.py", "--version", "main", "--timeout", "0.01"]
    )

    assert wait_for_pypi.main() == 2
    assert "not a release version" in capsys.readouterr().err


def test_main_passes_a_release_that_is_on_the_index(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.25"))
    monkeypatch.setattr(
        "sys.argv", ["wait_for_pypi.py", "--version", "0.6.25", "--timeout", "30"]
    )

    assert wait_for_pypi.main() == 0


def test_main_reports_the_distributions_that_never_arrived(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.24"))
    monkeypatch.setattr(
        "sys.argv", ["wait_for_pypi.py", "--version", "0.6.25", "--timeout", "0.05"]
    )

    assert wait_for_pypi.main() == 1


def _resolving(*codes: int):
    """A `uvx` that answers with ``codes`` in order, repeating the last."""

    answers = list(codes)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        code = answers[0] if len(answers) == 1 else answers.pop(0)
        return subprocess.CompletedProcess(
            args=["uvx"], returncode=code, stdout="", stderr=""
        )

    return fake_run


def test_resolution_waits_for_uvx_not_for_a_url(monkeypatch) -> None:
    asked: list[list[str]] = []

    def recording(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        asked.append(list(args[0]))  # type: ignore[index,arg-type]
        return subprocess.CompletedProcess(args=["uvx"], returncode=0, stdout="")

    monkeypatch.setattr(wait_for_pypi.subprocess, "run", recording)

    assert wait_for_pypi.resolves("0.6.25") is True
    assert asked == [["uvx", "--from", "mvge==0.6.25", "mvge", "--help"]]


def test_resolution_stops_waiting_when_uvx_succeeds(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(wait_for_pypi.subprocess, "run", _resolving(1, 0))
    monkeypatch.setattr(
        wait_for_pypi.time, "sleep", lambda seconds: slept.append(seconds)
    )

    assert wait_for_pypi.wait_for_resolution("0.6.25", 30, 5) is True
    assert slept == [5]


def test_resolution_that_keeps_failing_gives_up_at_the_timeout(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.subprocess, "run", _resolving(1))

    assert wait_for_pypi.wait_for_resolution("0.6.25", 0.2, 0.01) is False


def test_main_fails_when_the_index_has_it_and_uvx_cannot_install_it(
    monkeypatch, capsys
) -> None:
    """The v0.6.19 failure exactly: published, and not yet installable."""
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.25"))
    monkeypatch.setattr(wait_for_pypi.subprocess, "run", _resolving(1))
    monkeypatch.setattr(
        "sys.argv",
        [
            "wait_for_pypi.py",
            "--version",
            "0.6.25",
            "--resolve",
            "--timeout",
            "0.2",
            "--interval",
            "0.01",
        ],
    )

    assert wait_for_pypi.main() == 1
    assert "propagation lag" in capsys.readouterr().err


def test_main_passes_when_the_release_is_installable(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.25"))
    monkeypatch.setattr(wait_for_pypi.subprocess, "run", _resolving(0))
    monkeypatch.setattr(
        "sys.argv",
        ["wait_for_pypi.py", "--version", "0.6.25", "--resolve", "--timeout", "30"],
    )

    assert wait_for_pypi.main() == 0


def test_resolution_is_not_asked_for_unless_it_was_requested(monkeypatch) -> None:
    def never(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("uvx must not run without --resolve")

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", _serving("0.6.25"))
    monkeypatch.setattr(wait_for_pypi.subprocess, "run", never)
    monkeypatch.setattr(
        "sys.argv", ["wait_for_pypi.py", "--version", "0.6.25", "--timeout", "30"]
    )

    assert wait_for_pypi.main() == 0


def test_the_default_package_is_the_one_uvx_installs() -> None:
    assert wait_for_pypi.DEFAULT_PACKAGE == "mvge"


def test_the_project_document_is_what_is_asked(monkeypatch) -> None:
    """The version-specific URL is not the question.

    `/pypi/<name>/<version>/json` answers 200 while the index `uvx` reads is
    still stale, and 404 after the release published -- measured on
    `/pypi/mvge/0.6.99.json`, which answered 404 from cache for six minutes.
    The project document is the one cached under the key the upload purges.
    """
    asked: list[str] = []

    def every_round(url: str, timeout: float = 0) -> _Response:
        asked.append(url)
        return _Response(_document("0.6.25"))

    monkeypatch.setattr(wait_for_pypi.urllib.request, "urlopen", every_round)

    wait_for_pypi.release_state("mvge", "0.6.25")

    assert asked == [PROJECT]
