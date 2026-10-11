# Releasing to PyPI

How a `v*` tag becomes an installable `uvx mvgeos`. Uploads authenticate with
**OIDC trusted publishing**: GitHub mints a short-lived token from
`id-token: write`, PyPI exchanges it for a 15-minute upload credential, and
nothing long-lived is stored in this repository. It needs a one-time browser
step per project name, which is the whole of the human prerequisite — see
"PyPI setup" below.

## The eight distributions, not seven

The workspace has seven members plus the root distribution. All eight ship to
PyPI, because the root one is the package that owns the `mvgeos` name:

| # | Distribution | Depends on first-party |
| --- | --- | --- |
| 1 | `mvgeos-core` | — |
| 2 | `mvgeos-provider` | `mvgeos-core` |
| 3 | `mvgeos-tome` | — |
| 4 | `mvgeos-runes` | `mvgeos-core` |
| 5 | `mvgeos-agent` | `mvgeos-core`, `mvgeos-provider`, `mvgeos-tome`, `mvgeos-runes` |
| 6 | `mvgeos-cli` | `mvgeos-agent` and the above |
| 7 | `mvgeos` | `mvgeos-cli` — owns the `mvgeos` console script |
| 8 | `mvgeos-gui` | `mvgeos-agent` and the above |

The order is not written down anywhere; it is derived from the dependency
graph at release time:

```
python scripts/check_publish.py --list
```

That way a newly added package cannot be published out of sequence by
forgetting to edit a list.

Any order that puts a dependency before its dependents is valid, and this is
not the only one that has been proposed for the first release. This one puts
`mvgeos` seventh rather than last, because it depends only on `mvgeos-cli` and
`mvgeos-gui` is unrelated to it. PyPI does not check dependencies at upload
time, so ordering is purely about risk: landing the package that owns the
`mvgeos` name early means the headline command starts working before the
largest upload is attempted, rather than after.

## PyPI setup

Uploads authenticate with a **trusted publisher**, one per project name. Two
things must exist before an upload, and only a person can do them.

1. Create or log into the PyPI account that will own the releases, and enable
   two-factor authentication on it.
2. Register a GitHub Actions trusted publisher on **each of the eight project
   names**, with exactly these values:

   | Field | Value |
   | --- | --- |
   | Owner | `IAmNo1Special` |
   | Repository | `mvgeos` |
   | Workflow filename | `publish.yml` |
   | Environment | `pypi` |

The workflow filename and the environment are matched character for character
against the job that uploads. Rename either and PyPI rejects the exchange with a
message that does not mention either rename.

If any name is already claimed by someone else, stop and escalate. Do not
publish under a substitute name.

There is no API for this step — it is a browser form — which is why it is a
human prerequisite and not something an agent can unblock.

### The stored token this replaced

An earlier version of this workflow uploaded as `__token__` with a long-lived
`PYPI_API_TOKEN` environment secret. That credential could upload to every
project under the account, so anyone who obtained it had that capability, and
it outlived the run it existed for. Trusted publishing removes it rather than
rotating it.

If it is ever needed again, it is one step:

```
gh secret set PYPI_API_TOKEN --repo IAmNo1Special/mvgeos --env pypi
```

and the job must declare `environment: pypi` with the two `UV_PUBLISH_*` lines
restored. Prefer not to.

**This was tried and rolled back on 2026-10-09.** Trusted publishing was enabled
for six of the eight names and the other two were never registered, because the
registration needs a pypi.org browser session. The `v0.6.17` run therefore
uploaded `mvgeos-core`, `mvgeos-provider`, `mvgeos-tome`, `mvgeos-runes`,
`mvgeos-agent` and `mvgeos-cli`, and was refused on the seventh, `mvgeos`, with
`403 Invalid API Token`. Two facts made that failure expensive to diagnose and
are worth knowing before trying again. PyPI answers a *missing* registration
with the same "Invalid API Token" wording it uses for a bad credential. And
`verify-install` is `needs: publish`, so a refused upload skips the
clean-machine gate entirely and the run reads green for the part everyone is
watching.

Register **all eight** names before flipping the workflow. A `dry_run` dispatch
does not help here: it uploads nothing, so it cannot detect a missing
registration either. The only thing that detects it is an upload.

