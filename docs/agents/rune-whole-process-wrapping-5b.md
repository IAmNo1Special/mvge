# Phase 5b: whole-process wrapping (opt-in deployment posture)

Status: implemented. Wrapping runs a Phase 4 host process inside
an OS/container boundary. It is NOT the default and NOT a
release gate: unwrapped is the default, and a wrapped deployment
is proven per deployment (see "Deployment proof" below), not by
the unit/integration suite alone.

The honest claim, do not upgrade it: **on a wrapped deployment
only, untrusted runes run inside an OS/container boundary with
a stated, tested policy.** Unwrapped, the Phase 4 claim stands:
rune code runs outside the engine process, but a hostile host
rune can still read files its OS user can read and reach the
network. Pinned means unmodified, not benign, wrapped or not.

## Opt-in

Wrapping requires an explicit operator choice, in code:

```python
from mvge.runes.host import HostSession
from mvge.runes.wrapping import WrappingPolicy

policy = WrappingPolicy(
    enabled=True,
    backend="bwrap",  # or "docker"
    workspace_root=workspace,
    rune_dir=rune_dir,
)
session = HostSession(manifest, rune_dir, runner, wrapping_policy=policy)
```

Or derive it from a Phase 5a ladder policy, so write roots and
the network flag cannot diverge between the layers:

```python
from mvge.runes.wrapping import WrappingPolicy

policy = WrappingPolicy.from_subprocess_policy(ladder, backend="bwrap")
```

There is no configuration file, environment variable, or
manifest field that enables wrapping: a manifest cannot opt
itself into (or out of) wrapping, and an operator who never
constructs an enabled policy gets exactly the Phase 4 default.
An enabled policy with a missing, unknown, or unavailable
backend refuses with `WRAPPING_UNAVAILABLE` before any host
process starts. It never falls back to unwrapped.

## What is mounted and exposed, per backend

Both backends mount at identity paths, so paths carried on the
Phase 4 wire still resolve inside the wrap.

| Mount | Mode | Notes |
| --- | --- | --- |
| Workspace | read-only, except declared write roots | The operator's working area. A workspace that IS home (or an ancestor of home) is refused at construction. |
| Declared write roots | writable | Exactly the Phase 5a ladder roots when derived from a ladder policy. No other writable mounts exist. |
| Rune directory | read-only, bound last | Rune code stays read-only even when it sits under a declared write root. |
| Interpreter prefix(es) + engine source tree | read-only | Backend-internal runtime binds: the venv prefix, every prefix along the interpreter symlink chain, and the editable engine `src/`. Without the chain, a uv-managed interpreter fails inside the namespace with a missing executable. |
| System directories (`/usr`, `/lib`, `/bin`, `/sbin`, `/etc`) | read-only | bwrap only. |
| `/proc` | read-only | bwrap only, bound from the host (a fresh proc mount is not portable to restricted containers). |
| `/tmp` | tmpfs scratch | bwrap only; mounted before the workspace bind so a workspace under `/tmp` stays visible. Docker uses `--tmpfs /tmp` with a read-only container filesystem. |

Nothing else is mounted: no home directory, no `~/.agents`
tree, no credential directories. A mount of home, an ancestor
of home, `~/.agents`, `~/.ssh`, `~/.aws`, `~/.gnupg`, or
`~/.docker` is refused at validation time.

Environment: the host environment is the Phase 4 scrubbed
environment, and wrapping asserts after scrubbing that no
credential-shaped name survived. Docker adds a second
guarantee by construction: `docker run` does not inherit host
variables, and the Docker backend passes no `-e`/`--env`
flags. Credentials reach a wrapped host only over the Phase 4
wire (as plain-data calls), never as environment or files.
Credential-named files inside a mount (`.env`, `*.pem`,
`*.key`, `credentials.json`, `.netrc`) are reported by
`credential_files_in_mounts` and fail the deployment proof:
the engine cannot know whether a workspace `.env` is meant
for the rune, so the operator must remove it before claiming
a wrapped deployment is credential-free.

## Per-platform enforcement matrix

| Platform / backend | Enforcement | Mechanism and gaps |
| --- | --- | --- |
| Linux / `bwrap` | full | Bubblewrap mount namespace. Unbound paths are invisible (reads fail), declared roots are the only writable mounts, network is unshared unless the policy allows it. |
| Linux / `docker` | full | Docker with a read-only filesystem, tmpfs `/tmp`, identity mounts, `--network none` unless the policy allows it. The image must provide `python3` and the `mvge` package; a host interpreter path is mapped to `python3` inside the container. |
| macOS / `docker` | full | Same Docker backend, under a Docker daemon on macOS. Requires a daemon; without one, wrapping refuses. |
| macOS / (no backend) | unavailable | Seatbelt is a Phase 5a write policy, not whole-process wrapping, and is deliberately not a wrapping backend. Without Docker, wrapping refuses with `WRAPPING_UNAVAILABLE`. |
| Windows / `docker` (WSL2, Linux containers) | full | Same Docker backend, under WSL2. Docker availability on the target machine, including Windows/WSL2, is a deployment prerequisite. |
| Windows / (no Docker) | write-confined only, read/network documented gap | No native Windows wrapping backend in v1. The honest posture is the Phase 5a ACL ladder alone: temporary deny-write ACEs on workspace entries, with reads, sockets, and writes outside the workspace not intersected. Do not claim full confinement on Windows without Docker. |
| Windows containers | untested, not claimed | The Docker backend is not tested against Windows containers; do not present it as proven there. |

