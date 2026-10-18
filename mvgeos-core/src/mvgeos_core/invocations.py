from __future__ import annotations

import mimetypes
from base64 import b64encode
from dataclasses import dataclass
from typing import Any

from mvgeos_core.channel import MvgeResponse
from mvgeos_core.events import ContentType
from mvgeos_core.spells import SpellResultMessage


@dataclass
class SummonerRequest:
    role: str = "user"
    content: str | list[dict[str, Any]] | None = None
    timestamp: float = 0.0


MvgeInvocation = SummonerRequest | MvgeResponse | SpellResultMessage


@dataclass
class Attachment:
    """A file attached to a summoner message, carried as native content parts."""

    filename: str
    data: bytes
    mime_type: str | None = None


_FALLBACK_MIME_TYPE = "application/octet-stream"


def _attachment_mime_type(attachment: Attachment) -> str:
    if attachment.mime_type:
        return attachment.mime_type
    guessed, _ = mimetypes.guess_type(attachment.filename)
    return guessed or _FALLBACK_MIME_TYPE


def _attachment_part(attachment: Attachment) -> dict[str, Any]:
    mime_type = _attachment_mime_type(attachment)
    data_url = f"data:{mime_type};base64,{b64encode(attachment.data).decode('ascii')}"
    if mime_type.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": data_url}}
    return {
        "type": "file",
        "file": {"filename": attachment.filename, "file_data": data_url},
    }


def build_content_parts(
    text: str, attachments: list[Attachment]
) -> str | list[dict[str, Any]]:
    """Build summoner message content from text plus file attachments.

    Returns the plain text string when there are no attachments; otherwise
    returns content parts in the documented OpenRouter multimodal shape so
    images, PDFs, markdown, and text files travel natively.
    """
    if not attachments:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend(_attachment_part(attachment) for attachment in attachments)
    return parts


__all__ = [
    "Attachment",
    "MvgeInvocation",
    "SummonerRequest",
    "build_content_parts",
    "denormalize_content_blocks",
    "extract_text_blocks",
    "normalize_content_blocks",
]


def normalize_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalize any content shape to the canonical ``list[dict[str, Any]]`` form.

    Handles the raw shapes found in Tome JSON and in-flight invocations:
    ``None`` becomes ``[]``; a ``str`` becomes a single TEXT block; a bare
    ``dict`` becomes a single-element list; a list normalizes each item
    (dicts pass through, strs become TEXT blocks, anything else is
    stringified into a TEXT block).
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": ContentType.TEXT, "text": content}]
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                blocks.append(item)
            elif isinstance(item, str):
                blocks.append({"type": ContentType.TEXT, "text": item})
            else:
                blocks.append({"type": ContentType.TEXT, "text": str(item)})
        return blocks
    return [{"type": ContentType.TEXT, "text": str(content)}]


def denormalize_content_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return normalized blocks in their canonical list form.

    The inverse of :func:`normalize_content_blocks` for already-canonical
    input; provided for symmetric serialize/deserialize call sites.
    """
    return list(blocks)


def extract_text_blocks(content: list[dict[str, Any]] | str | None) -> str:
    """Extract and concatenate all TEXT and CONTEMPLATION block text.

    Replaces the buggy ``compaction._render_content`` which silently dropped
    ``ContentType.CONTEMPLATION`` (and ``SPELL_CAST``) blocks from summaries.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") in (ContentType.TEXT, ContentType.CONTEMPLATION)
    ]
    return " ".join(part for part in parts if part)
