from __future__ import annotations

import base64

from mvge.core.invocations import Attachment, build_content_parts


def test_no_attachments_returns_text_unchanged() -> None:
    assert build_content_parts("hello", []) == "hello"


def test_empty_text_no_attachments_returns_empty_string() -> None:
    assert build_content_parts("", []) == ""


def test_image_attachment_becomes_image_url_part() -> None:
    data = b"\x89PNG\r\n\x1a\n"
    parts = build_content_parts("", [Attachment(filename="pic.png", data=data)])
    assert isinstance(parts, list)
    assert parts == [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{base64.b64encode(data).decode()}"
            },
        }
    ]


def test_pdf_attachment_becomes_file_part() -> None:
    data = b"%PDF-1.4"
    parts = build_content_parts("", [Attachment(filename="doc.pdf", data=data)])
    assert parts == [
        {
            "type": "file",
            "file": {
                "filename": "doc.pdf",
                "file_data": (
                    f"data:application/pdf;base64,{base64.b64encode(data).decode()}"
                ),
            },
        }
    ]


def test_markdown_attachment_becomes_file_part_with_markdown_mime() -> None:
    data = b"# Title\n"
    parts = build_content_parts("", [Attachment(filename="notes.md", data=data)])
    assert parts == [
        {
            "type": "file",
            "file": {
                "filename": "notes.md",
                "file_data": (
                    f"data:text/markdown;base64,{base64.b64encode(data).decode()}"
                ),
            },
        }
    ]


def test_text_attachment_becomes_file_part_with_plain_mime() -> None:
    data = b"hello"
    parts = build_content_parts("", [Attachment(filename="a.txt", data=data)])
    assert parts == [
        {
            "type": "file",
            "file": {
                "filename": "a.txt",
                "file_data": (
                    f"data:text/plain;base64,{base64.b64encode(data).decode()}"
                ),
            },
        }
    ]


def test_unknown_extension_falls_back_to_octet_stream() -> None:
    data = b"\x00\x01"
    parts = build_content_parts("", [Attachment(filename="blob.unknownext", data=data)])
    assert isinstance(parts, list)
    file_part = parts[0]
    assert file_part["type"] == "file"
    assert file_part["file"]["file_data"].startswith(
        "data:application/octet-stream;base64,"
    )


def test_explicit_mime_type_overrides_guessing() -> None:
    data = b"data"
    parts = build_content_parts(
        "", [Attachment(filename="x.bin", data=data, mime_type="image/png")]
    )
    assert parts == [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{base64.b64encode(data).decode()}"
            },
        }
    ]


def test_text_comes_first_then_attachments_in_order() -> None:
    img = b"img"
    pdf = b"pdf"
    parts = build_content_parts(
        "look at these",
        [
            Attachment(filename="pic.png", data=img),
            Attachment(filename="doc.pdf", data=pdf),
        ],
    )
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "look at these"}
    assert parts[1]["type"] == "image_url"
    assert parts[2]["type"] == "file"
    assert parts[2]["file"]["filename"] == "doc.pdf"


def test_attachment_only_send_has_no_text_part() -> None:
    data = b"%PDF-1.4"
    parts = build_content_parts("", [Attachment(filename="doc.pdf", data=data)])
    assert isinstance(parts, list)
    assert all(part["type"] != "text" for part in parts)
