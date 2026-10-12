# ADR 0009: Two-Layer Invariant Scaffolding & Colocated Zero-Config Agent Architecture

## Status

Accepted (amended 2026-10-04: Layer-2 content list relocated to Runes)

> **Amended 2026-10-04.** The *decision* — separate the persona from
> engine-rendered scaffolding, and make agent creation zero-boilerplate — is the
> most load-bearing thing in this repo and is not in question. What drifted is the
> Layer-2 content list, and it drifted by **relocation, not deletion**:
>
> `render_prompt` (`mvgeos-agent/src/mvgeos_agent/environment.py:139-179`) now
> renders only two Layer-2 sections itself:
>
> - `Active spells:` (dynamically populated from coerced callables and Rune spells)
> - `Environment:` (OS, architecture, PowerShell syntax and cmdlet guidance on
>   Windows, CWD, current UTC timestamp)
>
> The other three moved to marketplace Runes, which contribute their sections
> dynamically through the `BEFORE_MVGE_START` Sigil:
>
> | Listed below | Owner now |
> | --- | --- |
> | `Self-Modification & Customization:` pointers | `selfmod-bridge` |
> | `<project_context>` (workspace `AGENTS.md` injection) | `steering-bridge` |
> | `<skills>` (indexed skill catalog) | `skills-bridge` |
>
> This is the Protocol Boundary Pattern applied to prompts: the engine owns what
> is invariant across every Mvge; a Rune owns what is situational. Two further
> corrections: `GUIDELINES.md` is named below but exists in no Mvge
> (`coding_mvge` has a single `system_prompt/SYSTEM.md`), and the Tier-1 anchor
> text quoted below is stale — the current default is `_SYSTEM_PROMPT_BODY` at
> `environment.py:76-80`.
>
> A consequence worth stating: because Layer 2 is now assembled from a base plus
> Rune contributions, `assemble_system_prompt` short-circuits when
> `"Active spells:"` already appears in the base (`environment.py:702`). That
> guard only makes sense under the amended model.

## Date

2026-09-02

## Context

Previously, agents in MvgeOS required manual spell list definitions, programmatic prompt wiring, and hardcoded tool registrations in Python code (e.g. `CodingMvge(spells=[bash, read, write, edit, find, list_files, grep])`). System prompts were tightly coupled with engine scaffolding, making self-modification and dynamic extension cumbersome.

Research across Pi, Google ADK, and modern Agent-Computer Interface (ACI) architectures demonstrated that separating static persona/guidelines from dynamic invariant engine scaffolding significantly improves model adherence, eliminates prompt degradation, and facilitates autonomous self-modification.

## Decision

We adopt the **Two-Layer Invariant Scaffolding Pattern** and **Colocated Zero-Config Agent Architecture**:

### 1. Two-Layer Invariant Scaffolding
- **Layer 1 (Persona & Guidelines)**: Stored in plain markdown files in `system_prompt/` colocated with agent code (`SYSTEM.md`, `GUIDELINES.md`) or resolved via dotagents precedence layers (caller $\rightarrow$ project $\rightarrow$ agent $\rightarrow$ default). If absent, falls back to a neutral 1-sentence Tier-1 anchor (`"You are an AI assistant equipped with spells(tools) to assist your Summoner(user). Be direct, concise, and technical."`).
- **Layer 2 (Invariant ACI Scaffolding)**: Dynamically rendered at prompt compilation time by the engine:
  - `Active spells:` (dynamically populated from coerced callables and rune spells)
  - `Environment:` (OS, architecture, PowerShell syntax & cmdlet guidance on Windows, CWD, current UTC timestamp)
  - `Self-Modification & Customization:` (on-demand pointers to `spells/AGENTS.md`, `skills/AGENTS.md`, `runes/AGENTS.md`, `system_prompt/AGENTS.md`, workspace `AGENTS.md`)
  - `<project_context>` (workspace `AGENTS.md` context injection)
  - `<skills>` (indexed skill catalog)

### 2. Caller-Adjacent Zero-Config Auto-Discovery
- `Mvge(name="...")` uses stack inspection to discover the caller module's directory.
- Auto-loads `.env` located next to the caller.
- Auto-discovers spells from caller-adjacent `spells/` directory using 3-tier discovery precedence:
  1. `__all__` in `spells/__init__.py`
  2. Module filename stem match (`def <stem>(...)` in `<stem>.py`)
  3. Single public function defined within the module
  4. Explicit `SpellDiscoveryError` if ambiguous or missing.
- Auto-resolves caller-adjacent `skills/`, `runes/`, and `system_prompt/` (`SYSTEM.md` and `GUIDELINES.md`).

### 3. Decomposed Modular Spells Subpackage
- Monolithic `spells.py` files are decomposed into a `spells/` subpackage with individual files (`bash.py`, `read.py`, `write.py`, `edit.py`, `find.py`, `list_files.py`, `grep.py`, `_process_tree.py`, `__init__.py`).
- Colocated `AGENTS.md` guides inside `spells/`, `skills/`, and `runes/` establish explicit authoring contracts for autonomous self-modification.

## Consequences

- **Zero Boilerplate**: Defining an agent requires only naming the Mvge the caller wrote — `root_mvge = Mvge(name="my_mvge")`. The engine resolves the active Mvge from the name it is given and the agents installed; it names no published artifact, so the catalog can rename or add a Mvge without a release here. Colocated discovery is what makes the boilerplate zero, not the spelling of one name.
- **Autonomous Self-Modification**: The agent can inspect and self-modify spells, skills, and runes by reading colocated `AGENTS.md` authoring contracts.
- **Robust Tool Discovery**: Python callables, async functions, and structured `MvgeSpell` objects are transparently coerced and schema-validated.
- **Clean Invariant Separation**: Agent authors focus exclusively on domain guidelines and persona; runtime environment, shell rules, tool schemas, and project context are managed invariantly by the engine.
