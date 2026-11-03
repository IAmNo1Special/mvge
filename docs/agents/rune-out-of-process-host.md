# Rune out-of-process host, Phase 4 v1

Third-party spell, hook, command, and skill rune code runs outside
the engine process, one host process per hosted rune. Realm,
spell-gateway, and approval runes remain in-process in v1,
protected by Phase 3 pinning. Realm hosting is Phase 4b, a
separate decision, and is not part of this document.

## Honest claim

After Phase 4: "Third-party spell/hook/command rune code runs
outside the engine process, with a declared, audited capability
surface. Realm, gateway, and approval runes remain in-process
and pinned."

Phase 4 is not a credential or filesystem boundary. A hostile
host rune can still read files its OS user can read and reach
the network. Do not upgrade this language: pinned means
unmodified, not benign, and hosted means out-of-process, not
confined. Confinement arrives with Phase 5a (per-spell effects)
and Phase 5b (whole-process wrapping, opt-in deployment).

## In-process-only tier (explicit)

| Rune class | Why it stays in-process in v1 |
|---|---|
| Realm runes (`realm` capability or `RealmProvider` type) | Realm factories receive the provider key from the engine; the key and the streaming realm protocol have no wire in v1 (Phase 4b). |
| Spell-gateway runes (`spell_gateway` manifest flag) | The gateway narrows the model's spell view and reveals spells live; that narrowing is engine-owned in v1. |
| approval-rune (by name, and any `Spell Gate` type) | Approval channels and spell-gate decisions never cross the wire. |
| Codec runes (`Codec` type or `session_codecs`) | A session codec is a live object with no wire channel in v1. |
| Spell-modifier runes (`Spell Modifier` type) | Live spell-call mutation has no wire channel in v1. |
| Any rune declaring `hosting: "in-process"` in its manifest | Explicit operator/rune declaration. |
| Any rune with an unrecognized `hosting` value | Fail-closed: unknown values are normalized to in-process by the manifest parser. |

Classification lives in `mvge.runes.host_classification.classify_manifest`
and is the single place that decides. `RuneRunner.host_rune`
raises `HostInProcessOnlyError` for the tier before any process
is started, and the normal in-process load path is untouched.

## Wire protocol v1

- Transport: newline-delimited JSON over host stdio. Stdout is
  the wire; host diagnostics go to stderr (captured to a log
  file by the engine).
- Envelope: `{"protocol": "mvge-rune-host/1", "kind": ...}`.
  Requests carry `id`/`type`/`payload` and expect a response;
  events (hello, ready, abort, spell updates, audit) expect none.
- Versioning: the host's first message must be a `hello` event
  with `protocol_version` 1. Any other version is rejected and
  the host is killed. There is no negotiation in v1.
- Limits: one message is at most 1 MiB (`MAX_MESSAGE_BYTES`).
  Oversize, malformed, or wrong-envelope messages are fatal:
  pending calls fail and the host is killed.
- Timeout and restart: a call that does not answer within its
  timeout kills the host. Restart is a fresh process: the host
  re-runs its factory and re-registers, and the engine clears
  the old registrations first (`HostSession.restart`).

## What crosses, and what does not

Cross: spell registration metadata, spell params and results
(plain JSON), hook data (plain JSON), command/shortcut args and
results, skill metadata, session-name and message calls, abort
as a message, streaming updates as messages, audit events.

Never cross: provider keys and engine-held credentials, realm
factories, approval channels, spell-gate decisions, live
backends, live sandbox objects, live spell objects.

Two fail-closed rules at the spell boundary:

- A cast carrying a live `SpellContext.backend` is refused
  with `HostInProcessOnlyError` before anything is sent.
- A cast whose params or context `state` is not
  JSON-serializable is refused the same way. A spell that
  needs a live backend or a non-serializable context object is
  in-process-only in v1, and must be classified as such.

Hook payloads follow the same rule: typed dataclass payloads
are converted with `asdict`; a payload that is not
JSON-serializable fails the invocation closed. Hosted hooks
receive plain JSON dicts, not typed dataclass objects: a rune
written for hosting must accept dicts.

## Unsupported facade methods (one table, parity gate)

`mvge.runes.host_classification.HOST_UNSUPPORTED_METHODS` is
the single table. The hosted runtime (`HostRuneAPI`) raises
`HostUnsupportedError` for every method in it, and the static
audit (`source_audit`, hosted mode) flags hosted source that
uses any of them. The parity test
(`tests/unit/runes/rune_host.py::TestHostParity`) asserts the
two verdicts agree for every method in the table and is a
release gate: do not duplicate the table anywhere else.

Unsupported in v1: `register_realm_factory`,
`register_provider`, `get_registered_providers`,
`get_registered_realm_factories`, `request_approval`,
`register_spell_gate`, `stream_model_text`,
`approval_presenter_source`, `sandbox`, `on_event`,
`emit_event`. Supported: spell, hook, command, shortcut, and
skill registrations, spell-list queries, session-name and
message calls, and audit events (see `HOST_SUPPORTED_METHODS`).

## Host lifecycle (POSIX and Windows)

The host is started with `sys.executable -m
mvge.runes.host_server --rune-dir <dir>`: no shell, no
POSIX-only call in the shipped path. It is stopped with a
`shutdown` request, then `terminate()`, then `kill()` — the
`subprocess` methods that exist on both POSIX and Windows.
The spawn environment is scrubbed of credential-shaped names
(`scrub_env`, by name only — values are never inspected or
logged), and the host scrubs its own environment again at
startup. `PYTHONUNBUFFERED` is set so host stderr is timely.

Windows coverage gap, stated plainly: the suite runs on the CI
platforms, but no Windows-only leg (job objects, console
control) is exercised, and Windows process teardown timing may
differ from POSIX. A Windows-specific teardown failure must be
reported as a platform gap, not worked around by weakening the
timeout/kill path on POSIX.

## Debugging a hosted rune

1. Read the host stderr log: `HostSession.stderr_log_path`
   (default a per-rune file under the system temp dir).
   Factory errors, unhandled spell exceptions, and host
   diagnostics land there. Never print to host stdout: stdout
   is the wire and anything else corrupts the protocol.
2. Run the host by hand for one rune:
   `python -m mvge.runes.host_server --rune-dir <rune-dir>`
   and speak NDJSON on stdin (`hello`, `init`,
   `spell_execute`) to reproduce a failure without the engine.
3. Attach a debugger to the host by pid
   (`HostSession.pid`): the host is a plain Python process,
   one per rune, so a breakpoint in rune code stops only that
   rune, never the engine session.

## Lifecycle gap (known, v1)

Hosts are not stopped automatically when a runner is discarded.
Call `HostSession.stop()` (or use the session as a context
manager) or `RuneRunner.shutdown_hosted_runes()`. The runner
has no general teardown hook that hosted sessions can safely
ride, so this is an explicit caller responsibility until a
later phase wires host shutdown into the session lifecycle.
