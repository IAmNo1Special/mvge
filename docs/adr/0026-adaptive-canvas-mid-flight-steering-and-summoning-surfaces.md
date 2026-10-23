# ADR 0026: Adaptive Dual-Purpose Canvas, In-Flight Steering Controls, and Sub-Mvge Summoning Surfaces

## Status

Accepted

## Date

2026-10-22

## Context

MvgeOS GUI (`mvgeos-gui`) provides a native desktop interface modeled on Google Antigravity IDE per ADR-0008. However, modern AI agent interfaces (Google Antigravity, OpenAI Canvas, Meta Muse, and Cursor Agent) have evolved past static three-column layouts and synchronous batch turn-taking:

1. **Artifact Modal Obstruction**: Artifacts (Implementation Plans, Code Reviews, Walkthroughs) currently render in a blocking modal dialog (`ui.dialog` in `artifact_drawer.py`). Inspecting an artifact obscures the conversation and prevents observing live Spells and Channeling output.
2. **Locked In-Flight Composer**: While `mvgeos-core` and `mvgeos-agent` provide native microkernel support for `get_steering_messages` and `get_follow_up_messages`, `mvgeos-gui` disables the composer textarea entirely during Channeling and exposes only a hard Stop button. Summoners cannot interject mid-flight guidance or queue follow-up prompts while a Mvge is working.
3. **Sub-Mvge Invisibility**: ADR-0022 established Sub-Mvge Summoning, but `mvgeos-gui` provides zero visual representation of child Mvges, leaving delegated tasks hidden or flattened.
4. **Terminology Leaks**: Visible UI copy currently leaks generic terms ("agent", "user", "thought") into user-facing labels and tooltips, violating the domain boundary defined in `CONTEXT.md`.

## Decision

1. **Adaptive Dual-Purpose Canvas Surface (`review_rail.py`, `artifact_drawer.py`, `shell.py`)**:
   - Transform the right panel into an adaptive, multi-surface **Canvas**:
     - Defaults to the Git Review Rail (changed files and unified diffs).
     - Seamlessly transitions to a full-height interactive Canvas when an Artifact, file preview, or sub-tome is opened.
     - Operates in a **Responsive Dual-Mode**: docked side-by-side split-pane on wide viewports (>1280px) and a non-blocking slide-over drawer on smaller displays, with an explicit Dock/Float toggle in the Canvas header.
     - Multi-surface tab bar in the Canvas header: `[Review (N)]`, `[Artifact: Title]`, `[Sub-Mvge: Name]`, allowing one-click switching and closing.
     - Multi-renderer support: Rich Markdown, interactive Mermaid diagrams, interactive Walkthrough checklists (with checkmark state persisted to the Tome payload), syntax-highlighted code with line numbers, and an isolated, sandboxed `<iframe>` for live HTML/SVG preview with strict Content Security Policy.

2. **In-Flight Steerable Composer (`composer.py`, `state.py`)**:
   - Keep the composer textarea editable while a Mvge is Channeling.
   - Provide dual in-flight action controls:
     - `[Steer Mvge]` (or `Cmd/Ctrl+Enter`): Injects steering guidance immediately into the active turn loop via `harness.steer()`, drained at the next turn boundary.
     - `[Queue Follow-up]` (or `Enter`): Adds a queued follow-up pill to the turn tray to execute when the active run settles.
     - `[Stop]`: Cooperative abort of the current channeling turn.

3. **Sub-Mvge Summoning Presentation (`step_cards.py`, `state.py`)**:
   - Render delegated sub-tasks in the transcript as dedicated **Summoning Cards** showing child Mvge name, live status attribution light, current Spell, and elapsed Mana.
   - An `[Inspect]` button on the Summoning Card projects the child Mvge's Tome directly into the side Canvas with live streaming deltas, leaving the parent conversation in view, along with an `Open as Full Tome` action.

4. **Strict Ubiquitous Language Enforcement across GUI Presentation**:
   - Standardize all user-facing strings, badges, tooltips, and empty states: *Summoner* (never user), *Mvge* (never agent), *Contemplation* (never thought), *Tome* (never session), *Channeling* (never streaming), and *Spells* (never tools).

## Contract

The key GUI abstractions that anchor the Canvas, Composer, and Step Card interfaces:

- `AppState` in `mvgeos_gui.state` manages client UI state, channeling flags, and active drawer/canvas selections.
- `render_composer` in `mvgeos_gui.components.composer` owns prompt input, in-flight action controls, and autocomplete.
- `render_review_rail` in `mvgeos_gui.components.review_rail` and `render_artifact_drawer` in `mvgeos_gui.components.artifact_drawer` anchor the right-hand inspection and Canvas surfaces.
- `render_contemplation_card` in `mvgeos_gui.components.step_cards` anchors reasoning and step card projection.

<!-- adr-contract: AppState, render_composer, render_review_rail, render_artifact_drawer, render_contemplation_card -->

## Consequences

- Summoners gain an adaptive side-by-side workspace combining Antigravity IDE developer ergonomics with OpenAI Canvas / Meta Muse interactive document capabilities.
- Long-running Mvge operations become steerable in real time without losing turn progress or requiring destructive aborts.
- Sub-Mvge delegations become transparently inspectable in parallel with the main conversation.
- UI presentation 100% conforms with the MvgeOS ubiquitous language.
