# ADR 0025: Dual-Crown Microkernel Positioning, First-Run Marketplace Profiles, and Headless Stdio RPC Mode

## Status

Accepted

## Date

2026-10-19

## Context

Mvge competes across two distinct categories of agent software:
1. **Interactive Coding Harnesses & Autonomous Agents** (`anomalyco/opencode`, `earendil-works/pi`, `huggingface/tau`, `nousresearch/hermes-agent`), where Summoners expect a single `uvx mvge` command to work out of the box with coding spells, `.agents` steering, skills, approval governance, and IDE/multi-client attach.
2. **Programmable Agent SDKs & Evaluation Harnesses** (`google/adk-python`, `huggingface/smolagents`, `deepseek-ai/deepseek-harness`), where engineers expect a small, unopinionated Python microkernel free of bundled personas or vendor bloat.

Today, running `uvx mvge` on a clean machine offers to install `opencode-realm` when `NoRealmRegisteredError` is raised, but installs no Mvge (`coding_mvge`), no coding Spells, no `.agents` Capability Bridges (`steering-bridge`, `skills-bridge`), and no `ApprovalGate` (`approval-rune`), leaving a cold-start user with a spell-less `"default-mvge"` whose casts fail closed (`reason="no_gate"`). Additionally, `mvge-cli` and `mvge-gui` host `Mvge` exclusively in-process with no headless bidirectional RPC wire protocol for IDEs, subprocess orchestrators, or messaging gateways.

## Decision

1. **Dual-Crown Microkernel + Flagship Marketplace Profiles (`mvge-cli`, `mvge-agent`, `mvge-marketplace`)**:
   - `mvge` remains a strict microkernel shipping zero concrete Mvges, Realms, coding Spells, or Capability Bridges in its wheel distributions.
   - First-run interactive startup (when `agents_dir()` has no installed Mvge) and `mvge setup --profile <name>` bootstrap a named **Profile** from `mvge-marketplace` (with an interactive TTY confirmation prompt and `--bootstrap` / `--no-bootstrap` flags for CI):
     - `minimal`: `coding_mvge`, `openrouter-realm`, `opencode-realm`, `approval-rune`, `steering-bridge`.
     - `coding` (**default bootstrap profile**): `minimal` + `seeker`, `skills-bridge`, `session-search`, `session-title`, `lsp-bridge`, `checkpoint-bridge`, `submvge-bridge`, `codeact-rune`.
     - `autonomous`: `coding` + `okf-bridge`, `adr-bridge`, `mcp-bridge`, `selfmod-bridge`, `skill-evolution`, `opentelemetry-bridge`, `eval-bridge`.

2. **Headless Bidirectional Stdio RPC Mode (`mvge --mode rpc` in `mvge.cli`)**:
   - Add `--mode rpc` to `mvge` implementing a strict `\n`-delimited JSONL stdio protocol compatible with `earendil-works/pi` (`--mode rpc`):
     - Inbound `stdin` commands: `prompt` (returning an immediate preflight `{ disposition }` response while turn events stream asynchronously), `steer`, `follow_up`, `abort`, `get_state`, `set_model`, `set_thinking_level`, `compact`, `fork`, `switch_session`, `get_tree`, `get_commands`, and `extension_ui_response`.
     - Outbound `stdout` frames: `response`, `O(1)` delta `MvgeEvent` frames (stripping cumulative `partial` message snapshots from `message_update`), and `extension_ui_request` (bound directly to `RuneRunner.set_approval_presenter` with `AbortSignal` and timeout cleanup).
   - Network daemons, multi-platform messaging gateways (Telegram, Discord, Slack, Matrix), cron schedulers, and trajectory/rubric evaluation suites (`adk eval` / ShareGPT RL export) live in `mvge-marketplace` as `gateway-bridge`, `cron-bridge`, and `eval-bridge` Runes built on top of the kernel's RPC and Tome seams.

## Contract

The existing engine symbols that anchor first-run resolution, marketplace installation, and CLI entry live in `mvge.core`, `mvge.agent`, `mvge.runes`, and `mvge.cli`:

- `agents_dir` and `extensions_dir` in `mvge.core.layers` resolve the global installation targets.
- `resolve_active_agent_name` and `install_mvge` in `mvge.agent` resolve and install Mvge packages from the marketplace.
- `install_rune` in `mvge.runes.installer` installs Rune packages and their dependencies.
- `MvgeGroup` and `main` in `mvge.cli.main` own the CLI entry point and mode dispatch.

<!-- adr-contract: agents_dir, extensions_dir, resolve_active_agent_name, install_mvge, install_rune, MvgeGroup, main -->

## Consequences

- `uvx mvge` delivers an immediate, `opencode`- and `pi`-beating coding environment on first launch without violating the microkernel package boundary.
- External IDE plugins, CI runners, sub-Mvge orchestrators, and messaging gateways can drive `mvge --mode rpc` over standard JSONL stdio without in-process Python coupling.
