"""Table-driven tests for content block normalization.

Covers all input shapes (str, None, dict, list[dict], list[str], list[mixed])
and all ContentType variants per issue #145.
"""

from __future__ import annotations

from mvge.core.events import ContentType
from mvge.core.invocations import (
    denormalize_content_blocks,
    extract_text_blocks,
    normalize_content_blocks,
)


class TestNormalizeContentBlocks:
    """normalize_content_blocks() converts any shape to list[dict[str, Any]]."""

    def test_none_becomes_empty_list(self) -> None:
        assert normalize_content_blocks(None) == []

    def test_str_becomes_single_text_block(self) -> None:
        result = normalize_content_blocks("hello")
        assert result == [{"type": ContentType.TEXT, "text": "hello"}]

    def test_empty_str_becomes_single_text_block(self) -> None:
        result = normalize_content_blocks("")
        assert result == [{"type": ContentType.TEXT, "text": ""}]

    def test_dict_becomes_single_block_list(self) -> None:
        block = {"type": ContentType.TEXT, "text": "hi"}
        assert normalize_content_blocks(block) == [block]

    def test_list_of_dicts_passes_through(self) -> None:
        blocks = [
            {"type": ContentType.TEXT, "text": "a"},
            {"type": ContentType.CONTEMPLATION, "text": "thinking"},
        ]
        assert normalize_content_blocks(blocks) == blocks

    def test_list_of_strs_becomes_text_blocks(self) -> None:
        result = normalize_content_blocks(["a", "b"])
        assert result == [
            {"type": ContentType.TEXT, "text": "a"},
            {"type": ContentType.TEXT, "text": "b"},
        ]

    def test_list_of_mixed_becomes_blocks(self) -> None:
        result = normalize_content_blocks(
            ["text", {"type": ContentType.TEXT, "text": "block"}, None, 42]
        )
        assert result == [
            {"type": ContentType.TEXT, "text": "text"},
            {"type": ContentType.TEXT, "text": "block"},
            {"type": ContentType.TEXT, "text": "None"},
            {"type": ContentType.TEXT, "text": "42"},
        ]

    def test_int_becomes_text_block(self) -> None:
        result = normalize_content_blocks(42)
        assert result == [{"type": ContentType.TEXT, "text": "42"}]

    def test_spell_cast_block_preserved(self) -> None:
        block = {
            "type": ContentType.SPELL_CAST,
            "spell_name": "read",
            "arguments": {},
        }
        assert normalize_content_blocks([block]) == [block]

    def test_contemplation_block_preserved(self) -> None:
        block = {"type": ContentType.CONTEMPLATION, "text": "hmm"}
        assert normalize_content_blocks([block]) == [block]


class TestDenormalizeContentBlocks:
    """denormalize_content_blocks() is the inverse of normalize."""

    def test_empty_list_stays_empty(self) -> None:
        assert denormalize_content_blocks([]) == []

    def test_single_text_block_round_trips(self) -> None:
        blocks = [{"type": ContentType.TEXT, "text": "hello"}]
        assert denormalize_content_blocks(blocks) == blocks

    def test_round_trip_preserves_all_types(self) -> None:
        original = [
            {"type": ContentType.TEXT, "text": "a"},
            {"type": ContentType.CONTEMPLATION, "text": "b"},
            {"type": ContentType.SPELL_CAST, "spell_name": "x"},
        ]
        normalized = normalize_content_blocks(original)
        assert denormalize_content_blocks(normalized) == original


class TestExtractTextBlocks:
    """extract_text_blocks() concatenates TEXT and CONTEMPLATION text.

    Replaces the buggy compaction._render_content which drops
    non-text blocks (issue #145).
    """

    def test_none_returns_empty(self) -> None:
        assert extract_text_blocks(None) == ""

    def test_str_returns_as_is(self) -> None:
        assert extract_text_blocks("hello") == "hello"

    def test_text_blocks_concatenated(self) -> None:
        blocks = [
            {"type": ContentType.TEXT, "text": "a"},
            {"type": ContentType.TEXT, "text": "b"},
        ]
        assert extract_text_blocks(blocks) == "a b"

    def test_contemplation_blocks_included(self) -> None:
        """The compaction bug: CONTEMPLATION was silently dropped."""
        blocks = [
            {"type": ContentType.TEXT, "text": "answer"},
            {"type": ContentType.CONTEMPLATION, "text": "thinking"},
        ]
        assert extract_text_blocks(blocks) == "answer thinking"

    def test_spell_cast_blocks_excluded(self) -> None:
        blocks = [
            {"type": ContentType.TEXT, "text": "result"},
            {"type": ContentType.SPELL_CAST, "spell_name": "read"},
        ]
        assert extract_text_blocks(blocks) == "result"

    def test_empty_list_returns_empty(self) -> None:
        assert extract_text_blocks([]) == ""

    def test_blocks_without_text_skipped(self) -> None:
        blocks = [
            {"type": ContentType.TEXT, "text": ""},
            {"type": ContentType.TEXT, "text": "hi"},
        ]
        assert extract_text_blocks(blocks) == "hi"