**The token was needed a second time, on 2026-10-10, for the same reason.** The
two names from the rollback above were still unregistered, so tags v0.6.20
through v0.6.23 each half-landed — refused at `mvgeos`, and the loop aborted
before it reached `mvgeos-gui` — and the v0.6.24 tag run was refused at `mvgeos`
again. A temporary `token-bridge.yml` (commit `b33a6d9`) uploaded exactly those
two distributions for 0.6.24 with `PYPI_API_TOKEN` at 14:42:52-55Z, and commit
`806d4fb` removed the workflow again. `PYPI_API_TOKEN` is no longer a secret on
the repo.

**The bridge is also how a missing publisher stays invisible.** Once
`mvgeos` 0.6.24 and `mvgeos-gui` 0.6.24 were on the index, the release looked
whole and the unregistered `mvgeos` publisher was undetectable — every later
`publish.yml` run was refused at exactly that name while the six registered ones
skipped on re-dispatch, which reads as "one broken package" rather than "two
names were never registered". That is what happened to 0.6.26.

The way to separate the two cases without reading PyPI's config (which has no
API): publish a package that *is* correctly registered **first**. An identical
OIDC token is minted once for the job and offered to all eight uploads, so a
success there proves the token, repository, workflow filename and environment
are all right, and isolates the refusal to one project's publisher. Running
`mvgeos-gui` before `mvgeos` is what identified the fault — which turned out to
be a typo in that one project's **workflow filename** field. It was refused for
the same reason across four runs while the field held a wrong value, and
succeeded on the run after the correction. With it corrected, 0.6.26 and 0.6.27
each uploaded all eight names over OIDC.

### A refused project is not a misconfigured one

PyPI answers "this OIDC token is not valid for project X" with a 403, which
means **X has no trusted publisher matching this run**. It does not mean the
workflow, the workflow filename, or the environment is wrong — if any of those
were wrong, *every* package in the loop would fail, because one token is minted
for the job and offered to all eight uploads.

So read the scope of the failure as the diagnosis:

| What failed | What it means |
| --- | --- |
| All eight | The workflow filename, owner, repository, or environment is wrong on every registration |
| Some, and it stops partway | Only those project names are unregistered; the rest are fine |

The run stops at the first refusal, so a failure at position seven tells you
nothing about position eight. That is exactly what happened on 2026-10-09:
`mvgeos` was refused at position seven and `mvgeos-gui` was never attempted.
Check every project page, not just the one named in the error.

### Where the registration goes depends on whether the project exists

The registration belongs on the project page, not the account page. A project
that does not exist yet takes a *pending* publisher, added on the account page
at `https://pypi.org/manage/account/publishing/`; a project that already exists
takes an ordinary trusted publisher, added on its own page at
`https://pypi.org/manage/project/<name>/settings/publishing/`.

All eight names already exist — the first release created them with a stored
token — so all eight belong on their project pages. A pending publisher had
been registered on the account page for all eight names, and six of them
worked; the two that 403d, `mvgeos` and `mvgeos-gui`, were the two that needed
the project page instead. **Do not assume one registration covers the set.**
Check each of the eight individually, and treat a name that answers
`403 Invalid API Token: OIDC scoped token is not valid for project 'X'` as
unregistered rather than as misconfigured.

Re-running the same tag is the recovery once registration is complete.
`UV_PUBLISH_CHECK_URL` skips what already landed, so a run that uploaded six
and refused the seventh resumes at the seventh. The tag is unchanged and PyPI
versions are immutable, so nothing needs re-releasing.

## What the workflow does

`.github/workflows/publish.yml` runs on any `v*` tag, in three jobs.

**`preflight`** builds every distribution with `uv build --no-sources` and then
runs `scripts/check_publish.py`. `--no-sources` is the important flag:
`[tool.uv.sources]` is a uv-only table that never reaches a wheel, so a build
that relies on the workspace resolving its own siblings is not evidence that
the wheel installs elsewhere. The check then asserts two things:

- every package carries the tag's version, so a release cannot ship a mixed set;
- every first-party `Requires-Dist` in every built wheel carries a version
  constraint, because a bare name resolves against whatever version of the
  sibling happens to be newest on PyPI.

