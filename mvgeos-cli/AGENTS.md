# mvgeos-CLI — Agent Instructions

This package implements the CLI entry point (`mvgeos` command): one-shot prompt execution, REPL, TUI, Tome management, and configuration management.

## Package-Specific Conventions

- All code follows the red-green-refactor TDD cycle: write failing test first, then implement
- No inline imports (`await import()`, `import("pkg").Type`). Top-level imports only
- Use `pathlib.Path` for all file path operations — never raw string concatenation
- Mock sync methods with `MagicMock()`, async methods with `AsyncMock()` — mixing causes "coroutine never awaited" warnings

## Testing

```bash
# Run this package's tests
uv run python -m pytest mvgeos-cli/tests/

# Run with coverage
uv run python -m pytest mvgeos-cli/tests/ --cov
```

Test paths follow pattern: `mvgeos-cli/tests/unit/<module>.py` and `mvgeos-cli/tests/integration/<module>.py`

## Key Types

| Type | Purpose |
| --- | --- |
| `app` | Typer entry point (`mvgeos` command) |
| `run_repl` | Streaming REPL with `StreamRenderer` |
| `run_tui` | Full-screen prompt_toolkit TUI |
| `StreamRenderer` | Renders channeled tokens to console |
| `ConsoleSink` / `Sink` | Output sinks for streaming |
| `SlashCompleter` | Tab completion for slash commands |
| `tome_app` | Tome management CLI (`mvgeos tome`) |
| `config_app` | Config CLI (`mvgeos config`) |
| `setup_app` | Setup CLI (`mvgeos setup`) |
| `info_app` | Runtime snapshot inspection CLI (`mvgeos info`) |
| `build_app` | Runtime manifest serialization CLI (`mvgeos build`) |

## Commands

| Command | Purpose |
| --- | --- |
| `mvgeos [prompt]` | Run a prompt, start REPL, or start TUI (`--tui`) |
| `mvgeos setup [check]` | Check missing rune system/Python dependencies |
| `mvgeos setup install [-y]` | Install missing rune system/Python dependencies |
| `mvgeos info [-o <path>]` | Display rich runtime snapshot (spells, runes, config, skills, diags) |
| `mvgeos build [--format json\|summary]` | Serialise resolved runtime manifest |
| `mvgeos tome list` | List all persisted tomes |
| `mvgeos tome show <id> [-f json\|markdown]` | Show Tome contents |
| `mvgeos tome export <id> [-f <fmt>] [-o <file>]` | Export Tome to JSON or Markdown |
| `mvgeos tome create [--cwd <dir>] [--parent <id>]` | Create new Tome |
| `mvgeos tome fork <id> [-l <leaf_id>]` | Fork Tome from a leaf |
| `mvgeos tome verify <id>` | Verify session file and entry integrity |
| `mvgeos config show [--agent-name <name>]` | Show configuration with provenance layers |
| `mvgeos config set <key> <value>` | Set configuration value in agent-scope |
| `mvgeos config get <key>` | Get configuration value with provenance |
| `mvgeos config reset` | Reset configuration to defaults |
| `mvgeos config path` | Show configuration file path |
| `mvgeos config refresh-models [--force]` | Re-fetch the model catalog from OpenRouter; reports ids added, removed, and every id that used to be free and no longer is |

## Dependencies

- `typer` — CLI framework
- `rich` — console rendering
- `prompt_toolkit` — TUI framework
- `mvgeos-core` — loop vocabulary, constants, errors
- `mvgeos-agent` — Mvge, harness, environment
- `mvgeos-provider` — Realm protocol and providers
- `mvgeos-runes` — Rune system

## Architecture

- One-shot path: `main.py` → resolves active agent via `agent_factory.py` (`Mvge`) → runs single prompt
- REPL path: `commands/repl.py` → interactive loop with streaming
- TUI path: `commands/tui.py` — full-screen interface
- All paths use `MvgeHarness` for the core turn loop
- Rune-registered commands and shortcuts are exposed via `runner.get_commands()` and `runner.get_shortcuts()`
