from agentic_harness import model


def test_mock_chat_echoes_last_message():
    m = model.MockModel()
    resp = m.chat([{"role": "user", "content": "hello"}])
    assert resp["choices"][0]["message"]["content"] == "Echo: hello"


def test_mock_chat_triggers_tool_call_when_tools_present():
    m = model.MockModel()
    resp = m.chat(
        [{"role": "user", "content": "call:calculate"}],
        tools=[{"type": "function", "function": {"name": "calculate"}}],
    )
    message = resp["choices"][0]["message"]
    assert message["content"] is None
    assert message["tool_calls"][0]["function"]["name"] == "calculate"


def test_mock_chat_no_tool_call_without_tools():
    m = model.MockModel()
    resp = m.chat([{"role": "user", "content": "call:calculate"}])
    assert resp["choices"][0]["message"].get("tool_calls") is None


def test_mock_chat_stream_yields_content_then_stop():
    m = model.MockModel()
    chunks = list(m.chat_stream([{"role": "user", "content": "a b"}]))
    joined = "".join(
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if "delta" in c["choices"][0] and "content" in c["choices"][0]["delta"]
    )
    assert joined == "Echo: a b"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_mock_chat_stream_tool_call():
    m = model.MockModel()
    chunks = list(
        m.chat_stream(
            [{"role": "user", "content": "call:calculate"}],
            tools=[{"type": "function", "function": {"name": "calculate"}}],
        )
    )
    assert len(chunks) == 1
    delta = chunks[0]["choices"][0]["delta"]
    assert delta["tool_calls"][0]["function"]["name"] == "calculate"
    assert chunks[0]["choices"][0]["finish_reason"] == "tool_calls"


def test_mock_list_models():
    assert model.MockModel().list_models() == ["mock"]


def test_openai_model_default_model_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    m = model.OpenAIModel()
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop-before-network")

    m._client.chat.completions.create = fake_create
    try:
        m.chat(messages=[{"role": "user", "content": "hi"}], model=None)
    except RuntimeError:
        pass
    assert captured["model"] == model.DEFAULT_MODEL


def test_openai_model_forwards_max_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    m = model.OpenAIModel()
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop-before-network")

    m._client.chat.completions.create = fake_create
    try:
        m.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=42)
    except RuntimeError:
        pass
    assert captured["max_tokens"] == 42


def test_openai_model_omits_max_tokens_when_not_given(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    m = model.OpenAIModel()
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop-before-network")

    m._client.chat.completions.create = fake_create
    try:
        m.chat(messages=[{"role": "user", "content": "hi"}])
    except RuntimeError:
        pass
    assert "max_tokens" not in captured


def test_openai_model_uses_custom_base_url(monkeypatch):
    m = model.OpenAIModel(api_key="k", base_url="http://127.0.0.1:9999/v1")
    assert str(m._client.base_url).rstrip("/") == "http://127.0.0.1:9999/v1"
