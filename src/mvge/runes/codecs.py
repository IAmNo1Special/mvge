"""Session codec discovery: runes opt in via ``session_codecs`` in manifest.json.

A codec entry is ``"module:ClassName"``. The module is imported with the
rune's own import roots on ``sys.path`` (the same roots rune entry points
get), the class is instantiated with no arguments, and the instance must
implement the SessionCodec protocol structurally (checked here so a broken
codec fails at discovery time, not mid-resume).

This module deliberately does not import mvge.tome: codecs are validated
structurally, keeping mvge.runes free of a session-persistence dependency.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.runes.loader import _inject_rune_paths, load_manifests
from mvge.runes.types import Diagnostic, DiagnosticKind

if TYPE_CHECKING:
    from mvge.tome.codec import SessionCodec

logger = logging.getLogger(__name__)

_CODEC_METHODS = (
    "detect",
    "looks_like_session",
    "parse_header",
    "parse_entries",
    "serialize_entry",
    "serialize_new_entry",
    "plan_append",
    "append_tip_lines",
    "apply_leaf",
    "leaf_id",
    "repair_on_open",
    "fork",
)


def _codec_diagnostic(
    rune_name: str, kind: DiagnosticKind, message: str, path: str = ""
) -> Diagnostic:
    return Diagnostic(kind=kind, rune_name=rune_name, message=message, path=path)


def _validate_codec(codec: Any) -> list[str]:
    """Return the names of missing/invalid SessionCodec members."""
    missing: list[str] = []
    name = getattr(codec, "name", None)
    if not isinstance(name, str) or not name:
        missing.append("name")
    for method in _CODEC_METHODS:
        if not callable(getattr(codec, method, None)):
            missing.append(method)
    return missing


def _load_codec_spec(
    spec: str, rune_name: str, rune_dir: Path, diagnostics: list[Diagnostic]
) -> Any | None:
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        diagnostics.append(
            _codec_diagnostic(
                rune_name,
                DiagnosticKind.PARSE_WARNING,
                f"Invalid session_codecs entry {spec!r}: expected 'module:ClassName'",
                path=str(rune_dir),
            )
        )
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        diagnostics.append(
            _codec_diagnostic(
                rune_name,
                DiagnosticKind.LOAD_FAILURE,
                f"Could not import session codec module {module_name!r}: {exc}",
                path=str(rune_dir),
            )
        )
        return None
    try:
        target = getattr(module, attr)
        codec = target() if isinstance(target, type) else target
    except Exception as exc:
        diagnostics.append(
            _codec_diagnostic(
                rune_name,
                DiagnosticKind.LOAD_FAILURE,
                f"Could not instantiate session codec {spec!r}: {exc}",
                path=str(rune_dir),
            )
        )
        return None
    missing = _validate_codec(codec)
    if missing:
        diagnostics.append(
            _codec_diagnostic(
                rune_name,
                DiagnosticKind.LOAD_FAILURE,
                f"Session codec {spec!r} does not implement the SessionCodec "
                f"protocol; missing: {', '.join(missing)}",
                path=str(rune_dir),
            )
        )
        return None
    logger.info("Loaded session codec %r from rune %s", codec.name, rune_name)
    return codec


def load_session_codecs(
    extension_dirs: Sequence[str | Path],
) -> tuple[list[SessionCodec], list[Diagnostic]]:
    """Load session codecs declared by runes in the given extension dirs.

    Only runes that declare ``session_codecs`` are touched: their modules are
    imported, everything else is left alone. Returns (codecs, diagnostics).
    """
    codecs: list[Any] = []
    diagnostics: list[Diagnostic] = []
    for ext_dir in extension_dirs:
        ext_path = Path(ext_dir).expanduser()
        for manifest in load_manifests(ext_path):
            if not manifest.session_codecs:
                continue
            rune_dir = Path(manifest.path) if manifest.path else ext_path
            _inject_rune_paths(rune_dir, rune_dir / "manifest.json")
            for spec in manifest.session_codecs:
                codec = _load_codec_spec(spec, manifest.name, rune_dir, diagnostics)
                if codec is not None:
                    codecs.append(codec)
    return codecs, diagnostics
