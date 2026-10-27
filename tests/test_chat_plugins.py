"""The optional read_chat and take_to_dm plugins."""
import json

import pytest

from plugins import read_chat as rc
from plugins import take_to_dm as dm


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "histories": {
            "signal:+1": [{"role": "user", "content": "hi", "at": 1000}],
            "signal:group:abc": [
                {"role": "user", "content": "Dom: hello", "at": 2000},
                {"role": "assistant", "content": "hi Dom", "at": 2001},
            ],
        },
        "summaries": {"signal:group:abc": "They said hello."},
        "names": {"signal:group:abc": "Friday Club"},
    }))
    monkeypatch.setattr(rc, "HISTORY_FILE", str(path))


def test_lists_chats_by_name(history):
    chats = rc.read_chat()["chats"]
    assert {c["chat"] for c in chats} == {"Friday Club", "private chat"}


def test_reads_a_chat_by_name_or_group(history):
    for ask in ("friday", "group"):
        result = rc.read_chat(ask)
        assert result["chat"] == "Friday Club"
        assert result["messages"][0].endswith("Dom: hello") and result["messages"][1].endswith("Niamh (you): hi Dom")
        assert result["earlier_summary"] == "They said hello."


def test_unknown_chat_and_missing_file(history, monkeypatch):
    assert "error" in rc.read_chat("nope")
    monkeypatch.setattr(rc, "HISTORY_FILE", "/nonexistent/history.json")
    assert "error" in rc.read_chat()


def test_read_chat_is_owner_only():
    from agentic_harness.plugins import PluginRegistry
    registry = PluginRegistry()
    rc.register(registry)
    assert registry.owner_only("read_chat")


def test_take_to_dm_returns_the_message():
    assert dm.take_to_dm("  carrying on  ")["dm"] == "carrying on"
    assert "error" in dm.take_to_dm("  ")


def test_read_chat_refuses_in_a_group(history):
    import plugins.file_ops as file_ops
    from plugins.read_chat import read_chat

    with file_ops.using_sandbox(file_ops.chat_sandbox("signal:group:abc"), chat="signal:group:abc"):
        assert "only works in the owner's private chat" in read_chat()["error"]
    with file_ops.using_sandbox(file_ops.chat_sandbox("signal:+44"), chat="signal:+44"):
        assert "error" not in read_chat()


def test_take_to_dm_is_only_offered_in_groups(client, monkeypatch):
    from agentic_harness import main as main_mod

    offered = []

    class Spy:
        def chat(self, messages, tools=None, **kwargs):
            offered.append({t["function"]["name"] for t in tools})
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", Spy())
    for chat in ("signal:+44", "signal:group:abc"):
        client.post("/v1/chat/completions", json={"user": chat, "messages": [{"role": "user", "content": "hi"}]})
    assert "take_to_dm" not in offered[0] and "take_to_dm" in offered[1]
