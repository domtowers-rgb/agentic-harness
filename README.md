# Agentic LLM Harness (Minimal)

Lightweight OpenAI-compatible harness that exposes a small subset of the Chat Completions API and supports a plugin architecture for function calls.

Repository: https://github.com/domtowers-rgb/agentic-harness

Install

One-line install (clones the repo, creates a venv, installs dependencies):

```bash
curl -fsSL https://raw.githubusercontent.com/domtowers-rgb/agentic-harness/main/install.sh | bash
```

Or clone it yourself and run `install.sh`:

```bash
git clone https://github.com/domtowers-rgb/agentic-harness.git
cd agentic-harness
./install.sh
```

Quick start

1. Install dependencies (skip if you used `install.sh` above):

```bash
pip install -r requirements.txt
```

2. Run (mock backend):

```bash
python -m agentic_harness.main
```

3. Use the OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1/chat/completions`, or open `http://127.0.0.1:8000/` in a browser for a minimal built-in chat UI - it renders assistant markdown (code blocks, lists, bold/italic, links) and saves conversations to your browser's local storage (per-browser, not synced anywhere), listed in the sidebar and restored automatically when you reopen the page.

Environment

- `AGENTIC_MODEL`: `mock` (default) or `openai` to use an OpenAI-compatible API.
- `OPENAI_API_KEY`: used if set; otherwise falls back to a placeholder value, which works fine for local servers that don't check it. Set a real key to use the actual OpenAI API.
- `OPENAI_BASE_URL`: point at a local OpenAI-compatible server (e.g. `http://127.0.0.1:1234/v1` for LM Studio, `http://127.0.0.1:11434/v1` for Ollama). Omit to use the real OpenAI API. Can also be set at runtime from the web UI's settings (⚙) panel (API endpoint field + Connect button), without restarting the server.
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

If a plugin fails to import (most commonly: you pulled a change that added a new dependency, like `python-pptx` for `create_presentation`, without re-running `pip install -r requirements.txt`), it's skipped rather than crashing the server - but not silently: the server logs `[plugins] failed to load '...': ...` at startup, and it just won't show up in `/v1/plugins` or the settings dialog. If a plugin you expect is missing, check the server's startup log for that line first.

By default, every loaded plugin is automatically added to the `tools` sent on each `/v1/chat/completions` request (merged in behind any `tools` the client already supplied, without duplicating names). `GET /v1/plugins` lists what's loaded. To use only a subset for one request, pass `"enabled_plugins": ["calculate", "get_current_time"]` in the request body - the web UI's settings (⚙) panel does this for you, with per-plugin checkboxes persisted in the browser. Changes there only take effect on the next "New chat", by design: keeping the tool list stable within a conversation lets a local inference server's own prompt-prefix caching actually help.

Built-in plugins:

- `calculate` - safe arithmetic (AST-based, no `eval`).
- `get_current_time` - current date/time, optionally in an IANA timezone.
- `fetch_url` - fetch a public http(s) URL's text. Refuses private/loopback/link-local addresses and does not follow redirects (basic SSRF protection).
- `web_search` - web search via Brave Search. Requires `BRAVE_API_KEY`; without it, the tool reports a clear error instead of failing silently.
- `read_file` / `write_file` / `list_files` - sandboxed to one directory (`AGENTIC_FILES_DIR`, default `workspace/`). Cannot read or write anything outside it.
- `create_presentation` - creates a PowerPoint (.pptx) file in the same sandboxed directory: a title slide plus one title+bullets slide per entry you give it.
- `run_command` - runs a shell command (not through a shell interpreter) with its cwd set to the sandbox directory, with a timeout. **Off by default** - an absolute-path command isn't contained by the sandbox cwd, so this grants real system access. Set `AGENTIC_ENABLE_SHELL=1` to opt in.

Additional environment variables used by the built-in plugins:

- `AGENTIC_FILES_DIR`: sandbox directory for `read_file`/`write_file`/`list_files`/`run_command` (default `workspace/`).
- `AGENTIC_ENABLE_SHELL`: set to `1` to enable `run_command`.
- `BRAVE_API_KEY`: enables `web_search`.

Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Runs on push/PR via GitHub Actions (`.github/workflows/tests.yml`). A couple of `fetch_url` tests hit a real public URL (`example.com`) and skip themselves if the network is unavailable rather than failing.
