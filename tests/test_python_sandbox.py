"""The optional run_python plugin. Skipped where bubblewrap can't run (not
installed, or user namespaces blocked - as on many CI machines)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

import plugins.file_ops as file_ops
from agentic_harness import main as main_mod
from agentic_harness import plugins as plugin_loader
from agentic_harness import tools as tools_mod

sandbox = pytest.importorskip("plugins.python_sandbox", reason="bubblewrap isn't installed", exc_type=ImportError)
try:
    subprocess.run(
        [sandbox.BWRAP, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib",
         "--symlink", "usr/lib64", "/lib64", "/usr/bin/true"],
        check=True, capture_output=True, timeout=20,
    )
except (OSError, subprocess.SubprocessError):
    pytest.skip("bubblewrap can't create a sandbox here", allow_module_level=True)


@pytest.fixture(autouse=True)
def chat_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path / "workspace")
    folder = file_ops.chat_sandbox("signal:+1555")
    with file_ops.using_sandbox(folder, chat="signal:+1555"):
        yield folder


def test_runs_code_and_returns_its_output():
    result = sandbox.run_python("print(sum(range(1, 101)))")
    assert result == {"exit_code": 0, "stdout": "5050\n", "stderr": ""}


def test_errors_come_back_for_the_model_to_fix():
    result = sandbox.run_python("1 / 0")
    assert result["exit_code"] == 1 and "ZeroDivisionError" in result["stderr"]


def test_works_on_the_chats_files_and_sends_what_it_makes(chat_folder):
    (chat_folder / "uploads").mkdir()
    (chat_folder / "uploads" / "spend.csv").write_text("item,cost\nrent,800\nfood,250\n")
    code = (
        "import pandas as pd, matplotlib.pyplot as plt\n"
        "df = pd.read_csv('uploads/spend.csv')\n"
        "print(df.cost.sum())\n"
        "df.plot.bar(x='item', y='cost'); plt.savefig('spend.png')\n"
    )
    result = sandbox.run_python(code)
    assert result["stdout"] == "1050\n"
    assert result["attachments"] == ["spend.png"] and (chat_folder / "spend.png").stat().st_size > 1000
    assert not [p for p in chat_folder.iterdir() if p.name.startswith(".")]  # no caches left behind


def test_nothing_outside_the_chat_folder_is_reachable(chat_folder, tmp_path):
    (tmp_path / "secret.txt").write_text("private")
    code = (
        "import os, socket\n"
        f"print([p for p in [{str(Path.home())!r}, {str(tmp_path / 'secret.txt')!r}, '/etc/passwd', '/proc/1/environ'] if os.path.exists(p)])\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), 3); print('network open')\n"
        "except OSError:\n    print('no network')\n"
        "for target in ['/usr/x', '/tmp/../usr/y', '../escape.txt']:\n"
        "    try:\n        open(target, 'w'); print('wrote', target)\n"
        "    except OSError:\n        pass\n"
    )
    result = sandbox.run_python(code)
    lines = result["stdout"].splitlines()
    assert lines[0] in ("[]", "['/proc/1/environ']")  # /proc/1 is the sandbox's own first process
    assert lines[1] == "no network"
    assert "wrote /usr/x" not in result["stdout"] and "wrote /tmp/../usr/y" not in result["stdout"]
    assert not (chat_folder.parent / "escape.txt").exists()


def test_a_runaway_script_is_stopped(monkeypatch):
    monkeypatch.setattr(sandbox, "TIMEOUT_SECONDS", 2)
    assert "stopped after 2 seconds" in sandbox.run_python("while True: pass")["error"]


def test_too_much_memory_is_refused(monkeypatch):
    monkeypatch.setattr(sandbox, "MEMORY_MB", 512)
    result = sandbox.run_python("x = bytearray(2 * 1024 ** 3)")
    assert result["exit_code"] != 0 and "MemoryError" in result["stderr"]


def test_only_offered_to_the_owner(client, monkeypatch):
    offered = []

    class Spy:
        def chat(self, messages, tools=None, **kwargs):
            offered.append({t["function"]["name"] for t in tools})
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(main_mod, "model_impl", Spy())
    monkeypatch.setitem(plugin_loader.registry._registry, "run_python",
                        {"callable": sandbox.run_python, "spec": None, "owner_only": True})
    monkeypatch.setattr(main_mod, "_PLUGIN_TOOLS", main_mod._PLUGIN_TOOLS + [{"type": "function", "function": {"name": "run_python"}}])
    for owner in (False, True):
        client.post("/v1/chat/completions", json={"owner": owner, "user": "signal:+1", "messages": [{"role": "user", "content": "hi"}]})
    assert "run_python" not in offered[0] and "run_python" in offered[1]


def test_files_it_makes_are_attached_to_the_reply(chat_folder):
    messages = []
    call = {"id": "c1", "function": {"name": "run_python", "arguments": json.dumps({"code": "open('notes.txt', 'w').write('hi')"})}}
    monkey_entry = {"callable": sandbox.run_python, "spec": None}
    plugin_loader.registry._registry.setdefault("run_python", monkey_entry)
    found = tools_mod.run_tool_calls(messages, [call], sandbox=chat_folder, chat="signal:+1555")
    assert [path for _, path in found] == ["notes.txt"]
    assert os.path.exists(chat_folder / "notes.txt")
