"""The optional podcast and council plugins."""
import time

import numpy as np
import pytest

import plugins.council as council
import plugins.file_ops as file_ops
import plugins.podcast as podcast


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path)
    return tmp_path


class TestPodcast:
    @pytest.fixture
    def voices(self, monkeypatch):
        """Stand-in voices: 0.5 s of a different tone per host, at
        different sample rates, recording what each was asked to say."""
        said = []

        def fake_synthesize(text, voice=None):
            said.append((voice, text))
            rate = 22050 if voice == podcast.VOICES[0] else 16000
            return (0.2 * np.sin(np.arange(rate // 2) * (0.05 if rate == 22050 else 0.09))).astype(np.float32), rate

        monkeypatch.setattr(podcast, "synthesize", fake_synthesize)
        return said

    SCRIPT = [
        {"host": 1, "text": "Welcome to **History Bites**!"},
        {"host": 2, "text": "Today: the Battle of Hastings, 1066."},
        {"host": 1, "text": "Let's dive in."},
    ]

    def test_voices_each_host_and_saves_one_audio_file(self, voices, sandbox):
        result = podcast.create_podcast("History Bites: 1066", self.SCRIPT)
        assert result["path"] == result["attachment"] == "History_Bites_1066.m4a"
        assert result["lines"] == 3 and "sent to the user automatically" in result["note"]
        # Host 1 and 2 got their own voices; markdown was read sensibly.
        assert voices == [
            (podcast.VOICES[0], "Welcome to History Bites!"),
            (podcast.VOICES[1], "Today: the Battle of Hastings, 1066."),
            (podcast.VOICES[0], "Let's dive in."),
        ]
        data = (sandbox / "History_Bites_1066.m4a").read_bytes()
        assert data[4:8] == b"ftyp"
        # 3 lines x 0.5 s + 3 pauses, all at one sample rate.
        assert result["duration_seconds"] == round(3 * 0.5 + 3 * podcast.PAUSE_SECONDS)

    def test_bad_scripts_are_explained(self, voices):
        assert "title is required" in podcast.create_podcast("", self.SCRIPT)["error"]
        assert "list of" in podcast.create_podcast("T", [])["error"]
        assert "line 1" in podcast.create_podcast("T", [{"host": 1, "text": "hi"}, {"host": 3, "text": "x"}])["error"]
        assert "too long" in podcast.create_podcast("T", [{"host": 1, "text": "x"}] * (podcast.MAX_LINES + 1))["error"]

    def test_is_registered_as_a_tool(self, client):
        names = {p["name"] for p in client.get("/v1/plugins").json()["plugins"]}
        assert "create_podcast" in names


class FakeBackend:
    def __init__(self, loaded=("model-a", "model-b", "model-c"), delay=0.0, failing=()):
        self.loaded, self.delay, self.failing = loaded, delay, failing
        self.asked = []

    def model_details(self):
        details = {m: {"loaded": True, "type": "llm"} for m in self.loaded}
        details.update({"unloaded-one": {"loaded": False, "type": "llm"}, "embedder": {"loaded": True, "type": "embeddings"}})
        return details

    def chat(self, messages, model=None, **kwargs):
        self.asked.append((model, messages[-1]["content"]))
        time.sleep(self.delay)
        if model in self.failing:
            raise RuntimeError("Failed to load model")
        return {"choices": [{"message": {"role": "assistant", "content": f"{model} says yes"}}]}


class TestCouncil:
    def test_asks_every_loaded_chat_model_and_returns_their_answers(self, monkeypatch):
        backend = FakeBackend()
        monkeypatch.setattr(council, "_backend", lambda: backend)
        result = council.ask_council("Is a hot dog a sandwich?")
        assert result["council"] == [
            {"model": "model-a", "answer": "model-a says yes"},
            {"model": "model-b", "answer": "model-b says yes"},
            {"model": "model-c", "answer": "model-c says yes"},
        ]
        assert "You are the judge" in result["instructions"]
        # Not the unloaded model, nor the embedding model.
        assert sorted(m for m, _ in backend.asked) == ["model-a", "model-b", "model-c"]

    def test_members_are_asked_at_the_same_time(self, monkeypatch):
        backend = FakeBackend(delay=0.4)
        monkeypatch.setattr(council, "_backend", lambda: backend)
        start = time.monotonic()
        council.ask_council("question")
        assert time.monotonic() - start < 0.9  # three 0.4 s answers in parallel, not 1.2 s in turn

    def test_a_member_that_fails_is_noted_not_fatal(self, monkeypatch):
        monkeypatch.setattr(council, "_backend", lambda: FakeBackend(failing=("model-b",)))
        answers = {a["model"]: a["answer"] for a in council.ask_council("q")["council"]}
        assert answers["model-a"] == "model-a says yes"
        assert answers["model-b"].startswith("(couldn't answer: Failed to load model")

    def test_needs_two_members(self, monkeypatch):
        monkeypatch.setattr(council, "_backend", lambda: FakeBackend(loaded=("only-one",)))
        assert "at least two chat models loaded, and only 1 is: only-one" in council.ask_council("q")["error"]

    def test_fixed_members_override_what_is_loaded(self, monkeypatch):
        backend = FakeBackend()
        monkeypatch.setattr(council, "_backend", lambda: backend)
        monkeypatch.setattr(council, "FIXED_MEMBERS", ["chosen-1", "chosen-2"])
        assert [a["model"] for a in council.ask_council("q")["council"]] == ["chosen-1", "chosen-2"]

    def test_through_the_chat_endpoint(self, client, monkeypatch):
        """The model calls ask_council; the members' answers come back to it
        as the tool result, for it to judge."""
        import json
        from agentic_harness import main as main_mod

        monkeypatch.setattr(council, "_backend", lambda: FakeBackend(loaded=("model-a", "model-b")))
        seen = {}

        class Judge:
            def __init__(self):
                self.step = 0

            def chat(self, messages, tools=None, **kwargs):
                self.step += 1
                if self.step == 1:
                    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "ask_council", "arguments": json.dumps({"question": "hot dog?"})},
                    }]}}]}
                seen["tool_result"] = json.loads(messages[-1]["content"])
                return {"choices": [{"message": {"role": "assistant", "content": "Verdict: both say yes."}}]}

        monkeypatch.setattr(main_mod, "model_impl", Judge())
        r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "/council hot dog?"}]})
        assert r.json()["choices"][0]["message"]["content"] == "Verdict: both say yes."
        assert [a["model"] for a in seen["tool_result"]["council"]] == ["model-a", "model-b"]
