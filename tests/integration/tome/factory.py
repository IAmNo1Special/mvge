from pathlib import Path

from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType


def test_factory_create_tome(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    write = factory.create_tome("/home/user/project", tome_id="tome-1")

    meta = factory.open_tome(write.tome_id)
    assert meta is not None
    assert meta.id == "tome-1"
    assert meta.cwd == "/home/user/project"


def test_factory_append_and_get_entries(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    write = factory.create_tome("/home/user/project", tome_id="tome-2")
    write.append(
        TomeEntry(
            id="entry-1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=0.0,
            payload={"text": "hello"},
        )
    )

    entries = factory.get_entries("tome-2")
    assert len(entries) == 1
    assert entries[0].id == "entry-1"


def test_factory_open_write_then_read_roundtrip(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    factory.create_tome("/home/user/project", tome_id="tome-3")

    write = factory.open_write("tome-3")
    write.append(
        TomeEntry(
            id="entry-1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1.0,
            payload={"text": "hello"},
        )
    )

    read = factory.open_read("tome-3")
    assert [e.id for e in read.get_entries()] == ["entry-1"]
    assert read.get_metadata() is not None
