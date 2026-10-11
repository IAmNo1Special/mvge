# Architecture Decision Records

| # | Title | Status | Date |
| --- | ------- | -------- | ------ |
| 0017 | [Project-Scoped Skills Are a Supported Layer](0017-project-scoped-skills-are-a-supported-layer.md) | Accepted | 2026-10-10 |
| 0016 | [A Sandbox Allow-List Is a Privilege Budget, Not a Wish List](0016-sandbox-privilege-is-a-budget-not-a-list.md) | Accepted | 2026-10-05 |
| 0015 | [OpenCode Zen as the Default Realm](0015-opencode-zen-default-realm.md) | Accepted | 2026-10-05 |
| 0014 | [One Resolved Layer Stack for the `.agents` Protocol](0014-one-resolved-layer-stack.md) | Accepted (amended 2026-10-10: decision 4, see ADR 0017) | 2026-10-04 |
| 0013 | [Cache-Free, Format-Agnostic Tome Persistence](0013-cache-free-format-agnostic-tome-persistence.md) | Accepted | 2026-10-04 |
| 0012 | [Deepened Mvge Turn-Driving Module](0012-deepened-mvge-turn-driving-module.md) | Accepted — migration incomplete, see note | 2026-09-12 |
| 0011 | [skill_evolution — MvgeOS Architecture for Autonomous Skill Evolution](0011-skill-evolution-rune-architecture.md) | Accepted (supersedes ADR 0010) | 2026-09-08 |
| 0010 | [knowledge_skill — MvgeOS Implementation of WikiSkill with Persistent Knowledge](0010-knowledge-skill-persistent-knowledge-architecture.md) | Superseded by ADR 0011 | 2026-09-06 |
| 0009 | [Two-Layer Invariant Scaffolding & Colocated Zero-Config Agent Architecture](0009-two-layer-invariant-scaffolding-and-colocated-agent-architecture.md) | Accepted (amended 2026-10-04) | 2026-09-02 |
| 0008 | [NiceGUI Desktop Application Architecture (1:1 Antigravity UI)](0008-nicegui-desktop-application.md) | Accepted (amended 2026-08: component inventory descoped to the shipped pipeline) | 2026-08-22 |
| 0007 | [Unified Tome Session System on Pi-Compatible JSONL Format](0007-unified-tome-session-system.md) | Accepted — mechanism superseded by ADR 0013 | 2026-08-03 |
| 0006 | [Extension Interoperability at Data Level](0006-extension-interoperability.md) | Superseded by ADR 0002, 0013, 0014 | 2026-07-28 |
| 0005 | [TDD for All Code](0005-tdd-mvge-agent.md) | Accepted | 2026-07-25 |
| 0004 | [OpenRouter as First Provider](0004-openrouter-realm.md) | Accepted (amended 2026-10-05: default Realm changed) | 2026-07-22 |
| 0003 | [Full JSONL Session Schema](0003-full-jsonl-schema.md) | Accepted — mechanism superseded by ADR 0013 | 2026-07-20 |
| 0002 | [dotagents Protocol Compliance](0002-dotagents-protocol.md) | Superseded by ADR 0014 | 2026-07-18 |
| 0001 | [Monorepo with uv](0001-monorepo-with-uv.md) | Accepted | 2026-07-15 |

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `Accepted` | The decision stands. |
| `Accepted (amended <date>: …)` | The decision stands; the note says which part moved. |
| `Accepted — mechanism superseded by ADR NNNN` | The decision stands; its implementation was replaced. Read both. |
| `Superseded by ADR …` | The decision is retired. Do not implement it. |

An ADR whose decision is retired but whose reasoning is still useful keeps both
records: the status line points forward, the body keeps the *why*.

## Keeping this index honest

`mvgeos-core/scripts/check_adr_contract.py` asserts that every symbol an ADR
commits to still exists in first-party source, and that every ADR declares a
status. It runs in CI alongside the marketplace contract check.

That check exists because this index drifted silently for 227 commits: ADR-0007
named `TomeLedger`, an in-memory `Index` and `filelock`, none of which existed,
and nothing noticed. Three greps would have caught it. When an ADR's mechanism is
deliberately replaced, amend or supersede the ADR in the same commit that
replaces the code — never let the two drift.
