# ADR 0025: Dual-Crown Microkernel Positioning, First-Run Marketplace Profiles, and Headless Stdio RPC Mode

## Status

Accepted

## Date

2026-10-19

## Context

MvgeOS competes across two distinct categories of agent software:
1. **Interactive Coding Harnesses & Autonomous Agents** (`anomalyco/opencode`, `earendil-works/pi`, `huggingface/tau`, `nousresearch/hermes-agent`), where Summoners expect a single `uvx mvgeos` command to work out of the box with coding spells, `.agents` steering, skills, approval governance, and IDE/multi-client attach.
2. **Programmable Agent SDKs & Evaluation Harnesses** (`google/adk-python`, `huggingface/smolagents`, `deepseek-ai/deepseek-harness`), where engineers expect a small, unopinionated Python microkernel free of bundled personas or vendor bloat.

Today, running `uvx mvgeos` on a clean machine offers to install `opencode-realm` when `NoRealmRegisteredError` is raised, but installs no Mvge (`coding_mvge`), no coding Spells, no `.agents` Capability Bridges (`steering-bridge`, `skills-bridge`), and no `ApprovalGate` (`approval-rune`), leaving a cold-start user with a spell-less `"default-mvge"` whose casts fail closed (`reason="no_gate"`). Additionally, `mvgeos-cli` and `mvgeos-gui` host `Mvge` exclusively in-process with no headless bidirectional RPC wire protocol for IDEs, subprocess orchestrators, or messaging gateways.

## Decision

1. **Dual-Crown Microkernel + Flagship Marketplace Profiles (`mvgeos-cli`, `mvgeos-agent`, `mvgeos-marketplace`)**:
   - `mvgeos` remains a strict microkernel shipping zero concrete Mvges, Realms, coding Spells, or Capability Bridges in its wheel distributions.
   - First-run interactive startup (when `agents_dir()` has no installed Mvge) and `mvgeos setup --profile <name>` bootstrap a named **Profile** from `mvgeos-marketplace` (with an interactive TTY confirmation prompt and `--bootstrap` / `--no-bootstrap` flags for CI):
     - `minimal`: `coding_mvge`, `openrouter-realm`, `opencode-realm`, `approval-rune`, `steering-bridge`.
     - `coding` (**default bootstrap profile**): `minimal` + `seeker`, `skills-bridge`, `session-search`, `session-title`, `lsp-bridge`, `checkpoint-bridge`, `submvge-bridge`.
     - `autonomous`: `coding` + `okf-bridge`, `adr-bridge`, `mcp-bridge`, `selfmod-bridge`, `skill-evolution`, `opentelemetry-bridge`, `codeact-rune`, `eval-bridge`.

2. **Headless Bidirectional Stdio RPC Mode (`mvgeos --mode rpc` in `mvgeos-cli`)**:
   - Add `--mode rpc` to `mvgeos-cli` implementing a strict `\n`-delimited JSONL stdio protocol compatible with `earendil-works/pi` (`--mode rpc`):
     - Inbound `stdin` commands: `prompt` (returning an immediate preflight `{ disposition }` response while turn events stream asynchronously), `steer`, `follow_up`, `abort`, `get_state`, `set_model`, `set_thinking_level`, `compact`, `fork`, `switch_session`, `get_tree`, `get_commands`, and `extension_ui_response`.
     - Outbound `stdout` frames: `response`, `O(1)` delta `MvgeEvent` frames (stripping cumulative `partial` message snapshots from `message_update`), and `extension_ui_request` (bound directly to `RuneRunner.set_approval_presenter` with `AbortSignal` and timeout cleanup).
   - Network daemons, multi-platform messaging gateways (Telegram, Discord, Slack, Matrix), cron schedulers, and trajectory/rubric evaluation suites (`adk eval` / ShareGPT RL export) live in `mvgeos-marketplace` as `gateway-bridge`, `cron-bridge`, and `eval-bridge` Runes built on top of the kernel's RPC and Tome seams.

## Contract

The existing engine symbols that anchor first-run resolution, marketplace installation, and CLI entry live in `mvgeos-core`, `mvgeos-agent`, `mvgeos-runes`, and `mvgeos-cli`:

- `agents_dir` and `extensions_dir` in `mvgeos_core.layers` resolve the global installation targets.
- `resolve_active_agent_name` and `install_mvge` in `mvgeos_agent` resolve and install Mvge packages from the marketplace.
- `install_rune` in `mvgeos_runes.installer` installs Rune packages and their dependencies.
- `MvgeosGroup` and `main` in `mvgeos_cli.main` own the CLI entry point and mode dispatch.

<!-- adr-contract: agents_dir, extensions_dir, resolve_active_agent_name, install_mvge, install_rune, MvgeosGroup, main -->

## Consequences

- `uvx mvgeos` delivers an immediate, `opencode`- and `pi`-beating coding environment on first launch without violating the microkernel package boundary.
- External IDE plugins, CI runners, sub-Mvge orchestrators, and messaging gateways can drive `mvgeos --mode rpc` over standard JSONL stdio without in-process Python coupling.
