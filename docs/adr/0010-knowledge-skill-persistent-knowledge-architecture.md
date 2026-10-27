# ADR 0010: knowledge_skill — Mvge Implementation of WikiSkill with Persistent Knowledge

## Status

Superseded by [ADR 0011](0011-skill-evolution-rune-architecture.md)

## Date

2026-09-06

> **Retired 2026-10-04.** Kept for the reasoning. `knowledge_skill` became
> `skill_evolution`; `Raw Knowledge` became `Raw Experience`; and the
> `.mvge/` paths named below are dead — see
> [ADR 0014](0014-one-resolved-layer-stack.md). This ADR had no `## Status`
> section while the index listed it as superseded, which is exactly the drift
> `mvge-core/scripts/check_adr_contract.py` now fails on.

Mvge needs compounding skill evolution per `WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution` (`arxiv:2608.27454` `§3`) but `wiki` is overloaded in AI tooling, the paper's `sqlite`/`wiki.db` diverges from `§3.2.2` incremental markdown patches, and naïve skill writes break 3-scope precedence (`SKILL_SCOPES` `mvge-runes/src/mvge_runes/loader.py:281`).

We adopt `knowledge_skill` Rune at `coding-mvge/src/coding_mvge/runes/knowledge_skill/` as our `WikiSkill` implementation: rename `wiki` -> `knowledge` throughout (`KNOWLEDGE_MAINTAINER_SYSTEM`, `knowledge/index.md`/`logs.md`/`skill-impact.md`/`patterns/*.md` `§3.1` + `knowledge/.gating.json` for `R_best` `§3.2.4` vs `.skill-lock.json` reproducibility lock `~/.agents/.skill-lock.json`), pure-markdown `KnowledgeStore` (`knowledge/store.py:1`) with exact `append`/`replace`/`insert_after` target matching, `Raw Knowledge` at `raw_knowledge/iter_<k>/<trace>.json` immutable, and `Skills` as `SKILL.md`+`PURPOSE.md` (`NAME_REGEX` `loader.py:287`, `name==path.name` `loader.py:328`) evolved atomically via `propose_skill_update` with **same-location saves** (`SkillManifest.path`/`scope` first-wins `loader.py:259`; `create` defaults to `PROJECT:.agents/skills/<name>/` if `.agents` exists else `AGENT:~/.agents/.mvge/coding_mvge/skills/<name>/`). Agent-scoped `~/.agents/.mvge/coding_mvge/{raw_knowledge,knowledge}` for now; shared `USER`/`PROJECT` knowledge exploration tracked in #137. `RuneRunner`/`SkillCatalog` split tracked in #138.

## Considered Options
- Keep `wiki` term and `wiki.db` sqlite+FTS5: rejected — `wiki` collides with `ai/ai agents` usage, sqlite diverged from paper and caused `FTS5: syntax error`/`datatype mismatch`.
- Mutate `SpellDefinition` `types.py:409` instead of `SkillManifest` `types.py:306`: rejected — conflates executable `Spell` (`MvgeState.spells` `types.py:236`) with declarative `Skill` (`get_skill_catalog` `rune_runner.py:286`).
- Shared `knowledge` across scopes immediately: deferred — Agent-only compounding matches paper `Fig.2` `Algorithm 1` `Wk` never-rolled-back `§3.2.4`; shared requires merge strategy.

## Consequences
- All `wiki_skill.*` Spells removed; code, prompts, `manifest.json:2`, and `CONTEXT.md:65` use `knowledge` exclusively.
- Skill edits are reviewable diffs in `knowledge/skill-impact.md` and reversible via `SkillManifest.path`; knowledge compounds via `KnowledgeMaintainer` stratified `≤5 failures + ≤3 successes` `Appx.C` and harness never rolls back `Wk`.
