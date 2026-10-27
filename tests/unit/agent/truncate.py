from __future__ import annotations

from mvge.core.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line_around_match,
    truncate_tail,
)


class TestFormatSize:
    def test_bytes(self) -> None:
        assert format_size(0) == "0B"
        assert format_size(512) == "512B"

    def test_kilobytes(self) -> None:
        assert format_size(1024) == "1.0KB"
        assert format_size(51_200) == "50.0KB"

    def test_megabytes(self) -> None:
        assert format_size(1024 * 1024) == "1.0MB"


class TestDefaults:
    def test_pi_parity(self) -> None:
        assert DEFAULT_MAX_LINES == 2000
        assert DEFAULT_MAX_BYTES == 51_200
        assert GREP_MAX_LINE_LENGTH == 500


class TestTruncateHead:
    def test_passthrough_when_small(self) -> None:
        content = "a\nb\nc"
        result = truncate_head(content)
        assert result.truncated is False
        assert result.text == content
        assert result.strategy is None
        assert result.shown_start is None
        assert result.shown_end is None
        assert result.total_lines == 3
        assert result.total_bytes == len(content.encode("utf-8"))
        assert result.version == 1

    def test_empty_content(self) -> None:
        result = truncate_head("")
        assert result.truncated is False
        assert result.text == ""
        assert result.total_lines == 0

    def test_trailing_newline_not_counted(self) -> None:
        result = truncate_head("a\nb\n")
        assert result.truncated is False
        assert result.total_lines == 2

    def test_zero_byte_tail(self) -> None:
        result = truncate_tail("abc", max_bytes=0)
        assert result.truncated is True
        assert result.text == ""
        assert result.shown_start is None

    def test_cuts_at_line_limit(self) -> None:
        content = "\n".join(f"line{i}" for i in range(10))
        result = truncate_head(content, max_lines=4)
        assert result.truncated is True
        assert result.strategy == "head"
        assert result.text == "\n".join(f"line{i}" for i in range(4))
        assert result.total_lines == 10
        assert result.shown_start == 1
        assert result.shown_end == 4

    def test_cuts_at_byte_limit_on_line_boundary(self) -> None:
        content = "\n".join(f"line{i}" for i in range(100))
        result = truncate_head(content, max_bytes=20)
        assert result.truncated is True
        assert result.text == "line0\nline1\nline2"
        assert len(result.text.encode("utf-8")) <= 20
        assert result.shown_start == 1
        assert result.shown_end == 3

    def test_never_splits_multibyte_characters(self) -> None:
        content = "héllo\nwörld\nplain"
        result = truncate_head(content, max_bytes=7)
        assert result.truncated is True
        result.text.encode("utf-8")
        assert result.text == "héllo"

    def test_first_line_exceeds_byte_limit(self) -> None:
        result = truncate_head("x" * 100, max_bytes=10)
        assert result.truncated is True
        assert result.text == ""
        assert result.shown_start is None
        assert result.shown_end is None


class TestTruncateTail:
    def test_passthrough_when_small(self) -> None:
        content = "a\nb\nc"
        result = truncate_tail(content)
        assert result.truncated is False
        assert result.text == content
        assert result.strategy is None

    def test_keeps_end_at_line_limit(self) -> None:
        content = "\n".join(f"line{i}" for i in range(10))
        result = truncate_tail(content, max_lines=3)
        assert result.truncated is True
        assert result.strategy == "tail"
        assert result.text == "line7\nline8\nline9"
        assert result.total_lines == 10
        assert result.shown_start == 8
        assert result.shown_end == 10

    def test_keeps_end_at_byte_limit(self) -> None:
        content = "\n".join(f"line{i}" for i in range(100))
        result = truncate_tail(content, max_bytes=20)
        assert result.truncated is True
        assert result.text.endswith("line99")
        assert len(result.text.encode("utf-8")) <= 20

    def test_single_huge_line_keeps_end(self) -> None:
        content = "x" * 1000
        result = truncate_tail(content, max_bytes=100)
        assert result.truncated is True
        assert result.text == "x" * 100
        assert len(result.text.encode("utf-8")) <= 100

    def test_single_huge_unicode_line_keeps_valid_text(self) -> None:
        content = "é" * 1000
        result = truncate_tail(content, max_bytes=100)
        assert result.truncated is True
        result.text.encode("utf-8")
        assert len(result.text.encode("utf-8")) <= 100
        assert result.text == "é" * 50


class TestTruncateLineAroundMatch:
    def test_short_line_untouched(self) -> None:
        assert truncate_line_around_match("short", [(0, 5)]) == "short"

    def test_no_matches_plain_cut(self) -> None:
        assert truncate_line_around_match("x" * 600, []) == "x" * 500 + "..."

    def test_match_at_start(self) -> None:
        line = "match" + "x" * 1000
        out = truncate_line_around_match(line, [(0, 5)], budget=100)
        assert out.startswith("match")
        assert out.endswith("...")
        assert len(out) <= 100 + len("...")

    def test_match_at_end(self) -> None:
        line = "x" * 1000 + "match"
        out = truncate_line_around_match(line, [(1000, 1005)], budget=100)
        assert out.startswith("...")
        assert out.endswith("match")

    def test_match_in_middle(self) -> None:
        line = "x" * 500 + "match" + "y" * 500
        out = truncate_line_around_match(line, [(500, 505)], budget=100)
        assert out.startswith("...")
        assert out.endswith("...")
        assert "match" in out

    def test_multi_match_covering_window(self) -> None:
        line = "aa" + "x" * 10 + "bb"
        out = truncate_line_around_match(line, [(0, 2), (12, 14)], budget=500)
        assert out == line

    def test_multi_match_falls_back_to_first(self) -> None:
        line = "aa" + "x" * 1000 + "bb"
        out = truncate_line_around_match(line, [(0, 2), (1012, 1014)], budget=100)
        assert "aa" in out
        assert "bb" not in out

    def test_unicode_safe(self) -> None:
        line = "é" * 400 + "match" + "é" * 400
        out = truncate_line_around_match(line, [(400, 405)], budget=100)
        out.encode("utf-8")
        assert "match" in out

    def test_minified_line_keeps_match(self) -> None:
        line = "x" * 5000 + "needle" + "y" * 5000
        out = truncate_line_around_match(line, [(5000, 5006)], budget=500)
        assert "needle" in out
        assert len(out) <= 500 + 2 * len("...")
