# Architecture Provenance & Interoperability

## Architectural Origins & Independence

Mvge is an independent, ground-up agentic operating system architecture built in Python.

While Mvge implements its own clean-room design and domain model (Mvges, Spells, Realms, Tomes, Runes, Sigils), its design was informed and inspired by notable open-source architectures and research in the autonomous agent ecosystem:

- **Pi (earendil-works/pi)**: Pioneer of lightweight, streaming JSONL session logs and modular extension hooks.
- **Google ADK (Agent Development Kit)**: Concepts around structural agent-environment scaffolding and declarative tool manifests.
- **Eve**: Event-driven multi-turn execution loops and asynchronous state trees.
- **arXiv Literature & State Machine Research**: Foundational papers on hierarchical multi-turn reasoning loops, memory compaction algorithms, and autonomous test-driven recovery.

---

## Frictionless Migration & Compatibility

Mvge was deliberately engineered with immediate, frictionless interoperability for users transitioning from other ecosystems:

### 1. Pi Session Compatibility
The Tome format (`mvge.tome`) was inspired by Pi's streaming JSONL session logs, but it is **not byte-compatible** with Pi session files (different header version, timestamp encoding, and entry schema). Native Pi support lives in the session codec architecture: `mvge.tome` defines a format-agnostic `SessionCodec` protocol, and the marketplace `pi-codec` rune provides a codec that reads, resumes, appends to, compacts, and forks real Pi session logs (JSONL v3/v4) natively — a Pi session stays a Pi session, with no conversion. Tome v1 remains the built-in default for new Mvge sessions.

### 2. Universal Agent Session Reconstruction (Roadmap)
Mvge's persistence and session abstractions are designed to expand beyond Pi. The roadmap includes universal session reconstructors capable of parsing and resuming trajectories originated in:
- **Codex CLI / Assistants**
- **Claude Code**
- **PaLM / Gemini Agent Harnesses**
- **DeepSeek Agent Harness**

By treating conversation logs, tool invocations, and execution snapshots as first-class, reconstructible state graphs, Mvge aims to serve as a universal substrate where summoners can continue sessions regardless of where they began.
