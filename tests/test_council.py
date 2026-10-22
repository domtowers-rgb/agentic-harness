"""The optional council plugin."""
import time

import pytest

import plugins.council as council
import plugins.file_ops as file_ops


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path)
    return tmp_path


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
