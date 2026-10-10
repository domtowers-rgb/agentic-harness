"""Files in and out: the upload/download endpoints, attachments reported
from tool results, and the read_document / create_document plugins. Every
test points the sandbox at a temp dir, so nothing touches workspace/."""
import json

import pytest

import plugins.file_ops as file_ops
import plugins.voice as voice
from agentic_harness import main as main_mod


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    # Every plugin and the server resolve paths via file_ops at call time.
    monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path)
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


DOC_ARGS = {"format": "docx", "title": "Worksheet", "sections": [{"heading": "Q1", "paragraphs": ["What is 2+2?"]}]}


class TestAttachmentsInResponses:
    def test_created_file_is_listed_in_the_response(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "model_impl", _TwoStepModel("create_file", DOC_ARGS))
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
        monkeypatch.setattr(main_mod, "model_impl", _TwoStepModel("create_file", DOC_ARGS))
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


class TestCreatePdf:
    def test_creates_a_pdf_that_reads_back_exactly(self, sandbox):
        from plugins.pdf_document import create_pdf
        from plugins.read_document import read_document

        result = create_pdf("Trip – Info", [
            {"heading": "Details", "paragraphs": ["Cost: **£5** & lunch. Ask “Sir” <trips@school>."], "bullets": ["Coat", "Pen"]},
            {"paragraphs": "6CO₂ → C₆H₁₂O₆, π ≈ 3.14"},
        ], filename="trip info.pdf")

        assert result["path"] == result["attachment"] == "trip_info.pdf"
        assert "sent to the user automatically" in result["note"]
        assert (sandbox / "trip_info.pdf").read_bytes()[:5] == b"%PDF-"
        content = read_document("trip_info.pdf")["content"]
        # Markup characters are escaped, not parsed; **bold** markers removed.
        for expected in ("Trip – Info", "Cost: £5 & lunch. Ask “Sir” <trips@school>.", "Coat", "6CO₂ → C₆H₁₂O₆, π ≈ 3.14", "Page 1"):
            assert expected in content
        assert "**" not in content

    def test_long_content_flows_onto_more_pages(self, sandbox):
        from plugins.pdf_document import create_pdf
        from plugins.read_document import read_document

        create_pdf("Long", [{"paragraphs": ["word " * 400] * 10}], filename="long")
        assert "--- page 2 ---" in read_document("long.pdf")["content"]

    def test_is_listed_as_an_attachment_in_responses(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "model_impl", _TwoStepModel("create_file", dict(DOC_ARGS, format="pdf")))
        r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "make a pdf"}]})
        [attachment] = r.json()["attachments"]
        assert (attachment["filename"], attachment["content_type"]) == ("Worksheet.pdf", "application/pdf")

    def test_validates_input(self):
        from plugins.pdf_document import create_pdf
        assert "error" in create_pdf("", [])
        assert "error" in create_pdf("T", "not a list")
        assert "error" in create_pdf("T", ["not an object"])
        assert "error" in create_pdf("T", [{"bullets": 5}])
        assert create_pdf("T", [], filename=".pdf")["path"] == "document.pdf"


class _ScriptedModel:
    """Plays back a list of chat responses in order, recording what the
    tools wrote along the way."""

    def __init__(self, *responses):
        self.responses = list(responses)

    def chat(self, messages, tools=None, **kwargs):
        return self.responses.pop(0)


class _CallThenCapture:
    """Makes one tool call, then records the tool's result and answers."""

    def __init__(self, name, arguments):
        self.call = _tool_call(name, arguments)
        self.tool_result = None

    def chat(self, messages, tools=None, **kwargs):
        if self.call:
            call, self.call = self.call, None
            return call
        self.tool_result = json.loads(messages[-1]["content"])
        return _answer()


