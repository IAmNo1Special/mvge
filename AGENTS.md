# Agent Instructions

## Pair Programming

Work as a pair programmer, not a silent code generator:

- **Think from first principles**: Don't settle for the first solution; question assumptions about the true nature of the problem.
- **Requirements first**: Verify requirements before implementing, especially when writing or changing tests.
- **Research before guessing**: Search the codebase for similar patterns; check online sources for verification.
- **Discuss before deciding**: When strategy is unclear, present options and trade-offs to the user instead of choosing silently.
- **Step-by-step for large changes**: Break down significant refactorings and get confirmation along the way.
- **Challenge assumptions**: If the user states something untrue, correct them directly.

## What to Avoid

- **Overwriting `.env` files** without explicit user confirmation
- **Creating new files** when editing existing ones would suffice
- **Global mutable state** in library code
- **Unnecessary dependencies** — check if existing code suffices first
- **Reflexive regression tests** — not every bug fix earns a test, and a test coupled to implementation details can be worse than none.
  Assert observable behavior, not internals: no private attributes, no patched machinery, no fake objects mirroring the code under test.
  If you catch yourself building scaffolding to observe an internal mechanism, stop — find the user-visible effect to assert on, or skip the test and give the reason in the pull request.
  Before writing a test, read a recent one in the same file and copy its shape.

## Before Claiming a Task Complete

Run the project's tests and linters and review your own diff for unintended scope creep.

## Design Philosophy & Standards

- **Strict Standards Adherence (No Proprietary Polyfills)**: MvgeOS adheres 100% to open, consensus-driven protocols and standards (e.g., the `.agents` protocol, `agentskills.io` specification, standard JSON Schema, OpenRouter APIs, MCP, standard Python typing and PEPs). Never implement proprietary fallbacks, vendor-specific shims, or polyfills for tools that diverge from standard protocols (e.g., strictly discover and inject `AGENTS.md`, never `CLAUDE.md`). External tools and ecosystems align with the open standard, not the other way around.
- **Protocol Boundary Pattern (Standard at the Boundary, Persona Inside)**: MvgeOS strictly enforces open, standard protocols at all system boundaries (filesystem directory names, network wire formats, HTTP headers, CLI arguments, and API payloads), while reserving MvgeOS domain terminology (Mvge, Spell, Mana, Realm, Tome, Rune, Sigil, Summoner) for internal Python abstractions, user-facing persona, and presentation.
  - **Filesystem boundaries**: Use standard protocol directory names — `~/.agents/sessions/` (never `tomes/`), `~/.agents/extensions/` (never `runes/`), `~/.agents/skills/`, `~/.agents/models.json`, `~/.agents/mcp.json`.
  - **Wire boundaries**: Use standard payload fields — `type: "session"` (never `"tome"`), `tools: [...]` (never `"spells"`), `tool_calls: [...]`, `usage: { prompt_tokens, completion_tokens }` (never `"mana"`).
  - **CLI boundaries**: Persona commands are the exclusive CLI interface (`mvgeos tome`, `mvgeos rune`).
- **Zero Backward Compatibility Burden**: Prioritize clean, greenfield architecture and modern standards over backward compatibility. Never retain legacy shims, deprecated code paths, or obsolete conventions.
- **Check the marketplace before claiming something is unimplemented**: Rune capabilities live in `mvge-marketplace`, not in this repository. Before asserting that some behaviour does not exist, grep `mvge-marketplace/runes/` for a Rune that already owns it. `steering-bridge` is the reference implementation for resolving the `.agents` layers and for Sigil payload discipline.
  - Read engine source and a Rune before concluding a gap. The failure mode is symmetrical in both directions: assuming a gap that a Rune already closed, and assuming a Rune does something the engine never asked it to.
  - Cite line numbers only for code in this repository. A `file:line` reference into `mvge-marketplace` will be stale the moment that repository changes, which is how a corrected claim in ADR-0014 came to assert work a Rune had already done.
- **Records must be checkable, not merely current**: An ADR, issue, or doc that describes code is a claim about code, and nothing notices when it stops being true. `mvgeos-core/scripts/check_adr_contract.py` enforces this for ADRs by asserting every symbol an ADR's `## Contract` section names still exists. When a decision's mechanism is deliberately replaced, amend or supersede the record **in the same commit** — never let the two drift.

