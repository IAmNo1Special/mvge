"""Tests for the install-verification harness.

`scripts/verify_install.py` exists to answer one question for a release: would a
stranger who has never seen this repository get a working `mvgeos`? The
expensive half of that -- installing from PyPI, calling a model -- is mocked out
by never being run here. What is under test is every judgement the harness makes
about what it saw, because each of those can be wrong while still printing a
confident green summary:

* an environment that leaks the host's ``HOME`` and quietly turns "a stranger's
  machine" into this machine;
* a sandbox that binds the operator's own directory and calls itself clean;
* a wrapped ``WARNING:`` miscounted as the model's first token, which
  understates time-to-first-token by however long Realm setup took;
* a Tome with no Spell cast reported as a completed task.

The transcript helpers are the fragile part. An earlier revision stamped only
the first of several lines and printed ``0.00s`` for the rest, which renders a
real gap as simultaneity. ``test_each_line_keeps_its_own_arrival_time`` is the
regression that stops that coming back.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest
import verify_install

from mvge.core.constants import DEFAULT_MODEL as CORE_DEFAULT_MODEL
from mvge.provider.realms import (
    DEFAULT_REALM,
    REALM_API_KEY_ENV,
    rune_for_realm,
)


def test_clean_env_cannot_see_the_host(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harness must not inherit state from the machine running it."""
    monkeypatch.setenv("MVGEOS_SESSION", "leaked")
    monkeypatch.setenv("HOME", "/home/somebody-else")

    env = verify_install.clean_env(
        tmp_path / "home", tmp_path / "cache", "sk-or-v", "OPENROUTER_API_KEY"
    )

    assert env["HOME"] == str(tmp_path / "home")
    assert env["OPENROUTER_API_KEY"] == "sk-or-v"
    assert "MVGEOS_SESSION" not in env
    # Rich reflows to the console width; the harness needs it pinned so a
    # multi-line preamble stays recognisable as one message.
    assert env["COLUMNS"] == "400"
    assert not [key for key in env if key.startswith("_")]


def test_the_keyless_default_run_carries_no_credential(
    tmp_path: pathlib.Path,
) -> None:
    """The default Realm's free tier is reached with no key, and must be.

    Exporting an empty variable would pass a test that only checked for the
    absence of a *value*, while leaving the CLI's own "read a credential" path
    open. The property that matters is that no key variable exists at all.
    """
    env = verify_install.clean_env(
        tmp_path / "home", tmp_path / "cache", "", "OPENCODE_API_KEY"
    )

    assert not [key for key in env if key.endswith("_API_KEY")]


def test_one_realms_key_is_never_exported_under_another_realms_name(
    tmp_path: pathlib.Path,
) -> None:
    """The CLI falls back to OPENROUTER_API_KEY for every Realm.

    So an OpenRouter key exported while testing ``--provider opencode`` reaches
    Zen, and the run fails at the host that received it rather than at the
    packaging under test. The variable the harness writes has to be named for
    the Realm it is actually testing.
    """
    env = verify_install.clean_env(
        tmp_path / "home", tmp_path / "cache", "sk-or-v", "OPENCODE_API_KEY"
    )

    assert env["OPENCODE_API_KEY"] == "sk-or-v"
    assert "OPENROUTER_API_KEY" not in env


def test_api_key_env_matches_the_engine() -> None:
    """The harness's copy of the Realm table must not drift from the engine's.

    The harness runs under a bare ``python3`` before any sync, so it cannot
    import the workspace. That is the only reason the table is duplicated, and
    this is the only thing holding the copy to the original.
    """
    assert verify_install.API_KEY_ENV_BY_REALM == REALM_API_KEY_ENV


@pytest.mark.parametrize("realm", ["opencode", "openrouter", "somebody-new", ""])
def test_the_rune_name_matches_the_engine(realm: str) -> None:
    """Installing the wrong Rune fails far from its cause, so the two agree.

    The harness re-derives this rather than looking it up, and an unknown Realm
    is the interesting case: the engine answers ``<realm>-realm`` for anything it
    does not know, and a harness that answered "the default Rune" instead would
    install a provider that cannot serve the model under test.
    """
    assert verify_install.realm_rune_for_realm(realm) == rune_for_realm(realm)


