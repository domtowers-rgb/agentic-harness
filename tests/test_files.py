"""Files in and out: the upload/download endpoints, attachments reported
from tool results, and the read_document / create_document plugins. Every
test points the sandbox at a temp dir, so nothing touches workspace/."""
import json

import pytest

import plugins.file_ops as file_ops
import plugins.powerpoint as powerpoint
import plugins.word_document as word_document
from agentic_harness import main as main_mod


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    # Each module imported SANDBOX_DIR by value, so each needs pointing at
    # the temp dir (_resolve_safe itself reads file_ops' copy).
    for module in (file_ops, powerpoint, word_document, main_mod):
        monkeypatch.setattr(module, "SANDBOX_DIR", tmp_path)
    return tmp_path


def _make_pdf(text: str) -> bytes:
    """A minimal one-page PDF with `text` on it - enough for pypdf to
    extract, without needing a PDF-writing library just for tests."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


class TestUploadAndDownload:
    def test_upload_saves_into_uploads_and_returns_the_path(self, client, sandbox):
        r = client.post("/v1/files", params={"filename": "My Report.pdf"}, content=b"%PDF data")
        assert r.status_code == 200
        assert r.json() == {"path": "uploads/My_Report.pdf", "filename": "My_Report.pdf", "size": 9}
        assert (sandbox / "uploads" / "My_Report.pdf").read_bytes() == b"%PDF data"

    def test_upload_never_overwrites(self, client):
        first = client.post("/v1/files", params={"filename": "a.txt"}, content=b"one").json()
        second = client.post("/v1/files", params={"filename": "a.txt"}, content=b"two").json()
        assert first["path"] == "uploads/a.txt"
        assert second["path"] == "uploads/a-2.txt"

    def test_upload_filename_cannot_escape_the_uploads_folder(self, client, sandbox):
        r = client.post("/v1/files", params={"filename": "../../etc/passwd"}, content=b"x")
        assert r.json()["path"] == "uploads/passwd"
        assert (sandbox / "uploads" / "passwd").exists()

    def test_upload_without_a_filename_gets_a_default(self, client):
        assert client.post("/v1/files", content=b"x").json()["path"] == "uploads/upload"

    def test_upload_over_the_size_limit_is_rejected(self, client, monkeypatch, sandbox):
        monkeypatch.setattr(main_mod, "MAX_UPLOAD_BYTES", 4)
        r = client.post("/v1/files", params={"filename": "big.bin"}, content=b"12345")
        assert r.status_code == 413
        assert not (sandbox / "uploads").exists()

    def test_download_returns_the_file(self, client, sandbox):
        (sandbox / "deck.pptx").write_bytes(b"pptx bytes")
        r = client.get("/v1/files/deck.pptx")
        assert r.status_code == 200
        assert r.content == b"pptx bytes"
        assert "deck.pptx" in r.headers["content-disposition"]

    def test_download_of_a_missing_file_is_404(self, client):
        assert client.get("/v1/files/nope.docx").status_code == 404

    def test_download_cannot_escape_the_sandbox(self, client):
        assert client.get("/v1/files/..%2F..%2Fetc%2Fpasswd").status_code == 404


def _tool_call(name, arguments):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}},
    ]}}]}


class _TwoStepModel:
    """Calls one tool, then answers."""

    def __init__(self, name, arguments):
        self.responses = [_tool_call(name, arguments), {"choices": [{"message": {"role": "assistant", "content": "Here you go."}}]}]

    def chat(self, messages, tools=None, **kwargs):
        return self.responses.pop(0)

    def chat_stream(self, messages, tools=None, **kwargs):
        response = self.responses.pop(0)
        message = response["choices"][0]["message"]
        if message.get("tool_calls"):
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, **message["tool_calls"][0]}]}}]}
        else:
            yield {"choices": [{"delta": {"content": message["content"]}}]}


DOC_ARGS = {"title": "Worksheet", "sections": [{"heading": "Q1", "paragraphs": ["What is 2+2?"]}]}


class TestAttachmentsInResponses:
    def test_created_file_is_listed_in_the_response(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "model_impl", _TwoStepModel("create_document", DOC_ARGS))
        r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "make a worksheet"}]})

        body = r.json()
        assert body["choices"][0]["message"]["content"] == "Here you go."
        [attachment] = body["attachments"]
        assert attachment["filename"] == "Worksheet.docx"
        assert attachment["url"] == "/v1/files/Worksheet.docx"
        assert attachment["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert client.get(attachment["url"]).content[:2] == b"PK"  # a real .docx (zip) file

    def test_no_attachments_field_when_nothing_was_created(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "model_impl", _TwoStepModel("get_current_time", {}))
        r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "time?"}]})
        assert "attachments" not in r.json()

    def test_streaming_sends_an_attachments_event_before_done(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "model_impl", _TwoStepModel("create_document", DOC_ARGS))
        with client.stream("POST", "/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "make a worksheet"}], "stream": True,
        }) as r:
            lines = [line for line in r.iter_lines() if line]
        assert lines[-1] == "data: [DONE]"
        event = json.loads(lines[-2][len("data: "):])
        assert [a["filename"] for a in event["attachments"]] == ["Worksheet.docx"]


class TestCreateDocument:
    def test_creates_a_readable_docx(self, sandbox):
        from plugins.read_document import read_document
        from plugins.word_document import create_document

        result = create_document("Trip Letter", [
            {"heading": "Details", "paragraphs": ["We leave at 9am."], "bullets": ["Packed lunch", "Coat"]},
            {"paragraphs": "A single string is fine too."},
        ], filename="trip letter")
        assert result["path"] == result["attachment"] == "trip_letter.docx"
        assert "sent to the user automatically" in result["note"]

        content = read_document("trip_letter.docx")["content"]
        assert content.splitlines() == [
            "## Trip Letter", "## Details", "We leave at 9am.", "- Packed lunch", "- Coat", "A single string is fine too.",
        ]

    def test_filename_extension_is_not_doubled(self):
        from plugins.powerpoint import create_presentation
        from plugins.word_document import create_document
        assert create_document("T", [], filename="Year_9 Checklist.docx")["path"] == "Year_9_Checklist.docx"
        assert create_presentation("T", [], filename="deck.PPTX")["path"] == "deck.pptx"
        assert create_document("T", [], filename=".docx")["path"] == "document.docx"

    def test_validates_input(self):
        from plugins.word_document import create_document
        assert "error" in create_document("", [])
        assert "error" in create_document("T", "not a list")
        assert "error" in create_document("T", ["not an object"])
        assert "error" in create_document("T", [{"bullets": 5}])


class TestReadDocument:
    def test_reads_a_pdf_page_by_page(self, sandbox):
        from plugins.read_document import read_document
        (sandbox / "notes.pdf").write_bytes(_make_pdf("Photosynthesis makes glucose"))
        result = read_document("notes.pdf")
        assert result["content"] == "--- page 1 ---\nPhotosynthesis makes glucose"
        assert "next_start" not in result

    def test_reads_a_pptx(self, sandbox):
        from plugins.powerpoint import create_presentation
        from plugins.read_document import read_document
        create_presentation("Cells", [{"title": "Mitochondria", "bullets": ["Energy"]}], filename="cells")
        content = read_document("cells.pptx")["content"]
        assert "--- slide 2 ---\nMitochondria\nEnergy" in content

    def test_reads_plain_text(self, sandbox):
        from plugins.read_document import read_document
        (sandbox / "list.csv").write_text("a,b\n1,2\n")
        assert read_document("list.csv")["content"] == "a,b\n1,2\n"

    def test_long_documents_are_read_in_chunks(self, sandbox, monkeypatch):
        import plugins.read_document as read_module
        monkeypatch.setattr(read_module, "MAX_CHARS", 4)
        (sandbox / "long.txt").write_text("abcdefghij")
        first = read_module.read_document("long.txt")
        assert (first["content"], first["next_start"], first["total_chars"]) == ("abcd", 4, 10)
        last = read_module.read_document("long.txt", start=8)
        assert last["content"] == "ij" and "next_start" not in last

    def test_unsupported_missing_and_escaping_paths(self, sandbox):
        from plugins.read_document import read_document
        (sandbox / "photo.jpg").write_bytes(b"\xff\xd8")
        assert "can't read .jpg" in read_document("photo.jpg")["error"]
        assert "no such file" in read_document("missing.pdf")["error"]
        assert "error" in read_document("../outside.txt")

    def test_corrupt_file_is_an_error_not_an_exception(self, sandbox):
        from plugins.read_document import read_document
        (sandbox / "broken.docx").write_bytes(b"not a zip")
        assert "couldn't read" in read_document("broken.docx")["error"]
