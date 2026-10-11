"""Prove the published distribution is usable by someone who has never seen it.

`publish.yml` already runs `uvx mvgeos --help` from a checkout-free runner, which
proves the name resolves and the console script exists. It proves nothing about
whether the thing then *works*: a package whose Realm Rune is missing, whose
credential never loads, or whose Tome never reaches disk all sail past `--help`
and fail on a stranger's first real task. This harness runs that first real
task and asserts on the three things a Summoner would notice:

1. `uvx mvgeos --help` exits zero.
2. Every first-party distribution in the resolved tree is at the released
   version -- the seven that make up the runtime closure, none missing, none
   from a different release.
3. The task produces its artifact, byte for byte.
4. A session Tome lands in ``$HOME/.agents/sessions/`` naming the model that
   served it, and records the Spell that was cast.

Assertion 2 exists because of a measured failure. On 2026-10-09 a release
half-landed: six of eight distributions reached PyPI and the root ``mvgeos``
was refused, and ``mvgeos 0.6.16`` declares ``mvgeos-cli>=0.6.16``, so a
mixed-version tree still satisfied every constraint it was given. ``--help``
exited zero on it and the first real task completed. Nothing that existed at
the time could tell. A half-published release is now a loud failure instead of
a silent one.

The ``>=`` floor was itself the second half of that defect, and it outlived the
half-land. On 2026-10-10 verify-install failed again on a release where all
eight packages had published::

    verify_install failed: a mixed-version tree resolved at released 0.6.24:
    mvgeos-agent 0.6.25, mvgeos-cli 0.6.25, mvgeos-core 0.6.25,
    mvgeos-provider 0.6.25, mvgeos-runes 0.6.25, mvgeos-tome 0.6.25

``uvx mvgeos==0.6.24`` resolves ``mvgeos-cli>=0.6.24`` to a 0.6.25 cli, so the
root pins to the release a stranger asked for and every sibling floats off it.
A fully-published release hands out a mixed tree exactly as a half-landed one
does, as long as the internal constraint is a floor. The internal constraints
are ``==`` pins now -- constrained, resolvable, and unable to float upward off
the release they shipped with. ``check_lockstep`` and ``check_wheels`` both
accept a pin, so neither gate changes.

This assertion stays regardless. It is the thing that noticed while the pins
were floors, and it is what will notice if the two ever drift apart again.

"Cold" here is precise, and the distinction matters. The scratch ``HOME`` starts
empty apart from the credential, and the uv cache is a fresh directory, so the
measured install is a real download rather than a cache hit.

How isolated the run is depends on the host, and the report says which it got
rather than rounding up. On a host with ``bwrap`` the harness re-runs itself
inside a mount, process, and UTS namespace in which the repository and the
operator's own ``HOME`` do not exist at all -- and it proves that from inside,
because a sandbox nobody checked is a claim, not evidence. Without ``bwrap`` it
still runs against a scratch ``HOME``, which is a weaker thing, and says so.

Usage:
    python scripts/verify_install.py --version 0.6.14
    OPENROUTER_API_KEY=sk-or-... python scripts/verify_install.py --version 0.6.14

The credential is read from ``OPENROUTER_API_KEY`` and written into the scratch
``HOME`` the way a Summoner would, so the run exercises the real key-resolution
path rather than being handed ``--api-key``.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

# The Realm and model the engine defaults to, and the cheapest thing on it. Zen
# serves its free tier with no credential at all and bills it at zero, so a
# release gate costs nothing and needs no key -- which matters more than the
# money: a gate that needs a secret cannot run on a fork, and a fork is exactly
# where a stranger's first contact happens. Overridable because a flaky upstream
# model is not a packaging failure and should not read as one.
DEFAULT_PROVIDER = "opencode"
DEFAULT_MODEL = "opencode/space-bunny-free"

# The Realm Rune and the Mvge a Summoner installs before their first task, named
# exactly as the README names them.
REALM_RUNE = "opencode-realm"
MVGE = "coding_mvge"

# Realms that bill, and so refuse a request with no credential. Recorded as an
# exception list rather than an exemption list so a new Realm defaults to
# needing one: the cost of that mistake is a confusing failure, and the cost of
# the opposite is a request sent with an empty Authorization header.
REALMS_REQUIRING_CREDENTIAL = frozenset({"openrouter"})

# The environment variable each Realm reads its key from.
#
# A copy of ``mvgeos_provider.realms.REALM_API_KEY_ENV``, which is the authority.
# The duplication is forced: this harness runs under a bare ``python3`` before
# any sync, so it cannot import the workspace it ships beside. ``
# test_api_key_env_matches_the_engine`` holds the two together, so the copy
# cannot drift without a red build.
#
# It matters because the CLI falls back to ``OPENROUTER_API_KEY`` for *every*
# Realm (``mvgeos-cli/src/mvgeos_cli/main.py``). Exporting an OpenRouter key
# while testing ``--provider opencode`` therefore hands it to Zen, and the run
# fails at the host that received it rather than at the packaging under test.
API_KEY_ENV_BY_REALM = {
    "openrouter": "OPENROUTER_API_KEY",
    "opencode": "OPENCODE_API_KEY",
}

# OpenRouter rejects a request whose declared ``max_tokens`` exceeds what the
# key's remaining credit can cover, and it rejects it *before* any tokens are
# spent. The engine's default is 4096, which is more than a nearly-exhausted
# key can offer, so a ``--provider openrouter`` run would fail on the account's
# budget rather than on the packaging it exists to check. This task needs a
# fraction of it. The default Realm bills its free tier at zero, so the cap is
# no longer about money there -- it keeps the gate's own turn short.
MAX_TOKENS = 2000

# The artifact the task is asked to produce, and what it must contain.
ARTIFACT = "hello.txt"
ARTIFACT_CONTENT = "hello from mvgeos"


def api_key_env_for_realm(realm: str) -> str:
    """The variable ``realm`` reads its key from, mirroring the engine's."""
    return API_KEY_ENV_BY_REALM.get(realm, f"{realm.upper()}_API_KEY")