def _answer(text="ok"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _chat(client, monkeypatch, user, *responses, content="go"):
    monkeypatch.setattr(main_mod, "model_impl", _ScriptedModel(*responses))
    body = {"messages": [{"role": "user", "content": content}]}
    if user is not None:
        body["user"] = user
    return client.post("/v1/chat/completions", json=body).json()


class TestPerChatSandboxes:
    def test_each_chat_gets_its_own_hashed_folder(self, sandbox):
        a, b = file_ops.chat_sandbox("signal:+15551234567"), file_ops.chat_sandbox("signal:group:abc")
        assert a.parent == b.parent == sandbox / "chats"
        assert a != b
        assert "5551234567" not in str(a)  # no phone numbers in folder names
        assert file_ops.chat_sandbox(None) == file_ops.chat_sandbox("") == sandbox

    def test_upload_goes_into_the_chats_own_folder(self, client, sandbox):
        r = client.post("/v1/files", params={"filename": "secret.txt", "user": "signal:group:A"}, content=b"group A only")
        assert r.json()["path"] == "uploads/secret.txt"
        assert (file_ops.chat_sandbox("signal:group:A") / "uploads" / "secret.txt").read_bytes() == b"group A only"
        assert not (sandbox / "uploads" / "secret.txt").exists()

    def _tool_result_for(self, client, monkeypatch, user, name, arguments):
        """Run one tool call as chat `user` (None = web UI) and return what
        the tool gave back."""
        model = _CallThenCapture(name, arguments)
        monkeypatch.setattr(main_mod, "model_impl", model)
        body = {"messages": [{"role": "user", "content": "go"}]}
        if user:
            body["user"] = user
        client.post("/v1/chat/completions", json=body)
        return model.tool_result

    def test_another_chat_cannot_read_it(self, client, monkeypatch):
        client.post("/v1/files", params={"filename": "secret.txt", "user": "signal:group:A"}, content=b"group A only")
        args = {"path": "uploads/secret.txt"}
        assert self._tool_result_for(client, monkeypatch, "signal:group:A", "read_document", args)["content"] == "group A only"
        assert "no such file" in self._tool_result_for(client, monkeypatch, "signal:group:B", "read_document", args)["error"]
        # The web UI works in the root, which has no uploads/ of its own.
        assert "no such file" in self._tool_result_for(client, monkeypatch, None, "read_document", args)["error"]
        # ...and read_file can't reach across either.
        assert "error" in self._tool_result_for(client, monkeypatch, "signal:group:B", "read_file", {"path": "../" + file_ops.chat_sandbox("signal:group:A").name + "/uploads/secret.txt"})

    def test_memory_is_separate_per_chat(self, client, monkeypatch):
        self._tool_result_for(client, monkeypatch, "signal:+1555", "remember", {"key": "colour", "value": "blue"})
        assert self._tool_result_for(client, monkeypatch, "signal:+1555", "recall", {"key": "colour"})["value"] == "blue"
        other = self._tool_result_for(client, monkeypatch, "signal:+1666", "recall", {"key": "colour"})
        assert other.get("value") != "blue"
        web_ui = self._tool_result_for(client, monkeypatch, None, "recall", {"key": "colour"})
        assert web_ui.get("value") != "blue"

    def test_created_file_is_in_the_chat_folder_and_downloadable(self, client, monkeypatch):
        body = _chat(client, monkeypatch, "signal:group:A", _tool_call("create_file", DOC_ARGS), _answer())
        [attachment] = body["attachments"]
        folder = file_ops.chat_sandbox("signal:group:A").name
        assert attachment["path"] == "Worksheet.docx"
        assert attachment["url"] == f"/v1/files/chats/{folder}/Worksheet.docx"
        assert client.get(attachment["url"]).content[:2] == b"PK"

    def test_web_ui_requests_still_use_the_root(self, client, monkeypatch, sandbox):
        body = _chat(client, monkeypatch, None, _tool_call("create_file", DOC_ARGS), _answer())
        assert body["attachments"][0]["url"] == "/v1/files/Worksheet.docx"
        assert (sandbox / "Worksheet.docx").exists()

    def test_bad_user_values_fall_back_to_the_root(self, client, monkeypatch):
        for bad in (123, "   ", "x" * 300):
            body = _chat(client, monkeypatch, bad, _tool_call("create_file", DOC_ARGS), _answer())
            assert body["attachments"][0]["url"] == "/v1/files/Worksheet.docx"


class TestRemadeDocuments:
    def test_same_tool_and_title_keeps_only_the_latest(self, client, monkeypatch):
        first = dict(DOC_ARGS, filename="draft")
        second = dict(DOC_ARGS, filename="final")
        body = _chat(client, monkeypatch, None,
                     _tool_call("create_file", first), _tool_call("create_file", second), _answer())
        assert [a["filename"] for a in body["attachments"]] == ["final.docx"]

    def test_different_titles_are_all_kept(self, client, monkeypatch):
        other = {"format": "docx", "title": "Answers", "sections": []}
        body = _chat(client, monkeypatch, None,
                     _tool_call("create_file", DOC_ARGS), _tool_call("create_file", other), _answer())
        assert [a["filename"] for a in body["attachments"]] == ["Worksheet.docx", "Answers.docx"]

    def test_same_title_in_different_formats_are_both_kept(self, client, monkeypatch):
        body = _chat(client, monkeypatch, None,
                     _tool_call("create_file", DOC_ARGS), _tool_call("create_file", dict(DOC_ARGS, format="pdf")), _answer())
        assert [a["filename"] for a in body["attachments"]] == ["Worksheet.docx", "Worksheet.pdf"]


def test_images_count_as_a_fixed_size_in_history_trimming():
    image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 2_000_000}}
    message = {"role": "user", "content": [{"type": "text", "text": "what is this?"}, image]}
    assert main_mod._content_chars(message) == len("what is this?") + main_mod.IMAGE_CHARS_ESTIMATE
    older = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    # A big photo no longer pushes the rest of the conversation out.
    assert main_mod._truncate_messages(older + [message], max_tokens=2048) == older + [message]


