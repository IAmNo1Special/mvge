# ADR 0023: Workspace Trust Gate, Per-Rune Disposable Scopes, and Universal Gateway-Last Active Discovery+

## Status

Accepted

## Date

2026-10-19

## Context

Three structural vulnerabilities and ordering defects exist in how `mvge-runes` and `mvge-agent` discover, isolate, and gate extensions and capabilities:

1. **Untrusted Project Code Auto-Execution**: `resolve_rune_layers` and `load_runes_from_paths` automatically import and execute `{cwd}/.agents/extensions/*/rune.py` and `{cwd}/.agents/spells/*.py` on startup before any `ApprovalGate` is consulted. Cloning an untrusted repository and running `mvge` inside it executes arbitrary Python at load time.
2. **Undisposed Rune Side-Effects on Unload**: `RuneAPI` does not scope registrations (`register_spell`, `on`, `register_command`, `register_provider`, `register_spell_gate`) to a disposable Rune lifecycle handle, forcing Runes like `mcp-bridge` to mutate `api._runner._spells` privately on shutdown.
3. **Broken `seeker` Active Discovery+ Gateway Ordering & Coverage**:
   - `seeker` implements a 3-stage Active Discovery+ pipeline (MCP-Zero `arXiv:2506.01056` + DCI `ripgrep` `arXiv:2605.05242` + NLT `YES`/`NO` grid `arXiv:2607.03953`) that reduces baseline per-turn schema Mana by 96.5%–99.4% (~650 Mana/turn vs. 18,700–53,600 Mana/turn) and prevents tool-selection accuracy collapse past 20–30 tools.
   - However, `load_manifests` iterates in raw filesystem inode order rather than guaranteeing `spell_gateway: true` Runes load last; `RuneRunner.widen_global_allowlist` does not bump `_spell_version` and `MvgeHarness._prepare_next_turn` does not refresh `LoopContext.spells` (causing same-turn `SpellNotFoundError` after `tool_search`); `tool_search` is blind to all registered Rune and builtin spells; `skills-bridge` injects the full `<available_skills>` XML even when a gateway is active; and `mcp-bridge` / `seeker` do not share `.agents/mcp.json` discovery.

## Decision

1. **Kernel Workspace Trust Gate (`mvge-runes` & `mvge-core`)**:
   - Before loading any executable artifact from `Scope.PROJECT` (`{cwd}/.agents/extensions/`, `{cwd}/.agents/spells/`), the engine verifies Workspace Trust in `~/.agents/approval/trust.json`.
   - Adopting `huggingface/tau`'s crash-safe persistence model enhanced with content hashing: workspace paths are canonicalized (`fcntl(fd, F_GETPATH)` on macOS to prevent case-insensitive APFS alias bypasses, `os.path.normcase` on Windows), writes use a fail-closed write-ahead undo journal (`trust.json.pending`), and trust entries bind `(canonical_workspace_path, sha256_tree_digest_of_project_dot_agents)` with nearest-ancestor resolution (`trust-exact`, `trust-parent`, `trust-run`, `decline-exact`, `decline-run`).
   - If a `git pull` mutates any executable file or manifest under `{cwd}/.agents/`, the digest changes and trust is re-requested. In non-interactive mode without prior trust (or when declined), `Scope.PROJECT` code is skipped with a `LOAD_WARNING` diagnostic while `Scope.USER` and `Scope.AGENT` layers load normally.

2. **Per-Rune Disposable Registration Scopes (`mvge-runes`)**:
   - Following `deepseek-harness` (Cordis), every registration made through a `RuneAPI` instance (`register_spell`, `on`, `register_command`, `register_provider`, `register_realm_factory`, `register_spell_gate`) is tracked in a per-Rune disposable scope and automatically unrolled when that Rune is unloaded, disabled, or replaced during `reload()`.

