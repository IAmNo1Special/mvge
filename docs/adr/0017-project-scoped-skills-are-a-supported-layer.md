# ADR 0017: Project-Scoped Skills Are a Supported Layer

## Status

Accepted

## Date

2026-10-10

## Amends

[ADR 0014](0014-one-resolved-layer-stack.md), decision 4.

## Context

ADR 0014 gave the `.agents` scheme one resolved layer stack and one rule for the
project layer: *anchored, never ambient*. That rule was written about Runes.
Skills were left out of it, and the gap between what the two halves of the
product believed turned out to be three different spellings of one path.

`mvge_core.constants` named the project rune directory, `PROJECT_RUNE_PATH =
".agents/extensions"`. It named no skills counterpart, so the project skills
directory had no engine-side derivation to agree with, and three sites spelled
it out:

| Site | Spelling | Anchor |
| --- | --- | --- |
| `mvge-gui/src/mvge_gui/state.py` | `project_path / ".agents" / "skills"` | the named project — correct |
| `seeker/paths.py` (marketplace) | `Path(".agents") / "skills"` | **nothing** |
| `seeker` docs and tests | `.agents/skills` | — |

The GUI's copy was right and the Rune's copy was not, which is the only
interesting fact here. `seeker` held the project directory as a *module-level
relative constant* and let `Path.resolve()` and `Path.is_dir()` bind it to the
process working directory at the moment of the call. `paths.missing()` filters
that same value, so the failure was quiet in both directions: launched from an
unrelated checkout, `seeker` silently searched *that* checkout's skills, and
launched from a directory with no `.agents/skills` it silently reported the
project root as absent rather than as never-asked-for.

ADR 0014 already says why that class of bug is worse for Runes than for
content, and it said it in terms that apply unchanged here:

> This applies where a wrong answer loads the wrong *code*.

A Skill is not code. It is instructions the Mvge will follow. Loading one from a
project the caller did not name is the same failure with a worse outcome
shape: the Rune misbehaves, the Mvge obeys. A wrong cwd does not merely return
the wrong answer, it *changes what the agent does*.

The question this ADR answers is not "is this a bug" — it plainly is. It is
whether project-scoped skills are a *layer*, because the two available repairs
lead in opposite directions. Deleting the candidate would have been a
behaviour change for any checkout that has a `.agents/skills` directory, made
on a guess, in a Rune that had no standing to guess about the layer stack.

## Decision

1. **Project-scoped skills are a supported layer.** `.agents/skills` is the
   sibling of `.agents/extensions` in the same protocol scheme: what a project
   carries, rather than what a user carries. ADR 0014 decision 4 lists standard
   names and bans proprietary subdirectories; `.agents/skills` is a standard
   name, and the engine's own GUI already read it before this ADR existed.

2. **The engine derives it, like every other project layer.**
   `PROJECT_SKILL_PATH = ".agents/skills"` joins `PROJECT_RUNE_PATH` in
   `mvge_core.constants`, and `project_skills_dir(project_dir)` joins the
   resolvers in `mvge_core.layers`.

3. **Anchored, never ambient — identically to Runes.** `project_dir` is a
   required argument. It is anchored by the same `_anchor_project_dir` that
   `resolve_rune_layers` uses, and it is *omitted* when no project is named.
   There is no default-to-cwd path, and none may be added.

4. **`~/.claude/skills` is not a layer and does not move.** It is a third-party
   convention. Relocating `$MVGE_GLOBAL_DIR` does not relocate someone else's
   directory, and it is not ours to derive.

5. **A Rune that reaches for it names a project.** `seeker` takes an explicit
   project directory and passes it down; it does not hold a relative constant.
   A caller with no project gets the user layer and the Claude convention, and
   `missing()` names exactly the roots it could not search — which is how the
   silent-empty-search problem ADR 0014 already fixed stays fixed here.

## Contract

Symbols this decision commits to. `PROJECT_SKILL_PATH` is the protocol-facing
name and must not be renamed, on the same terms as `PROJECT_RUNE_PATH`.
`project_skills_dir` is the resolver, and it takes a required project
directory.

<!-- adr-contract: PROJECT_RUNE_PATH, PROJECT_SKILL_PATH, _anchor_project_dir, project_skills_dir, skills_dir, global_agents_dir -->

## Consequences

**Positive**

- The project skills layer has the derivation the other three had. Installation
  and discovery ask the same question and get the same answer, because they are
  the same call.
- A Rune that searches skills and the GUI that lists them cannot disagree about
  where a project's skills live.
- The silent failure closes. `missing()` cannot report a root that was never
  asked about as one that is merely absent.

**Negative**

- `seeker` gains a parameter, and every caller that constructs `SkillSearchSpell`
  or `SkillExecuteSpell` has to decide what to pass. The default is no project,
  which is the correct default: it is the one that cannot be wrong.
- Two repositories move for one fix. The engine gains the constant and resolver;
  the marketplace Rune gains the anchor. This ADR lands with the engine half so
  its `## Contract` is true on arrival — `check_adr_contract.py` resolves
  every contract symbol against first-party source, and an ADR that commits to
  a name no code has yet would fail CI on the commit that records it.

**Neutral**

- `okf-bridge` writes `graph.bundle_root or Path(".")`. It looked like the same
  pattern under a grep for relative `Path` literals and is not: that is a
  default output location, chosen by the caller that omitted a bundle root, and
  it is a *write* target rather than a search root. It is not in scope here.