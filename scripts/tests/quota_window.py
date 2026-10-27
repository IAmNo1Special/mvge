"""Tests for the PyPI new-project quota window helper.

`scripts/pypi_new_project_window.py` exists to answer one question: a `429 Too
many new projects created` is a full account quota, not a broken distribution,
and the only useful thing to say about it is when to come back. Everything here
protects that arithmetic, because the whole reason the script exists is that
reading the quota as a daily reset turns one day into four and sends somebody
chasing a limit request that was never needed.

The HTTP call itself is mocked. The pure decision -- which creation ages out
first, and how many windows the remaining names need -- is the part that can be
wrong while still producing a confident-looking report, so that is what is
under test.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any

import pypi_new_project_window as window
import pytest


def _payload(
    uploads: list[str],
    *,
    version: str = "0.6.8",
    release_uploads: list[str] | None = None,
) -> dict[str, Any]:
    """A minimal `pypi.org/pypi/<name>/json` body.

    ``urls`` carries the current version's files; ``releases[version]`` carries
    the same files again. PyPI sends both, and the helper reads both, so a fake
    that only populates one would let a regression in the other path through.
    """
    files = [{"upload_time_iso_8601": stamp} for stamp in uploads]
    return {
        "info": {"version": version},
        "urls": list(files),
        "releases": {version: list(files if release_uploads is None else [])},
    }


def _stub_index(
    monkeypatch: pytest.MonkeyPatch,
    index: dict[str, Any | Exception],
) -> None:
    """Serve `index` from `published_at` without touching the network.

    An `Exception` value in the map is raised instead of returned, which is how
    the HTTP failure paths are reached.
    """

    def fake_published_at(name: str, timeout: float = 30.0) -> Any:
        outcome = index.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return None
        return window.dt.datetime.fromisoformat(outcome)

    monkeypatch.setattr(window, "published_at", fake_published_at)


class _FakeResponse:
    """The context manager `urllib.request.urlopen` returns."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def _stub_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    outcome: dict[str, Any] | Exception,
) -> list[str]:
    """Serve `outcome` from `urlopen`, recording every URL requested."""
    requested: list[str] = []

    def fake_urlopen(url: str, timeout: float = 30.0) -> Any:
        requested.append(url)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)

    monkeypatch.setattr(window.urllib.request, "urlopen", fake_urlopen)
    return requested


# ---------------------------------------------------------------------------
# published_at: which creation leaves the window first
# ---------------------------------------------------------------------------


def test_published_at_asks_the_json_api_for_that_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = _stub_urlopen(monkeypatch, _payload(["2026-10-05T11:24:00.000000Z"]))

    window.published_at("mvge-tome")

    assert requested == ["https://pypi.org/pypi/mvge-tome/json"]


def test_published_at_takes_the_earliest_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The creation that leaves the window first is the one that frees the slot.

    The files of one version land seconds apart and the last of them is the one
    a naive "newest upload" reading would pick. That reading delays the answer
    by the gap between the first and last upload, which is exactly the
    information the script exists to eliminate.
    """
    _stub_urlopen(
        monkeypatch,
        _payload(
            [
                "2026-10-05T11:24:31.000000Z",
                "2026-10-05T11:24:03.000000Z",
                "2026-10-05T11:24:47.000000Z",
            ]
        ),
    )

    landed = window.published_at("mvge-cli")

    assert landed == dt.datetime(2026, 10, 5, 11, 24, 3, tzinfo=dt.UTC)


def test_published_at_counts_a_version_with_no_files_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name whose latest version has no files still occupies a slot.

    `urls` is empty in that case -- PyPI reports nothing for the current
    version -- but the project exists and its creation is in the window. Reading
    only `urls` reports a published project as absent, which sends the release
    looking for a slot it already holds.
    """
    _stub_urlopen(
        monkeypatch,
        {
            "info": {"version": "0.6.8"},
            "urls": [],
            "releases": {
                "0.6.8": [{"upload_time_iso_8601": "2026-10-05T11:24:03.000000Z"}]
            },
        },
    )

    assert window.published_at("mvge") == dt.datetime(
        2026, 10, 5, 11, 24, 3, tzinfo=dt.UTC
    )


