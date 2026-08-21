import json

from agentic_harness import main as main_mod, model


def test_chat_completions_basic(client):
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Echo: hello"


def test_chat_completions_streaming(client):
    with client.stream(
        "POST", "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi there"}], "stream": True},
    ) as r:
        assert r.status_code == 200
        lines = [line for line in r.iter_lines() if line]
    assert lines[-1] == "data: [DONE]"
    assert any("Echo: hi there" in line for line in lines)


def test_tool_call_loop_executes_and_continues(client, monkeypatch):
    calls = {"n": 0}

    class LoopModel:
        def chat(self, messages, tools=None, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": f"call_{calls['n']}", "type": "function",
                                     "function": {"name": "hello", "arguments": "{}"}}],
                }}]}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    monkeypatch.setattr(main_mod, "model_impl", LoopModel())
    r = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": [{"type": "function", "function": {"name": "hello"}}],
    })
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "done"
    assert calls["n"] == 3


def test_tool_call_loop_respects_iteration_cap(client, monkeypatch):
    calls = {"n": 0}

    class InfiniteModel:
        def chat(self, messages, tools=None, **kwargs):
            calls["n"] += 1
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"call_{calls['n']}", "type": "function",
                                 "function": {"name": "hello", "arguments": "{}"}}],
            }}]}

    monkeypatch.setattr(main_mod, "model_impl", InfiniteModel())
    r = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": [{"type": "function", "function": {"name": "hello"}}],
    })
    assert r.status_code == 200
    # 1 initial call + MAX_TOOL_ITERATIONS follow-ups, then it gives up and
    # returns the last (still tool_calls-bearing) response.
    assert calls["n"] == main_mod.MAX_TOOL_ITERATIONS + 1


def test_unknown_tool_call_returns_400(client, monkeypatch):
    class BadToolModel:
        def chat(self, messages, tools=None, **kwargs):
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                 "function": {"name": "does_not_exist", "arguments": "{}"}}],
            }}]}

    monkeypatch.setattr(main_mod, "model_impl", BadToolModel())
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})
    assert r.status_code == 400


def test_streaming_tool_call_executes_and_continues(client, monkeypatch):
    class StreamLoopModel:
        def chat_stream(self, messages, tools=None, **kwargs):
            if messages[-1]["content"] == "go":
                yield {"choices": [{
                    "delta": {"tool_calls": [
                        {"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": "hello", "arguments": "{}"}},
                    ]},
                    "finish_reason": "tool_calls",
                }]}
            else:
                yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(main_mod, "model_impl", StreamLoopModel())
    with client.stream("POST", "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": [{"type": "function", "function": {"name": "hello"}}],
        "stream": True,
    }) as r:
        lines = [line for line in r.iter_lines() if line]

    assert lines[-1] == "data: [DONE]"
    events = [json.loads(line[len("data: "):]) for line in lines[:-1]]
    contents = [
        e["choices"][0]["delta"].get("content")
        for e in events
        if "content" in e["choices"][0].get("delta", {})
    ]
    assert "done" in contents


def test_models_endpoint(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    assert {"id": "mock", "object": "model"} in r.json()["data"]


def test_status_endpoint_mock_backend(client):
    r = client.get("/v1/status")
    assert r.status_code == 200
    assert r.json()["backend"] == "mock"


def test_plugins_endpoint_lists_registered_plugins(client):
    r = client.get("/v1/plugins")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()["plugins"]}
    assert "calculate" in names
    assert "hello" in names


def test_connect_requires_base_url(client):
    r = client.post("/v1/connect", json={})
    assert r.status_code == 400


def test_connect_reports_failure_cleanly(client, monkeypatch):
    def fake_list_models(self):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(model.OpenAIModel, "list_models", fake_list_models)
    r = client.post("/v1/connect", json={"base_url": "http://127.0.0.1:1/v1"})
    assert r.status_code == 400
    assert "could not connect" in r.json()["detail"]


def test_connect_success_switches_backend(client, monkeypatch):
    # Register the current values with monkeypatch so they're restored after
    # this test regardless of what the endpoint mutates them to.
    monkeypatch.setattr(main_mod, "model_impl", main_mod.model_impl)
    monkeypatch.setattr(main_mod, "MODEL_BACKEND", main_mod.MODEL_BACKEND)
    monkeypatch.setattr(main_mod, "CURRENT_BASE_URL", main_mod.CURRENT_BASE_URL)

    def fake_list_models(self):
        return ["some-model"]

    monkeypatch.setattr(model.OpenAIModel, "list_models", fake_list_models)
    r = client.post("/v1/connect", json={"base_url": "http://127.0.0.1:1234/v1"})
    assert r.status_code == 200
    assert r.json()["models"] == ["some-model"]

    status = client.get("/v1/status").json()
    assert status["backend"] == "openai"
    assert status["base_url"] == "http://127.0.0.1:1234/v1"


def test_enabled_plugins_filters_tools_sent_to_model(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["tools"] = tools
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())

    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert "calculate" in [t["function"]["name"] for t in captured["tools"]]

    client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
        "enabled_plugins": ["calculate", "get_current_time"],
    })
    assert sorted(t["function"]["name"] for t in captured["tools"]) == ["calculate", "get_current_time"]

    client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
        "enabled_plugins": [],
    })
    assert captured["tools"] == []


def test_client_supplied_tool_not_duplicated(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["tools"] = tools
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "calculate", "description": "custom override"}}],
    })
    calculate_tools = [t for t in captured["tools"] if t["function"]["name"] == "calculate"]
    assert len(calculate_tools) == 1
    assert calculate_tools[0]["function"]["description"] == "custom override"


def test_truncate_messages_drops_oldest_when_over_budget():
    from agentic_harness.main import _truncate_messages
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(50)]
    out = _truncate_messages(messages, max_tokens=10)
    assert len(out) < len(messages)


def test_truncate_messages_keeps_short_history():
    from agentic_harness.main import _truncate_messages
    messages = [{"role": "user", "content": "hi"}]
    assert _truncate_messages(messages, max_tokens=1000) == messages
