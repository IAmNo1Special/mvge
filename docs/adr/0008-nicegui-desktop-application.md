# ADR 0008: NiceGUI Desktop Application Architecture (1:1 Antigravity UI)

## Status

Accepted (amended 2026-08: component inventory descoped to the shipped pipeline)

## Context

Mvge provides CLI and TUI interfaces for Summoners to interact with Mvges. However, complex agentic workflows—such as inspecting multi-file diffs, reviewing structured markdown artifacts, monitoring concurrent subagents, tracking active skills, and managing multi-project workspaces—benefit substantially from a rich graphical desktop interface.

The design goal is to provide a 1:1 pixel-accurate desktop experience matching Google Antigravity IDE, powered natively in Python by NiceGUI.

## Decision

We will implement `mvge-gui` as a dedicated workspace package within the Mvge monorepo:

1. **Package Placement**:
   - `mvge-gui` workspace member in `pyproject.toml`.
   - Entry point: `mvge-gui` CLI command.

2. **Desktop Runtime & Windowing**:
   - Built on NiceGUI with PyWebView (`native=True`) by default for an OS-native desktop window without browser chrome.
   - Includes a `--web` flag to run as a local web service if requested.

3. **Backend Integration & Event Bus**:
   - Direct in-process execution connecting `MvgeHarness` and `CodingMvge`.
   - Asynchronous event bus consuming `MvgeEvent`s (channelling tokens, spell dispatches, mana usage, step progress) in real-time.

4. **1:1 Antigravity Design System**:
   - Custom CSS tokens overriding Quasar dark theme with obsidian (`#0e1117`) and slate (`#1b1e27`) surfaces, border accents, and Inter/JetBrains Mono typography.
   - Three-column layout:
     - **Left Navigation**: Back/forward navigation, New Conversation button, Conversation History, Scheduled Tasks, Project workspace tree with relative time badges and branch tags, Settings.
     - **Center Viewport**:
       - Empty state with centered project selector dropdown ("New Conversation v"), search bar, project list with settings gear, New Project / Quick Start / No Project, rich input pill card with attachment button, model selector dropdown, speech mic, submit button.
       - Active state with top breadcrumbs, Open IDE button, message bubbles, collapsible step cards (Worked for Xs, Explored files, Ran commands with terminal prompt), artifact review badges.
       - Bottom floating input dock with autocomplete for `@` mentions (files, skills, subagents) and `/` slash commands, attachment handling, model switcher, speech mic, and cancel/stop button.
     - **Right Inspector Panel**:
       - Subagents list with status & execution times.
       - Files Changed with diff counts and interactive diff modal.
       - Artifacts list with slide-over markdown review drawer.
       - Skills used and Background tasks.

5. **Testing & Code Quality**:
   - Google Python Style Guide, Ruff formatting, Mypy strict mode.
   - pytest test suite with >=90% test coverage in `mvge-gui/tests/unit/` and `mvge-gui/tests/integration/`.

## Consequences

- Summoners gain a native desktop experience without requiring Node.js/Electron.
- The GUI shares the unified Tome JSONL storage and Mvge agent abstractions without duplication.
- Live Git state, artifacts, and subagent lifecycles are visualized in real time.

## Amendment (2026-08): shipped pipeline

The core decisions above stand (NiceGUI + PyWebView, native window, dedicated
workspace package). The component inventory in this ADR's Decision section
drifted from what shipped; the divergence is now deliberate:

- **Single composer**: chat_panel's inline composer is the only prompt input.
  The standalone bottom floating input dock (`input_dock.py`) and the
  standalone conversation view (`conversation_view.py`) were deleted rather
  than re-wired; autocomplete behavior lives behind `chat_panel.handle_tab`
  and `AutocompleteService`.
- **No dedicated inspector module**: the Right Inspector Panel's concerns are
  distributed across `review_rail.py`, `artifact_drawer.py`, side panels, and
  the status bar instead of a single `inspector.py`.
- **Settings ships twice over**: a settings view plus two wired modals
  (application and workspace) rendered in the shell overlay layer, backed by
  `ConfigService` keyring persistence.
- **Mvge status light**: `AppState.mvge_status` (idle / channeling / working)
  is driven by `AgentService` from run events.

"1:1 Antigravity" remains the visual aspiration for styling only, not a
specification of module inventory.
