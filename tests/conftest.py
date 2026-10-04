import os
import sys

# Ensure the project root is importable regardless of where pytest is invoked
# from, so `import agentic_harness` / `import plugins` work.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from fastapi.testclient import TestClient

from agentic_harness import main as main_mod


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    """Redirect the audit log to a throwaway path for every test, so tests
    that exercise the tool-call loop (most of them) don't write into the
    real project's audit.log as a side effect of running the suite. Tests
    that specifically test audit logging override this themselves."""
    monkeypatch.setattr(main_mod, "AUDIT_LOG_FILE", str(tmp_path / "audit.log"))


@pytest.fixture(autouse=True)
def hello_tool(monkeypatch):
    """A trivial tool the tool-loop tests can call by name. (It used to
    be a real example plugin, but every loaded plugin's definition is
    sent to the model with every prompt, so it now exists only here.)"""
    from agentic_harness import plugins

    def hello(name: str = "world") -> str:
        return f"Hello, {name}!"

    spec = {"name": "hello", "description": "Say hello to someone",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": []}}
    monkeypatch.setitem(plugins.registry._registry, "hello", {"callable": hello, "spec": spec})


@pytest.fixture
def client():
    """A TestClient against the real app, running whatever backend main.py
    currently has installed (MockModel by default - no AGENTIC_MODEL=openai
    is set in the test environment)."""
    # Addressed to 127.0.0.1 like a real local client - the server refuses
    # other Host names (TestClient's default is "testserver").
    return TestClient(main_mod.app, base_url="http://127.0.0.1")