**`publish`** uploads in the derived order, one distribution per step, with an
OIDC token minted for the job. Nothing in that job reads a credential, and the
job fails immediately if no token was minted rather than uploading nothing and
reporting success.

`UV_PUBLISH_CHECK_URL=https://pypi.org/simple` makes a re-run after a partial
failure skip whatever already landed. PyPI versions are immutable, so without
this a retry after uploading four of eight packages would fail on the first.

**That skip only works if the rebuild is byte-identical**, which is what the uv
pin in every workflow is for. See "A re-dispatch that will not resume" below.

### A re-dispatch that will not resume

`--check-url` skips a distribution that is already on the index, but it
compares **hashes**, not names. So a re-dispatch only resumes if the rebuild
produces the same bytes as the upload did. It does not, by default, and the
reason is one line in every workflow:

```yaml
- uses: astral-sh/setup-uv@v7     # no `version:` — installs newest uv
```

uv writes itself into what it builds. Every wheel carries

```
Generator: uv 0.12.24
```

in its `WHEEL` metadata, that line is hashed into `RECORD`, and so the file's
sha256 changes when uv does. Three builders, one commit, one package:

| Built by | `mvgeos_core-0.6.17-py3-none-any.whl` |
| --- | --- |
| uv 0.12.10 | `8b1999f4…` |
| uv 0.12.24 | `9c156697…` |
| uv 0.13.0 | `39207b40…` |
| what PyPI holds | `9c156697…` |

uv 0.13.0 shipped 2026-10-09 at 19:49. A re-dispatch run at 23:28 that morning
installed it, rebuilt the same commit, and could not skip anything:

```
error: Local file and index file do not match for
`mvgeos_core-0.6.17-py3-none-any.whl`. Local: sha256=39207b40…, Remote: sha256=9c156697…
```

Each of those three hashes was reproduced from a clean worktree at the release
commit, so the difference is the builder and nothing else. All twelve files of
the v0.6.17 release — six wheels and six sdists — rebuild byte-for-byte under
uv 0.12.24, which is the proof that the wheel was always reproducible and only
the builder had moved.

That failure names a hash mismatch and nothing else, so read cold it looks like
a corrupted artifact or a tampered index, and both are wrong conclusions.
**If a re-dispatch stops on a package that is already on PyPI at the right
version, compare the two hashes before anything else.** If the local one changes
when nothing in the repository did, the builder moved and the pin is the cause.

Every `setup-uv` step therefore pins `version: "0.12.24"`.
`scripts/tests/uv_pin.py` fails the `release-tooling` job if any of them stops
pinning, or drifts to a version that is not the one the published artifacts were
built with. Bumping the pin is a deliberate act, and it means every artifact
built before it is no longer reproducible from its tag.

**To recover a release stopped this way**, pin uv to the version that built what
is already on PyPI — not to the newest one — then re-dispatch the same tag. The
six that landed are skipped by hash and the run resumes at the first one that
did not. Note that this is independent of how the upload authenticates: a
stored token and an OIDC token hit the same `--check-url` comparison.

### When PyPI refuses a project as new

PyPI caps how many *new projects* one account may create in a window, and
answers `429 Too many new projects created` at the cap. The cap is on project
creation, not on uploads to existing projects, so a refusal means "come back
later", not "this distribution is broken".

That is why the first release is the one most likely to hit it: eight names,
eight creations. When it happens, the `publish` job **stops on the first
refusal**. It does not retry, and that is deliberate. PyPI advertises the
policy as `"project.create.user";q=4;w=86400` — four creations counted in the
trailing 24 hours. A retry inside a run cannot outrun that window, so it can
only spend runner minutes; a refused request may itself count against the
window it is waiting for. The run fails red, and the step summary prints when
the quota next has room. Read that time, and re-dispatch the same tag after
it. Re-running is safe and is the normal path, not an exception.

**The quota frees as a trailing window, not one slot per day.** A creation
leaves the window when it turns 24 hours old, so the time a slot frees is the
upload time of the *oldest* project in the window, plus 24 hours. Creations
made seconds apart therefore age out seconds apart, and the whole quota returns
at once. The 0.6.6 first release created its first four names in an 8-second
span, so the full quota of four came back in one go rather than over four days.
Do not plan around a daily drip.

