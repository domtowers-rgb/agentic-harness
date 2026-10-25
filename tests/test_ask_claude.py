"""The optional ask_claude plugin, against a stand-in `claude` command - the
tests never contact Claude."""
import importlib
import json
import os
import stat

import pytest

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
question = sys.stdin.read()
with open(os.environ.get("FAKE_LOG") or os.path.join(os.path.dirname(__file__), "log.json"), "w") as f:
    json.dump({"argv": sys.argv[1:], "question": question, "cwd": os.getcwd(), "env": dict(os.environ)}, f)
mode = open(os.path.join(os.path.dirname(__file__), "mode")).read().strip()
if mode == "error":
    print(json.dumps({"is_error": True, "result": "Usage limit reached"}))
elif mode == "garbage":
    print("not json"); print("signed out", file=sys.stderr); sys.exit(1)
elif mode == "slow":
    import time; time.sleep(5)
else:
    print(json.dumps({"is_error": False, "result": "391.", "modelUsage": {"claude-sonnet-5-5": {}}}))
'''


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "mode").write_text("ok")
    monkeypatch.setenv("AGENTIC_CLAUDE_PATH", str(fake))
    monkeypatch.setenv("BRAVE_API_KEY", "secret-from-the-harness")
    import plugins.ask_claude as ask_claude
    module = importlib.reload(ask_claude)
    module.tmp = tmp_path
    return module


def _log(plugin):
    return json.loads((plugin.tmp / "log.json").read_text())


def test_asks_and_relays_the_answer(plugin):
    assert plugin.ask_claude("What's 17 times 23?") == {
        "answer": "391.", "from": "claude-sonnet-5-5", "note": "Tell the user this answer came from Claude.",
    }
    seen = _log(plugin)
    assert seen["question"] == "What's 17 times 23?"  # over stdin...
    assert "What's 17 times 23?" not in " ".join(seen["argv"])  # ...not visible on the command line


def test_runs_as_a_plain_question_with_nothing_else(plugin):
    plugin.ask_claude("hi")
    seen = _log(plugin)
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == ""  # Claude's own tools all off
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert not os.path.exists(seen["cwd"])  # an empty folder, removed afterwards
    assert "BRAVE_API_KEY" not in seen["env"]  # none of the harness's settings or keys


def test_problems_come_back_as_errors(plugin):
    (plugin.tmp / "mode").write_text("error")
    assert plugin.ask_claude("hi") == {"error": "Claude couldn't answer: Usage limit reached"}
    (plugin.tmp / "mode").write_text("garbage")
    assert "signed out" in plugin.ask_claude("hi")["error"]
    assert "question is required" in plugin.ask_claude("  ")["error"]


def test_a_slow_answer_is_given_up_on(plugin, monkeypatch):
    (plugin.tmp / "mode").write_text("slow")
    monkeypatch.setattr(plugin, "TIMEOUT_SECONDS", 1)
    assert "didn't answer within 1 seconds" in plugin.ask_claude("hi")["error"]


def test_registered_as_owner_only(plugin):
    from agentic_harness.plugins import PluginRegistry

    registry = PluginRegistry()
    plugin.register(registry)
    assert registry.owner_only("ask_claude")
