import os
import sys
import json

# Ensure project root is on sys.path so `agentic_harness` imports work when
# running this script directly from any CWD.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agentic_harness import model, plugins


def main():
    print("Loading plugins...")
    plugins.load_plugins()

    print("\nTesting MockModel.chat")
    mm = model.MockModel()
    resp = mm.chat([{"role": "user", "content": "hello"}])
    print(json.dumps(resp, indent=2))

    print("\nTesting MockModel.chat_stream")
    for chunk in mm.chat_stream([{"role": "user", "content": "hello streaming world"}]):
        print("CHUNK:", chunk)

    print("\nTesting plugin registry")
    p = plugins.registry.get("hello")
    if p:
        print("hello plugin call ->", p["callable"](name="Tester"))
    else:
        print("hello plugin not found")

    print("\nAttempting FastAPI TestClient call (if available)")
    try:
        from fastapi.testclient import TestClient
        import agentic_harness.main as main_mod
        client = TestClient(main_mod.app)
        r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]})
        print("status:", r.status_code)
        print("json:", r.json())
    except Exception as e:
        print("TestClient skipped:", type(e).__name__, e)


if __name__ == "__main__":
    main()
