"""Unit tests for the SessionCodec protocol and the built-in Tome v1 codec."""

from __future__ import annotations

import json

import pytest

from mvge.tome.codec import AppendPlan, SessionCodec, TomeV1Codec
from mvge.tome.types import TomeEntry, TomeEntryType, TomeVersionError


def _message(entry_id: str, parent_id: str | None = None) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=parent_id,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"role": "user", "content": "hi"},
    )


def _header(**overrides):
    header = {
        "type": "session",
        "version": 1,
        "id": "tome-1",
        "timestamp": "2026-09-19T00:00:00Z",
        "cwd": "/tmp",
    }
    header.update(overrides)
    return header


class TestSessionCodecProtocol:
    def test_tome_v1_codec_satisfies_protocol(self):
        assert isinstance(TomeV1Codec(), SessionCodec)

    def test_protocol_is_runtime_checkable_for_rune_codecs(self):
        class Fake(SessionCodec):
            name = "fake"

            def detect(self, header):
                return False

            def looks_like_session(self, header):
                return False

            def parse_header(self, header):
                raise TomeVersionError("nope")

            def parse_entries(self, header, lines, *, source=""):
                return []

            def serialize_entry(self, entry):
                return None

            def serialize_new_entry(self, entry, existing):
                return {}, entry

            def apply_leaf(self, header, entries, leaf):
                return header, entries

            def leaf_id(self, header, entries):
                return None

        assert isinstance(Fake(), SessionCodec)


class TestTomeV1CodecDetect:
    def test_detects_v1_session_header(self):
        assert TomeV1Codec().detect(_header()) is True

    def test_rejects_pi_v3_header(self):
        pi_v3 = {
            "type": "session",
            "version": 3,
            "id": "abc",
            "timestamp": "2026-09-19T00:00:00.000Z",
            "cwd": "/tmp",
        }
        assert TomeV1Codec().detect(pi_v3) is False

    def test_rejects_pi_v4_header(self):
        pi_v4 = {"v": 4, "kind": "header", "id": "abc", "storageVersion": 1}
        assert TomeV1Codec().detect(pi_v4) is False

    def test_rejects_non_session_documents(self):
        assert TomeV1Codec().detect({"kind": "entry"}) is False
        assert TomeV1Codec().detect({}) is False

    def test_looks_like_session_covers_any_session_version(self):
        codec = TomeV1Codec()
        assert codec.looks_like_session({"type": "session", "version": 99}) is True
        assert codec.looks_like_session({"kind": "header"}) is False


class TestTomeV1CodecHeader:
    def test_parse_header_returns_metadata(self):
        meta = TomeV1Codec().parse_header(_header(model="m", spells=["a"]))
        assert meta.id == "tome-1"
        assert meta.model == "m"
        assert meta.spells == ["a"]
        assert meta.version == 1

    def test_parse_header_reads_name(self):
        meta = TomeV1Codec().parse_header(_header(name="Coffee Chat"))
        assert meta.name == "Coffee Chat"

    def test_parse_header_name_defaults_to_none(self):
        assert TomeV1Codec().parse_header(_header()).name is None

    def test_parse_header_rejects_wrong_version(self):
        with pytest.raises(TomeVersionError):
            TomeV1Codec().parse_header(_header(version=2))

    def test_parse_header_missing_version_defaults_to_current(self):
        header = _header()
        del header["version"]
        assert TomeV1Codec().detect(header) is True
        assert TomeV1Codec().parse_header(header).version == 1

    def test_parse_header_rejects_missing_id(self):
        header = _header()
        del header["id"]
        with pytest.raises(KeyError):
            TomeV1Codec().parse_header(header)

    def test_parse_header_requires_cwd_and_timestamp(self):
        header = _header()
        del header["cwd"]
        with pytest.raises(KeyError):
            TomeV1Codec().parse_header(header)