class TestCreateFile:
    SECTIONS = [
        {"heading": "Cells", "paragraphs": ["Cells are **tiny**."], "bullets": ["Nucleus", "Mitochondria"]},
        {"bullets": "Just one point"},
    ]

    def test_docx(self, sandbox):
        from plugins.create_file import create_file
        from plugins.read_document import read_document
        result = create_file("docx", "Biology", self.SECTIONS)
        assert result["attachment"] == "Biology.docx"
        assert read_document("Biology.docx")["content"].splitlines() == [
            "## Biology", "## Cells", "Cells are tiny.", "- Nucleus", "- Mitochondria", "- Just one point",
        ]

    def test_bold_in_docx_is_real_bold(self, sandbox):
        from docx import Document
        from plugins.create_file import create_file
        create_file("docx", "B", [{"paragraphs": ["Cost: **£5** each"]}])
        runs = [(r.text, bool(r.bold)) for r in Document(str(sandbox / "B.docx")).paragraphs[1].runs]
        assert runs == [("Cost: ", False), ("£5", True), (" each", False)]

    def test_pdf(self, sandbox):
        from plugins.create_file import create_file
        from plugins.read_document import read_document
        assert create_file("PDF", "Biology", self.SECTIONS)["attachment"] == "Biology.pdf"
        assert "Cells are tiny." in read_document("Biology.pdf")["content"]

    def test_pptx_turns_each_section_into_a_slide(self, sandbox):
        from plugins.create_file import create_file
        from plugins.read_document import read_document
        result = create_file(".pptx", "Biology", self.SECTIONS)
        assert result["attachment"] == "Biology.pptx"
        assert result["slide_count"] == 3  # title slide + one per section
        content = read_document("Biology.pptx")["content"]
        assert "--- slide 2 ---\nCells\nCells are tiny.\nNucleus\nMitochondria" in content  # ** stripped
        assert "--- slide 3 ---\nJust one point" in content

    def test_unknown_format_and_bad_sections(self):
        from plugins.create_file import create_file
        assert "format must be one of" in create_file("xlsx", "T", [])["error"]
        assert "error" in create_file("pptx", "T", "not a list")
        assert "error" in create_file("pptx", "T", ["not an object"])


class TestTranscribe:
    def test_returns_the_transcription(self, client, monkeypatch):
        seen = {}

        def fake(audio):
            seen["audio"] = audio
            return {"text": "see you at four", "language": "en", "duration": 2.1}

        monkeypatch.setattr(voice, "transcribe", fake)
        r = client.post("/v1/transcribe", content=b"aac bytes")
        assert r.status_code == 200
        assert r.json() == {"text": "see you at four", "language": "en", "duration": 2.1}
        assert seen["audio"] == b"aac bytes"

    def test_undecodable_audio_is_a_clean_error(self, client, monkeypatch):
        def broken(audio):
            raise ValueError("Invalid data found when processing input")

        monkeypatch.setattr(voice, "transcribe", broken)
        assert client.post("/v1/transcribe", content=b"not audio").status_code == 422

    def test_empty_and_oversized_requests(self, client, monkeypatch):
        assert client.post("/v1/transcribe", content=b"").status_code == 400
        monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", 3)
        assert client.post("/v1/transcribe", content=b"1234").status_code == 413

    def test_cross_site_requests_are_refused(self, client):
        assert client.post("/v1/transcribe", content=b"x", headers={"Origin": "https://evil.example"}).status_code == 403


