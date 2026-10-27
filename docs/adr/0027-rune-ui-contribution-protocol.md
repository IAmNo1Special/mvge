# ADR 0027: Rune UI Contribution Protocol and Dynamic Presentation Extensibility

## Status

Accepted (amended 2026-10-25: AppState GUI methods moved to mvge-desktop companion repository)

## Date

2026-10-23

## Context

Mvge enforces a strict microkernel separation where `mvge-core` and `mvge-agent` provide the minimal loop vocabulary, while specialized capabilities (Skills, Sub-Mvges, Checkpoints, Approval Gates, Telemetry) are packaged and distributed as modular Runes via `mvge-marketplace`.

However, the desktop interface (`mvge-gui`) had accrued several architectural discrepancies:

1. **Static UI Dependencies on Absent Runes**: Specialized feature surfaces—specifically the Skills management view, Sub-Mvge canvas projections, and approval badge hooks—were statically hardcoded into the left navigation rail and Canvas tab hierarchy. In fresh or minimal environments where only a provider Realm rune (e.g. `openrouter-realm`) is installed, the GUI displayed "Skills" in the sidebar and empty scaffolding for capabilities the active Mvge could not execute.
2. **Lack of Extensibility for Marketplace Runes**: While runes could expose Spells, Sigil hooks, and CLI slash commands via `manifest.json`, runes had no declarative mechanism to contribute UI entry points (such as navigation sidebar views or Adaptive Canvas tabs). A rune implementing a task scheduler or test runner had no standard interface to inject a "Scheduler" or "Tests" view into `mvge-gui`.

## Decision

1. **Declarative Rune UI Contributions in `manifest.json`**:
   Runes declare their contributed UI entry points declaratively in `manifest.json` under an optional `"ui"` object:
   - `"views"`: Array of navigation sidebar views (`id`, `title`, `icon`, `order`, `description`).
   - `"canvas_tabs"`: Array of Adaptive Canvas tabs (`id`, `title`, `icon`, `order`).
   Runes contributing UI entry points declare `"UIView"` or `"UICanvasTab"` in their `"types"` list.

2. **Typed Manifest Ingestion in `mvge-runes`**:
   - `mvge_runes.types` defines `RuneUIView`, `RuneUICanvasTab`, and `RuneUIContributions`.
   - `load_manifest` parses the `"ui"` dictionary into typed structures attached to `RuneManifest.ui`.
   - `list_installed_runes()` includes the `"ui"` dictionary for all installed extensions.

3. **Dynamic Presentation and Extensible Routing in `mvge-gui`**:
   - `AppState` exposes `get_contributed_views()` and `get_contributed_canvas_tabs()`, which inspect installed and enabled runes and order contributions by rank.
   - Core sidebar navigation is restricted to microkernel surfaces (`Home`, `Chat`, `Sessions`, `Marketplace`, `Diagnostics`, `Settings`). Rune-dependent surfaces like `Skills` only render when their backing rune (e.g. `skills-bridge`) is installed and enabled.
   - `shell.py` routes view dispatches to custom view handlers (e.g., `render_skills_panel`) when registered, or falls back to a standard `_render_contributed_rune_view` for third-party marketplace runes.
   - `canvas.py` mounts contributed canvas tabs dynamically and falls back to a standard surface view when selected.
   - Quick switcher (`command_palette.py`) dynamically enables or disables navigation commands according to active rune contributions.

4. **Marketplace Runes Manifest Alignment**:
   - `skills-bridge` declares a `views` entry for the `"skills"` panel.
   - `submvge-bridge` declares a `canvas_tabs` entry for `"sub_mvge"`.
   - `checkpoint-bridge` declares a `canvas_tabs` entry for `"review"`.

## Contract

The key types governing the Rune UI contribution protocol in the engine:

- `RuneUIView` in `mvge.runes.types` models a navigation item.
- `RuneUICanvasTab` in `mvge.runes.types` models an Adaptive Canvas tab.
- `RuneUIContributions` in `mvge.runes.types` anchors the manifest UI payload.

(GUI methods `AppState.get_contributed_views` and `AppState.get_contributed_canvas_tabs` reside in the `mvge-desktop` companion repository).

<!-- adr-contract: RuneUIView, RuneUICanvasTab, RuneUIContributions -->

## Consequences

- The GUI strictly reflects the installed runtime: no feature surface is visible in the primary navigation unless its backing Rune is installed and enabled.
- Marketplace runes gain first-class UI extensibility, enabling third-party runes to add sidebar views and canvas tabs without modifying core `mvge-gui` source code.
- Standards adherence is preserved: the protocol is fully declarative in `manifest.json`.