Network policy, all platforms: all-or-none in v1. Denied
means `bwrap --unshare-net` or `docker --network none`.
Allowed means the host network (bwrap) or the default bridge
network (Docker). There is NO per-destination egress
allowlist in v1, and `WrappingPolicy.network_description`
says so. A deployment that needs a destination allowlist is
not satisfied by this module.

## Composition with earlier phases

- Phase 4: wrapping wraps the host command only. The host
  still speaks the Phase 4 wire, the engine-side capability
  gate in `HostSession` still applies to every wire
  registration (a wrapped host speaking the wire directly
  gains nothing), and `classify_manifest` still refuses the
  in-process-only tier before any process starts. Validation
  here refuses an in-process-only manifest outright, so
  wrapping cannot be used to claim confinement for a rune the
  host itself will not host. Realm, gateway, approval, codec,
  and spell-modifier runes stay in-process and pinned.
- Phase 5a: `WrappingPolicy.from_subprocess_policy` derives
  wrapping from a ladder policy. The outer boundary is
  stricter than the ladder (unbound paths are invisible, not
  merely unwritable). A ladder run nested inside a wrapped
  host is best-effort: nested bwrap may fail inside a
  namespace, and that failure is fail-closed via
  `SANDBOX_UNAVAILABLE`, not claimed as proven.
- Phase 3: wrapping does not replace pinning. Pin verification
  happens at load, before a host is started, and a wrapped
  host still runs the pinned tree.
- Phase 2B: capabilities are unchanged. Wrapping adds no
  capability and grants none; the wire gate still enforces
  the consented set.

## Deployment proof

A deployment is claimed confined only after the proof script
passes against that deployment's own workspace, backend, and
mounts. The script runs probes inside the wrapped argv and
fails loudly on any confinement failure:

```sh
uv run python scripts/verify_wrapped_deployment.py \
  --workspace /path/to/workspace \
  --backend bwrap \
  --write-root data
```

What it checks, and what output proves confinement:

- `PASS secret-read-denied`: a probe inside the wrap cannot
  read a secret file placed outside the mounts. Failure means
  an unbound path leaked into the namespace.
- `PASS outside-write-denied`: a probe cannot write outside
  the mounts. Failure means a writable mount escapes the
  workspace.
- `PASS workspace-root-write-denied` (when the workspace root
  is not a declared write root): a probe cannot write to the
  workspace root. Failure means the workspace was mounted
  writable without a declaration.
- `PASS declared-root-write`: a probe CAN write to each
  declared write root, and the file is visible from the host
  afterwards. Failure means the deployment is broken (too
  tight), not merely safe.
- `PASS credential-env-absent`: no credential-shaped
  environment variable is visible inside the wrap. The probe
  prints the environment keys it sees; the script asserts
  none matches the Phase 4 credential-name rule.
- `PASS network-denied` (when network is denied): a probe
  cannot open a socket. Failure means the network namespace
  or `--network none` did not apply.
- `PASS no-credential-files-in-mounts`: no credential-named
  file exists under any mount. Failure lists the files; the
  operator removes them and re-runs. (Pass
  `--allow-workspace-credential-files` only to record an
  explicit operator decision that a workspace credential file
  is intended for the rune; the proof then prints a WARNING
  instead of a PASS for this check.)

The script exits non-zero on any FAIL, and prints the exact
wrapped argv it used, so the operator can re-run any probe
by hand. A wrapped deployment whose proof has not been run
(or has failed) must not be described as confined.

## Explicitly still deferred

The approved one-time escalation deferred from Phase 5a (F3)
is NOT built here. Widening a wrapped deployment today means
constructing a new, wider policy -- exactly one explicit
operator act. An approved one-time widening that binds to a
single call still needs the approval model the review does
not specify (who approves, what a justification is, how it
binds to one call), and a token-less `escalate()` would be
theater, since any caller could already construct a wider
policy directly. That path stays follow-up work, not a silent
omission.

## Honest claim for this stage

After 5b, on a wrapped deployment only: untrusted runes run
inside an OS/container boundary with a stated, tested policy.
Unwrapped remains the default, and unwrapped runes carry the
Phase 4 claim only. Windows without Docker remains
write-confined with a read/network gap. Network in v1 is
all-or-none, with no destination allowlist.
