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


@pytest.fixture
def client():
    """A TestClient against the real app, running whatever backend main.py
    currently has installed (MockModel by default - no AGENTIC_MODEL=openai
    is set in the test environment)."""
    return TestClient(main_mod.app)