To read the current state and get that time:

```
python scripts/pypi_new_project_window.py
```

The publish job appends the same report to its step summary when it is
refused. It needs no credentials: it reads the public index.

If more names remain than the window holds, the report says so, and it takes
more than one window. That is the only case where asking PyPI for a lift is
worth the wait: open a request at `https://github.com/pypi/support/issues/new`,
choosing "Limit Request", and note that this is a coordinated first release of
eight distributions in dependency order, how many already landed, and that the
account is not being recreated. Expect it to take days to be answered.

### A release is not installable the moment the upload returns

`publish` returning green means PyPI accepted the bytes. It does not mean the
index has them. Measured on `v0.6.19`: the upload returned at 00:03:12Z, the
run's `verify-install` job started at 00:03:14Z, and its first command failed
with

```
[1/5] uvx mvgeos --help -> exit 1 in 0.14s
error: No solution found when resolving tool dependencies
  cause: Because there is no version of mvgeos==0.6.19 and you require
         mvgeos==0.6.19, we can conclude that your requirements are unsatisfiable.
```

The release had landed. Nothing had propagated it yet.

`uvx` resolves against the *simple index*, `https://pypi.org/simple/<name>/`,
and PyPI serves that from cache -- measured on `https://pypi.org/simple/mvgeos/`
as `cache-control: max-age=600, public`. Warehouse caches it under the
surrogate key `project/<name>` and purges that key when a file is uploaded
(`warehouse/api/simple.py`, `warehouse/cache/origin/__init__.py`), so the purge
is what makes a new release visible there, and it propagates over seconds. Both
gate jobs therefore wait, in `scripts/wait_for_pypi.py`, before they assert
anything.

**Do not fix that wait by polling the version-specific URL.** It is the obvious
answer and it is wrong in both directions, both measured:

- `https://pypi.org/pypi/<name>/<version>/json` answers **200 too early**. A URL
  nobody has requested is served from origin, so it answers 200 the moment the
  release commits -- while the purge above has not propagated. Waiting for it is
  waiting for something that has already happened.
- The same URL answers **404 too long** afterwards. A 404 for a version that
  does not exist yet is produced before the view runs, so it carries no cache
  key and the upload's purge cannot invalidate it. `published_files.py` asks
  that exact URL seconds before the upload in the publish job, seeding the cache
  with a 404 that outlives the release. Measured on
  `https://pypi.org/pypi/mvgeos/0.6.99.json`: 404 from the Fastly cache,
  `x-cache: MISS, HIT`, on every request across a six-minute window, with no
  `cache-control` on the response to bound it.

So the wait reads the **project document**, `https://pypi.org/pypi/<name>/json`,
which lists every release under `releases` and is cached under the key the
upload purges. It is the one PyPI document whose freshness is the index's
freshness. `verify` asks it for all eight names, so a half-landed release is
named after one bounded wait rather than after eight.

`verify-install` goes further and waits for the command a stranger types,
`uvx --from mvgeos==<version> mvgeos --help`, until it exits zero. That
resolves the whole runtime closure, and it is the only claim that settles the
question: a URL can be right about a release that is not yet installable, and
`uvx` cannot be wrong about it.

The wait is 120 seconds, polling every 5. A name still absent when it expires
is a release that did not land, not a slow index, and the run says which one it
saw.

**`verify`** checks out nothing at all and runs `uvx mvgeos --help`. Because
there is no clone and no `uv sync`, uvx resolves the command from PyPI exactly
as a stranger's machine would. That job is the acceptance test; `pip download`
is not.

Its first step is the wait above, asked of every name in the publish order, so
a half-landed release is reported as one. The version comes from `preflight`'s
job output, which is also why `preflight` is in this job's `needs`: a job that
does not need another job reads its outputs as the empty string, and the empty
version turns the check into `/pypi/<name>//json`, which is 200 for every
package that has ever existed. That is exactly what ran on the `v0.6.25` tag --
eight lines of `mvgeos-core : HTTP 200` with the version blank, over a run
whose publish had been refused.