def _m4a_tone(seconds=1.5):
    """A real AAC-in-M4A file, like a Signal voice note, made with PyAV."""
    import io
    import math

    import av
    import numpy as np

    rate = 44100
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        stream = container.add_stream("aac", rate=rate)
        stream.layout = "mono"
        samples = (0.3 * np.sin(2 * math.pi * 440 * np.arange(int(rate * seconds)) / rate)).astype(np.float32)
        for start in range(0, len(samples), 1024):
            frame = av.AudioFrame.from_ndarray(samples[start:start + 1024].reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


class TestAudioDecoding:
    def test_a_signal_style_m4a_decodes_to_16khz_samples(self):
        # The exact path that failed live: faster-whisper's own decoder
        # broke against PyAV 19 on every voice note.
        samples = voice._decode(_m4a_tone(1.5))
        assert samples.dtype.name == "float32"
        assert abs(len(samples) / 16000 - 1.5) < 0.1
        assert 0.1 < abs(samples).max() <= 1.0

    def test_near_empty_audio_skips_the_model(self, monkeypatch):
        def no_model():
            raise AssertionError("the model shouldn't be loaded for no audio")

        import faster_whisper
        monkeypatch.setattr(faster_whisper, "WhisperModel", no_model)
        assert voice.transcribe(_m4a_tone(0.02))["text"] == ""

    def test_garbage_is_an_error(self):
        with pytest.raises(Exception):
            voice._decode(b"definitely not audio")


class TestSpeech:
    def test_speakable_reads_markdown_sensibly(self):
        from plugins.voice import speakable
        text = (
            "## Plan\n- **Wednesday** at *3:30*\n1. Bring `goggles`\n"
            "See [the club page](https://example.org/club) or https://example.org.\n```\nprint(1)\n```"
        )
        assert speakable(text) == (
            "Plan Wednesday at 3:30 Bring goggles See the club page or (link in the message). (code shown in the message)"
        )

    def test_speakable_leaves_snake_case_and_maths_alone(self):
        from plugins.voice import speakable
        assert speakable("Open my_file_name.txt, then 2 * 3 * 4 = 24.") == "Open my_file_name.txt, then 2 * 3 * 4 = 24."

    def test_long_text_is_cut_at_a_sentence(self, monkeypatch):
        monkeypatch.setattr(voice, "MAX_SPOKEN_CHARS", 40)
        spoken = voice.speakable("First sentence is here. Second sentence goes on. Third one too.")
        assert spoken == "First sentence is here. Second sentence goes on. The rest is in the message." or \
            spoken == "First sentence is here. The rest is in the message."

    def test_endpoint_returns_audio(self, client, monkeypatch):
        seen = {}

        def fake_speak(text):
            seen["text"] = text
            return b"m4a bytes"

        monkeypatch.setattr(voice, "speak", fake_speak)
        r = client.post("/v1/speak", json={"text": "Hello there"})
        assert (r.status_code, r.headers["content-type"], r.content) == (200, "audio/mp4", b"m4a bytes")
        assert seen["text"] == "Hello there"

    def test_endpoint_errors(self, client, monkeypatch):
        assert client.post("/v1/speak", json={"text": "  "}).status_code == 400

        assert client.post("/v1/speak", json={"text": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403

    def test_real_speech_round_trips_through_transcription(self):
        """Piper -> .m4a -> Whisper should give back (roughly) the words.
        Uses the real models, so it's skipped if they aren't available."""
        try:
            audio = voice.speak("The quick brown fox jumps over the lazy dog.")
            heard = voice.transcribe(audio)["text"].lower()
        except Exception as exc:
            pytest.skip(f"speech or transcription models unavailable: {exc}")
        assert audio[4:8] == b"ftyp"  # an MP4/M4A container
        assert "quick brown fox" in heard and "lazy dog" in heard


class TestVoiceIsAPlugin:
    def test_status_lists_the_voice_endpoints_when_installed(self, client):
        endpoints = client.get("/v1/status").json()["endpoints"]
        assert {"/v1/transcribe", "/v1/speak", "/v1/chat/completions"} <= set(endpoints)

    def test_plugins_can_add_endpoints(self, tmp_path, monkeypatch):
        import sys
        import types
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentic_harness import plugins as loader

        # The loader lists the folder, then imports "plugins.<name>" - so
        # stand the module in under that name.
        (tmp_path / "pinger.py").write_text("")
        pinger = types.ModuleType("plugins.pinger")
        pinger.register_routes = lambda app: app.add_api_route("/v1/ping", lambda: {"pong": True}, methods=["GET"])
        monkeypatch.setitem(sys.modules, "plugins.pinger", pinger)

        app = FastAPI()
        loader.load_plugins(str(tmp_path), app=app)
        assert TestClient(app).get("/v1/ping").json() == {"pong": True}

    def test_missing_voice_libraries_skip_the_plugin(self, monkeypatch, capsys):
        """Without the voice libraries, the plugin fails to import - so the
        loader skips it, logs why, and its endpoints never exist."""
        import importlib.util
        import sys
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "piper" else real_find_spec(name, *a, **k))
        monkeypatch.delitem(sys.modules, "plugins.voice", raising=False)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentic_harness import plugins as loader
        app = FastAPI()
        loader.load_plugins(app=app)

        assert "failed to load 'plugins.voice'" in capsys.readouterr().out
        assert TestClient(app).post("/v1/speak", json={"text": "hi"}).status_code == 404