def realm_rune_for_realm(realm: str) -> str:
    """The Rune that provides ``realm``, mirroring the engine's derivation.

    An unmapped Realm falls back to ``<realm>-realm``, which is the convention
    both Runes we ship use. That is deliberately not "the default Rune": naming
    ``opencode-realm`` for a Realm we know nothing about would install a
    provider that cannot serve the model under test and fail somewhere far from
    the cause.
    """
    if not realm:
        return ""
    if realm == DEFAULT_PROVIDER:
        return REALM_RUNE
    return f"{realm}-realm"


TASK = f"Create a file named {ARTIFACT} containing exactly the text: {ARTIFACT_CONTENT}"

# The first-party distributions `uvx mvgeos` resolves. Seven, not eight:
# `mvgeos-gui` is published in lockstep but nothing depends on it -- it is a
# sibling front end, not part of the closure -- so a stranger running the
# documented command never installs it. Reading this off the published metadata
# at 0.6.17:
#
#   mvgeos      -> mvgeos-cli
#   mvgeos-cli  -> mvgeos-agent, mvgeos-core, mvgeos-provider,
#                  mvgeos-tome, mvgeos-runes
#
# Asserting all eight are *present* would fail forever and for a reason that has
# nothing to do with the release. That `mvgeos-gui` exists on the index at the
# same version is checked by the publish workflow, which asks PyPI for all eight
# by HTTP.
RUNTIME_CLOSURE = (
    "mvgeos",
    "mvgeos-cli",
    "mvgeos-agent",
    "mvgeos-core",
    "mvgeos-provider",
    "mvgeos-tome",
    "mvgeos-runes",
)

# Marks the probe's answer so a uv banner, a warning, or a Realm's chatter on
# stdout cannot be mistaken for it.
PROBE_MARKER = "MVGEOS-PROBE:"

# Lines the CLI prints about itself before the model says anything. They are
# local diagnostics, so counting one as the first token would understate the
# time-to-first-token by however long Realm setup took -- which is most of it.
LOCAL_PREAMBLE = re.compile(r"^(WARNING|Error)\s*:", re.IGNORECASE)

# Set on the re-executed child so it cannot sandbox itself again. The underscore
# prefix is load-bearing: ``scrubbed`` drops private keys from every ``uvx``
# child, so neither the marker nor the host paths reach the process under test.
SANDBOX_MARKER = "_MVGEOS_VERIFY_IN_SANDBOX"
SANDBOX_HOST_PATHS = "_MVGEOS_VERIFY_HOST_PATHS"

