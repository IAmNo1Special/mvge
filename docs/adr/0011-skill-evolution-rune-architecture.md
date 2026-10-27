# ADR 0011: skill_evolution — Mvge Architecture for Autonomous Skill Evolution

## Status

Accepted (supersedes [ADR 0010](0010-knowledge-skill-persistent-knowledge-architecture.md))

## Date

2026-09-08

> **Status normalised 2026-10-04.** This ADR carried its status as an inline
> `**Status**:` line rather than a `## Status` section, so
> `mvge-core/scripts/check_adr_contract.py` could not read it and reported it
> as having no status at all. One canonical form, so the check can be trusted.

## Context

ADR 0010 established the initial port of `WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution` (`arxiv:2608.27454`) under the rune name `knowledge_skill`. However, architectural analysis and pair programming review revealed critical friction:

1. **Terminology dissonance**: The term "knowledge" obscured the primary capability of the rune: autonomous, compounding **Skill Evolution**.
2. **Leaky completion seam in Experience Consolidator**: `KnowledgeConsolidator` accepted an untyped `llm_client: object | None`, inspected `unittest.mock` internals (`isinstance(client, (MagicMock, AsyncMock))`, `_mock_children`) directly in production code, and used reflection (`inspect.signature`) across disparate client shapes.
3. **Global mutable state in Proposer Mvge**: The proposer's `finish` and `read_file` spells were implemented as stateless functions that relied on a process-wide `_GLOBAL_CONTEXT` to locate workspace directories and record outputs, violating Mvge invariants against global mutable state in library code.

## Decision

We rename the built-in rune from `knowledge_skill` to `skill_evolution` and deepen its architecture:

1. **Rune & Storage Structure**:
   - Built-in rune lives at `coding-mvge/src/coding_mvge/runes/skill_evolution/` with manifest name `"skill_evolution"`.
   - On-disk storage is organized under `~/.agents/.mvge/{agent}/`:
     - `raw_experience/iter_<k>/<trace>.json`: Immutable execution traces harvested from turn invocations.
     - `skill_evolution/`: Compiled evolution store (`index.md`, `logs.md`, `skill-impact.md`, and `patterns/*.md`).
   - Spells are renamed to `skill_evolution_consolidate` and `skill_evolution_export`.
   - Slash commands are renamed to `/skill-evolution-consolidate`, `/skill-evolution-export`, `/skill-evolution-stats`, and `/skill-evolution-propose`.

2. **Callable Completion Seam (`CompleteFn`)**:
   - `ExperienceConsolidator` accepts a standardized completion callable:
     ```python
     CompleteFn = Callable[[list[dict[str, str]], AbortSignal | None], Awaitable[str]]
     ```
   - Mirrors Pi's `StreamFn` and Mvge's `SummarizeFn`.
   - In production, an adapter wraps `Realm.complete()`.
   - In hermetic tests, tests supply an async function returning canned text. All `unittest.mock` imports, `_mock_children` inspections, and signature sniffers are purged from production code.

3. **`SkillEvolutionEngine` Deep Module**:
   - Extract a deep module `SkillEvolutionEngine` that encapsulates:
     - Skill schema validation (`NAME_REGEX`, 1-64 chars, frontmatter invariants).
     - Scoping rules between `PROJECT` (`.agents/skills/<name>`) and `AGENT` (`~/.agents/.mvge/{agent}/skills/<name>`).
     - Atomic patch operations (`append`, `replace`, `insert_after`) and unified diff generation.
     - Filesystem mutations and `skill_evolution/skill-impact.md` audit trail appending.
     - Restricted sandboxed reading of patterns, traces, and skill files.
   - Interface: `apply_proposal(proposal) -> SkillEvolutionResult` and `read_file(path) -> SpellResult`.
   - Spells in `proposer_mvge` delegate directly to the engine instance. `context.py` and `_GLOBAL_CONTEXT` are eliminated.

## Consequences

- Completely eliminates runtime mock introspection and global mutable state.
- Aligns domain vocabulary cleanly around Skill Evolution.
- Unit and integration tests target the clean interfaces (`CompleteFn` and `SkillEvolutionEngine`) directly, establishing hermetic test suites.