def test_the_harness_owns_the_only_two_marketplace_copies() -> None:
    """The release gate's two artifact names are the engine's only forced copies.

    They are forced because the gate runs under a bare ``python3`` before any
    sync, so it cannot ask the catalog what to install. What is *not* forced is
    anything else: the engine discovers installed artifacts, so no package may
    decide which Mvge runs by naming one.

    This is the counterpart to a check that cannot live here. Enumerating every
    artifact the marketplace publishes would mean naming all of them in the
    engine -- leakage by the act of guarding against it. The marketplace's own
    suite owns that check; what this pins is the smaller, local invariant that
    the two copies are the ones the gate actually resolves.
    """
    assert rune_for_realm(verify_install.DEFAULT_PROVIDER) == verify_install.REALM_RUNE
    assert verify_install.MVGE


def test_a_bare_invocation_gates_the_engines_default_realm() -> None:
    """What CI actually runs, with no flags, must be the Realm the engine defaults to.

    This is the assertion that was missing. An earlier revision hardcoded
    ``default="openrouter"`` in the parser while ``DEFAULT_PROVIDER`` and the
    engine both said ``opencode``, so every other test in this file stayed green
    and the gate would have exercised a Realm no new Summoner starts on.
    """
    arguments = verify_install.build_parser().parse_args(["--version", "0.0.0"])

    assert arguments.provider == DEFAULT_REALM
    assert arguments.model == CORE_DEFAULT_MODEL


def test_the_default_gate_requires_a_credential_and_says_so() -> None:
    """The gate needs a key now, and the assertion is that it cannot pretend otherwise.

    It used to assert the opposite -- that the default Realm costs nothing and
    needs no key. That was true while Zen served its free tier anonymously and
    is measured false now (ADR 0020): every free slug on the live Zen catalogue
    refuses a request that does not originate inside OpenCode, and OpenRouter's
    `:free` slugs answer 401. There is no credential-free default left to assert.

    So the invariant is inverted rather than deleted. The gate must *require*
    `OPENROUTER_API_KEY` and exit non-zero without it, because the failure this
    file exists to prevent is a green gate that never called a model. A default
    that quietly stopped calling one is how that would happen.
    """
    assert verify_install.DEFAULT_PROVIDER in verify_install.REALMS_REQUIRING_CREDENTIAL

    assert REALM_API_KEY_ENV[verify_install.DEFAULT_PROVIDER] == "OPENROUTER_API_KEY"
    assert verify_install.DEFAULT_MODEL.startswith(
        f"{verify_install.DEFAULT_PROVIDER}/"
    )

    # And it is free. A router id like `openrouter/free` does not carry the
    # `:free` suffix, so the source of truth is the shipped catalog's free
    # bucket rather than the spelling. Asserting the suffix here would be the
    # mistake this test exists to catch: a name that looks free is not
    # evidence, the catalog is.
    catalogued = {
        entry[0]
        for bucket in ("free", "paid")
        for entry in json.loads(
            (
                pathlib.Path(verify_install.__file__).resolve().parent.parent
                / "src"
                / "mvge"
                / "provider"
                / "models.json"
            ).read_text()
        )[bucket]
    }
    assert verify_install.DEFAULT_MODEL in catalogued, (
        f"{verify_install.DEFAULT_MODEL} is not in the shipped catalog at all; "
        "a default the catalog has never heard of cannot be resolved, let "
        "alone run"
    )


