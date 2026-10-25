from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from mvge.core.spells import ExecutionMode
from mvge.runes.types import (
    RuneManifest,
    RuneShortcut,
    RuneUICanvasTab,
    RuneUIContributions,
    RuneUIView,
    SigilHook,
)


def _extract_string_list(data: dict[str, Any], key: str) -> list[str]:
    raw = data.get(key, [])
    if not isinstance(raw, list):
        return []
    return [dep for dep in raw if isinstance(dep, str)]


def _parse_ui_contributions(raw_ui: Any) -> RuneUIContributions:
    if not isinstance(raw_ui, dict):
        return RuneUIContributions()

    views: list[RuneUIView] = []
    for item in raw_ui.get("views", []):
        if isinstance(item, dict) and "id" in item and "title" in item:
            views.append(
                RuneUIView(
                    id=str(item["id"]),
                    title=str(item["title"]),
                    icon=str(item.get("icon", "extension")),
                    order=int(item.get("order", 100)),
                    description=str(item.get("description", "")),
                )
            )

    canvas_tabs: list[RuneUICanvasTab] = []
    for item in raw_ui.get("canvas_tabs", []):
        if isinstance(item, dict) and "id" in item and "title" in item:
            canvas_tabs.append(
                RuneUICanvasTab(
                    id=str(item["id"]),
                    title=str(item["title"]),
                    icon=str(item.get("icon", "extension")),
                    order=int(item.get("order", 100)),
                )
            )

    return RuneUIContributions(views=views, canvas_tabs=canvas_tabs)


def load_manifest(path: Path) -> RuneManifest | None:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        with manifest_path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return None

    if "name" not in data or "version" not in data:
        return None

    hooks = []
    for hook_str in data.get("hooks", []):
        with contextlib.suppress(ValueError):
            hooks.append(SigilHook(hook_str))

    shortcuts = []
    for sc_data in data.get("shortcuts", []):
        if isinstance(sc_data, str):
            shortcuts.append(RuneShortcut(key=sc_data))
        elif isinstance(sc_data, dict):
            shortcuts.append(
                RuneShortcut(
                    key=sc_data.get("key", ""),
                    description=sc_data.get("description", ""),
                )
            )

    system_deps = _extract_string_list(data, "system_deps")
    python_deps = _extract_string_list(data, "python_deps")

    exec_mode_str = data.get("execution_mode", "parallel")
    try:
        execution_mode = ExecutionMode(exec_mode_str)
    except ValueError:
        execution_mode = ExecutionMode.PARALLEL

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True

    spell_gateway = data.get("spell_gateway", False)
    if not isinstance(spell_gateway, bool):
        spell_gateway = False

    runtime = str(data.get("runtime", "python")).strip().lower() or "python"

    raw_types = data.get("types")
    types: list[str] = []
    if isinstance(raw_types, list):
        types = [str(t) for t in raw_types if t]
    elif "type" in data and data["type"]:
        types = [str(data["type"])]

    commands = _extract_string_list(data, "commands")
    session_codecs = _extract_string_list(data, "session_codecs")
    ui = _parse_ui_contributions(data.get("ui"))

    return RuneManifest(
        name=data["name"],
        version=data["version"],
        description=data.get("description", ""),
        hooks=hooks,
        entry_point=data.get("entry_point", ""),
        shortcuts=shortcuts,
        system_deps=system_deps,
        python_deps=python_deps,
        execution_mode=execution_mode,
        enabled=enabled,
        runtime=runtime,
        types=types,
        spell_gateway=spell_gateway,
        commands=commands,
        session_codecs=session_codecs,
        ui=ui,
    )
