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
- `AGENTIC_MAX_TOOL_ITERATIONS`: cap on tool-call round trips per request (default `8`). Once it's used up, the model gets one last round with `tool_choice: "none"` and a short note asking it to answer from what it already has - so a request never ends on a tool-call turn with no text. If even that comes back empty, the reply is a plain "stopped after N rounds of tool calls" message.
- `AGENTIC_DEFAULT_MAX_TOKENS`: `max_tokens` used when a request omits it - the web UI always omits it (default `2048`, was previously a hardcoded `512`). Matters a lot for local "thinking" models: too low and the model can burn its whole budget on `reasoning_content` and get cut off before producing any real answer - every token generated toward a response that never completes is pure wasted decode time, not saved time.
- `AGENTIC_HISTORY_MAX_TOKENS`: how much conversation history to keep, in the same rough chars/4 heuristic `_truncate_messages` uses (default `2048`). Tune this to roughly match your local model's actual configured context window. Oldest messages are dropped first, but the newest message is always kept even if it's over budget on its own (a long paste is sent rather than silently dropped), as is a leading system message.
- `AGENTIC_RELOAD`: set to `1` to auto-restart on code changes (uvicorn's `--reload`). Off by default - on at least one real Windows setup, that reload path respawned a worker using the base interpreter instead of an active venv, silently losing venv-installed dependencies (a plugin's own dependency would just vanish - watch the `[plugins] failed to load` startup log if you enable this).
- `AGENTIC_PERSONALITY_FILE`: path to a small system-prompt file (default `SOUL.md`). Its content is prepended as a system message on every request that doesn't already supply its own - your own system message always wins over it. Kept intentionally small (guideline: ~100 tokens, estimated via the same rough chars/4 heuristic used elsewhere in this file); going over just prints a startup warning rather than failing, since it's a guideline, not a hard limit. Edit `SOUL.md` directly, or point this at a different file. Empty or missing file means no personality prompt is added at all.
- `AGENTIC_AUDIT_LOG`: path to the tool-call audit log (default `audit.log`). See "Audit log" below.

**If the requested model isn't loaded on the backend** (e.g. `AGENTIC_DEFAULT_MODEL` names a model that isn't actually running in LM Studio), `/v1/chat/completions` (non-streaming only) returns `404` with a machine-readable body instead of an opaque `500`:

```json
{"detail": {"type": "model_not_found", "model": "the-requested-name", "available_models": ["a", "b"]}}
```

This is what lets a client offer a fix instead of a dead end - see
[agentic-gateway](https://github.com/domtowers-rgb/agentic-gateway), which
asks the user (over Signal) to pick from `available_models` and retries
automatically once they do. Any other backend failure (e.g. the server
being unreachable at all) still comes back as a plain `502`. Not covered:
the streaming path, whose errors surface inside an already-started SSE
response - the web UI is the only caller of that path today.

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

If a tool call fails - an unknown tool name, arguments that aren't valid JSON, or the plugin itself raising an error - the error goes back to the model as that call's result (`{"error": "..."}`), so it can retry, try something else, or explain, rather than the whole request failing. Tools run in a worker thread, so a slow one (a web fetch, say) doesn't hold up other requests.

By default, every loaded plugin is automatically added to the `tools` sent on each `/v1/chat/completions` request (merged in behind any `tools` the client already supplied, without duplicating names). `GET /v1/plugins` lists what's loaded. To use only a subset for one request, pass `"enabled_plugins": ["calculate", "get_current_time"]` in the request body - the web UI's settings (⚙) panel does this for you, with per-plugin checkboxes persisted in the browser. Changes there only take effect on the next "New chat", by design: keeping the tool list stable within a conversation lets a local inference server's own prompt-prefix caching actually help.

Built-in plugins:

- `calculate` - safe arithmetic (AST-based, no `eval`).
- `get_current_time` - current date/time, optionally in an IANA timezone.
- `fetch_url` - fetch a public http(s) URL as readable text: HTML pages are converted to plain text (scripts, styles, menus, footers, cookie banners and hidden elements dropped; if the page marks its main content with `<main>`, only that is kept) and returned with the page title, typically a small fraction of the raw HTML's size. JSON and plain text pass through as-is; binary content (PDFs, images) is refused. Capped at `AGENTIC_FETCH_MAX_CHARS`. Refuses private/loopback/link-local addresses and does not follow redirects (basic SSRF protection). A failed DNS lookup is reported as such (with one retry for a temporary resolver failure), not as a private address.
- `web_search` - web search via Brave Search. Requires `BRAVE_API_KEY` (a free key from https://brave.com/search/api/ covers about 2,000 searches a month); without it, every call returns a "not configured" error - check `audit.log` if searches never seem to find anything. Result titles and snippets are flattened to plain text.
- `read_file` / `write_file` / `list_files` - sandboxed to one directory (`AGENTIC_FILES_DIR`, default `workspace/`). Cannot read or write anything outside it.
- `create_presentation` - creates a PowerPoint (.pptx) file in the same sandboxed directory: a title slide plus one title+bullets slide per entry you give it.
- `remember` / `recall` / `forget` - a small persistent key-value notes store (in `memory.json` in the same sandboxed directory), so the model can save and retrieve small facts across separate conversations, not just within one.
- `run_command` - runs a shell command (not through a shell interpreter) with its cwd set to the sandbox directory, with a timeout. **Off by default** - an absolute-path command isn't contained by the sandbox cwd, so this grants real system access. Set `AGENTIC_ENABLE_SHELL=1` to opt in.

Additional environment variables used by the built-in plugins:

- `AGENTIC_FILES_DIR`: sandbox directory for `read_file`/`write_file`/`list_files`/`run_command` (default `workspace/`).
- `AGENTIC_ENABLE_SHELL`: set to `1` to enable `run_command`.
- `BRAVE_API_KEY`: enables `web_search`.
- `AGENTIC_FETCH_MAX_CHARS`: max characters of page text `fetch_url` returns (default `8000`, applied after HTML-to-text conversion). The whole result goes into the model's context, so keep it well inside your model's window.

Audit log

Every tool call (name, arguments, result - each truncated to 500 characters) is appended as one JSON line to `AGENTIC_AUDIT_LOG` (default `audit.log`), since tool calls execute automatically with no approval step. Local-only, never sent anywhere, same trust model as `workspace/` - but it does contain whatever the tools were called with, so treat it as sensitive.

Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Runs on push/PR via GitHub Actions (`.github/workflows/tests.yml`). A couple of `fetch_url` tests hit a real public URL (`example.com`) and skip themselves if the network is unavailable rather than failing.

Performance with local models

The biggest lever for local model speed - quantization, GPU layer offload, context size - lives in the model server (LM Studio, Ollama, llama.cpp), not this harness. What the harness does control:

- **Prompt-prefix reuse**: conversation history is append-only and `enabled_plugins` stays fixed for a whole conversation, so the prompt sent each turn shares an identical prefix with the last one - this is what lets a local backend's own KV-cache reuse actually kick in (only the new tail gets reprocessed, not the whole conversation from scratch). Don't toggle plugins mid-conversation if you care about this.
- **`AGENTIC_DEFAULT_MAX_TOKENS`**: see above - too low wastes decode time on responses that get cut off before finishing.
- **`SOUL.md`**: adding an instruction like "keep reasoning brief" measurably cuts `reasoning_content` token usage on models that do extended thinking - worth trying if your model over-analyzes simple requests.
- **Trim your plugin set** in the settings (⚙) panel for everyday chat - every enabled plugin adds to the tool definitions sent on the first message of each conversation, whether or not you end up using it.
