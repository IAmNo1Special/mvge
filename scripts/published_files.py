#!/usr/bin/env python3
"""Which built files PyPI does not have yet, for one package version.

`publish.yml` uploads eight distributions and stops at the first refusal, so a
release that fails partway leaves the index half-written and the only recovery is
to run the same tag again. Something has to decide what is still missing, and
that is this.

It was `uv publish --check-url`, and it does not work. uv compares the *hash* of
the rebuilt file against the published one and fails when they differ, which for
a wheel built by `setup-uv@v7` they usually do: the wheel embeds
``Generator: uv <version>`` in its ``WHEEL`` metadata, and setup-uv installs the
newest uv available, so the same commit rebuilt on a different day produces
different bytes. Measured on `v0.6.17`: the published
``mvge.core-0.6.17-py3-none-any.whl`` carries ``Generator: uv 0.12.24`` and a
rebuild of the identical commit carries a different version. Every other byte of
the wheel, including the entire package payload, is identical -- only that line
and the ``RECORD`` hash of it differ. uv refused with

    error: Local file and index file do not match for
    `mvge.core-0.6.17-py3-none-any.whl`

which is the resume mechanism refusing to resume.

PyPI versions are immutable, so the question is not "are these bytes the same"
but "is this filename already there". That is what this asks, per file, against
the JSON API. A version that exists with a file missing is possible -- an upload
that died between two files -- so the check is per file rather than per version,
and a version that is wholly absent reports every file as missing.

Usage:
    python scripts/published_files.py --package mvgeos --version 0.6.17 \\
        --dist-dir dist

Prints one filename per line for each file not yet on PyPI. Prints nothing and
exits 0 when everything has landed. Exits 1 on any failure to determine that,
rather than reporting a file as missing and letting the caller try to upload
something that is already there.

Stdlib only, for the same reason `verify_install.py` is: this runs between a
release and the index, and nothing that stands there should itself need
installing.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# PyPI's JSON API is the check. The simple index would answer the same question
# from inside the publish job without a second host, but it is HTML, and
# parsing an index page to decide what may be uploaded is the kind of thing that
# silently stops matching when PyPI changes its markup.
API = "https://pypi.org/pypi/{package}/{version}/json"

# Long enough for a slow index, short enough that a hung connection fails the
# step instead of occupying a runner until the job timeout. uv is retrying
# three times inside this; this script is not retrying, because its job is to
# report and let the caller decide.
TIMEOUT = 30


def published_filenames(package: str, version: str) -> set[str] | None:
    """Filenames PyPI already holds for this version, or ``None`` if unknown.

    ``None`` means the question could not be answered and the caller must not
    treat anything as missing. An empty set means the answer is genuinely "none
    of them", which is the ordinary first-release case.
    """
    url = API.format(package=package, version=version)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        # 404 is the normal answer for a version that has not been uploaded,
        # and it is the one case that means "nothing is there".
        if error.code == 404:
            return set()
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None

    urls = body.get("urls")
    if not isinstance(urls, list):
        return None
    names = set()
    for entry in urls:
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str):
            names.add(entry["filename"])
    return names


def missing_files(package: str, version: str, filenames: list[str]) -> list[str] | None:
    """Which of ``filenames`` PyPI does not hold. ``None`` if undeterminable."""
    published = published_filenames(package, version)
    if published is None:
        return None
    return [name for name in filenames if name not in published]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist-dir", required=True)
    arguments = parser.parse_args()

    import pathlib

    dist = pathlib.Path(arguments.dist_dir) / arguments.package
    if not dist.is_dir():
        print(
            f"published_files: {dist} does not exist, so nothing can be checked",
            file=sys.stderr,
        )
        return 1

    # Wheels and sdists only. `uv build --out-dir` leaves a `.gitignore` in the
    # directory it creates, so an unfiltered listing reports a file that can
    # never be published as missing, and every already-landed package then looks
    # incomplete and gets re-uploaded. `.whl` is what check_publish.py counts
    # too, so the two agree on what a distribution is.
    suffixes = (".whl", ".tar.gz")
    filenames = sorted(
        path.name
        for path in dist.iterdir()
        if path.is_file() and path.name.endswith(suffixes)
    )
    if not filenames:
        print(f"published_files: {dist} holds no distributions", file=sys.stderr)
        return 1

    missing = missing_files(arguments.package, arguments.version, filenames)
    if missing is None:
        # Reporting these as missing would send the caller into an upload that
        # is guaranteed to fail on an immutable version, and the resulting 400
        # reads like a packaging defect rather than like an index that was down.
        print(
            f"published_files: could not reach the PyPI index for "
            f"{arguments.package} {arguments.version}, so what is missing is "
            f"unknown. Refusing to guess: uploading a file that is already "
            f"published fails on an immutable version, and that failure looks "
            f"like a packaging defect.",
            file=sys.stderr,
        )
        return 1

    for name in missing:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