3. **Gateway-Last Loading & Universal Active Discovery+ (`mvge-runes`, `mvge-agent`, `seeker`)**:
   - **Deterministic Gateway-Last Order**: `load_manifests` sorts entries deterministically by name, and `load_runes_from_paths` / `RuneRunner.load_rune_loads` partitions manifests so all standard Runes load first across `USER -> AGENT -> PROJECT` and any `"spell_gateway": true` Rune (`seeker`) is guaranteed to load last. `RuneRunner.refresh_runes` preserves gateway designation across hot-reloads.
   - **Unified 4-Verb Gateway Surface & Strict `search -> activate -> execute` State Machine (`search`, `activate`, `execute`, `unload`)**:
     - `seeker` exposes four orthogonal lifecycle meta-spells (~550 Mana total) across all capability kinds (`"spell"`, `"skill"`, `"mcp"`) and enforces a strict per-Tome discovery ledger (`discovered_spells`, `discovered_skills`, `discovered_mcps`, and `activated_skills`, rehydrated from Tome history on session resume):
       1. `search(query: str, kinds: list[Literal["spell", "skill", "mcp"]] = ["spell", "skill", "mcp"], domain: str | None = None, max_results: int = 5)` (`SpellExecutionMode.PARALLEL`): searches Spells (`api.get_discoverable_spells()` + on-disk spells), Skills (`RuneRunner.skill_catalog` + DCI `rg`), and MCP servers (`.agents/mcp.json` layers) simultaneously in a single DCI + NLT pass. Returns compact candidate cards (`name`, `kind`, `description`, and resource/tool summary) and records every returned candidate in `discovered_spells`, `discovered_skills`, or `discovered_mcps`.
       2. `activate(spells: list[str] = [], skills: list[str] = [], mcp_servers: list[str] = [], skill_resource: str | None = None)` (`SpellExecutionMode.PARALLEL`): **requires every requested capability to have been returned by a prior `search` in the current Tome** (refusing un-searched names with an explicit instruction to call `search` first, preventing the model from guessing `bash` from pretraining memory and bypassing Active Discovery+). For discovered Spells, widens `_global_spell_allowlist` and returns full JSON Schemas; for discovered Skills, records the skill in `activated_skills` and progressively discloses the full `SKILL.md` `<skill_content>` body and bundled `scripts/` + `references/` manifest (or reads a specific `skill_resource`); for discovered MCP servers, connects the server (with no `max_connections` ceiling), registers its `mcp_<server>_*` spells, and widens `_global_spell_allowlist`.
       3. `execute(skill_name: str, script: str, args: list[str] = [])` (`SpellExecutionMode.SEQUENTIAL`): **requires `skill_name` to be in `activated_skills`** (ensuring the Mvge has searched for the Skill and read its `SKILL.md` instructions before invoking any bundled script) and executes `scripts/<script>` via `SpellContext.backend` (`ExecutionBackend`).
       4. `unload(spells: list[str] = [], skills: list[str] = [], mcp_servers: list[str] = [])` (`SpellExecutionMode.SEQUENTIAL`): narrows Spells from `_global_spell_allowlist`, disconnects named MCP servers and unregisters their `mcp_<server>_*` spells, and removes named Skills from `activated_skills` (marking their `<skill_content>` invocations eligible for pruning during `CONTEXT_TRANSFORM`).
   - **100% Non-Gateway Spell Digestion (Including `coding_mvge` Core Spells)**: When `seeker` is active, **all** non-gateway spells — including `coding_mvge`'s 9 core spells (`bash`, `read`, `write`, `edit`, `find`, `grep`, `list_files`, `read_url`, `search_web`), all 27+ first-party Rune spells, and all MCP tools — are digested behind `seeker`'s 4 gateway meta-spells (`{"search", "activate", "execute", "unload"}`).
   - **Same-Turn Allowlist Widening & Automatic Idle-Turn TTL Eviction**:
     - `RuneRunner.widen_global_allowlist`, `RuneRunner.narrow_global_allowlist`, and `RuneRunner.set_global_spell_allowlist` increment `_spell_version`, and `MvgeHarness` refreshes `LoopContext.spells` in `prepare_next_turn` so unlocked spells are callable in the next step of the same turn.
     - `RuneRunner.narrow_global_allowlist(names)` and `RuneAPI.narrow_global_allowlist(names)` remove unlocked spells from `_global_spell_allowlist` while refusing to remove pinned gateway meta-spells.
     - On `SigilHook.TURN_END`, any non-gateway spell uncast for `idle_turn_ttl` consecutive turns (default `8`) is automatically evicted via `narrow_global_allowlist`; when all tools from a connected MCP server are evicted, its `MCPConnector` is closed automatically.
   - **Shared Spell, Skill, and MCP Catalogs**: `RuneAPI` exposes `has_spell_gateway` and `get_discoverable_spells()`. `skills-bridge` populates `RuneRunner.skill_catalog` during `RESOURCES_DISCOVER` and suppresses `<available_skills>` prompt injection when `has_spell_gateway` is `True`. `seeker` queries `get_discoverable_spells()`, `RuneRunner.skill_catalog`, and all `.agents/mcp.json` layers, and `heal-my-goap` is removed from `mvge-marketplace`.

## Contract

The existing engine symbols governing layer resolution, manifest loading, gateway allowlists, and skill cataloging live in `mvge-core`, `mvge-runes`, and `mvge-agent`:

- `resolve_rune_layers` and `approval_dir` in `mvge_core.layers` resolve the `.agents` extension layers and `~/.agents/approval/` directory.
- `load_manifests` and `load_runes_from_paths` in `mvge_runes.loader` discover and load Rune manifests.
- `RuneRunner.load_rune_loads`, `RuneRunner.widen_global_allowlist`, `RuneRunner.set_global_spell_allowlist`, and `RuneRunner.get_all_registered_spells` manage Rune lifecycle and gateway allowlists.
- `RuneAPI.widen_global_allowlist` and `SkillCatalog` in `mvge_runes` expose allowlist widening and skill cataloging to Runes.

<!-- adr-contract: resolve_rune_layers, approval_dir, load_manifests, load_runes_from_paths, RuneRunner.load_rune_loads, RuneRunner.widen_global_allowlist, RuneRunner.set_global_spell_allowlist, RuneRunner.get_all_registered_spells, RuneAPI.widen_global_allowlist, SkillCatalog -->

## Consequences

- Cloning an untrusted repository can no longer execute `{cwd}/.agents/` code without explicit Summoner trust pinned to the directory's content digest.
- Enabling `seeker` provides a uniform ~650-Mana baseline tool surface across all Mvges, Runes, Skills, and MCP servers with zero `SpellNotFoundError` race conditions on same-turn tool unlocking.
