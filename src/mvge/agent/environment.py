from __future__ import annotations

import logging
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mvge.agent.agent_name import validate_agent_name
from mvge.agent.config_manager import ConfigLayer, ConfigManager, ConfigValue
from mvge.agent.snapshot import RuntimeSnapshot, assemble_snapshot
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.events import PromptSource as PromptSource
from mvge.core.events import QueueMode
from mvge.core.layers import (
    ResolvedLayer,
    Scope,
    agent_dir,
    global_agents_dir,
    resolve_rune_layers,
)
from mvge.core.spells import MvgeSpell
from mvge.provider.model_registry import resolve_default_model
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import (
    BeforeMvgeStartData,
    Diagnostic,
    RuneManifest,
    SigilHook,
    SkillDiagnostic,
    SkillManifest,
    SpellDefinition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedPrompt:
    """A system prompt resolved through the discovery chain.

    The ``source`` and ``path`` expose which layer won for runtime
    introspection.
    """

    text: str
    source: PromptSource
    path: Path | None = None


_VALID_CONTEMPLATION_LEVELS = ("none", "low", "medium", "high")


@dataclass(frozen=True)
class AgentConfig:
    """Fully coerced agent configuration values."""

    model_id: str
    temperature: float
    max_tokens: int
    contemplation_level: str
    contemplation_budget: int | None
    exclude_contemplation: bool
    queue_mode: QueueMode
    spell_names: list[str] | None
    runes_paths: list[Path]
    rune_layers: list[ResolvedLayer]
    realm_preference: str = ""


_SYSTEM_PROMPT_BODY = (
    "You are a Mvge (pronounced 'mage') (AI agent) in the Mvge system equipped with "
    "spells to assist your Summoner (user). You have access to spells to interact with "
    "your current environment. Be direct, concise, and technical."
)

DEFAULT_SYSTEM_PROMPT = _SYSTEM_PROMPT_BODY

SYSTEM_MD_FILENAME = "SYSTEM.md"
APPEND_SYSTEM_MD_FILENAME = "APPEND_SYSTEM.md"

DEFAULT_SYSTEM_MD = _SYSTEM_PROMPT_BODY + "\n"


def get_environment_info(cwd: str | Path | None = None) -> list[str]:
    """Return OS, terminal environment, working directory, and UTC timestamp."""
    os_name = platform.system()
    os_release = platform.release()
    arch = platform.machine()

    lines = [
        f"- Operating System: {os_name} {os_release} ({sys.platform}, {arch})",
    ]

    if sys.platform == "win32":
        lines.append("- Shell: PowerShell (powershell.exe)")
        lines.append(
            "- Shell Syntax: Use PowerShell syntax and cmdlets. Chain commands "
            "with ';' rather than '&&'. Use non-interactive flags (e.g. "
            "'Get-Date' instead of interactive 'date')."
        )
    else:
        shell = os.environ.get("SHELL", "/bin/bash")
        lines.append(f"- Shell: {shell}")

    effective_cwd = str(cwd) if cwd else str(Path.cwd())
    lines.append(f"- Working Directory: {effective_cwd}")

    now_utc = datetime.now(UTC).strftime("%A, %B %d, %Y, %I:%M %p UTC")
    lines.append(f"- Current Date & Time: {now_utc}")

    return lines


def _read_text(path: Path, strip_frontmatter: bool = False) -> str:
    """Read a file's text, stripped, returning empty on error.

    If ``strip_frontmatter`` is True, strips leading YAML frontmatter
    (delimited by '---') per the .agents Protocol specification.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("Could not read file at %s", path)
        return ""

    if strip_frontmatter and text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2].strip() if len(parts) >= 3 else ""

    return text


def render_prompt(
    body: str,
    spells: Sequence[str] = (),
    cwd: str | Path | None = None,
    append_text: str = "",
) -> str:
    """The single rendering every entry point goes through.

    Renders Layer 2 invariant scaffolding only: the persona body, active
    spells, and environment info (OS, shell, date/time UTC, PowerShell
    rules).

    Repository steering (AGENTS.md discovery, progressive disclosure, and
    ``<project_context>`` injection) is owned by the ``steering-bridge``
    marketplace rune, which contributes its section dynamically via the
    ``BEFORE_MVGE_START`` sigil. Self-modification pointers are owned by
    the ``selfmod-bridge`` marketplace rune (``teach``/``scaffold_skill``/
    ``scaffold_spell``/``snapshot``/``restore``).
    """
    parts: list[str] = []
    if body:
        parts.append(body)

    spells_list = list(spells)
    spell_list_str = (
        "\n".join(f"  - {s}" for s in spells_list) if spells_list else "  (none)"
    )
    parts.append(
        f"\nActive spells:\n{spell_list_str}\n\n"
        "Spells are executable tools occupying the functions namespace "
        "(bash, read, write, etc.). Use them to perform actions in your "
        "environment. They are distinct from declarative skills (SKILL.md)."
    )

    parts.append("\nEnvironment:")
    parts.extend(get_environment_info(cwd))

    if append_text:
        parts.append(f"\n{append_text}")

    return "\n".join(parts)


def resolve_config_dir(name: str, config_dir: Path | None = None) -> Path:
    """The one place a Mvge's configuration lives.

    Defaults to the dotagents path `~/.agents/agents/{name}/`. An explicit
    directory overrides it, which is what tests and embedders pass.
    """
    if config_dir is not None:
        return config_dir
    return agent_dir(name)


def ensure_config_files(name: str, config_dir: Path | None = None) -> Path:
    """Create SYSTEM.md with editable defaults if absent.

    The file is the Summoner's configuration surface, so it is seeded
    with real content rather than left empty. Existing files are never
    overwritten.
    """
    resolved = resolve_config_dir(name, config_dir)
    resolved.mkdir(parents=True, exist_ok=True)

    path = resolved / SYSTEM_MD_FILENAME
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        path.write_text(DEFAULT_SYSTEM_MD, encoding="utf-8")

    return resolved


def resolve_append_system_prompts(
    *,
    project_dir: Path | None = None,
    caller_dir: Path | None = None,
    global_dir: Path | None = None,
) -> list[tuple[Path, str]]:
    """Discover APPEND_SYSTEM.md files in general-to-specific order:
    1. Global (~/.agents/APPEND_SYSTEM.md)
    2. Project (<project>/.agents/APPEND_SYSTEM.md)
    3. Caller (<caller>/system_prompt/APPEND_SYSTEM.md or <caller>/APPEND_SYSTEM.md)

    Returns a list of (path, content) tuples for non-empty files found.
    """
    results: list[tuple[Path, str]] = []

    # 1. Global (~/.agents/APPEND_SYSTEM.md)
    g_dir = global_dir if global_dir is not None else global_agents_dir()
    global_file = g_dir / APPEND_SYSTEM_MD_FILENAME
    if global_file.is_file():
        content = _read_text(global_file)
        if content:
            results.append((global_file, content))

    # 2. Project (<project>/.agents/APPEND_SYSTEM.md)
    proj_base = project_dir if project_dir is not None else Path.cwd()
    project_file = proj_base / ".agents" / APPEND_SYSTEM_MD_FILENAME
    if project_file.is_file():
        content = _read_text(project_file)
        if content:
            results.append((project_file, content))

    # 3. Caller (<caller>/system_prompt/APPEND_SYSTEM.md or <caller>/APPEND_SYSTEM.md)
    if caller_dir is not None:
        caller_sys_dir = caller_dir / "system_prompt" / APPEND_SYSTEM_MD_FILENAME
        caller_file = caller_dir / APPEND_SYSTEM_MD_FILENAME
        if caller_sys_dir.is_file():
            content = _read_text(caller_sys_dir)
            if content:
                results.append((caller_sys_dir, content))
        elif caller_file.is_file():
            content = _read_text(caller_file)
            if content:
                results.append((caller_file, content))

    return results


def resolve_system_prompt(
    agent_name: str = DEFAULT_AGENT_NAME,
    *,
    custom: str = "",
    config_dir: Path | None = None,
    project_dir: Path | None = None,
    caller_dir: Path | None = None,
    global_dir: Path | None = None,
    default: str = DEFAULT_SYSTEM_PROMPT,
    filename: str = SYSTEM_MD_FILENAME,
) -> ResolvedPrompt:
    """Resolve the system prompt from the discovery chain:
    custom -> caller SYSTEM.md -> project SYSTEM.md -> agent-scope SYSTEM.md -> default
    and append any discovered APPEND_SYSTEM.md files in general-to-specific order
    (Global -> Project -> Caller).
    """
    resolved_base: ResolvedPrompt
    if custom:
        custom_path = Path(custom).expanduser()
        if custom_path.is_file():
            resolved_base = ResolvedPrompt(
                text=_read_text(custom_path),
                source=PromptSource.CUSTOM_PATH,
                path=custom_path,
            )
        else:
            resolved_base = ResolvedPrompt(
                text=custom, source=PromptSource.CUSTOM_LITERAL
            )
    elif (
        caller_dir is not None
        and (caller_sys_dir := caller_dir / "system_prompt" / filename).is_file()
        and (text := _read_text(caller_sys_dir))
    ):
        resolved_base = ResolvedPrompt(
            text=text, source=PromptSource.AGENT_MD, path=caller_sys_dir
        )
    elif (
        caller_dir is not None
        and (caller_file := caller_dir / filename).is_file()
        and (text := _read_text(caller_file))
    ):
        resolved_base = ResolvedPrompt(
            text=text, source=PromptSource.AGENT_MD, path=caller_file
        )
    else:
        proj_base = project_dir if project_dir is not None else Path.cwd()
        std_project_file = proj_base / ".agents" / filename
        agent_dir = resolve_config_dir(agent_name, config_dir)
        agent_file = agent_dir / filename

        if std_project_file.is_file() and (text := _read_text(std_project_file)):
            resolved_base = ResolvedPrompt(
                text=text, source=PromptSource.PROJECT_MD, path=std_project_file
            )
        elif agent_file.is_file() and (text := _read_text(agent_file)):
            resolved_base = ResolvedPrompt(
                text=text, source=PromptSource.AGENT_MD, path=agent_file
            )
        elif (agent_sys_dir := agent_dir / "system_prompt" / filename).is_file() and (
            text := _read_text(agent_sys_dir)
        ):
            resolved_base = ResolvedPrompt(
                text=text, source=PromptSource.AGENT_MD, path=agent_sys_dir
            )
        elif (agent_md := agent_dir / "agent.md").is_file() and (
            text := _read_text(agent_md)
        ):
            resolved_base = ResolvedPrompt(
                text=text, source=PromptSource.AGENT_MD, path=agent_md
            )
        else:
            resolved_base = ResolvedPrompt(text=default, source=PromptSource.BUILTIN)

    appends = resolve_append_system_prompts(
        project_dir=project_dir,
        caller_dir=caller_dir,
        global_dir=global_dir,
    )
    if appends:
        parts = [resolved_base.text] if resolved_base.text else []
        parts.extend(content for _, content in appends)
        return ResolvedPrompt(
            text="\n\n".join(parts),
            source=resolved_base.source,
            path=resolved_base.path,
        )

    return resolved_base


def coerce_agent_config(
    config: Mapping[str, ConfigValue],
    *,
    agent_name: str = DEFAULT_AGENT_NAME,
    extension_dir: str | None = None,
    runes_paths: Sequence[str] | None = None,
    project_dir: Path | str | None = None,
    rune_extras: Sequence[ResolvedLayer] = (),
) -> AgentConfig:
    """Coerces raw resolved config mapping into a validated AgentConfig."""

    def _get(key: str, default: Any) -> Any:
        return config.get(key, ConfigValue(default, ConfigLayer.DEFAULTS)).value

    # Model, Realm-first (ADR-0028). An explicit model wins; otherwise
    # the stored Realm preference resolves through the per-Realm default
    # table; otherwise there is no model. An empty model_id is an
    # undecided session, not a silent OpenRouter session: the CLI owns
    # what happens next (bootstrap, a Realm prompt, or a Realm-choice
    # error), and this layer must not fill the hole for it. A preference
    # for a Realm with no default is an error, not a fallback to another
    # Realm's model.
    raw_model = config.get("model")
    explicit_model = (
        str(raw_model.value).strip()
        if raw_model is not None and str(raw_model.value).strip()
        else ""
    )
    raw_realm = config.get("realm")
    realm_preference = (
        str(raw_realm.value).strip()
        if raw_realm is not None and str(raw_realm.value).strip()
        else ""
    )
    if explicit_model:
        model_id = explicit_model
    elif realm_preference:
        resolved = resolve_default_model(realm=realm_preference)
        if resolved is None:
            raise ValueError(
                f"Realm {realm_preference!r} has no default model. "
                "Set a model with --model, or a Realm with a default "
                "with `mvge config set realm <realm>`."
            )
        model_id = resolved
    else:
        model_id = ""

    # Temperature
    raw_temp = _get("temperature", 0.7)
    try:
        if isinstance(raw_temp, bool):
            raise TypeError("temperature cannot be boolean")
        temperature = float(raw_temp)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid temperature in config (%r); falling back to default %s",
            raw_temp,
            0.7,
        )
        temperature = 0.7

    # Max tokens
    raw_max_tokens = _get("max_tokens", 4096)
    try:
        if isinstance(raw_max_tokens, bool):
            raise TypeError("max_tokens cannot be boolean")
        max_tokens = int(raw_max_tokens)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid max_tokens in config (%r); falling back to default %s",
            raw_max_tokens,
            4096,
        )
        max_tokens = 4096

    # Contemplation level
    raw_level = str(_get("contemplation_level", "medium"))
    if raw_level in _VALID_CONTEMPLATION_LEVELS:
        contemplation_level = raw_level
    else:
        logger.warning(
            "Invalid contemplation_level in config (%r); "
            "falling back to default 'medium'",
            raw_level,
        )
        contemplation_level = "medium"

    # Contemplation budget
    raw_budget = _get("contemplation_budget", None)
    if raw_budget is None:
        contemplation_budget = None
    else:
        try:
            if isinstance(raw_budget, bool):
                raise TypeError("contemplation_budget cannot be boolean")
            contemplation_budget = int(raw_budget)
        except (ValueError, TypeError):
            logger.warning(
                "Invalid contemplation_budget in config (%r); "
                "falling back to default None",
                raw_budget,
            )
            contemplation_budget = None

    # Exclude contemplation
    exclude_contemplation = bool(_get("exclude_contemplation", False))

    # Queue mode
    raw_queue = _get("queue_mode", QueueMode.ONE_AT_A_TIME)
    if isinstance(raw_queue, QueueMode):
        queue_mode = raw_queue
    else:
        try:
            queue_mode = QueueMode(str(raw_queue))
        except ValueError:
            logger.warning(
                "Invalid queue_mode in config (%r); "
                "falling back to default 'one-at-a-time'",
                raw_queue,
            )
            queue_mode = QueueMode.ONE_AT_A_TIME

    # Spells enabled
    spells_enabled = _get("spells_enabled", [])
    spell_names = list(spells_enabled) if spells_enabled else None

    # Rune layers. The standard stack always comes first; every declared
    # extra is appended to it, so configuring ``rune_paths`` narrows nothing
    # away -- it extends the layering rather than replacing it as a side
    # effect of being set. That inversion is the whole point: a config that
    # named one directory used to silently drop the user and agent layers.
    extras: list[ResolvedLayer] = list(rune_extras)
    if extension_dir:
        extras.append(ResolvedLayer(Scope.PROJECT, Path(extension_dir).expanduser()))
    if runes_paths is not None:
        declared: Any = runes_paths
    else:
        declared = _get("rune_paths", None) or []
    extras.extend(
        ResolvedLayer(Scope.PROJECT, Path(str(p)).expanduser()) for p in declared
    )

    rune_layers = resolve_rune_layers(
        agent_name, project_dir=project_dir, extras=extras
    )

    return AgentConfig(
        model_id=model_id,
        realm_preference=realm_preference,
        temperature=temperature,
        max_tokens=max_tokens,
        contemplation_level=contemplation_level,
        contemplation_budget=contemplation_budget,
        exclude_contemplation=exclude_contemplation,
        queue_mode=queue_mode,
        spell_names=spell_names,
        runes_paths=[layer.path for layer in rune_layers],
        rune_layers=rune_layers,
    )


@dataclass(frozen=True)
class MvgeEnvironment:
    """Consolidated agent environment holding resolved configuration and resources.

    Achieves 1-to-1 parity with Pi's ``AgentSessionServices`` + ``ResourceLoader``.
    """

    agent_name: str
    model_id: str
    temperature: float
    max_tokens: int
    contemplation_level: str
    contemplation_budget: int | None
    exclude_contemplation: bool
    queue_mode: QueueMode
    spell_names: list[str] | None
    runes_paths: list[Path]
    config: dict[str, ConfigValue]
    resolved_prompt: ResolvedPrompt
    diagnostics: list[Diagnostic | SkillDiagnostic] = field(default_factory=list)
    spells: list[MvgeSpell | SpellDefinition] = field(default_factory=list)
    runner: RuneRunner | None = None
    config_manager: ConfigManager | None = None
    agent_config: AgentConfig | None = None
    global_dir: Path | None = None
    active_spells_dir: Path | None = None
    #: Inputs used to resolve the base SYSTEM.md chain. Recorded by
    #: :meth:`resolve` so engine reload can re-run the identical discovery
    #: (custom -> caller -> project -> agent-scope -> default) even when the
    #: environment was built by an external caller (CLI/GUI) with different
    #: inputs.
    prompt_resolve_kwargs: dict[str, Any] = field(default_factory=dict)
    #: The resolved Rune layer stack, each layer carrying its own scope.
    #: ``runes_paths`` is this list's path projection: consumers that need to
    #: know *which layer* a directory is read this, consumers that only scan
    #: for Runes take the paths.
    rune_layers: list[ResolvedLayer] = field(default_factory=list)

    @classmethod
    def resolve(
        cls,
        agent_name: str = DEFAULT_AGENT_NAME,
        *,
        project_dir: Path | None = None,
        config_dir: Path | None = None,
        global_dir: Path | None = None,
        overrides: dict[str, Any] | None = None,
        custom_prompt: str = "",
        spells: list[MvgeSpell | SpellDefinition] | None = None,
        runner: RuneRunner | None = None,
        config_manager: ConfigManager | None = None,
        has_config_manager: bool = True,
        allow_unknown_agent: bool = False,
        caller_dir: Path | None = None,
        extension_dir: str | None = None,
        runes_paths: Sequence[str] | None = None,
        rune_extras: Sequence[ResolvedLayer] = (),
        active_spells_dir: Path | None = None,
    ) -> MvgeEnvironment:
        """Resolve all environment resources and configuration layers."""
        validate_agent_name(
            agent_name,
            agent_config_base=config_dir.parent if config_dir is not None else None,
            project_dir=project_dir,
            allow_create=allow_unknown_agent or (config_dir is not None),
        )
        cm = config_manager or ConfigManager(
            agent_name=agent_name,
            project_dir=project_dir,
            agent_config_base=config_dir.parent if config_dir is not None else None,
        )
        if overrides:
            cm = cm.with_overrides(**overrides)
        resolved_config = cm.load()

        coerced = coerce_agent_config(
            resolved_config,
            agent_name=agent_name,
            extension_dir=extension_dir,
            runes_paths=runes_paths,
            project_dir=project_dir,
            rune_extras=rune_extras,
        )

        resolved_prompt_inputs: dict[str, Any] = {
            "agent_name": agent_name,
            "custom": custom_prompt,
            "config_dir": config_dir,
            "project_dir": project_dir,
            "caller_dir": caller_dir,
            "global_dir": global_dir,
            "default": DEFAULT_SYSTEM_PROMPT,
        }
        resolved_prompt = resolve_system_prompt(**resolved_prompt_inputs)

        diags: list[Diagnostic | SkillDiagnostic] = []
        if runner is not None:
            diags.extend(runner.diagnostics)
            diags.extend(runner.skill_catalog.skill_diagnostics)

        return cls(
            agent_name=agent_name,
            model_id=coerced.model_id,
            temperature=coerced.temperature,
            max_tokens=coerced.max_tokens,
            contemplation_level=coerced.contemplation_level,
            contemplation_budget=coerced.contemplation_budget,
            exclude_contemplation=coerced.exclude_contemplation,
            queue_mode=coerced.queue_mode,
            spell_names=coerced.spell_names,
            runes_paths=coerced.runes_paths,
            rune_layers=coerced.rune_layers,
            config=resolved_config,
            resolved_prompt=resolved_prompt,
            diagnostics=diags,
            spells=spells or [],
            runner=runner,
            config_manager=cm if has_config_manager else None,
            agent_config=coerced,
            global_dir=global_dir,
            active_spells_dir=active_spells_dir,
            prompt_resolve_kwargs=resolved_prompt_inputs,
        )

    def render_system_prompt(
        self,
        *,
        cwd: str | Path | None = None,
        append_text: str = "",
    ) -> str:
        """Synchronously render Layer 2 invariant scaffolding using resolved state."""
        return self.render_prompt(
            body=self.resolved_prompt.text,
            spells=self.spell_names or [],
            cwd=cwd,
            append_text=append_text,
        )

    def build_sigil_payload(
        self,
        *,
        base_prompt: str,
        spell_names: Sequence[str],
        config_dir: str | Path | None,
        custom_prompt: str = "",
        cwd: str | Path | None = None,
        active_spells_dir: Path | None = None,
    ) -> BeforeMvgeStartData:
        """Build the BEFORE_MVGE_START payload from engine state.

        Populates the rune rehydration fields (``spells_dir``,
        ``system_path``, ``runes_paths``) from this environment, as flat
        strings matching the existing ``str``-typed payload fields. The
        returned payload is a fresh object per call; the engine caches its
        own copy and fans out defensive copies so rune mutations can never
        corrupt the cache or double-append prompt sections.
        """
        effective_cwd = str(cwd) if cwd else str(Path.cwd())
        effective_config_dir = (
            str(config_dir)
            if config_dir is not None
            else str(resolve_config_dir(self.agent_name))
        )
        effective_spells_dir = (
            active_spells_dir
            if active_spells_dir is not None
            else self.active_spells_dir
        )
        return BeforeMvgeStartData(
            base_prompt=base_prompt,
            spell_names=list(spell_names),
            config_dir=effective_config_dir,
            custom_prompt=custom_prompt,
            agent_name=self.agent_name,
            cwd=effective_cwd,
            runes_paths=[p.as_posix() for p in self.runes_paths],
            system_path=(
                self.resolved_prompt.path.as_posix()
                if self.resolved_prompt.path
                else None
            ),
            spells_dir=(
                effective_spells_dir.as_posix() if effective_spells_dir else None
            ),
        )

    async def assemble_system_prompt(
        self,
        runner: RuneRunner | None = None,
        *,
        base_prompt: str | None = None,
        custom_prompt: str = "",
        cwd: str | Path | None = None,
        spell_names: Sequence[str] | None = None,
        config_dir: str | Path | None = None,
        render_scaffolding: bool = True,
        active_spells_dir: Path | None = None,
    ) -> str:
        """Asynchronously assemble the final system prompt string.

        With a runner, emits ``BEFORE_MVGE_START`` so runes can rewrite the
        base prompt (e.g. steering-bridge injects repository steering).
        Then renders Layer 2 invariant scaffolding (active spells,
        environment, engine capability pointers).
        """
        effective_runner = runner if runner is not None else self.runner
        effective_base = (
            base_prompt if base_prompt is not None else self.resolved_prompt.text
        )
        effective_cwd = str(cwd) if cwd else str(Path.cwd())
        effective_spells = (
            list(spell_names)
            if spell_names is not None
            else (list(self.spell_names) if self.spell_names else [])
        )
        effective_config_dir = (
            str(config_dir)
            if config_dir is not None
            else str(resolve_config_dir(self.agent_name))
        )

        if effective_runner is not None:
            prompt_data = self.build_sigil_payload(
                base_prompt=effective_base,
                spell_names=effective_spells,
                config_dir=effective_config_dir,
                custom_prompt=custom_prompt,
                cwd=effective_cwd,
                active_spells_dir=active_spells_dir,
            )
            result_data = await effective_runner.emit_chain(
                SigilHook.BEFORE_MVGE_START, prompt_data
            )
            if isinstance(result_data, BeforeMvgeStartData):
                effective_base = result_data.base_prompt
            elif isinstance(result_data, dict):
                effective_base = str(result_data.get("base_prompt", effective_base))
            elif hasattr(result_data, "base_prompt"):
                effective_base = str(result_data.base_prompt)

        if not render_scaffolding or "Active spells:" in effective_base:
            return effective_base

        return self.render_prompt(
            body=effective_base,
            spells=effective_spells,
            cwd=effective_cwd,
            append_text="",
        )

    @staticmethod
    def render_prompt(
        body: str,
        spells: Sequence[str] = (),
        cwd: str | Path | None = None,
        append_text: str = "",
    ) -> str:
        """Render a prompt with body, spells, and environment."""
        return render_prompt(
            body=body,
            spells=spells,
            cwd=cwd,
            append_text=append_text,
        )

    def build_snapshot(self) -> RuntimeSnapshot:
        """Assemble a resolved runtime snapshot for introspection."""
        rune_manifests: list[RuneManifest] = (
            self.runner.loaded_manifests if self.runner is not None else []
        )
        skills: list[SkillManifest] = (
            self.runner.skill_catalog.get_skills() if self.runner is not None else []
        )
        rune_diagnostics: list[Diagnostic] = (
            self.runner.diagnostics if self.runner is not None else []
        )
        skill_diagnostics: list[SkillDiagnostic] = (
            self.runner.skill_catalog.skill_diagnostics
            if self.runner is not None
            else []
        )

        config_source_files: dict[ConfigLayer, Path | None] = {}
        if self.config_manager is not None:
            config_source_files = {
                ConfigLayer.AGENT: self.config_manager.agent_config_path,
                ConfigLayer.PROJECT: self.config_manager.project_config_path,
            }

        return assemble_snapshot(
            agent_name=self.agent_name,
            model=self.model_id,
            spells=self.spells,
            rune_manifests=rune_manifests,
            config_values=self.config if self.config_manager is not None else {},
            config_source_files=config_source_files,
            resolved_prompt=self.resolved_prompt,
            skills=skills,
            rune_diagnostics=rune_diagnostics,
            skill_diagnostics=skill_diagnostics,
        )
