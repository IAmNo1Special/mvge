# mvgeos-GUI — Agent Instructions

This package implements the native desktop graphical interface for MvgeOS powered by NiceGUI and PyWebView. It provides a 1:1 visual experience matching Google Antigravity IDE for multi-turn Summoner interactions, live Spell tracking, Git diff inspection, and Tome navigation.

## Package-Specific Conventions

- All code follows the red-green-refactor TDD cycle: write failing test first, then implement
- No inline imports (`await import()`, `import("pkg").Type`). Top-level imports only
- Use `pathlib.Path` for all file path operations — never raw string concatenation
- Mock sync methods with `MagicMock()`, async methods with `AsyncMock()` — mixing causes "coroutine never awaited" warnings

## Testing

```bash
# Run this package's tests
uv run python -m pytest mvgeos-gui/tests/

# Run with coverage
uv run python -m pytest mvgeos-gui/tests/ --cov
```

Test paths follow pattern: `mvgeos-gui/tests/unit/<module>.py` and `mvgeos-gui/tests/integration/<module>.py`

## Key Types & Services

| Type | Purpose |
| --- | --- |
| `AppState` | Central reactive UI state (project, conversations, streaming tokens, artifacts, git diff, settings) |
| `AgentService` | Async execution bridge running `Mvge` and `MvgeHarness`, consuming event bus |
| `TomeService` | Session persistence, conversation loading, and branch forking via `TomeHandleFactory` |
| `ConfigService` | Provider and workspace configuration manager |
| `GitDiffService` | Workspace Git diff calculation, staged/unstaged changes, and diff line stats |
| `AutocompleteService` | Fuzzy autocomplete provider for `@` mentions (files, skills, subagents) and `/` slash commands |
| `init_app` | NiceGUI layout builder assembling shell, themes, and reactive watchers |
| `main` | CLI entry point (`mvgeos-gui`) supporting native PyWebView window and `--web` mode |

## UI Architecture

1. **Left Navigation (`sidebar.py`, `home_screen.py`)**:
   - Project selector and workspace switching
   - Conversation history grouped by relative time badges
   - Settings access (app + workspace modals) and scheduled tasks

2. **Center Viewport (`shell.py`, `chat_panel.py`)**:
   - **Active State (`chat_panel.py`)**: Message bubbles, channeling responses, collapsible step cards (`step_cards.py`, `message_parts.py`), inline composer with `@`/`/` autocomplete popup, attachment chips, model switcher, and cancel/stop button
   - **Side panels**: file tree (`file_tree.py`) and diff viewer (`diff_viewer.py`)
   - **Terminal overlay** (`terminal_panel.py`)

3. **Overlays (mounted in `shell.py`)**:
   - Command palette (`command_palette.py`)
   - Artifact drawer (`artifact_drawer.py`)
   - Application settings modal (`settings_modal.py`) and Project Workspace settings modal (`workspace_settings_modal.py`), backed by `config_service.py`
   - Diff review modal (`diff_review.py`)

4. **Right Rail & Status**:
   - Review rail (`review_rail.py`) with changed files
   - Status bar (`status_bar.py`) reflecting `AppState.mvge_status` (idle / channeling / working), model, and Mana usage

## Dependencies

- `nicegui` — Python UI framework
- `pywebview` — Native OS window container without browser chrome
- `pathspec` — Path and pattern matching
- `mvgeos-agent` — Core agent loop, types, and harness
- `mvgeos-provider` — Realm protocol and providers
- `mvgeos-tome` — JSONL session persistence
- `mvgeos-runes` — Rune extension system
- `mvgeos-cli` — CLI and credential helpers