# A read-only view of an ordinary Linux install, and nothing else. Bubblewrap
# cannot mount onto a destination that does not exist, so a target missing here
# is skipped rather than fatal: a host without ``/lib64`` should still verify.
SANDBOX_BINDS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

# systemd-resolved is reached through the ``/etc/resolv.conf`` symlink, which
# resolves outside ``/etc``. Bind only the target and the sandbox has no DNS,
# which would stop ``uvx`` reaching PyPI -- the opposite of this harness's job.
SANDBOX_DNS = "/run/systemd/resolve"

SANDBOX_PATH = "/usr/bin:/bin"

# The harness is stdlib-only on purpose, so a system interpreter is enough to
# re-enter it. Binding the running one instead would drag a workspace ``.venv``
# into the sandbox, which is the workspace this harness exists to ignore.
SANDBOX_PYTHONS = ("/usr/bin/python3", "/usr/local/bin/python3")


def clean_env(
    home: pathlib.Path, cache: pathlib.Path, api_key: str, api_key_env: str
) -> dict[str, str]:
    """Return an environment that cannot see the machine running the harness.

    Built from nothing rather than filtered from ``os.environ``. A filter has to
    enumerate every variable that could leak state, and forgetting one silently
    turns "a stranger's machine" into "this machine, wearing a hat".

    The credential is carried only when one was supplied, and only under the
    name the Realm under test reads. The default Realm serves its free tier
    keyless, so the default run must genuinely have no credential in it --
    exporting an empty ``OPENROUTER_API_KEY`` would look like the same thing
    while leaving a code path that reads a credential open.
    """
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "UV_CACHE_DIR": str(cache),
        "UV_NO_CONFIG": "1",
        # Rich reflows to the console width, and a wrapped preamble stops
        # looking like a preamble: `--approval-mode` prints one WARNING line at
        # 80 columns and two at 80 if it overflows, so the second half no longer
        # starts with "WARNING:" and reads as model output. Pinning a width wide
        # enough for the CLI's own diagnostics keeps one message on one line,
        # which is what makes the stream machine-readable at all.
        "COLUMNS": "400",
    }
    if api_key:
        env[api_key_env] = api_key
    return env


def is_model_output(line: str) -> bool:
    """Whether a line of stdout came from the model rather than the CLI.

    The first non-preamble line is the channeling the task asked to measure, so
    an empty line or a local ``WARNING:`` must not claim the timestamp.
    """
    stripped = line.strip()
    if not stripped:
        return False
    return LOCAL_PREAMBLE.match(stripped) is None


def sandbox_interpreter() -> str:
    """The interpreter used to re-enter the harness inside a sandbox."""
    for candidate in SANDBOX_PYTHONS:
        if pathlib.Path(candidate).exists():
            return candidate
    return sys.executable


def _needs_bind(target: str | pathlib.Path) -> bool:
    """Whether ``target`` still requires its own bind, once ``/usr`` is bound.

    On a distribution that symlinks ``/lib64`` and ``/bin`` into ``/usr``, the
    contents arrive with the ``/usr`` bind but the *path* does not: nothing
    recreates ``/lib64/ld-linux-x86-64.so.2``, so the sandboxed interpreter
    cannot start. Hence the roots are always bound -- the bind is what creates
    the path -- while anything already inside one of them, such as a ``python3``
    symlink under ``/usr/bin``, is skipped, because a second bind onto a symlink
    destination fails outright.
    """
    path = pathlib.Path(target)
    if not path.exists():
        return False
    roots = [pathlib.Path(root).resolve() for root in SANDBOX_BINDS]
    return not any(path.resolve().is_relative_to(root) for root in roots)