def test_the_gate_exits_non_zero_without_the_credential(monkeypatch, capsys) -> None:
    """A missing key is a failure, not a skip.

    The cheapest way for this gate to start lying is to treat a missing
    credential as "nothing to check here" and exit zero. Asserted directly
    rather than by reading the branch.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["verify_install.py", "--version", "0.0.0"])
    code = verify_install.main()
    assert code == 2, (
        "the gate returned "
        f"{code} with no credential; it must exit 2 so a run that never called "
        "a model cannot report green"
    )
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("WARNING: --approval-mode=allow-all: approved.", False),
        ("Error: API key required.", False),
        ("", False),
        ("   ", False),
        ("The file hello.txt has been created.", True),
        ("Stop reason: stop", True),
    ],
)
def test_local_preamble_is_not_the_first_token(line: str, expected: bool) -> None:
    assert verify_install.is_model_output(line) is expected


def _scratch(tmp_path: pathlib.Path) -> pathlib.Path:
    scratch = tmp_path / "scratch"
    for name in ("home", "cache", "work", "harness"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    return scratch


def _bound_sources(command: list[str]) -> list[str]:
    """The host paths a bwrap argv exposes, read off the command itself."""
    return [command[i + 1] for i, arg in enumerate(command) if arg == "--ro-bind"]


def _bound_writes(command: list[str]) -> list[str]:
    """The host paths a bwrap argv mounts read-write."""
    return [command[i + 1] for i, arg in enumerate(command) if arg == "--bind"]


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "The sandbox argv is Linux-only. SANDBOX_BINDS is a tuple of absolute "
        "POSIX paths, and bubblewrap does not exist on Windows, so there is no "
        "OS bind for the command to carry and the assertion cannot pass. The "
        "harness only reaches this code on a Linux runner; a Windows sandbox "
        "would remove the skip."
    ),
)
def test_sandbox_binds_no_host_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sandbox must expose the scratch and the OS, never the operator.

    Binding the host's ``HOME`` would let a green run pass off state the release
    gate was supposed to prove nothing about. Binding the harness *in place* is
    the subtler version of the same mistake: bubblewrap materialises a
    destination's parents, so it rebuilds the operator's whole tree with the
    checkout at the bottom of it. Nothing under ``$HOME`` may appear at all.
    """
    home = tmp_path / "home-of-somebody"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(verify_install.shutil, "which", lambda _: "/usr/bin/bwrap")

    command = verify_install.sandbox_command(_scratch(tmp_path), ["python3", "x.py"])

    assert command is not None
    sources = _bound_sources(command) + _bound_writes(command)
    assert "/usr" in sources
    assert [source for source in sources if str(home) in source] == []
    assert [source for source in sources if ".paperclip" in source] == []
    assert [source for source in sources if source.startswith(str(tmp_path))] == [
        str(_scratch(tmp_path))
    ]