def test_published_at_reads_a_name_with_no_uploads_at_all_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No files anywhere means the creation time is unknown, not zero.

    Returning the epoch would make this project look like the oldest thing on
    PyPI and pin `next_slot_at` to 1970.
    """
    _stub_urlopen(
        monkeypatch, {"info": {"version": "0.0.0"}, "urls": [], "releases": {}}
    )

    assert window.published_at("mvge-nope") is None


def test_published_at_reads_a_404_as_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(
        monkeypatch, urllib.error.HTTPError("u", 404, "Not Found", None, None)
    )

    assert window.published_at("mvge-tome") is None


def test_published_at_reraises_every_other_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 is a full quota and a 500 is an outage; neither is "not published".

    Collapsing either into `None` would report a project as missing when it is
    on the index, and the release would wait for a slot it never needed.
    """
    _stub_urlopen(monkeypatch, urllib.error.HTTPError("u", 429, "Too Many", None, None))

    with pytest.raises(urllib.error.HTTPError) as caught:
        window.published_at("mvge-tome")

    assert caught.value.code == 429


# ---------------------------------------------------------------------------
# describe: the arithmetic over a mocked index
# ---------------------------------------------------------------------------


def test_describe_reads_the_index_for_every_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def record(name: str) -> dt.datetime:
        asked.append(name)
        return dt.datetime(2026, 10, 5, 11, 24, tzinfo=dt.UTC)

    monkeypatch.setattr(window, "published_at", record)
    window.describe(
        ["mvge-core", "mvge-cli"], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC)
    )

    assert asked == ["mvge-core", "mvge-cli"]


def test_describe_splits_published_from_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(
        monkeypatch,
        {
            "mvge-core": "2026-10-05T11:24:03+00:00",
            "mvge-cli": "2026-10-05T11:25:00+00:00",
        },
    )

    report = window.describe(
        ["mvge-cli", "mvge-core", "mvge-tome", "mvge-runes"],
        now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
    )

    assert sorted(report["published"]) == ["mvge-cli", "mvge-core"]
    assert sorted(report["missing"]) == ["mvge-runes", "mvge-tome"]


def test_describe_frees_a_slot_a_full_window_after_the_oldest_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The instant a slot frees is the oldest creation plus 24 hours.

    Not the newest, and not midnight tomorrow. This single line is the whole
    reason the script replaced a manual reading of the quota.
    """
    _stub_index(
        monkeypatch,
        {
            "mvge-core": "2026-10-05T11:24:03+00:00",
            "mvge-cli": "2026-10-05T11:24:31+00:00",
        },
    )
    now = dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC)

    report = window.describe(["mvge-core", "mvge-cli"], now=now)

    assert report["next_slot_at"] == "2026-10-06T11:24:03+00:00"
    assert report["seconds_until_next_slot"] == 84243


def test_describe_frees_a_batch_created_seconds_apart_in_one_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four names created seconds apart all leave the window together.

    Reading this as one slot per day turns one day into four and sends someone
    chasing a limit request that was never needed. The whole batch ages out when
    the OLDEST of them turns 24 hours old, so the fifth name needs one window
    and not four.
    """
    _stub_index(
        monkeypatch,
        {
            "mvge-core": "2026-10-05T11:24:03+00:00",
            "mvge-cli": "2026-10-05T11:24:07+00:00",
            "mvge-tome": "2026-10-05T11:24:19+00:00",
            "mvge-runes": "2026-10-05T11:24:22+00:00",
        },
    )
    now = dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC)

    report = window.describe(
        [
            "mvge-core",
            "mvge-cli",
            "mvge-tome",
            "mvge-runes",
            "mvge-agent",
        ],
        now=now,
    )

    # 24h after the OLDEST creation, not after the newest: 11:24:03, not 11:24:22.
    assert report["next_slot_at"] == "2026-10-06T11:24:03+00:00"
    assert report["seconds_until_next_slot"] == 84243
    assert report["missing"] == ["mvge-agent"]
    assert report["slots_in_next_window"] == 1
    assert report["windows_needed"] == 1


