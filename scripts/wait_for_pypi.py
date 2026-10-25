#!/usr/bin/env python3
"""Wait for a release to reach the index before anything claims it is installable.

`publish.yml` uploads eight distributions and then, seconds later, two jobs ask
PyPI whether the release is there and whether a stranger can install it. Both
questions were being asked too early. On v0.6.19 the upload returned at
00:03:12Z and `verify-install`'s first command failed at 00:03:26Z:

```
[1/5] uvx mvgeos --help -> exit 1 in 0.14s
error: No solution found when resolving tool dependencies
  cause: Because there is no version of mvgeos==0.6.19 and you require
         mvgeos==0.6.19, we can conclude that your requirements are unsatisfiable.
```

The release had landed. The index had not caught up. Waiting is the whole of the
fix, and what to wait *for* is the part that is not obvious, because the obvious
answer is wrong in both directions.

``https://pypi.org/pypi/<name>/<version>/json`` answers **200 too early**. A
version-specific URL that has never been requested is not cached, so it is
served from origin: 200 the moment the release commits, while the cache purge
the upload triggers for the index ``uvx`` actually resolves against has not
propagated yet. ``uvx`` reads the simple index, and PyPI serves that from cache
-- measured on ``https://pypi.org/simple/mvgeos/`` as
``cache-control: max-age=600, public`` -- under a key warehouse purges on
upload (``warehouse/api/simple.py``, ``warehouse/cache/origin/__init__.py``). So
a 200 on the version URL does not mean the release is installable, and waiting
for it is waiting for something that has already happened.

The same URL answers **404 too long** afterwards. A 404 for a version that does
not exist yet is produced before the view runs, so it carries no cache key and
the upload's purge cannot invalidate it. ``published_files.py`` asks that exact
URL seconds *before* the upload in the publish job, which seeds the cache with a
404 that outlives the release it is about to become. Measured on
``https://pypi.org/pypi/mvgeos/0.6.99.json``: 404 from the Fastly cache on every
request for the whole of a six-minute observation window, ``x-cache: MISS,
HIT``, with no ``cache-control`` on the response to bound it.

So presence is read from ``https://pypi.org/pypi/<name>/json``, the project
document, which lists every release under ``releases`` and is cached under the
key the upload purges. It asks the same question -- is this version on the index
-- and it is fresh when the index is fresh, because warehouse purges
``project/<name>`` for the simple index and the project document together.

And the only question that settles the matter outright is the one a stranger's
machine asks, so ``--resolve`` waits for ``uvx`` itself to install the release
rather than for any URL.

Usage:
    python scripts/wait_for_pypi.py --version 0.6.25
    python scripts/wait_for_pypi.py --version 0.6.25 --resolve
    python scripts/wait_for_pypi.py --version 0.6.25 \\
        --package mvgeos --package mvgeos-gui --timeout 300

Prints one line per distribution and exits 0 when everything asked for has
arrived. Exits 1 when the timeout expires with something still absent, which is
the half-landed release the `verify` job exists to name. Exits 2 when the
version cannot be named, because a version that resolves to nothing turns every
question below into the vacuous one this script exists to stop: the URL
``/pypi/mvgeos//json`` is 200 for every package that has ever existed.

Stdlib only, for the same reason as the other release scripts: it stands
between a release and the index on a runner, under a bare ``python3``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The project document, read once per package and searched for the version,
# rather than the version's own URL. The docstring above records the two
# measurements that rule that URL out as a readiness signal.
PROJECT_API = "https://pypi.org/pypi/{package}/json"

# The distribution `uvx` installs, and the one `--resolve` waits for.
DEFAULT_PACKAGE = "mvge"

# Long enough for a slow index, short enough that a hung connection fails the
# step instead of occupying a runner until the job timeout.
REQUEST_TIMEOUT = 30

# How long a release is given to propagate. The failure this fixes outran no
# wait at all; two minutes is propagation with room to spare and is short
# enough that a release which never landed still gets diagnosed inside the
# `verify` job's own ten-minute budget.
WAIT_TIMEOUT = 120
POLL_INTERVAL = 5

# What the index last said about a distribution. The difference between the last
# two is the one that matters to the person reading the log: an absent
# distribution is a release that did not land, an unknown one is a question the
# index declined to answer, and reporting the second as the first sends whoever
# reads it to look for an upload problem that does not exist.
PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"


def release_state(package: str, version: str) -> str:
    """What the index says about ``package`` at ``version``, right now."""
    url = PROJECT_API.format(package=package)
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        # A project that has never been published answers 404 for the document
        # itself. That is the absent answer: there is no release inside it.
        if error.code == 404:
            return ABSENT
        return UNKNOWN
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
        ValueError,
    ):
        return UNKNOWN

    releases = body.get("releases")
    if not isinstance(releases, dict):
        return UNKNOWN
    files = releases.get(version)
    if not isinstance(files, list) or not files:
        return ABSENT
    return PRESENT


def wait_for_release(
    packages: list[str], version: str, timeout: float, interval: float
) -> dict[str, str]:
    """Poll until every package is on the index at ``version``, or time runs out.

    The packages are asked in rounds rather than one at a time, so eight
    distributions wait once rather than eight times: a release that half-landed
    is diagnosed after one timeout, not after the sum of eight.
    """
    pending = list(dict.fromkeys(packages))
    states = dict.fromkeys(pending, ABSENT)
    started = time.monotonic()
    while pending:
        for name in list(pending):
            state = release_state(name, version)
            states[name] = state
            if state == PRESENT:
                pending.remove(name)
                elapsed = time.monotonic() - started
                print(f"{name} {version}: on the index after {elapsed:.0f}s")
        if not pending:
            break
        if time.monotonic() - started >= timeout:
            break
        time.sleep(interval)

    for name in pending:
        elapsed = time.monotonic() - started
        if states[name] == UNKNOWN:
            print(
                f"{name} {version}: the index did not answer, so whether it is "
                f"there is unknown, after {elapsed:.0f}s"
            )
        else:
            print(f"{name} {version}: not on the index after {elapsed:.0f}s")
    return states


def resolves(version: str) -> bool:
    """Whether ``uvx`` can install the released distribution.

    The command is the one that failed on v0.6.19, run verbatim: the same
    ``uvx --from mvgeos==<version> mvgeos --help`` the harness runs first. Exit
    zero means a stranger's machine can install this release, which is the only
    claim the job makes.
    """
    completed = subprocess.run(
        ["uvx", "--from", f"mvge=={version}", "mvge", "--help"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def wait_for_resolution(version: str, timeout: float, interval: float) -> bool:
    """Wait until ``uvx --from mvge==VERSION mvge --help`` exits zero."""
    started = time.monotonic()
    while True:
        if resolves(version):
            elapsed = time.monotonic() - started
            print(f"uvx mvge=={version} --help: exit 0 after {elapsed:.0f}s")
            return True
        if time.monotonic() - started >= timeout:
            print(f"uvx mvge=={version} --help: still failing after {timeout:.0f}s")
            return False
        time.sleep(interval)


def normalized_version(version: str) -> str:
    """The release version as the index spells it, or ``""`` if it is unusable.

    ``v0.6.5`` is what a tag gives and ``0.6.5`` is what PyPI records, and the
    two are not interchangeable here. Anything that is not a version at all is
    refused rather than looked up: a version that resolves to the empty string
    asks the index a question it answers 200 for every package that has ever
    existed, which is the defect this whole script exists to close.
    """
    body = version.strip().lstrip("v")
    if not body or not body[0].isdigit():
        return ""
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True, help="released version, e.g. 0.6.25"
    )
    parser.add_argument(
        "--package",
        action="append",
        help=f"distribution to wait for; repeatable (default: {DEFAULT_PACKAGE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=WAIT_TIMEOUT,
        help=f"seconds to wait for the index (default: {WAIT_TIMEOUT})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=POLL_INTERVAL,
        help=f"seconds between polls (default: {POLL_INTERVAL})",
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="also wait until uvx can install the release, not only see it",
    )
    arguments = parser.parse_args()

    version = normalized_version(arguments.version)
    if not version:
        print(
            f"wait_for_pypi: {arguments.version!r} is not a release version, so "
            "there is nothing to wait for. Refusing rather than asking the "
            "index a question every package that ever existed answers 200.",
            file=sys.stderr,
        )
        return 2
    if arguments.timeout <= 0 or arguments.interval <= 0:
        print(
            "wait_for_pypi: --timeout and --interval must be positive",
            file=sys.stderr,
        )
        return 2

    packages = arguments.package or [DEFAULT_PACKAGE]
    states = wait_for_release(packages, version, arguments.timeout, arguments.interval)
    missing = [name for name in packages if states.get(name) != PRESENT]
    if missing:
        print(
            f"wait_for_pypi failed: {len(missing)} of {len(packages)} "
            f"distribution(s) are not on the index at {version}. The lines "
            "above name each one and say how long the index was asked.",
            file=sys.stderr,
        )
        return 1

    if arguments.resolve and not wait_for_resolution(
        version, arguments.timeout, arguments.interval
    ):
        print(
            f"wait_for_pypi failed: {version} is on the index but uvx cannot "
            "install it. That is the propagation lag, not a packaging fault: "
            "re-dispatch this job when it has had longer.",
            file=sys.stderr,
        )
        return 1

    print(f"wait_for_pypi ok: {version} is on the index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
