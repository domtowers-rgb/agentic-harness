# Agentic LLM Harness (Minimal)

Lightweight OpenAI-compatible harness that exposes a small subset of the Chat Completions API and supports a plugin architecture for function calls.

Quick start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run (mock backend):

```bash
python -m agentic_harness.main
```

3. Use the OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1/chat/completions`, or open `http://127.0.0.1:8000/` in a browser for a minimal built-in chat UI.

Environment

- `AGENTIC_MODEL`: `mock` (default) or `openai` to use an OpenAI-compatible API.
- `OPENAI_API_KEY`: required by the SDK; any non-empty string works for local servers that don't check it.
- `OPENAI_BASE_URL`: point at a local OpenAI-compatible server (e.g. `http://127.0.0.1:1234/v1` for LM Studio, `http://127.0.0.1:11434/v1` for Ollama). Omit to use the real OpenAI API.
- `AGENTIC_DEFAULT_MODEL`: model name used when a request omits `model` (default `gpt-4o-mini`). Set this to your loaded local model's id when using a local server.
- `AGENTIC_MAX_TOOL_ITERATIONS`: cap on tool-call round trips per request before giving up (default `8`).

Using a local model (e.g. LM Studio, Ollama, llama.cpp server)

```bash
export AGENTIC_MODEL=openai
export OPENAI_BASE_URL=http://127.0.0.1:1234/v1
export OPENAI_API_KEY=local
export AGENTIC_DEFAULT_MODEL=google/gemma-4-12b-qat
python -m agentic_harness.main
```

Plugins

Plugins live in the `plugins/` folder. Each module should expose a `register(registry)` function that calls `registry.register(name, callable, spec)`.
