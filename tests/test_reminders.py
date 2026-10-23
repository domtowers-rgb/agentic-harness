"""The optional reminders plugin."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import plugins.file_ops as file_ops
import plugins.reminders as reminders
from agentic_harness import main as main_mod

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path / "workspace")
    monkeypatch.setattr(reminders, "REMINDERS_FILE", str(tmp_path / "reminders.json"))
    monkeypatch.setattr(reminders, "TIMEZONE", "Europe/London")
    return tmp_path / "reminders.json"


def _as(chat, **kwargs):
    with file_ops.using_sandbox(file_ops.chat_sandbox(chat), chat=chat):
        return reminders.reminder(**kwargs)


class TestParseWhen:
    def test_local_time_date_alone_and_relative(self):
        assert reminders.parse_when("2026-11-13 10:00").isoformat() == "2026-11-13T10:00:00+00:00"  # GMT by then
        assert reminders.parse_when("2026-10-23 10:00").isoformat() == "2026-10-23T10:00:00+01:00"  # still BST
        assert reminders.parse_when("2026-11-13").hour == 9
        assert reminders.parse_when("in 2 hours", now=NOW) == NOW + timedelta(hours=2)
        assert reminders.parse_when("in 1 week", now=NOW) == NOW + timedelta(weeks=1)

    def test_anything_else_is_explained(self):
        with pytest.raises(ValueError, match="use e.g."):
            reminders.parse_when("next Friday-ish")


class TestReminderTool:
    def test_set_list_and_cancel(self, store):
        result = _as("signal:+1", action="set", when="2099-11-13 10:00", text="Dom - book the Thai place!")
        assert result["when"] == "Friday 13 November 2099 at 10:00"
        assert _as("signal:+1", action="list")["reminders"] == [
            {"id": result["set"], "when": "Friday 13 November 2099 at 10:00", "text": "Dom - book the Thai place!"},
        ]
        assert _as("signal:+1", action="cancel", id=result["set"]) == {"cancelled": result["set"]}
        assert _as("signal:+1", action="list") == {"reminders": []}

    def test_saved_owner_only(self, store):
        _as("signal:+1", action="set", when="in 1 hour", text="hi")
        assert store.stat().st_mode & 0o777 == 0o600
        assert json.loads(store.read_text())[0]["chat"] == "signal:+1"

    def test_chats_only_see_their_own(self):
        set_by_1 = _as("signal:+1", action="set", when="in 1 hour", text="mine")["set"]
        assert _as("signal:group:abc", action="list") == {"reminders": []}
        assert "error" in _as("signal:group:abc", action="cancel", id=set_by_1)
        assert len(_as("signal:+1", action="list")["reminders"]) == 1

    def test_mistakes_come_back_as_errors_the_model_can_fix(self):
        assert "in the past" in _as("signal:+1", action="set", when="2020-01-01 10:00", text="x")["error"]
        assert "use e.g." in _as("signal:+1", action="set", when="soonish", text="x")["error"]
        assert "text is required" in _as("signal:+1", action="set", when="in 1 hour")["error"]
        assert "action must be" in _as("signal:+1", action="snooze")["error"]

    def test_a_chat_can_only_have_so_many(self, monkeypatch):
        monkeypatch.setattr(reminders, "MAX_PER_CHAT", 2)
        for _ in range(2):
            _as("signal:+1", action="set", when="in 1 hour", text="x")
        assert "already has 2" in _as("signal:+1", action="set", when="in 1 hour", text="x")["error"]

    def test_not_offered_without_a_chat_to_send_to(self):
        assert "only work in chats" in _as(None, action="set", when="in 1 hour", text="x")["error"]


class TestDelivery:
    def test_due_ones_are_listed_until_marked_sent(self, client):
        _as("signal:+1", action="set", when="in 1 minute", text="soon")
        _as("signal:+1", action="set", when="in 3 days", text="later")
        assert client.get("/v1/reminders/due").json() == {"reminders": []}

        due = reminders.due_reminders(now=datetime.now(timezone.utc) + timedelta(minutes=2))
        assert [r["text"] for r in due] == ["soon"]
        assert client.post(f"/v1/reminders/{due[0]['id']}/sent").json() == {"removed": due[0]["id"]}
        assert client.post(f"/v1/reminders/{due[0]['id']}/sent").status_code == 404
        assert [r["text"] for r in _as("signal:+1", action="list")["reminders"]] == ["later"]

    def test_the_tool_knows_which_chat_the_request_is_for(self, client, monkeypatch):
        """A real request: the "user" field reaches the tool as its chat."""
        class Model:
            calls = 0

            def chat(self, messages, tools=None, **kwargs):
                Model.calls += 1
                if Model.calls == 1:
                    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "reminder", "arguments": json.dumps({"action": "set", "when": "in 2 hours", "text": "Book it!"})},
                    }]}}]}
                return {"choices": [{"message": {"role": "assistant", "content": "Done - I'll remind you."}}]}

        monkeypatch.setattr(main_mod, "model_impl", Model())
        client.post("/v1/chat/completions", json={"user": "signal:group:abc", "messages": [{"role": "user", "content": "remind me"}]})
        assert [r["text"] for r in _as("signal:group:abc", action="list")["reminders"]] == ["Book it!"]