def test_sandbox_keeps_the_network(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsharing the network would prove a run fails offline, not one that works.

    PyPI resolution and the Realm call are the two things under test, so the net
    namespace has to stay the host's. ``--unshare-all`` is the trap: it reads
    like everything-isolated and quietly takes the network with it, so both that
    and the explicit flag are rejected rather than just the obvious one.
    """
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(verify_install.shutil, "which", lambda _: "/usr/bin/bwrap")

    command = verify_install.sandbox_command(_scratch(tmp_path), ["python3"])

    assert command is not None
    assert "--unshare-net" not in command
    assert "--unshare-all" not in command


def test_no_sandbox_is_reported_rather_than_assumed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host without bwrap gets no sandbox, and that must be visible."""
    monkeypatch.setattr(verify_install.shutil, "which", lambda _: None)

    command = verify_install.sandbox_command(_scratch(tmp_path), ["python3"])

    assert command is None


def test_host_paths_reachable_spots_a_leaked_directory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reachable host path is the one thing that voids the whole run."""
    present = tmp_path / "visible"
    present.mkdir()
    monkeypatch.setenv(
        verify_install.SANDBOX_HOST_PATHS,
        json.dumps([str(present), str(tmp_path / "hidden")]),
    )

    assert verify_install.host_paths_reachable() == [str(present)]


def test_host_paths_reachable_is_empty_outside_a_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(verify_install.SANDBOX_HOST_PATHS, raising=False)

    assert verify_install.host_paths_reachable() == []


def test_each_line_keeps_its_own_arrival_time() -> None:
    """Regression: a transcript that flattens later lines to 0.00s."""
    transcript = verify_install.stamp_lines(
        [(0.61, "WARNING: local"), (2.56, "The file hello.txt has been created.")]
    )

    assert "[   0.61s] WARNING: local" in transcript
    assert "[   2.56s] The file hello.txt has been created." in transcript


def _tome(
    tmp_path: pathlib.Path,
    name: str,
    model: str,
    spells: list[str],
) -> pathlib.Path:
    """A minimal Tome: a session header plus one message per Spell cast."""
    sessions = tmp_path / ".agents" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / name
    records: list[dict[str, object]] = [
        {"type": "session", "id": "abc", "model": model, "spells": spells}
    ]
    for spell in spells:
        records.append(
            {
                "type": "message",
                "payload": {
                    "role": "assistant",
                    "content": [{"type": "spell_cast", "spell_cast": {"name": spell}}],
                },
            }
        )
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_newest_tome_prefers_the_most_recent(tmp_path: pathlib.Path) -> None:
    older = _tome(tmp_path, "older.jsonl", "openai/gpt-4o-mini", ["write"])
    newer = _tome(tmp_path, "newer.jsonl", "openai/gpt-4o-mini", ["write"])
    # Lock files sit beside the Tomes and must never be mistaken for one.
    (older.parent / "newer.jsonl.lock").write_text("", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert verify_install.newest_tome(older.parent) == newer


def test_newest_tome_is_none_when_nothing_persisted(
    tmp_path: pathlib.Path,
) -> None:
    assert verify_install.newest_tome(tmp_path / "sessions") is None


def test_session_header_survives_a_corrupt_leading_line(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        "{not json\n" + json.dumps({"type": "session", "model": "openai/gpt-4o-mini"}),
        encoding="utf-8",
    )

    header = verify_install.session_header(path)

    assert header is not None
    assert header["model"] == "openai/gpt-4o-mini"


def test_cast_spells_names_the_spell_once(tmp_path: pathlib.Path) -> None:
    path = _tome(tmp_path, "t.jsonl", "openai/gpt-4o-mini", ["write", "write", "read"])

    assert verify_install.cast_spells(path) == ["write", "read"]


def test_a_tome_with_no_spell_cast_is_detectable(tmp_path: pathlib.Path) -> None:
    """The run must not count as a completed task when nothing was written."""
    path = _tome(tmp_path, "t.jsonl", "openai/gpt-4o-mini", [])

    assert verify_install.cast_spells(path) == []


def _coherent(version: str) -> dict[str, str]:
    return dict.fromkeys(verify_install.RUNTIME_CLOSURE, version)


def test_a_coherent_tree_is_accepted() -> None:
    assert verify_install.lockstep_failures(_coherent("0.6.17"), "0.6.17") == []


def test_the_half_landed_release_is_detected() -> None:
    """The 2026-10-09 tree: six at 0.6.17, root at 0.6.16, gui never attempted.

    Every constraint `mvgeos 0.6.16` declared was satisfied by this tree --
    `mvgeos-cli>=0.6.16` against a 0.6.17 cli -- and `uvx mvgeos --help` exited
    zero on it. It is the shape the assertion exists to refuse.
    """
    resolved = _coherent("0.6.17")
    resolved["mvge"] = "0.6.16"

    failures = verify_install.lockstep_failures(resolved, "0.6.17")

    assert len(failures) == 1
    assert any("mvge 0.6.16" in failure for failure in failures)


def test_the_gui_is_not_required_in_the_runtime_tree() -> None:
    """The companion desktop package is published separately and not in runtime
    closure.
    """
    assert "mvge-desktop" not in verify_install.RUNTIME_CLOSURE
    assert "mvgeos-gui" not in verify_install.RUNTIME_CLOSURE

    resolved = dict(_coherent("0.6.17"), **{"mvge-desktop": "0.6.17"})

    assert verify_install.lockstep_failures(resolved, "0.6.17") == []


def test_a_name_outside_the_closure_does_not_fail_the_tree() -> None:
    """A third-party `mvge-*` name arriving is not this gate's business."""
    resolved = dict(_coherent("0.6.17"), **{"mvge-someone-elses": "0.6.17"})

    assert verify_install.lockstep_failures(resolved, "0.6.17") == []


def test_a_probe_that_ran_nothing_is_a_failure_not_a_pass() -> None:
    """An empty mapping is "the probe failed", not "nothing is wrong"."""
    failures = verify_install.lockstep_failures({}, "0.6.17")

    assert len(failures) == 1
    assert "probe returned nothing" in failures[0]


def test_a_missing_name_alone_is_enough_to_fail() -> None:
    resolved = {"mvge-other": "0.6.17"}

    failures = verify_install.lockstep_failures(resolved, "0.6.17")

    assert len(failures) == 1
    assert "not installed by this release: mvge" in failures[0]


def test_a_missing_bwrap_is_reported_as_the_known_reason(monkeypatch) -> None:
    monkeypatch.setattr(verify_install.shutil, "which", lambda _name: None)

    usable, why_not = verify_install.sandbox_usable()

    assert usable is False
    assert why_not == "no bwrap on PATH"


def test_a_bwrap_that_cannot_unshare_is_not_treated_as_usable(monkeypatch) -> None:
    """The GitHub Actions runner case: installed, on PATH, and denied.

    Checking only for the binary's presence sent the first CI run of this
    script straight into a sandbox it could not create, and it died there with
    a bare exit code instead of the transcript that makes a failure readable.
    """
    monkeypatch.setattr(verify_install.shutil, "which", lambda _name: "/usr/bin/bwrap")

    def denied(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["bwrap"],
            returncode=1,
            stdout="",
            stderr="bwrap: setting up uid map: Permission denied\n",
        )

    monkeypatch.setattr(verify_install.subprocess, "run", denied)

    usable, why_not = verify_install.sandbox_usable()

    assert usable is False
    assert "uid map" in why_not


def test_a_working_bwrap_is_usable(monkeypatch) -> None:
    monkeypatch.setattr(verify_install.shutil, "which", lambda _name: "/usr/bin/bwrap")

    def works(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["bwrap"], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(verify_install.subprocess, "run", works)

    assert verify_install.sandbox_usable() == (True, "")


def _index_html(*versions: str) -> str:
    """A minimal PyPI simple-index page listing ``versions`` of mvge."""
    return "\n".join(
        '<a href="https://files.pythonhosted.org/packages/xx/'
        f'mvge-{version}-py3-none-any.whl">mvge-{version}-py3-none-any.whl</a>'
        for version in versions
    )


class _IndexResponse:
    """The part of urlopen's return the harness reads: a context manager with a body."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> _IndexResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _serve_index(monkeypatch: pytest.MonkeyPatch, bodies: list[str]) -> None:
    """Point the harness's index reads at ``bodies``, one per request."""

    def fake_urlopen(*_args: object, **_kwargs: object) -> _IndexResponse:
        return _IndexResponse(bodies.pop(0))

    monkeypatch.setattr(verify_install.urllib.request, "urlopen", fake_urlopen)


def test_version_on_index_finds_the_released_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index lists the version once it has propagated."""
    _serve_index(monkeypatch, [_index_html("0.6.24", "0.6.25")])

    assert verify_install.version_on_index("0.6.25") is True


def test_version_on_index_does_not_match_a_version_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.6.2 must not match 0.6.25 -- the match is on the full filename stem."""
    _serve_index(monkeypatch, [_index_html("0.6.25")])

    assert verify_install.version_on_index("0.6.2") is False


def test_an_unreachable_index_reads_as_not_yet_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient network failure is "not visible yet", not a crash.

    Only URLError is caught: a bug in the check itself must fail loudly
    rather than turn into a ten-minute wait.
    """

    def dead(*_args: object, **_kwargs: object) -> None:
        raise verify_install.urllib.error.URLError("connection refused")

    monkeypatch.setattr(verify_install.urllib.request, "urlopen", dead)

    assert verify_install.version_on_index("0.6.25") is False


def test_wait_returns_as_soon_as_the_version_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-10-11 case: missing on the first poll, present on the second."""
    _serve_index(monkeypatch, [_index_html("0.6.24"), _index_html("0.6.24", "0.6.25")])
    sleeps: list[float] = []
    monkeypatch.setattr(verify_install.time, "sleep", sleeps.append)

    assert (
        verify_install.wait_for_version_on_index("0.6.25", timeout=600, interval=60)
        is True
    )
    assert sleeps == [60]


def test_wait_gives_up_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A version that never lands is a clear failure, not an endless wait."""
    _serve_index(monkeypatch, [_index_html("0.6.24")] * 20)
    now = [0.0]
    monkeypatch.setattr(verify_install.time, "monotonic", lambda: now[0])

    def advance(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(verify_install.time, "sleep", advance)

    assert (
        verify_install.wait_for_version_on_index("0.6.25", timeout=600, interval=60)
        is False
    )
    assert now[0] >= 600