def test_describe_never_reports_a_wait_in_the_past(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot that already freed reads as available now, not as negative seconds.

    The release that acts on this runs unattended against a timestamp it cannot
    re-check, and a negative interval is the kind of thing that turns into a
    sleep.
    """
    _stub_index(monkeypatch, {"mvge-core": "2026-10-01T00:00:00+00:00"})

    report = window.describe(
        ["mvge-core"], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC)
    )

    assert report["seconds_until_next_slot"] == 0


def test_describe_counts_windows_by_ceiling_division(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names beyond one window's quota are reported in windows, not in one wait.

    Five missing names at a quota of four cannot land in the next window. The
    report has to say so, or the release re-dispatches every 24 hours and burns
    a run discovering it again.
    """
    _stub_index(monkeypatch, {})

    report = window.describe(
        [f"mvge-pkg{index}" for index in range(5)],
        now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
    )

    assert report["windows_needed"] == 2
    assert report["slots_in_next_window"] == window.QUOTA


def test_describe_clamps_the_slots_in_the_next_window_to_the_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(monkeypatch, {})

    report = window.describe(
        [f"mvge-pkg{index}" for index in range(9)],
        now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
    )

    assert report["slots_in_next_window"] == window.QUOTA == 4
    assert report["windows_needed"] == 3


def test_describe_reports_no_windows_when_nothing_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(monkeypatch, {"mvge-core": "2026-10-05T11:24:03+00:00"})

    report = window.describe(
        ["mvge-core"], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC)
    )

    assert report["missing"] == []
    assert report["windows_needed"] == 0
    assert report["slots_in_next_window"] == 0


def test_describe_sorts_published_names_so_two_runs_render_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report that reshuffles between runs cannot be diffed against the last.

    The publish workflow writes this into a job summary, where two identical
    runs have to produce identical bytes.
    """
    _stub_index(
        monkeypatch,
        {
            "mvge-tome": "2026-10-05T11:24:03+00:00",
            "mvge-cli": "2026-10-05T11:24:04+00:00",
            "mvge-core": "2026-10-05T11:24:05+00:00",
        },
    )

    report = window.describe(
        ["mvge-tome", "mvge-cli", "mvge-core"],
        now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
    )

    assert list(report["published"]) == ["mvge-cli", "mvge-core", "mvge-tome"]


def test_describe_defaults_to_the_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting `now` still yields a real instant rather than a TypeError.

    `main` never passes one; this is the path every actual invocation takes.
    """
    _stub_index(monkeypatch, {"mvge-core": "2026-10-05T11:24:03+00:00"})

    report = window.describe(["mvge-core"])

    assert dt.datetime.fromisoformat(report["checked_at"]).tzinfo is dt.UTC


def test_describe_reports_the_window_and_quota_it_measured_against(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report carries the policy it read, so a changed quota is visible.

    PyPI can change `q` or `w`. A report that hardcoded the expectation would
    keep answering with the old policy and nobody would see the difference.
    """
    _stub_index(monkeypatch, {})

    report = window.describe([], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC))

    assert report["window_seconds"] == 86400
    assert report["quota"] == 4


# ---------------------------------------------------------------------------
# render: what a person holding the release actually reads
# ---------------------------------------------------------------------------


def test_format_stamp_renders_in_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timestamps are quoted in UTC whatever offset the machine is on.

    The report is read by whoever is holding the release, possibly in a
    different timezone from the runner, and the release is dispatched from the
    printed instant.
    """
    _stub_index(monkeypatch, {})

    assert (
        window._format_stamp("2026-10-06T07:24:03-04:00") == "2026-10-06 11:24:03 UTC"
    )


def test_render_states_the_policy_it_assumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(monkeypatch, {})

    text = window.render(
        window.describe([], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC))
    )

    assert "4 new projects per account per trailing 24 hours" in text