class TestTomeV1CodecEntries:
    def test_parse_entries_round_trips(self):
        codec = TomeV1Codec()
        entries = [_message("a"), _message("b", parent_id="a")]
        lines = [json.dumps(e.to_dict()) for e in entries]
        parsed = codec.parse_entries({}, lines, source="test")
        assert [e.id for e in parsed] == ["a", "b"]
        assert parsed[1].parent_id == "a"

    def test_parse_entries_skips_damaged_lines(self, caplog):
        codec = TomeV1Codec()
        lines = [json.dumps(_message("a").to_dict()), "not json{{{", "[1,2]"]
        with caplog.at_level("WARNING", logger="mvge.tome.codec"):
            parsed = codec.parse_entries({}, lines, source="test")
        assert [e.id for e in parsed] == ["a"]

    def test_serialize_entry_is_to_dict(self):
        codec = TomeV1Codec()
        entry = _message("a")
        assert codec.serialize_entry(entry) == entry.to_dict()

    def test_serialize_new_entry_returns_line_and_entry(self):
        codec = TomeV1Codec()
        entry = _message("a")
        line, stored = codec.serialize_new_entry(entry, [])
        assert line == entry.to_dict()
        assert stored is entry


class TestTomeV1CodecLeaf:
    def test_apply_leaf_appends_leaf_and_sets_header(self):
        codec = TomeV1Codec()
        entries = [_message("a")]
        leaf = TomeEntry(
            id="leaf-1",
            parent_id=None,
            type=TomeEntryType.LEAF,
            timestamp=1001.0,
            payload={"targetId": "a"},
        )
        header, new_entries = codec.apply_leaf(_header(), entries, leaf)
        assert header["activeLeafId"] == "a"
        assert new_entries[-1].type == TomeEntryType.LEAF
        assert len(new_entries) == 2

    def test_leaf_id_prefers_leaf_entries(self):
        codec = TomeV1Codec()
        leaf = TomeEntry(
            id="leaf-1",
            parent_id=None,
            type=TomeEntryType.LEAF,
            timestamp=1001.0,
            payload={"targetId": "b"},
        )
        entries = [_message("a"), _message("b", parent_id="a"), leaf]
        assert codec.leaf_id(_header(activeLeafId="a"), entries) == "b"

    def test_leaf_id_falls_back_to_header(self):
        codec = TomeV1Codec()
        assert codec.leaf_id(_header(activeLeafId="a"), [_message("a")]) == "a"

    def test_leaf_id_none_when_no_leaf_info(self):
        codec = TomeV1Codec()
        assert codec.leaf_id(_header(), [_message("a")]) is None


class TestPlanAppend:
    def test_default_plan_appends_single_line(self):
        codec = TomeV1Codec()
        entry = _message("a")
        plan = codec.plan_append(entry, [], _header())
        assert plan.rewrite is False
        assert plan.header is None
        assert plan.lines == [entry.to_dict()]
        assert plan.stored is entry

    def test_override_can_request_rewrite(self):
        class RewriteCodec(TomeV1Codec):
            name = "rewrite"

            def plan_append(self, entry, existing, header):
                return AppendPlan(
                    lines=[{"migrated": True}],
                    stored=entry,
                    rewrite=True,
                    header={"id": "new"},
                )

        codec = RewriteCodec()
        plan = codec.plan_append(_message("a"), [], _header())
        assert plan.rewrite is True
        assert plan.header == {"id": "new"}
        assert plan.lines == [{"migrated": True}]

    def test_override_can_emit_multi_line_transaction(self):
        class TxnCodec(TomeV1Codec):
            name = "txn"

            def plan_append(self, entry, existing, header):
                return AppendPlan(
                    lines=[[{"write": 1}, {"write": 2}]],
                    stored=entry,
                )

        plan = TxnCodec().plan_append(_message("a"), [], _header())
        assert plan.rewrite is False
        assert plan.lines == [[{"write": 1}, {"write": 2}]]


class TestAppendTipLines:
    def test_default_returns_none(self):
        codec = TomeV1Codec()
        assert codec.append_tip_lines(_header(), [], _message("a")) is None

    def test_override_returns_lines(self):
        class TipCodec(TomeV1Codec):
            name = "tip"

            def append_tip_lines(self, header, entries, leaf):
                return [{"tip": leaf.payload["targetId"]}]

        leaf = TomeEntry(
            id="l1",
            parent_id=None,
            type=TomeEntryType.LEAF,
            timestamp=1001.0,
            payload={"targetId": "a"},
        )
        lines = TipCodec().append_tip_lines(_header(), [], leaf)
        assert lines == [{"tip": "a"}]


class TestFork:
    def test_default_fork_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            TomeV1Codec().fork(source="s", dest_dir="d", new_id="n")


class TestRepairOnOpen:
    def test_default_is_noop(self):
        assert TomeV1Codec().repair_on_open("whatever.jsonl") is False
