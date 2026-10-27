# ADR 0014: One Resolved Layer Stack for the `.agents` Protocol

## Status

Accepted (amended 2026-10-10: decision 4, see ADR 0017)

> **Amended 2026-10-10.** Decision 4 listed the standard layer names and named
> `PROJECT_RUNE_PATH` as the only project-layer constant, because at the time it
> was the only one there was. Decision 3 -- project is anchored, never ambient --
> is unchanged and is now applied to Skills as well, which the decision did not
> mention because no engine name covered them. ADR 0017 adds `PROJECT_SKILL_PATH`
> and `project_skills_dir`, which is the same rule applied to the second kind
> of thing a project layer carries.

## Date

2026-10-04

## Supersedes

The directory scheme in [ADR 0002](0002-dotagents-protocol.md) and
[ADR 0006](0006-extension-interoperability.md).

## Context

`AGENTS.md` states the **Protocol Boundary Pattern**: standard protocol
directory names at the filesystem seam (`~/.agents/sessions/`,
`~/.agents/extensions/`), Mvge terminology only inside Python. ADR-0002 chose
the opposite — a proprietary `.mvge/` subdirectory:

```
.agents/.mvge/runes/manifest.json
.agents/.mvge/tomes/
.agents/.mvge/auth/
.agents/.mvge/models.json
```

No `.mvge/` directory exists in any `src/`. The last two `.mvge` matches in
first-party Rune code were legacy compatibility string tests
(`mvge-agent/src/mvge_agent/rune_lifecycle.py:121,128`), which the repo's own
**Zero Backward Compatibility Burden** rule says to delete; they are deleted.
The one surviving `.mvge` path is `mvge-gui/src/mvge_gui/main.py:27`,
which writes NiceGUI's signed-cookie secret to `~/.mvge/.storage_secret`.
That is outside the `.agents` layer entirely and out of scope here.

Resolution had become the resolver of record — `global_agents_dir()` and
`GLOBAL_DIR_ENV` — and its own docstring named the failure this decision
exists to prevent:

> *"the install path and the discovery path resolving differently is what allows
> a rune to install successfully and then load nowhere."*

That failure was not prevented, because resolution was duplicated in three
in-repo sites that disagreed:

| Site | Divergence |
| --- | --- |
| `RuneLifecycle.resolve_paths` | Infers `Scope` by substring-matching the path string; still recognised the dead `.mvge/{agent}/runes` and `.mvge/runes` forms. |
| `Mvge.__init__` | ~~Appended colocated `runes/`, config `runes/`+`extensions/`, package `runes/` — a second precedence pass with its own rules.~~ **Deleted 2026-10-11**: `resolve_active_agent_name` answers which Mvge runs, once, and `discover_agent_rune_extras` reads that answer. |
| `dynamic_commands.get_extension_dirs` | Used the ambient `Path.cwd()` and probed the non-standard `<cwd>/extensions`. Rune *CLI command* discovery, so the same divergence class as Rune loading. |

A fourth site, `approval-rune/gate.py:71`
(`DEFAULT_DATA_DIR = Path.home() / ".agents" / "approval"`), ignores
`MVGE_GLOBAL_DIR` entirely. That code lives in the marketplace repository and
is tracked there.

Persona and prompt resolution is deliberately **not** in this set.
`environment.py`'s working-directory fallbacks serve unrelated purposes —
rendering the working directory as an environment fact, resolving the
`SYSTEM.md` / `APPEND_SYSTEM.md` chains, and handing `cwd` to a Rune — and the
difference is in kind. Rune discovery is a search path, where a wrong cwd
loads the wrong *code*. Persona resolution is content, where a wrong cwd costs
a missing project paragraph and nothing else. No first-party engine module
reads `AGENTS.md`; `steering-bridge` owns it through `BEFORE_MVGE_START`.

`steering-bridge` was the reference implementation of this decision: it derives
its resolver's global layer from `global_agents_dir()` and honours
`MVGE_GLOBAL_DIR`. Every resolver here is now one call into
`mvge_core.layers`, which is that shape.

### Implementation note