## Development Rules

MVGEOS IS A CUSTOM IMPLEMENTATION OF THE ARCHITECTURE INTRODUCED BY [Pi](https://github.com/earendil-works/pi) CHECK OUT IT'S SOURCE CODE FOR A REFRESHER ON HOW WE ARE MAKING MVGEOS

## Conversational Style

- Keep answers short and direct
- No emojis in commits, issues, PR comments, or code (`README.md` files are exempt — emojis are welcome in READMEs)
- No fluff or cheerful filler text
- Technical prose only

## Code Quality

- Read files in full before wide-ranging changes
- No inline imports (`await import()`, `import("pkg").Type`). Top-level imports only
- Use `pathlib.Path` for all file path operations — never raw string concatenation
- Follow MvgeOS terminology (Mvge, Spell, Realm, Mana, Mana Pool, Tome, Invocation, Summoner, Rune, Skill, Channeling, Sigil, Contemplation, Leaf, Fork)
- All code follows the red-green-refactor TDD cycle: write failing test first, then implement

## Commands

- Canonical test invocations live in the [Testing Standards Charter](TESTING.md); always use the module form (`uv run python -m pytest ...`)
- After code changes, run the full suite with coverage (full output, no tail)
- Never run `uv run build` or `uv run test` unless requested
- If you create or modify a test file, run it and iterate until it passes
- **Shell chaining follows the host**: `&&` on POSIX shells; on Windows use `;` (`&&` needs PowerShell 7+). `MvgeEnvironment` injects the correct rule per platform at runtime, so do not hardcode one shell's syntax in prompts or code.

## Dependency Management

- Use `uv` exclusively — never `pip`
- `uv sync` for installation, `uv add` for adding dependencies
- `uv.lock` is the source of truth
- `pyproject.toml` is the single package configuration source

## Python Conventions

- Google Python Style Guide
- `ruff` for linting and formatting (line-length 88)
- `mypy` strict mode for all packages
- Pre-commit hooks via `.pre-commit-config.yaml` (ruff, mypy, pytest)
- 88-character line length limit

## Testing

Testing standards are defined in the [Testing Standards Charter](TESTING.md) — the single authority for how MvgeOS is tested. Its hard requirements:

- **Two tiers only**: unit (`<package>/tests/unit/`) and integration (`<package>/tests/integration/`), mirroring source structure, flat file naming, never `__init__.py` in test dirs
- **Hermetic suites**: no network, no real secrets, no wall-clock dependence; external seams mocked at dependency boundaries (HTTP clients, sleep, environment)
- **Coverage floor**: enforced on bare full-suite runs via pyproject, measured over first-party source only (branch coverage); pre-commit applies no coverage gate
- **Skip policy**: env-guarded skips require a justified skip reason and are expected to be rare

## Debugging

- **RuntimeWarning "coroutine never awaited"** = async mock used on sync method. Mock sync methods with `MagicMock()`, async methods with `AsyncMock()`
- **CoverageWarning "couldn't parse"** = test creates temp files outside project; add to `[tool.coverage.run] omit`
- **TUI tests fail silently** = check indentation of early returns in rendering loops
- **Footer/status-line assertions must match a model-ID prefix**, not the full slug — `_fit_footer` truncates to console width

## Architecture

Mvge is a single unified Python package under `src/mvge/` (following the `google/adk-python` architectural style):

| Submodule | Purpose |
| --- | --- |
| `mvge.core` | Canonical loop vocabulary: abort, invocations, spells, events, pure turn loop (zero dependencies) |
| `mvge.agent` | Mvge class, session lifecycle (MvgeHarness, MvgeState), environment, spell coercion, installer |
| `mvge.provider` | Realm protocol + model registry; concrete Realms ship as marketplace runes, including the default `opencode` one |
| `mvge.tome` | JSONL session persistence with file locking |
| `mvge.runes` | Rune/Extension manifest, loader, sigil hooks |
| `mvge.cli` | CLI entry point (`mvge` command) |

The desktop GUI application (`mvgeos-gui`) has been extracted to its own standalone companion repository at `/mnt/storage/projects/mvge-desktop`.

Config follows dotagents protocol at `~/.agents/`.
Mvges (agents) are installable via marketplace (`mvge mvge install <name>`) at `~/.agents/agents/`.
Rune/Extension manifests at `~/.agents/extensions/`.

**Prompt & Agent Architecture**:
Follows the Two-Layer Invariant Scaffolding Pattern (ADR 0009). Layer 1 (persona) is defined in colocated `system_prompt/SYSTEM.md`. Layer 2 (spells, environment, UTC date/time, PowerShell rules, `<project_context>`, and self-modification pointers) is dynamically rendered by `MvgeEnvironment`.
Agents use zero-config auto-discovery: an Mvge named for its own directory auto-discovers spells from `spells/`, `.env`, and colocated assets (`system_prompt/`, `skills/`, `runes/`). Naming no agent at all adopts the single installed one (`resolve_active_agent_name`).

**`_build_spells()` interface**: must use `self._runner` (not `self._state.rune_runner`) and return all spell types (rune spells from enabled runes only + builtin/discovered spells). **Watcher cleanup**: watchers are owned by `RuneLifecycle` — stop them via `lifecycle.shutdown()` (BaseMvge.close() does this; never reference a singular `_watcher`).

**Terminology updates**: `ProviderRegistry` → `RealmRegistry` (Realm = provider abstraction). `Model.provider` field removed — provider derived from model ID prefix (e.g., `nvidia/nemotron` → provider = "nvidia").

## Key Types (MvgeOS Terminology)

| Concept | Type |
| --- | --- |
| Agent | Mvge |
| User | Summoner |
| Tools | Spells |
| Tokens | Mana |
| Context window | Mana Pool |
| Provider | Realm |
| Session | Tome |
| Message | Invocation |
| Streaming | Channeling |
| Extension | Rune |
| Callback | Sigil |
| Reasoning effort | Contemplation |

## Changelog & Releases

- **git-cliff** configured at `cliff.toml` — generates CHANGELOG.md from conventional commits
- **Automated version bumps**: `bump` job in `.github/workflows/ci.yml` — runs after `test`/`coverage`/`lint` pass on pushes to `main`, scans commits since last tag (`feat`→minor, `fix`/`perf`→patch, `!`/`BREAKING CHANGE`→major; `docs`/`test`/`ci`/etc. produce no bump), bumps package version via `uv version --bump` + `uv.lock`, and pushes `chore(release): vX.Y.Z` + `v*` tag, then dispatches `release.yml` and `publish.yml` itself (the tag push uses `GITHUB_TOKEN`, so GitHub's loop guard stops it triggering either workflow on its own)
- **Release workflow**: `.github/workflows/release.yml` — triggers on `v*` tags or manual dispatch
- **Publish workflow**: `.github/workflows/publish.yml` — triggers on `v*` tags or manual dispatch, and the `bump` job in `ci.yml` also dispatches it explicitly against the tag on every version bump, because a `GITHUB_TOKEN` tag push does not fire workflows; uploads `mvge` to PyPI using trusted publishing (OIDC), then proves `uvx mvge --help` from a checkout-free runner and runs one real task through `scripts/verify_install.py`. `scripts/check_publish.py` is the preflight gate: it verifies metadata and package readiness. Full procedure, secret setup, and the migration path: `docs/agents/releasing.md`
- **Conventional commits required**: `feat(scope):`, `fix(scope):`, `perf(scope):`, `refactor(scope):`, `docs(scope):`, `test(scope):`, `build(scope):`, `ci(scope):`, `chore(scope):`, `revert(scope):`
- **Breaking changes**: include `BREAKING CHANGE:` in commit body
- **Commands**:
  - `uv run git-cliff --config cliff.toml --unreleased` — preview unreleased changes
  - `uv run git-cliff --config cliff.toml --output CHANGELOG.md` — generate full changelog
  - `git tag vX.Y.Z && uv run git-cliff --config cliff.toml --tag vX.Y.Z --output CHANGELOG.md` — release

## Agent skills

### Issue tracker

GitHub Issues via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single context — root `CONTEXT.md`. See `docs/agents/domain.md`.
