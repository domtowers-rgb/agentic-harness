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
    # 1 initial call + MAX_TOOL_ITERATIONS follow-ups + 1 final no-tools
    # round. This model keeps emitting tool calls even then, so the reply
    # is the plain-text fallback rather than an empty, tool_calls-only turn.
    assert calls["n"] == main_mod.MAX_TOOL_ITERATIONS + 2
    message = r.json()["choices"][0]["message"]
    assert message["content"] == main_mod._tool_limit_fallback()
    assert not message.get("tool_calls")


def _tool_call_response(name, arguments="{}", call_id="call_1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}],
    }}]}


def _text_response(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class ScriptedModel:
    """Returns `responses` in order (repeating the last one), recording the
    messages and tools each call was given."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": kwargs.get("tool_choice")})
        return self.responses[min(len(self.calls), len(self.responses)) - 1]


def _last_tool_result(model_call):
    tool_messages = [m for m in model_call["messages"] if m["role"] == "tool"]
    return json.loads(tool_messages[-1]["content"])


def test_tool_call_limit_asks_for_a_final_answer_with_tool_choice_none(client, monkeypatch):
    loops = [_tool_call_response("hello", call_id=f"call_{i}") for i in range(main_mod.MAX_TOOL_ITERATIONS + 1)]
    fake = ScriptedModel(*loops, _text_response("here's what I found"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    assert r.json()["choices"][0]["message"]["content"] == "here's what I found"
    final_call = fake.calls[-1]
    assert final_call["tool_choice"] == "none"
    assert all(c["tool_choice"] in (None, "auto") for c in fake.calls[:-1])
    assert final_call["messages"][-1] == main_mod.TOOL_LIMIT_NUDGE


def test_unknown_tool_call_is_reported_back_to_the_model(client, monkeypatch):
    fake = ScriptedModel(_tool_call_response("does_not_exist"), _text_response("sorry, no such tool"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "sorry, no such tool"
    assert _last_tool_result(fake.calls[1]) == {"error": "unknown function: does_not_exist"}


def test_a_plugin_that_raises_is_reported_back_to_the_model(client, monkeypatch):
    def boom():
        raise ValueError("upstream timed out")

    monkeypatch.setitem(main_mod.plugins.registry._registry, "boom", {"callable": boom, "spec": None})
    fake = ScriptedModel(_tool_call_response("boom"), _text_response("that tool failed"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "that tool failed"
    assert _last_tool_result(fake.calls[1]) == {"error": "boom failed: ValueError: upstream timed out"}


def test_invalid_tool_arguments_are_reported_back_to_the_model(client, monkeypatch):
    fake = ScriptedModel(_tool_call_response("hello", arguments="{not json"), _text_response("oops"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    assert r.status_code == 200
    assert "not valid JSON" in _last_tool_result(fake.calls[1])["error"]


def test_tools_run_off_the_event_loop(client, monkeypatch):
    import asyncio

    seen = {}

    def where_am_i():
        try:
            asyncio.get_running_loop()
            seen["on_event_loop"] = True
        except RuntimeError:
            seen["on_event_loop"] = False
        return "ok"

    monkeypatch.setitem(main_mod.plugins.registry._registry, "where_am_i", {"callable": where_am_i, "spec": None})
    monkeypatch.setattr(main_mod, "model_impl", ScriptedModel(_tool_call_response("where_am_i"), _text_response("done")))
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    assert seen == {"on_event_loop": False}


def test_missing_model_returns_a_machine_readable_404(client, monkeypatch):
    class MissingModelModel:
        def chat(self, messages, tools=None, **kwargs):
            raise RuntimeError("404 - model 'ghost-model' not found")

        def list_models(self, *args, **kwargs):
            return ["real-model-a", "real-model-b"]

    monkeypatch.setattr(main_mod, "model_impl", MissingModelModel())
    monkeypatch.setattr(model, "DEFAULT_MODEL", "ghost-model")
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail == {
        "type": "model_not_found",
        "model": "ghost-model",
        "available_models": ["real-model-a", "real-model-b"],
    }


def test_missing_model_uses_the_explicit_model_field_over_the_default(client, monkeypatch):
    class MissingModelModel:
        def chat(self, messages, tools=None, **kwargs):
            raise RuntimeError("model not found")

        def list_models(self, *args, **kwargs):
            return ["real-model"]

    monkeypatch.setattr(main_mod, "model_impl", MissingModelModel())
    r = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
        "model": "explicitly-requested-ghost",
    })
    assert r.status_code == 404
    assert r.json()["detail"]["model"] == "explicitly-requested-ghost"


def test_a_failure_that_is_not_a_missing_model_returns_a_plain_502(client, monkeypatch):
    class DownModel:
        def chat(self, messages, tools=None, **kwargs):
            raise RuntimeError("connection refused")

        def list_models(self, *args, **kwargs):
            # The requested model *is* in the list - so a real, available
            # model still failed for some other reason (backend down, etc).
            return [main_mod.model.DEFAULT_MODEL]

    monkeypatch.setattr(main_mod, "model_impl", DownModel())
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502


def test_a_failure_falls_back_to_502_if_the_model_list_is_also_unavailable(client, monkeypatch):
    class TotallyDownModel:
        def chat(self, messages, tools=None, **kwargs):
            raise RuntimeError("connection refused")

        def list_models(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(main_mod, "model_impl", TotallyDownModel())
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502


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


def test_personality_prepended_when_client_supplies_no_system_message(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["messages"] = messages
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    monkeypatch.setattr(main_mod, "PERSONALITY", "Be concise and friendly.")

    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})

    assert captured["messages"][0] == {"role": "system", "content": "Be concise and friendly."}
    assert captured["messages"][1] == {"role": "user", "content": "hi"}


def test_personality_not_prepended_when_client_supplies_own_system_message(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["messages"] = messages
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    monkeypatch.setattr(main_mod, "PERSONALITY", "Be concise and friendly.")

    client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "system", "content": "Custom system prompt."},
            {"role": "user", "content": "hi"},
        ],
    })

    assert captured["messages"][0] == {"role": "system", "content": "Custom system prompt."}
    assert len(captured["messages"]) == 2


def test_no_personality_configured_leaves_messages_untouched(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["messages"] = messages
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    monkeypatch.setattr(main_mod, "PERSONALITY", "")

    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})

    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_load_personality_returns_empty_for_missing_file():
    assert main_mod._load_personality("this/file/does/not/exist.txt") == ""


def test_load_personality_strips_whitespace(tmp_path):
    path = tmp_path / "SOUL.md"
    path.write_text("  Be nice.  \n", encoding="utf-8")
    assert main_mod._load_personality(str(path)) == "Be nice."


def test_load_personality_warns_when_over_token_guideline(tmp_path, capsys):
    path = tmp_path / "SOUL.md"
    path.write_text("x" * 1000, encoding="utf-8")  # ~250 estimated tokens, over the 100 guideline
    main_mod._load_personality(str(path))
    assert "over the 100-token guideline" in capsys.readouterr().out


def test_load_personality_no_warning_when_within_guideline(tmp_path, capsys):
    path = tmp_path / "SOUL.md"
    path.write_text("Be nice.", encoding="utf-8")
    main_mod._load_personality(str(path))
    assert capsys.readouterr().out == ""


def test_max_tokens_defaults_when_omitted(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert captured["max_tokens"] == main_mod.DEFAULT_MAX_TOKENS


def test_max_tokens_falls_back_to_default_on_explicit_null(client, monkeypatch):
    # A client-sent "max_tokens": null must not slip through as None - that
    # would omit the cap entirely downstream (model.py only forwards
    # max_tokens when it's not None), reproducing the original
    # runaway-generation bug this default exists to prevent.
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": None})
    assert captured["max_tokens"] == main_mod.DEFAULT_MAX_TOKENS


def test_max_tokens_explicit_value_is_respected(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 77})
    assert captured["max_tokens"] == 77


def test_history_max_tokens_is_configurable(client, monkeypatch):
    captured = {}

    class SpyModel:
        def chat(self, messages, tools=None, **kwargs):
            captured["messages"] = messages
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", SpyModel())
    monkeypatch.setattr(main_mod, "HISTORY_MAX_TOKENS", 1)  # ~4 chars of budget - forces heavy trimming
    monkeypatch.setattr(main_mod, "PERSONALITY", "")  # isolate from the personality prepend

    long_history = [{"role": "user", "content": "x" * 100} for _ in range(20)]
    client.post("/v1/chat/completions", json={"messages": long_history})
    assert len(captured["messages"]) < len(long_history)


def test_audit_log_records_successful_tool_call(client, monkeypatch, tmp_path):
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(main_mod, "AUDIT_LOG_FILE", str(log_path))

    class ToolModel:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function",
                                     "function": {"name": "hello", "arguments": '{"name": "Dave"}'}}],
                }}]}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    monkeypatch.setattr(main_mod, "model_impl", ToolModel())
    client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "say hi"}],
        "tools": [{"type": "function", "function": {"name": "hello"}}],
    })

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "hello"
    assert "Dave" in entry["args"]
    assert "Hello" in entry["result"]
    assert "timestamp" in entry


def test_audit_log_records_unknown_tool_call(client, monkeypatch, tmp_path):
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(main_mod, "AUDIT_LOG_FILE", str(log_path))

    monkeypatch.setattr(main_mod, "model_impl", ScriptedModel(_tool_call_response("does_not_exist"), _text_response("ok")))
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "does_not_exist"
    assert "unknown function" in entry["result"]


def test_audit_log_truncates_long_values():
    result = main_mod._truncate_for_audit("x" * 1000)
    assert len(result) < 1000
    assert result.endswith("...(truncated)")


def test_audit_log_short_values_not_truncated():
    assert main_mod._truncate_for_audit("short") == "short"


def test_audit_log_write_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(main_mod, "AUDIT_LOG_FILE", "this/path/does/not/exist/audit.log")
    main_mod._audit_log("some_tool", {"a": 1}, {"ok": True})  # must not raise


def test_truncate_messages_drops_oldest_when_over_budget():
    from agentic_harness.main import _truncate_messages
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(50)]
    out = _truncate_messages(messages, max_tokens=10)
    assert len(out) < len(messages)


def test_truncate_messages_keeps_short_history():
    from agentic_harness.main import _truncate_messages
    messages = [{"role": "user", "content": "hi"}]
    assert _truncate_messages(messages, max_tokens=1000) == messages


def test_truncate_messages_never_drops_the_newest_message():
    from agentic_harness.main import _truncate_messages
    messages = [{"role": "user", "content": "old"}, {"role": "user", "content": "x" * 10000}]
    assert _truncate_messages(messages, max_tokens=10) == [messages[-1]]


def test_truncate_messages_keeps_a_leading_system_message():
    from agentic_harness.main import _truncate_messages
    messages = [{"role": "system", "content": "be terse"}] + [{"role": "user", "content": "x" * 100} for _ in range(10)]
    out = _truncate_messages(messages, max_tokens=60)
    assert out[0] == messages[0]
    assert out[-1] == messages[-1]
    assert len(out) < len(messages)


def test_truncate_messages_does_not_start_on_an_orphaned_tool_result():
    from agentic_harness.main import _truncate_messages
    messages = [
        {"role": "user", "content": "x" * 400},
        {"role": "assistant", "content": "let me check " * 5, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "y" * 40},
        {"role": "user", "content": "next question"},
    ]
    # 80-char budget: the assistant turn has to go, which would leave the
    # tool result first - it goes too rather than being sent orphaned.
    out = _truncate_messages(messages, max_tokens=20)
    assert out == [messages[-1]]


def test_truncate_messages_tolerates_non_string_content():
    from agentic_harness.main import _truncate_messages
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "user", "content": [{"type": "text", "text": "x" * 100}]},
        {"role": "user", "content": "hi"},
    ]
    assert _truncate_messages(messages, max_tokens=5) == [messages[-1]]


def test_streaming_tool_call_limit_asks_for_a_final_answer(client, monkeypatch):
    rounds = []

    class StreamInfiniteModel:
        def chat_stream(self, messages, tools=None, **kwargs):
            rounds.append({"tool_choice": kwargs.get("tool_choice"), "last": dict(messages[-1])})
            if kwargs.get("tool_choice") == "none":
                yield {"choices": [{"delta": {"content": "final answer"}, "finish_reason": "stop"}]}
                return
            yield {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": f"call_{len(rounds)}", "type": "function",
                 "function": {"name": "hello", "arguments": "{}"}},
            ]}, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(main_mod, "model_impl", StreamInfiniteModel())
    with client.stream("POST", "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "go"}], "stream": True,
    }) as r:
        lines = [line for line in r.iter_lines() if line]

    assert len(rounds) == main_mod.MAX_TOOL_ITERATIONS + 1
    assert rounds[-1] == {"tool_choice": "none", "last": main_mod.TOOL_LIMIT_NUDGE}
    assert lines[-1] == "data: [DONE]"
    assert "final answer" in lines[-2]


def test_streaming_tool_call_limit_falls_back_when_no_text_comes_back(client, monkeypatch):
    class StreamToolsForever:
        def chat_stream(self, messages, tools=None, **kwargs):
            yield {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_x", "type": "function", "function": {"name": "hello", "arguments": "{}"}},
            ]}, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(main_mod, "model_impl", StreamToolsForever())
    with client.stream("POST", "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "go"}], "stream": True,
    }) as r:
        lines = [line for line in r.iter_lines() if line]

    fallback = json.loads(lines[-2][len("data: "):])
    assert fallback["choices"][0]["delta"]["content"] == main_mod._tool_limit_fallback()


def test_streaming_unknown_tool_is_reported_back_and_continues(client, monkeypatch):
    class StreamBadTool:
        def chat_stream(self, messages, tools=None, **kwargs):
            if messages[-1]["role"] == "tool":
                yield {"choices": [{"delta": {"content": json.loads(messages[-1]["content"])["error"]}}]}
                return
            yield {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "nope", "arguments": "{}"}},
            ]}, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(main_mod, "model_impl", StreamBadTool())
    with client.stream("POST", "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "go"}], "stream": True,
    }) as r:
        lines = [line for line in r.iter_lines() if line]

    assert "unknown function: nope" in lines[-2]
    assert lines[-1] == "data: [DONE]"


def test_a_tool_is_never_run_twice(client, monkeypatch):
    runs = []

    def half_done(**kwargs):
        runs.append(kwargs)
        raise TypeError("bug inside the plugin, after a side effect")

    monkeypatch.setitem(main_mod.plugins.registry._registry, "half_done", {"callable": half_done, "spec": None})
    fake = ScriptedModel(_tool_call_response("half_done", arguments='{"x": 1}'), _text_response("ok"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})

    assert runs == [{"x": 1}]
    assert "TypeError" in _last_tool_result(fake.calls[1])["error"]


def test_wrong_argument_names_come_back_as_an_error(client, monkeypatch):
    fake = ScriptedModel(_tool_call_response("hello", arguments='{"nmae": "typo"}'), _text_response("ok"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})
    assert "unexpected keyword argument 'nmae'" in _last_tool_result(fake.calls[1])["error"]


def test_non_object_arguments_are_refused(client, monkeypatch):
    fake = ScriptedModel(_tool_call_response("hello", arguments='["a", "list"]'), _text_response("ok"))
    monkeypatch.setattr(main_mod, "model_impl", fake)
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "go"}]})
    assert "JSON object" in _last_tool_result(fake.calls[1])["error"]


class TestCrossSiteProtection:
    def test_local_requests_and_same_origin_web_ui_are_allowed(self, client):
        assert client.get("/v1/status").status_code == 200
        assert client.get("/v1/status", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
        assert client.get("/v1/status", headers={"Origin": "http://localhost:8000"}).status_code == 200

    def test_cross_site_requests_are_refused(self, client):
        for origin in ("https://evil.example", "null", "http://127.0.0.1.evil.example"):
            r = client.post("/v1/connect", headers={"Origin": origin, "Content-Type": "text/plain"},
                            content='{"base_url": "http://evil.example/v1"}')
            assert r.status_code == 403, origin
        assert client.get("/v1/status").json()["base_url"] != "http://evil.example/v1"

    def test_cross_site_upload_is_refused(self, client, tmp_path, monkeypatch):
        import plugins.file_ops as file_ops
        monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path)
        r = client.post("/v1/files", params={"filename": "x.txt"}, headers={"Origin": "https://evil.example"}, content=b"x")
        assert r.status_code == 403
        assert not (tmp_path / "uploads").exists()

    def test_dns_rebinding_host_is_refused(self):
        from fastapi.testclient import TestClient
        rebinding = TestClient(main_mod.app, base_url="http://evil.example:8000")
        assert rebinding.get("/v1/status").status_code == 400

    def test_extra_hosts_can_be_allowed(self, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr(main_mod, "ALLOWED_HOSTS", main_mod.ALLOWED_HOSTS + ["harness.lan"])
        # The Origin check reads ALLOWED_HOSTS live; the Host check was
        # configured at startup, so only the former is exercised here.
        local = TestClient(main_mod.app, base_url="http://127.0.0.1")
        assert local.get("/v1/status", headers={"Origin": "http://harness.lan:8000"}).status_code == 200


def test_stopping_a_stream_early_closes_the_model_stream():
    """When the consumer goes away (client disconnected), the model stream
    must be closed promptly - not read to the end in the background."""
    import asyncio
    import threading
    import time

    produced, closed = [], threading.Event()

    def slow_model_stream():
        try:
            for i in range(1000):
                time.sleep(0.01)
                produced.append(i)
                yield {"n": i}
        finally:
            closed.set()

    async def consume_two_then_stop():
        from contextlib import aclosing
        async with aclosing(main_mod._iter_in_thread(slow_model_stream())) as chunks:
            got = []
            async for chunk in chunks:
                got.append(chunk["n"])
                if len(got) == 2:
                    break
            return got

    assert asyncio.run(consume_two_then_stop()) == [0, 1]
    assert closed.wait(2), "model stream was never closed"
    assert len(produced) < 10  # stopped within a few items, not all 1000