**`verify-install`** runs `scripts/verify_install.py`, which is the half
`verify` structurally cannot reach. `--help` returns before a Realm is
contacted, so a release whose credential never loads, whose Realm Rune is
missing, or whose Tome never reaches disk is green and still unusable. The
script builds a scratch `HOME` that starts empty — no credential, no config —
installs `opencode-realm` and `coding_mvge` into it, runs one real task, and
fails unless the artifact is byte-correct and a session Tome naming the model
lands under `.agents/sessions/`. It reports cold-to-first-token on the way.

It waits first, and it waits last in the job on purpose: the bubblewrap install
above it is fifteen seconds of propagation the run gets for free, so the wait
usually starts after the release is already installable. That is a saving, not
the gate. The gate is `--resolve`, which polls `uvx --from mvgeos==<version>
mvgeos --help` until it exits zero, because that command resolves the whole
runtime closure and is the only thing that proves the release is installable
rather than merely published.

It also asserts the resolved tree is one coherent release: every first-party
distribution present is at the released version, and none of the seven that
make up the runtime closure is missing. Seven, not eight — `mvgeos-gui` is
published in lockstep and installed by nothing, so requiring it would fail on
every correct release. That `mvgeos-gui` exists on the index at the same
version is the publish workflow's job, which asks PyPI for all eight by HTTP.

The check exists because of a measured failure — on 2026-10-09 a release
uploaded six of eight and was refused on the seventh, and because
`mvgeos 0.6.16` declares `mvgeos-cli>=0.6.16`, the mixed tree that resulted
satisfied every constraint it was given. `uvx mvgeos --help` exited zero on it
and a real task completed. Run against that tree the gate now says:

```
[1/5] uvx mvgeos --help -> exit 0 in 5.43s
[2/5] resolved tree -> mvgeos 0.6.16, mvgeos-agent 0.6.17, mvgeos-cli 0.6.17, mvgeos-core 0.6.17, mvgeos-provider 0.6.17, mvgeos-runes 0.6.17, mvgeos-tome 0.6.17
verify_install failed: not installed by this release: mvgeos-gui
verify_install failed: a mixed-version tree resolved at released 0.6.16: mvgeos-agent 0.6.17, mvgeos-cli 0.6.17, mvgeos-core 0.6.17, mvgeos-provider 0.6.17, mvgeos-runes 0.6.17, mvgeos-tome 0.6.17
```

Step 1 passing on the line above is the point: it is what the gate did before.

Unlike `verify`, this job checks the repository out — only to get the script.
Every mvgeos the run touches still comes from PyPI through uvx, so the thing
under test is the published distribution and never the workspace.

The script is stdlib-only so that the thing standing between a release and this
check cannot itself fail to install, and it needs **no secret at all**: the
default Realm, `opencode`, serves its free tier keyless. That is worth more
than the money it saves. A gate requiring a credential cannot run on a fork,
and a fork is exactly where a stranger's first contact with this repository
happens.

An earlier revision failed on purpose when the `OPENROUTER_API_KEY` repository
secret was absent, on the reasoning that a skipped gate is indistinguishable
from a passing one. That reasoning was right and the conclusion was wrong: the
gate was not skipping, it was refusing to run at all over a credential it no
longer spends. The secret is still passed through for an optional
`--provider openrouter` run, and the harness exits 2 naming the variable if it
is asked for a billing Realm and finds none.

Where `bwrap` is available — the job installs it, and most developer machines
have it — the script re-runs itself inside a sandbox and says so on its first
line. That is a real narrowing of what the run can see: a mount, process, and
UTS namespace in which the checkout and the operator's own `HOME` do not exist,
which is the difference between "a stranger's HOME" and "a stranger's machine".
The network namespace is deliberately *not* unshared, because resolving mvgeos
from PyPI and reaching a Realm are the two things under test. The script checks
the isolation from the inside and fails if a host path is still reachable, so a
sandbox that leaks is a red gate rather than a quiet one. Pass `--no-sandbox`
to run without it.

The gate follows the engine's own defaults rather than naming a model of its
own: `opencode/space-bunny-free` on the `opencode` Realm, billed at zero. That
is the Realm and model a new Summoner reaches first, so it is the one worth
testing. `--max-tokens` is still capped to keep the turn short. Override any of
it when a specific failure needs a different target:

```bash
python scripts/verify_install.py \
  --version 0.6.14 --provider openrouter --model openai/gpt-4o-mini --max-tokens 8000
```

That invocation needs `OPENROUTER_API_KEY` in the environment, and the harness
will say so and exit 2 if it is missing.

## The normal path

The `bump` job in `ci.yml` creates and pushes the tag after a green `main`, then
dispatches `Release` and `Publish` itself. Nothing has to be done by hand after a
merge to `main`.

The dispatch is explicit because the tag push alone is not a trigger. That push
uses `GITHUB_TOKEN`, and GitHub's loop guard stops a `GITHUB_TOKEN` push from
starting any workflow, so the `v*` tag trigger in `publish.yml` never fires from
the `bump` job. The `bump` job calls both workflows instead:

```
gh workflow run Release --ref main -f version=v0.6.5 -f dry_run=false
gh workflow run Publish --ref v0.6.5 -f tag=v0.6.5 -f dry_run=false
```

`Publish` is dispatched against the tag rather than `main` on purpose. A tag push
gives the workflow `GITHUB_REF=refs/tags/v0.6.5`, so `preflight` builds exactly
the tagged commit. Dispatching against `main` would build whatever `main` holds
when the runner starts, which can be a commit that landed after the tag, and
those wheels would be published and permanently labelled `v0.6.5`.

**The cost of that choice: `--ref <tag>` runs the workflow file *as the tag has
it*, not as `main` has it.** Any fix merged to `main` after a tag is cut does not
exist for a re-dispatch of that tag. Measured on v0.6.17, tagged before the uv
pin existed:

```
gh workflow run publish.yml --ref v0.6.17 -f tag=v0.6.17 -f dry_run=false
   -> the pin was absent, so uv 0.13.0 was installed, and the run rebuilt
      mvgeos-core at 39207b40... and failed against PyPI's 9c156697...

gh workflow run publish.yml -f tag=v0.6.17 -f dry_run=false
   -> runs from main, pin applies, uv 0.12.24, and every package is
      recognised as already published and skipped
```

So a fix to the release tooling cannot rescue the tag it was cut before. Either
cut a new tag, or dispatch from `main` — but only when the *source* at the tag is
unchanged and only the tooling moved. Check first:

```
git diff <tag>..main -- '*/pyproject.toml' pyproject.toml
```

Empty means no package version moved, so the wheels `main` builds are the
released ones and dispatching from `main` is safe. If anything moved, they are
not, and only a new tag is correct.

## When a publish run needs redoing by hand

Re-run the same tag. `UV_PUBLISH_CHECK_URL` skips every package that already
landed, so the run resumes rather than restarting:

```
gh workflow run publish.yml --ref v0.6.5 -f tag=v0.6.5 -f dry_run=true
gh workflow run publish.yml --ref v0.6.5 -f tag=v0.6.5 -f dry_run=false
```

Dry run first. It builds and validates everything and uploads nothing.

The most common reason for a red `Publish` run is PyPI's cap on new project
creations, not a packaging fault. See "When PyPI refuses a project as new" above.

A hand-pushed tag also fires `publish.yml` through its own `v*` tag trigger,
because a push made with a person's token is not covered by the loop guard. That
path builds the tag correctly too, but it is not needed and it skips the gates
`bump` waits on.

## After a release

The `mvgeos-gui` upload is last because it is the largest and the least likely
to be the reason someone ran `uvx mvgeos`. If one upload in the middle fails,
the run stops there. Re-run the same tag: `--check-url` skips the packages
that already landed and continues with the rest.

## When the bump job cannot land

The `bump` job commits, rebases onto whatever `main` is at that moment, tags,
and pushes. It holds a concurrency group, so a second push to `main` queues
behind a bump already in flight rather than racing it.

If a hand push lands on `main` in the middle of a bump and the rebase
conflicts, the job aborts the rebase and exits green with a warning instead of
red. Nothing is tagged and nothing is released, which is deliberate: the next
push to `main` re-runs the whole bump against content that already contains the
conflict resolution. Read the warning on the run rather than assuming a release
happened.