def sandbox_command(scratch: pathlib.Path, argv: list[str]) -> list[str] | None:
    """The argv that re-runs this harness inside a throwaway sandbox.

    The mount, process, IPC, UTS, and cgroup namespaces are unshared. The
    network is deliberately not: resolving ``mvgeos`` from PyPI and reaching a
    Realm are the two things being proved, and cutting the network would prove
    something else entirely -- a run that fails for want of DNS is not evidence
    of a broken release.

    What makes this a stranger's machine rather than a stranger's ``HOME`` is the
    binds. The host's ``HOME``, working tree, and every file outside
    ``SANDBOX_BINDS`` are simply absent, so the run cannot accidentally pass by
    reading state the operator already had. Returns ``None`` when the host has no
    sandbox, which the caller reports rather than quietly falling back.
    """
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        return None
    host_paths = json.dumps([os.environ.get("HOME", ""), str(pathlib.Path.cwd())])
    command = [
        bubblewrap,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--chdir",
        str(scratch),
    ]
    for target in SANDBOX_BINDS:
        if pathlib.Path(target).exists():
            command += ["--ro-bind", target, target]
    for target in (sandbox_interpreter(),):
        if _needs_bind(target):
            command += ["--ro-bind", str(target), str(target)]
    if pathlib.Path(SANDBOX_DNS).is_dir():
        command += ["--ro-bind", SANDBOX_DNS, SANDBOX_DNS]
    # The whole scratch, read-write, because everything in it was made by this
    # run: the credential, the cache the install downloads into, the directory
    # the task writes its artifact to, and the harness's own copy. It is the
    # only writable host path the sandbox has.
    command += ["--bind", str(scratch), str(scratch)]
    command += [
        "--setenv",
        "PATH",
        SANDBOX_PATH,
        "--setenv",
        SANDBOX_MARKER,
        "1",
        "--setenv",
        SANDBOX_HOST_PATHS,
        host_paths,
    ]
    return [*command, *argv]


def host_paths_reachable() -> list[str]:
    """Which host paths the sandbox failed to hide.

    Empty outside a sandbox, where the concept does not apply. Inside one, a
    non-empty result means the isolation is decorative: the run could have
    picked up the operator's Realm cache or a local checkout, and every green
    line after it would be worth nothing.
    """
    raw = os.environ.get(SANDBOX_HOST_PATHS, "")
    if not raw:
        return []
    return [path for path in json.loads(raw) if path and pathlib.Path(path).exists()]


def newest_tome(sessions: pathlib.Path) -> pathlib.Path | None:
    """The most recently written session JSONL, or ``None`` when none exists."""
    if not sessions.is_dir():
        return None
    tomes = [p for p in sessions.glob("*.jsonl") if p.is_file()]
    if not tomes:
        return None
    return max(tomes, key=lambda p: p.stat().st_mtime)


def session_header(tome: pathlib.Path) -> dict[str, Any] | None:
    """The Tome's ``session`` record, which names the model that served it."""
    with tome.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "session":
                return entry
    return None


def cast_spells(tome: pathlib.Path) -> list[str]:
    """Names of the Spells the Tome records as cast.

    A Tome with no Spell cast means the model answered from memory and never
    touched the filesystem, so the artifact cannot be attributed to the run.
    """
    cast: list[str] = []
    with tome.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = entry.get("payload") or {}
            for block in payload.get("content") or []:
                if not isinstance(block, dict):
                    continue
                call = block.get("spell_cast") or {}
                name = call.get("name")
                if isinstance(name, str) and name not in cast:
                    cast.append(name)
    return cast


