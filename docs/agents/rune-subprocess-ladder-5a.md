# Phase 5a: fail-closed subprocess ladder

Status: implemented. This is a subprocess ladder, not whole-process
wrapping: it confines the file effects of subprocesses a spell
launches. Confining rune code itself is Phase 5b, an opt-in
deployment posture, and nothing here claims it.

## Policy

A spell's ladder policy is seeded from two declarations, and
nothing else:

- `SpellDefinition.read_only`: a read-only spell resolves to
  no write roots, regardless of any other declaration.
- Declared write roots: spell-level `SpellDefinition.write_roots`
  win over rune-level manifest `filesystem_write_roots` when
  the spell declares any. Roots are relative to the spell
  workspace root; `"."` means the workspace itself.

The filesystem capability (deferred from Phase 2B) is minted
in this phase because its enforcing gate now exists: the
ladder in `mvge.runes.subprocess_policy`. It governs no
`RuneAPI` method, so its entry in `CAPABILITY_GATES` is an
empty method tuple by design. Declaring the capability without
roots grants nothing writable. Network capabilities are still
not minted: no network gate exists.

Re-consent for write roots (Phase 5a fix): the stored
declaration tracks `filesystem_write_roots` alongside
capabilities, and the declaration hash covers both. Widening
a rune's declared roots triggers `declaration_changed` and
the same re-consent path as a capability change, and the
store's `granted_write_roots` keeps only the roots consented
at consent time until re-consent. Narrowing roots needs no
new consent, and unchanged roots never re-trigger. Live-spell
ladder wiring will consume `granted_write_roots` when it
lands; until then the store-level grant is the mechanism
the re-consent path enforces.

## Backends and enforcement reporting

Every run reports `enforcement` explicitly on its result:
`full` means the backend enforces all policy dimensions it
claims (reads, writes, and network as specified); `partial`
means writes are confined but reads and/or network are not.
Consult the reported value. Do not assume full confinement.

| Backend | Platform | Enforcement | Mechanism and gaps |
| --- | --- | --- | --- |
| `bwrap` | Linux | full | Bubblewrap mount namespace. The workspace is bound read-only and only declared roots are bound writable, so unbound paths are invisible to the child. Network is unshared unless the policy allows it. `/tmp` is a tmpfs scratch dir, not a policy root, mounted before the workspace bind so a workspace under `/tmp` stays visible. `/proc` is bound read-only from the host (a fresh proc mount is not portable to restricted containers). |
| `seatbelt` | macOS | partial | `sandbox-exec` profile denying file writes outside the declared roots. Reads and network are not denied. The profile relies on Seatbelt's more-specific-rule governing a subtree; macOS CI is the live proof point for profile correctness. |
| `windows-acl` | Windows | partial | Temporary deny-write ACEs (`icacls`) on workspace entries disjoint from every declared root, restored after the child exits. Reads and sockets are not intersected. Writes outside the workspace are not intersected. A top-level entry containing a declared root beneath it is left unprotected rather than breaking that root. A failed restore raises loudly instead of leaving deny ACEs silently in place. |

Selection is first-available in the order above. A named
backend that is unavailable, or no available backend at all,
refuses with `SANDBOX_UNAVAILABLE` and the child never
executes. There is no unconfined fallback path.

## Audit/runtime parity

The audit (`mvge.runes.source_audit`) and the runtime policy
both import `filesystem_write_admitted` and
`resolve_effective_roots` from `mvge.runes.subprocess_policy`.
Neither invents its own mapping. The parity test
(`tests/unit/runes/subprocess_policy.py`) runs every fixture
through both verdicts and is a release gate.

Scope of the gate, stated plainly:

- The admission filesystem check fires only for runes that
  opted into the filesystem capability. Legacy manifests and
  runes that never declared it keep their pre-5a admission
  posture. The ladder itself stays fail-closed for their
  sandboxed runs (no declared roots means no writable binds).
- The audit's write-call list is a heuristic review aid. It
  never claims to block in-process escape: dynamically built
  calls, `exec`, and `getattr` bypass it.
- Path-level enforcement belongs to the backend at runtime.
  The audit checks the declaration, not the path.

## Migrated writing spells

- `codeact-rune` `execute_python`: declares write roots
  `["."]` (code it executes may write workspace files, which
  is its purpose) and the manifest declares the filesystem
  capability with the same roots.
- `checkpoint-bridge` `checkpoint_restore_file`: declares
  write roots `["."]` (it restores workspace files) and the
  manifest declares the filesystem capability with the same
  roots. `checkpoint_diff` is already `read_only` and declares
  no roots.
- `mvge setup` (CLI): not a spell and not model-invoked. It
  is operator-invoked installation of system dependencies,
  which intentionally writes outside any workspace. The
  ladder does not gate operator CLI invocations, and no
  policy is declared for it. That exclusion is deliberate,
  not an oversight: gating it would break its purpose.

Runes whose writes target `.agents` layers rather than the
workspace (policy files, skill stores, knowledge dirs) are
not migrated in this phase: the roots model is
workspace-relative and cannot express those targets. Their
admission posture is unchanged, and expressing
`.agents`-layer roots is follow-up work, not a silent
extension of this model.

## Explicitly out of scope: one-time escalation

The review's 5a acceptance also names an escalation test
(no justification gives no escalation; an approved one-time
escalation widens exactly one call, then standing policy
resumes). That half is not built here, deliberately: a
wider-than-declared run today requires a new declared policy,
so the no-justification half holds (there is no escalation
path to abuse), but an approved one-time widening needs an
approval model the review does not specify (who approves,
what a justification is, how it binds to one call). A
token-less `escalate()` would be theater, since any caller
could already construct a wider policy directly. The
approved-escalation path is follow-up work pending that
approval design, not a silent omission.

## Honest claim for this stage

After 5a: spell/subprocess effects are confined per-spell
with a fail-closed policy. Rune code itself is not yet
confined. A pinned rune is still unmodified, not benign.