`Scope` and every path function moved to `mvge_core.layers`;
`mvge_core.constants` retains only the four protocol-facing names
(`DEFAULT_AGENT_NAME`, `DEFAULT_MODEL`, `GLOBAL_DIR_ENV`, `PROJECT_RUNE_PATH`).
`Scope` had to move down regardless of module layout, because
`mvge-core/tests/unit/dependency_direction.py` forbids core importing runes
and the resolver needs to *rank* scopes.

`constants.py` deliberately keeps no re-export facade. Two import paths to one
path-derivation helper is the failure this ADR exists to end. The supported
import path for everything below is the package root, `from mvge_core
import ...`, which is where the moved symbols are re-exported. That is also
what the marketplace runes use, so moving the symbols out of `constants` did
not reach them.


Seven of the thirty commits before this ADR were fixes to some variant of this.
`Scope` existed as an enum but was metadata only: precedence was list order, and
a user-supplied `rune_paths` config silently replaced the whole layering as a
side effect. Both are now decisions 5 and 6.

## Decision

One module owns the layer stack and is the only place a path is derived.

1. **Single resolver.** Every global-layer path is produced by one call. The
   resolution is read at call time, never frozen at import — the original
   `DEFAULT_SESSION_DIR` constant was resolved once during import and could not be
   redirected afterwards.

2. **Layer order, most general to most specific.** global → user → agent →
   project. `$MVGE_GLOBAL_DIR` overrides the global layer for every read and
   every write, which is what makes an isolated run possible.

3. **Project is anchored, never ambient — for search paths.** The project layer
   is derived from an explicit project directory and is *omitted entirely* when
   none is given, so the ambient working directory can never leak into a search
   path. This applies where a wrong answer loads the wrong *code*.

   It deliberately does **not** apply to persona resolution. `resolve_system_prompt`
   and `resolve_append_system_prompts` fall back to `Path.cwd()` when no project
   directory is given, and that is correct: a wrong answer there costs a missing
   project section of `SYSTEM.md`, not a wrong Rune. `AGENTS.md` steering is not
   in scope at all — no first-party engine module reads it; `steering-bridge`
   owns it through `BEFORE_MVGE_START` (see ADR-0009).

4. **Standard names only.** `sessions/`, `agents/`, `agents/<name>/extensions/`,
   `agents/<name>/skills/`, `skills/`, `extensions/`, `auth/`, `approval/`,
   `models.json`, `history`. No proprietary subdirectory.

5. **`Scope` becomes a ranking rule, not a label.** Precedence derives from the
   layer a path belongs to, not from the order a caller happened to concatenate
   strings in. Installation and discovery ask the same question and get the same
   answer, because they are the same call.

6. **`rune_paths` narrows or extends the stack explicitly.** It never replaces
   the layering as a side effect of being set.

7. **`GLOBAL_DIR_ENV` is honoured everywhere, including by Runes.** A Rune that
   hardcodes `~/.agents` is a bug, and the test suite asserts it.

## Contract

Symbols this decision commits to. `GLOBAL_DIR_ENV` and `PROJECT_RUNE_PATH` are
the protocol-facing names and must not be renamed. `global_agents_dir` is the
documented single resolver. `Scope` is what decision 5 turns into a ranking rule.

<!-- adr-contract: GLOBAL_DIR_ENV, PROJECT_RUNE_PATH, global_agents_dir, sessions_dir, agents_dir, extensions_dir, skills_dir, auth_dir, auth_file, approval_dir, models_file, history_file, Scope, RuneScope -->

## Consequences

**Positive**

- One place to change when the layout moves. Install path and discovery path
  cannot diverge, because they are one call rather than two.
- `MVGE_GLOBAL_DIR` isolates a whole run — global Rune layer, Tomes, auth,
  approval state and model cache together. That is what lets the root
  `conftest.py` make the suite hermetic regardless of what the developer has
  installed.
- The legacy `.mvge/{agent}/runes` matching becomes deletable, removing the
  last first-party reference to a scheme this decision retired.
- `approval-rune` stops writing grants outside the relocated global layer.

**Negative**

- A caller that genuinely wants a bespoke path list must now say so. There is no
  longer an implicit "my list *is* the precedence", which was quietly how
  `rune_paths` worked.
- Moving the five resolvers onto one module is a cross-package change touching
  core, agent, runes, and cli. It cannot be done package-locally.
- Any Rune already published against the old resolvers needs re-checking. The
  `mvge-marketplace` contract check gates engine removals but not path
  resolution.