def run_uvx(
    version: str,
    env: dict[str, str],
    args: list[str],
    *,
    cwd: pathlib.Path,
) -> tuple[int, str, float]:
    """Run ``uvx --from mvgeos==VERSION mvgeos ...`` under ``env``.

    Returns the exit code, the combined output, and the elapsed seconds.
    """
    command = ["uvx", "--from", f"mvgeos=={version}", "mvgeos", *args]
    started = time.monotonic()
    process = subprocess.run(
        command,
        env=scrubbed(env),
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    elapsed = time.monotonic() - started
    return process.returncode, process.stdout + process.stderr, elapsed


def scrubbed(env: dict[str, str]) -> dict[str, str]:
    """The child environment, minus the private ``_``-prefixed carrier keys."""
    return {k: v for k, v in env.items() if not k.startswith("_")}


def resolved_distributions(version: str, env: dict[str, str]) -> dict[str, str]:
    """Every first-party distribution this release actually resolves to.

    ``uvx --from mvgeos==VERSION python`` builds the same environment the
    console script runs in and then asks that environment what it holds, so the
    answer describes the resolved tree rather than what the release claimed to
    contain.

    An empty mapping means the probe did not run -- no interpreter, or uv
    refused before python started. The caller turns that into one reported
    failure; the distinction between "the probe is broken" and "the tree is
    wrong" matters more to the person reading the log than to the exit code,
    which is 1 either way.
    """
    probe = (
        "import json\n"
        "from importlib.metadata import distributions\n"
        "found = {\n"
        "    d.metadata['Name']: d.version\n"
        "    for d in distributions()\n"
        "    if (d.metadata['Name'] or '').startswith('mvgeos')\n"
        "}\n"
        f"print('{PROBE_MARKER}' + json.dumps(found, sort_keys=True))\n"
    )
    process = subprocess.run(
        ["uvx", "--from", f"mvgeos=={version}", "python", "-c", probe],
        env=scrubbed(env),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    for line in process.stdout.splitlines():
        if not line.startswith(PROBE_MARKER):
            continue
        try:
            parsed = json.loads(line[len(PROBE_MARKER) :])
        except json.JSONDecodeError:
            return {}
        # Only a flat name->version mapping is a usable answer; anything else
        # means the probe printed something this harness does not understand,
        # which is the "could not run" case rather than a pass.
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            return {}
        return parsed
    return {}


def lockstep_failures(resolved: dict[str, str], version: str) -> list[str]:
    """Why ``resolved`` is not one coherent release of the runtime closure.

    Split out from ``main`` so the rule is readable on its own and testable
    without installing anything.
    """
    if not resolved:
        return ["the resolved-tree probe returned nothing"]
    failures = []
    missing = [name for name in RUNTIME_CLOSURE if name not in resolved]
    if missing:
        failures.append("not installed by this release: " + ", ".join(missing))
    off = {name: found for name, found in resolved.items() if found != version}
    if off:
        failures.append(
            f"a mixed-version tree resolved at released {version}: "
            + ", ".join(f"{name} {found}" for name, found in sorted(off.items()))
        )
    return failures


def stamp_lines(lines: list[tuple[float, str]]) -> str:
    """Render ``(elapsed, text)`` pairs as the timestamped transcript.

    Every line keeps the moment it actually arrived. An earlier version stamped
    only the first of several and printed 0.00s for the rest, which makes a real
    gap look like simultaneity -- the one thing a timing report must not do.
    """
    return "\n".join(f"[{elapsed:7.2f}s] {text}" for elapsed, text in lines)


def sandbox_usable() -> tuple[bool, str]:
    """Whether a sandbox can actually be created here, and why not if it cannot.

    "Is bwrap on PATH" is the wrong question. On a GitHub Actions ubuntu runner
    the workflow installs bubblewrap, it is on PATH, and it still cannot create
    a user namespace:

    ```
    bwrap: setting up uid map: Permission denied
    ```

    That is what happened the first time this script ever executed in CI, on
    2026-10-09. The harness took the sandbox path on the strength of the binary
    existing and died there, so a release gate that had never run before
    reported a bare exit code and an exit-1 with none of the transcript that
    makes a failure diagnosable.

    So the probe is the cheapest thing that exercises the capability rather than
    the file's presence: unshare a user namespace and bind the root read-only.
    Anything weaker passes on a host where bwrap exists and cannot do what this
    harness needs.

    Returns ``(usable, reason)``, and ``reason`` is empty when usable. The
    caller reports the reason rather than falling back silently, because a
    weaker run has to be described as weaker to be worth anything.
    """
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        return False, "no bwrap on PATH"
    completed = subprocess.run(
        [bubblewrap, "--unshare-user", "--ro-bind", "/", "/", "true"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode == 0:
        return True, ""
    said = (completed.stderr or completed.stdout or "").strip().splitlines()
    return False, (
        "bwrap is on PATH but cannot unshare a user namespace here ("
        f"{said[-1] if said else f'exit {completed.returncode}'})"
    )


def run_sandboxed(arguments: argparse.Namespace) -> int:
    """Re-run the whole harness inside a sandbox and return its exit code.

    The harness is copied into the scratch rather than bound from its checkout,
    and that is not tidiness. Bubblewrap materialises a destination's parent
    directories, so binding ``.../mvgeos/scripts/verify_install.py`` in place
    rebuilds the entire ``/home/vanluther/...`` chain inside the sandbox -- the
    operator's tree, recreated, with the repository sitting at the bottom of it.
    The run still passed, which is exactly the failure mode worth being afraid
    of: a green line that proves nothing because the thing it should not have
    seen was sitting right there.

    Nothing about the measurement changes. The copy is the measuring instrument,
    not the thing measured; every ``mvgeos`` still arrives from PyPI.
    """
    outer = pathlib.Path(tempfile.mkdtemp(prefix="mvgeos-verify-sandbox-"))
    scratch = outer / "scratch"
    for name in ("home", "cache", "work", "harness"):
        (scratch / name).mkdir(parents=True)
    harness = scratch / "harness" / pathlib.Path(__file__).name
    shutil.copyfile(pathlib.Path(__file__).resolve(), harness)
    command = sandbox_command(
        scratch,
        [
            sandbox_interpreter(),
            str(harness),
            *sys.argv[1:],
            "--scratch",
            str(scratch),
            "--no-sandbox",
        ],
    )
    if command is None:
        shutil.rmtree(outer, ignore_errors=True)
        return 1
    try:
        completed = subprocess.run(command, check=False)
        return completed.returncode
    finally:
        if arguments.keep:
            print(f"sandbox scratch kept at {outer}")
        else:
            shutil.rmtree(outer, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    """The harness's argument parser.

    Built by a named function rather than inline in :func:`main` so the defaults
    can be asserted without running the gate. The Realm, model, and Rune a bare
    invocation reaches are decisions, and an earlier revision proved they can be
    wrong while every test stays green: ``--provider`` still defaulted to
    ``openrouter`` after the engine's default had moved to ``opencode``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="released version to test")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model slug to run")
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help="Realm to force; the slug alone derives one from its prefix",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_TOKENS,
        help="cap the model's declared budget; keeps the gate off the key's limit",
    )
    parser.add_argument(
        "--help-only",
        action="store_true",
        help="prove resolution only, skipping the paid task",
    )
    parser.add_argument(
        "--sandbox",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="re-run inside a bwrap sandbox that hides the repo and this HOME",
    )
    parser.add_argument(
        "--scratch",
        default=None,
        help="directory to build HOME, cache, and work in",
    )
    parser.add_argument("--keep", action="store_true", help="keep the scratch HOME")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()

    api_key_env = api_key_env_for_realm(arguments.provider)
    api_key = os.environ.get(api_key_env, "")
    if (
        arguments.provider in REALMS_REQUIRING_CREDENTIAL
        and not api_key
        and not arguments.help_only
    ):
        print(
            f"verify_install failed: the {arguments.provider!r} Realm needs a "
            f"credential and {api_key_env} is not set. The default "
            f"({DEFAULT_PROVIDER!r}) serves a free tier keyless and needs "
            f"neither, so the cheaper gate is to drop --provider. A green "
            f"gate that never called a model would be a lie.",
            file=sys.stderr,
        )
        return 2

    already_sandboxed = bool(os.environ.get(SANDBOX_MARKER))
    if arguments.sandbox and not already_sandboxed:
        usable, why_not = sandbox_usable()
        if not usable:
            print(
                f"verify_install: {why_not}, so the run cannot be isolated "
                "from this machine. Proceeding against a scratch HOME only, "
                "which is a weaker claim than a clean machine.",
                file=sys.stderr,
            )
        else:
            return run_sandboxed(arguments)

    scratch = pathlib.Path(
        arguments.scratch or tempfile.mkdtemp(prefix="mvgeos-verify-")
    )
    owns_scratch = arguments.scratch is None
    home = scratch / "home"
    cache = scratch / "cache"
    work = scratch / "work"
    for directory in (home / ".agents" / "auth", cache, work):
        directory.mkdir(parents=True, exist_ok=True)

    # Seed the credential only when there is one. The default Realm serves a
    # free tier keyless, and a gate that silently supplied a key would stop
    # testing the thing a stranger actually has: no key. When there is a key it
    # is written the way a Summoner types it, under the Realm's own name
    # (`~/.agents/auth/<realm>.json`, per `mvgeos_agent.auth`), so the real
    # key-resolution path is exercised rather than bypassed with --api-key. The
    # Rune and the Mvge are installed by the run below either way.
    if api_key:
        (home / ".agents" / "auth" / f"{arguments.provider}.json").write_text(
            json.dumps({"api_key": api_key}), encoding="utf-8"
        )
    realm_rune = realm_rune_for_realm(arguments.provider)
    env = clean_env(home, cache, api_key, api_key_env)
    version = arguments.version

    failures: list[str] = []
    stamps: list[float] = []
    leaked = host_paths_reachable()
    try:
        print(f"verify_install: mvgeos=={version} on a scratch HOME")
        print(f"  HOME   {home}")
        print(f"  cache  {cache} (empty: the install below is a real download)")
        isolation = (
            "sandbox; this HOME and the repo are absent"
            if already_sandboxed
            else "scratch HOME only"
        )
        print(f"  isolation  {isolation}")
        for path in leaked:
            failures.append(f"{path} is reachable from inside the run")

        code, output, elapsed = run_uvx(version, env, ["--help"], cwd=work)
        print(f"\n[1/5] uvx mvgeos --help -> exit {code} in {elapsed:.2f}s")
        if code != 0:
            failures.append(f"`--help` exited {code}")
            print(output[-2000:])

        # Run even when `--help` failed: a resolution failure and a mixed tree
        # are different faults and the log should say which one it hit.
        resolved = resolved_distributions(version, env)
        print(
            "[2/5] resolved tree -> "
            + (", ".join(f"{n} {v}" for n, v in sorted(resolved.items())) or "nothing")
        )
        for failure in lockstep_failures(resolved, version):
            failures.append(failure)

        if arguments.help_only:
            for failure in failures:
                print(f"verify_install failed: {failure}", file=sys.stderr)
            return 1 if failures else 0

        code, output, _ = run_uvx(
            version,
            env,
            ["rune", "install", realm_rune, "--confirm-python-deps"],
            cwd=work,
        )
        print(f"[3/5] rune install {realm_rune} -> exit {code}")
        if code != 0:
            failures.append(f"`rune install {realm_rune}` exited {code}")
            print(output[-2000:])

        code, output, _ = run_uvx(
            version,
            env,
            ["mvge", "install", "--confirm-python-deps", MVGE],
            cwd=work,
        )
        print(f"[4/5] mvge install {MVGE} -> exit {code}")
        if code != 0:
            failures.append(f"`mvge install {MVGE}` exited {code}")
            print(output[-2000:])

        task_args = [
            "--agent-name",
            MVGE,
            "--model",
            arguments.model,
            "--provider",
            arguments.provider,
            "--approval-mode",
            "allow-all",
            "--max-tokens",
            str(arguments.max_tokens),
            TASK,
        ]
        started = time.monotonic()
        process = subprocess.Popen(
            [
                "uvx",
                "--from",
                f"mvgeos=={version}",
                "mvgeos",
                *task_args,
            ],
            env=scrubbed(env),
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        lines: list[tuple[float, str]] = []
        assert process.stdout is not None
        for line in process.stdout:
            elapsed = time.monotonic() - started
            text = line.rstrip("\n")
            if text.strip() and is_model_output(text) and not stamps:
                stamps.append(elapsed)
            lines.append((elapsed, text))
        code = process.wait()
        total = time.monotonic() - started

        print(f"[5/5] real task -> exit {code} in {total:.2f}s")
        print(stamp_lines(lines))
        if code != 0:
            failures.append(f"the task exited {code}")

        artifact = work / ARTIFACT
        if not artifact.is_file():
            failures.append(f"{ARTIFACT} was never created")
        elif artifact.read_text(encoding="utf-8") != ARTIFACT_CONTENT:
            failures.append(f"{ARTIFACT} does not contain the requested text")

        tome = newest_tome(home / ".agents" / "sessions")
        if tome is None:
            failures.append("no session Tome was persisted")
        else:
            header = session_header(tome)
            if header is None:
                failures.append(f"{tome.name} has no session header")
            elif header.get("model") != arguments.model:
                failures.append(
                    f"Tome names model {header.get('model')!r}, expected "
                    f"{arguments.model!r}"
                )
            spells = cast_spells(tome)
            if not spells:
                failures.append(f"{tome.name} records no Spell cast")
            else:
                print(f"\ntome   {tome.name} ({tome.stat().st_size} bytes)")
                print(f"model  {header.get('model') if header else None}")
                print(f"spells {', '.join(spells)}")
    finally:
        if arguments.keep:
            print(f"\nscratch kept at {scratch}")
        elif owns_scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    print()
    if stamps:
        print(f"cold to first token: {stamps[0]:.2f}s")
    if failures:
        for failure in failures:
            print(f"verify_install failed: {failure}", file=sys.stderr)
        return 1
    print("verify_install ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