def test_render_names_the_oldest_creation_in_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader needs the age-out instant itself, not just the conclusion.

    Without it there is nothing to check the printed slot time against.
    """
    _stub_index(monkeypatch, {"mvge-core": "2026-10-05T11:24:03+00:00"})

    text = window.render(
        window.describe(["mvge-core"], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC))
    )

    assert "Oldest creation in the window: 2026-10-05 11:24:03 UTC." in text
    assert "A slot next frees: **2026-10-06 11:24:03 UTC**." in text


def test_render_lists_the_names_still_waiting_for_a_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(monkeypatch, {"mvge-core": "2026-10-05T11:24:03+00:00"})

    text = window.render(
        window.describe(
            ["mvge-core", "mvge-cli"],
            now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
        )
    )

    assert "Still to create (1): `mvge-cli`" in text
    assert "1 of 2" in text


def test_render_says_plainly_when_a_release_needs_more_than_one_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(monkeypatch, {})

    text = window.render(
        window.describe(
            [f"mvge-pkg{index}" for index in range(6)],
            now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
        )
    )

    assert "it takes 2 windows" in text
    assert "Re-run once a slot frees." in text


def test_render_omits_the_multi_window_advice_when_one_window_is_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four names at a quota of four need no warning about windows.

    Printing it anyway trains the reader to skip the line, which is how the
    five-name case stops being noticed.
    """
    _stub_index(monkeypatch, {})

    text = window.render(
        window.describe(
            [f"mvge-pkg{index}" for index in range(4)],
            now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC),
        )
    )

    assert "windows" not in text


def test_render_says_nothing_is_waiting_when_every_name_is_on_the_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_index(monkeypatch, {"mvge-core": "2026-10-05T11:24:03+00:00"})

    text = window.render(
        window.describe(["mvge-core"], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC))
    )

    assert "Every distribution is on the index." in text
    assert "Still to create" not in text


def test_render_calls_a_refusal_a_full_quota_and_not_a_bad_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closing instruction is the part that prevents a wasted release run.

    A 429 reads like a failure, and the obvious response is to go fix the
    distribution. This sentence is what stops that.
    """
    _stub_index(monkeypatch, {})

    text = window.render(
        window.describe(["mvge-cli"], now=dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC))
    )

    assert "A refusal is a full quota, not a bad distribution." in text
    assert "it skips what already landed" in text


# ---------------------------------------------------------------------------
# main: the entry point the publish workflow calls
# ---------------------------------------------------------------------------


def test_main_renders_markdown_and_reports_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` never blocks. It reports, and the caller decides what it means.

    A non-zero exit here would fail the release workflow for a condition the
    workflow is supposed to be able to read and re-dispatch.
    """
    _stub_index(monkeypatch, {})
    monkeypatch.setattr(window, "workspace_packages", dict)
    monkeypatch.setattr(window, "publish_order", lambda _packages: ["mvge-cli"])
    monkeypatch.setattr("sys.argv", ["pypi_new_project_window.py"])

    assert window.main() == 0
    assert "### PyPI new-project quota" in capsys.readouterr().out


def test_main_emits_json_when_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` output has to round-trip: a follow-up job diffs these reports."""
    _stub_index(monkeypatch, {})
    monkeypatch.setattr(window, "workspace_packages", dict)
    monkeypatch.setattr(window, "publish_order", lambda _packages: ["mvge-cli"])
    monkeypatch.setattr("sys.argv", ["pypi_new_project_window.py", "--json"])

    assert window.main() == 0

    report = json.loads(capsys.readouterr().out)
    assert report["missing"] == ["mvge-cli"]
    assert report["quota"] == window.QUOTA
