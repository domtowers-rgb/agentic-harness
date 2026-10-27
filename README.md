# Agentic LLM Harness

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

3. Use the OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1/chat/completions`, or open `http://127.0.0.1:8000/` in a browser for the built-in chat UI: attach files and photos (paperclip, drag and drop or paste), a Think toggle and model picker in the composer, a live status line while a reply is in progress ("Reading your message… 4s", "Searching the web…", "Running some code… 12s", or a ticking "Thinking…"), tool use and a tokens-per-second tag on each reply, charts and images the tools make shown inline, Copy, Retry and Edit-and-resend, and chats you can search and rename - saved in your browser's local storage (per-browser, not synced anywhere). Light, dark or system theme in Settings.

Running as a service

To keep it running - started at boot, restarted if it stops - run it as a systemd user service. `deploy/agentic-harness.service` assumes the project is at `~/agentic-harness` with its `venv`; edit the paths if not.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/agentic-harness.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agentic-harness
sudo loginctl enable-linger $USER
```

The last line lets it start at boot without you logging in (once is enough). Then `systemctl --user status agentic-harness` shows whether it's running, `journalctl --user -u agentic-harness -f` follows its log, and `systemctl --user restart agentic-harness` picks up changes to the code, `.env` or `SOUL.md` (all read once, at startup).

Backups

What isn't in git - both `.env` files, `SOUL.md`, `reminders.json`, every chat's `memory.json`, and agentic-gateway's `history.json` (history, summaries and its notes on people) and `chat_settings.json` - is backed up by `deploy/backup.sh` into one `.tar.gz` in `~/agentic-backups/` (readable by your account only), keeping the newest 7. To run it nightly at 03:30 (or at the next start-up, if the machine was off):

```bash
cp deploy/agentic-backup.service deploy/agentic-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agentic-backup.timer
```

`systemctl --user start agentic-backup.service` backs up right now; `systemctl --user list-timers agentic-backup.timer` shows the next run. Settings (environment variables for the script): `BACKUP_DIR`, `KEEP`, `HARNESS_DIR`, `GATEWAY_DIR`. To restore, stop the services, look inside a backup with `tar -tzf <file>`, and extract what you need - e.g. `tar -xzf <file> -C / home/<you>/agentic-gateway/history.json` - then start them again.

Configuration

Settings are read from a `.env` file in the project folder at startup, so nothing needs exporting first and the model server connection survives a restart:

```bash
cp .env.example .env    # then edit .env
```

`.env.example` lists every setting with its default; `.env` itself is gitignored, so API keys in it stay on this machine. One `KEY=VALUE` per line, `#` for comments. A variable set in the real environment still wins over the file (handy for a one-off, e.g. `PORT=8001 python -m agentic_harness.main`), and `AGENTIC_ENV_FILE` points at a different file. The settings:


- `AGENTIC_MODEL`: `mock` (default) or `openai` to use an OpenAI-compatible API.
- `OPENAI_API_KEY`: used if set; otherwise falls back to a placeholder value, which works fine for local servers that don't check it. Set a real key to use the actual OpenAI API.
- `OPENAI_BASE_URL`: point at a local OpenAI-compatible server (e.g. `http://127.0.0.1:8080/v1` for llama.cpp's `llama-server`, `http://127.0.0.1:1234/v1` for LM Studio, `http://127.0.0.1:11434/v1` for Ollama). Omit to use the real OpenAI API. Can also be set at runtime from the web UI's settings (⚙) panel (API endpoint field + Connect button), without restarting the server.
- `AGENTIC_DEFAULT_MODEL`: model name used when a request omits `model` (default `gpt-4o-mini`). Set this to your loaded local model's id when using a local server.
- `AGENTIC_MAX_TOOL_ITERATIONS`: cap on tool-call round trips per request (default `8`). Once it's used up, the model gets one last round with `tool_choice: "none"` and a short note asking it to answer from what it already has - so a request never ends on a tool-call turn with no text. If even that comes back empty, the reply is a plain "stopped after N rounds of tool calls" message.
- `AGENTIC_DEFAULT_MAX_TOKENS`: `max_tokens` used when a request omits it - the web UI always omits it (default `2048`, was previously a hardcoded `512`). Matters a lot for local "thinking" models: too low and the model can burn its whole budget on `reasoning_content` and get cut off before producing any real answer - every token generated toward a response that never completes is pure wasted decode time, not saved time.
- `AGENTIC_TIMEZONE`: time zone for today's date, which is added to the system prompt (e.g. `Europe/London`; default: this machine's). Date only, not the time - anything that changes at the very start of the prompt makes a local server reprocess the whole prompt instead of reusing its cache, and a date changes once a day. When `web_search` is available, a line nudging the model to search rather than guess at current or uncertain facts is added too. `AGENTIC_INCLUDE_DATE=0` leaves both out.
- `AGENTIC_LOAD_CONTEXT_LENGTH`: context length `/v1/models/load` (the gateway's `/loadmodel`) loads models with, e.g. `32768`; a copy already loaded with less is reloaded. Models LM Studio loads by itself on demand use its own per-model default context - set that in LM Studio's model settings.
- `AGENTIC_DEFAULT_TEMPERATURE`: `temperature` used when a request omits it - the web UI and agentic-gateway both do (default `0.7`). Lower is more predictable but flatter: at `0` a model gives the same stock phrasing every time.
- `AGENTIC_HISTORY_MAX_TOKENS`: how much conversation history to keep (capped automatically by the context the model is actually loaded with, where the server says - llama.cpp and LM Studio do - leaving room for the tool definitions, system prompt and reply) (an image in a message counts as about 250 tokens, not its data size), in the same rough chars/4 heuristic `truncate_messages` (agentic_harness/prompt.py) uses (default `2048`). Tune this to roughly match your local model's actual configured context window. Oldest messages are dropped first, but the newest message is always kept even if it's over budget on its own (a long paste is sent rather than silently dropped), as is a leading system message.
- `AGENTIC_RELOAD`: set to `1` to auto-restart on code changes (uvicorn's `--reload`). Off by default - on at least one real Windows setup, that reload path respawned a worker using the base interpreter instead of an active venv, silently losing venv-installed dependencies (a plugin's own dependency would just vanish - watch the `[plugins] failed to load` startup log if you enable this).
- `AGENTIC_PERSONALITY_FILE`: path to a small system-prompt file (default `SOUL.md`). Its content is put first, as the system message, on every request; if the request has its own system message (agentic-gateway uses one to explain who's in a group chat), the two are combined - personality first - since many local chat templates accept only one system message. Kept intentionally small (guideline: ~200 tokens, estimated at ~4 characters per token); going over just prints a startup warning rather than failing, since it's a guideline, not a hard limit. Edit `SOUL.md` directly, or point this at a different file. Empty or missing file means no personality prompt is added at all.
- `AGENTIC_TTS_VOICE` / `AGENTIC_TTS_DIR` / `AGENTIC_TTS_MAX_CHARS`: the spoken-reply voice, where voices are kept, and the most text spoken per reply (see "Voice messages").
- `AGENTIC_PODCAST_VOICES` / `AGENTIC_PODCAST_HOSTS`: the two podcast hosts' Piper voices and names (defaults `en_GB-alba-medium,en_GB-alan-medium` and `Alba,Alan`).
- `AGENTIC_COUNCIL_MODELS`: fixed council members, comma-separated model ids (default: every chat model currently loaded).
- `AGENTIC_WHISPER_MODEL`: speech model for voice messages (default `small`; see "Voice messages").
- `AGENTIC_ALLOWED_HOSTS`: extra hostnames the server answers to, comma-separated (see "Browser protection").
- `AGENTIC_AUDIT_LOG`: path to the tool-call audit log (default `audit.log`). See "Audit log" below.

`GET /v1/models` lists the backend's models OpenAI-style; with llama.cpp or LM Studio, each entry also has `"loaded": true/false` and `"type"` (`llm`, `vlm` - can see images - or `embeddings`), read from llama.cpp's `/props` or LM Studio's own `/api/v0/models` - extra fields that OpenAI-compatible clients ignore.

`POST /v1/models/load` with `{"model": "<id>"}` makes that the only chat model loaded on an LM Studio backend (a llama.cpp server runs the one model it was started with, so it answers `501` saying so): every other loaded chat model is ejected first (embedding models are left alone), then it's loaded unless it already is - via LM Studio's own `/api/v1/models` management API. Returns `{"model", "unloaded", "already_loaded", "load_time_seconds"}`; 501 for other backends, 404 for an unknown model, 502 with LM Studio's reason if loading fails. It affects everything using that LM Studio, not just the caller; one load runs at a time.

**Leaked working is cleaned out of replies**: `<think>...</think>` blocks the server didn't separate out are removed, and a reply that's just a JSON object of the model's working - `{"thinking": ..., "plan": ...}`, as some "agent-style" models produce - has its answer field used if it has one, or otherwise the model is asked once more for its actual reply in plain text. Seen live in a Signal group: such a reply, saved in history, taught the model to keep doing it. JSON a user actually asked for is left alone. A streamed reply has already been sent by the time it's finished, so it's corrected afterwards with an extra `replace_content` event (`{"old", "new"}`), which the web UI applies and other clients skip.

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

Using a local model (e.g. LM Studio, Ollama, llama.cpp server) - in `.env`:

```bash
AGENTIC_MODEL=openai
OPENAI_BASE_URL=http://127.0.0.1:1234/v1
AGENTIC_DEFAULT_MODEL=google/gemma-4-12b-qat
```

then `python -m agentic_harness.main`.

Plugins

Plugins live in the `plugins/` folder. Each module should expose a `register(registry)` function that calls `registry.register(name, callable, spec)` (to add model tools) and/or a `register_routes(app)` function (to add HTTP endpoints, as the optional voice plugin does) - `plugins/current_time.py` is a short example. Keep each `spec`'s descriptions brief: every enabled tool's definition is sent at the start of every prompt (the 12 built-in tools come to about 900 tokens on Gemma 4). Identical definitions from one prompt to the next are cached by local servers like LM Studio, so the cost is mostly paid on the first prompt after the tool list changes or the model reloads - but it's still worth not wasting.

If a plugin fails to import (most commonly: you pulled a change that added a new dependency, like `python-pptx` for `create_file`, without re-running `pip install -r requirements.txt`), it's skipped rather than crashing the server - but not silently: the server logs `[plugins] failed to load '...': ...` at startup, and it just won't show up in `/v1/plugins` or the settings dialog. If a plugin you expect is missing, check the server's startup log for that line first.

If a tool call fails - an unknown tool name, arguments that aren't valid JSON, or the plugin itself raising an error - the error goes back to the model as that call's result (`{"error": "..."}`), so it can retry, try something else, or explain, rather than the whole request failing. Tools run in a worker thread, so a slow one (a web fetch, say) doesn't hold up other requests.

By default, every loaded plugin is automatically added to the `tools` sent on each `/v1/chat/completions` request (merged in behind any `tools` the client already supplied, without duplicating names). `GET /v1/plugins` lists what's loaded. To use only a subset for one request, pass `"enabled_plugins": ["web_search", "get_current_time"]` in the request body - the web UI's settings (⚙) panel does this for you, with per-plugin checkboxes persisted in the browser. Changes there only take effect on the next "New chat", by design: keeping the tool list stable within a conversation lets a local inference server's own prompt-prefix caching actually help.

Built-in plugins:

- `get_current_time` - current date/time: local (`AGENTIC_TIMEZONE`, the same zone as the date in the system prompt and reminder times) unless the model asks for another IANA timezone.
- `fetch_url` - fetch a public http(s) URL as readable text: HTML pages are converted to plain text (scripts, styles, menus, footers, cookie banners and hidden elements dropped; if the page marks its main content with `<main>`, only that is kept) and returned with the page title, typically a small fraction of the raw HTML's size. JSON and plain text pass through as-is; binary content (PDFs, images) is refused. Capped at `AGENTIC_FETCH_MAX_CHARS`. Refuses private/loopback/link-local addresses and does not follow redirects (basic SSRF protection). A failed DNS lookup is reported as such (with one retry for a temporary resolver failure), not as a private address.
- `web_search` - web search via Brave Search. Requires `BRAVE_API_KEY` (a free key from https://brave.com/search/api/ covers about 2,000 searches a month); without it, every call returns a "not configured" error - check `audit.log` if searches never seem to find anything. Result titles and snippets are flattened to plain text.
- `read_file` / `write_file` / `list_files` - sandboxed to one directory (`AGENTIC_FILES_DIR`, default `workspace/`). Cannot read or write anything outside it.
- `create_file` - creates a Word (`docx`), PDF or PowerPoint (`pptx`) file in the same sandboxed directory and sends it to the user (see "Files in and out"): a title, then per section an optional heading, paragraphs and bullet points - for `pptx`, each section becomes a slide. `**bold**` works in docx and pdf. PDFs are A4 with page numbers, using DejaVu Sans (or Arial on Windows) when installed so Greek, symbols and the like render properly; otherwise Helvetica, which covers Western European text only. One tool for all three formats rather than one each: every tool's definition goes into every prompt, so this keeps the prompt shorter.
- `create_podcast` (optional plugin, `plugins/podcast.py`) - the model writes a two-host conversation and the plugin voices it - host 1 and host 2 in different Piper voices (`AGENTIC_PODCAST_VOICES`, default Alba and Alan; names in `AGENTIC_PODCAST_HOSTS`), with short pauses between turns - as one `.m4a` file sent to the user (a playable audio message in Signal). Needs the voice plugin's libraries; skipped without them. A one-minute episode took about half a minute end to end on a local model.
- `reminder` (optional plugin, `plugins/reminders.py`) - set, list or cancel reminders for the current chat: "remind me to book the Thai place on Friday at 10" is stored against the chat's id (the request's `user` field) with a time in `AGENTIC_TIMEZONE` (`2026-11-13 10:00`, a date alone meaning 9am, or `in 2 hours`). A client delivers due ones: `GET /v1/reminders/due` lists them, and `POST /v1/reminders/{id}/sent` removes one once it's been sent - so a client that's down delivers late rather than never. agentic-gateway does this, sending each to its chat. Stored in `reminders.json` (`AGENTIC_REMINDERS_FILE`; owner-only, outside the sandbox so the file tools can't touch it), at most 20 per chat. Not offered to requests without a chat id (the web UI), which nothing could deliver to.
- `ask_council` (optional plugin, `plugins/council.py`) - for a message starting `/council` (or asking for the council): the question goes, at the same time, to every chat model currently loaded on the backend (or `AGENTIC_COUNCIL_MODELS`), and their answers come back for the chat's own model to judge - naming each position and giving a verdict. With LM Studio's LM Link the members can be on different machines. Needs at least two models loaded.
- `read_document` - reads the text of a PDF, Word (.docx), PowerPoint (.pptx) or plain-text file in the same directory, including files the user has sent. Long documents come back `AGENTIC_READ_MAX_CHARS` (default `8000`) at a time; the result's `next_start` says where to continue. Scanned PDFs (images of text) have no extractable text.
- `memory` - a small persistent key-value notes store (in `memory.json` in the chat's own sandbox): the model can `save`, `get` (one, or all) and `delete` small facts across separate conversations, not just within one. One tool rather than three, to keep the prompt small. Saved safely: an unreadable file is moved aside, never overwritten.
- `read_chat` (optional plugin, `plugins/read_chat.py`, owner only) - in your private chat with the bot, "what happened in the group?": lists the other chats agentic-gateway keeps, or reads one's latest messages and summary, straight from the gateway's history file (`AGENTIC_GATEWAY_HISTORY`, default `~/agentic-gateway/history.json` - the gateway needs `GATEWAY_HISTORY_FILE` set). Refused in a group chat, where it would put other people's private conversations in front of everyone.
- `take_to_dm` (optional plugin, `plugins/take_to_dm.py`, group chats only) - when someone in a group asks to carry on privately: the response carries `"dm"`, an opening private message for the client to send to whoever asked (agentic-gateway does, and records it in that person's private chat). The model is told it *will* be sent - the client may decline, e.g. to another AI.
- `stay_silent` (optional plugin, `plugins/stay_silent.py`) - lets the model send no reply at all, e.g. in a group where another AI keeps chatting with it in circles, or a message isn't for it. Calling it ends the turn there - the response is empty (with `"silent": true`), and any text written alongside the call ("I'll stay quiet now") is dropped, since in a group with another bot that would only start the next round. Offered only to requests with a chat id, not the web UI.
- `react` (optional plugin, `plugins/react.py`) - lets the model answer a short message ("thanks!", "done") with an emoji reaction (👍 ❤️ 😂 😮 😢 🙏 🎉) instead of a reply: the turn ends with no text and the response carries `"reaction"` for the client to put on the message. The client decides whether to allow it - agentic-gateway only does for short messages that aren't questions, and never twice in a row. Offered only to requests with a chat id.
- `ask_claude` (optional plugin, `plugins/ask_claude.py`) - when the user asks the model to "ask Claude...", it puts a self-contained question to Claude through the [Claude Code](https://docs.claude.com/en/docs/claude-code) command line on this machine and relays the answer, saying it came from Claude. Uses the `claude` command signed in to your Claude account, so it counts toward your plan's usage like asking in the app - no API key or per-request charge. Run as a plain question and answer: Claude's tools are all off (no files, shell, MCP servers or saved session), in an empty folder, with a minimal environment, the question sent over stdin. Only the question leaves the machine - not the chat or its notes - and only for the owner (`"owner": true`, like `run_python`). Setup: `curl -fsSL https://claude.ai/install.sh | bash`, then `~/.local/bin/claude auth login` (choose your Claude account). Settings: `AGENTIC_CLAUDE_PATH` (default: `claude` on PATH, else `~/.local/bin/claude`), `AGENTIC_CLAUDE_MODEL` (default: Claude Code's own, e.g. `opus`), `AGENTIC_CLAUDE_TIMEOUT` (default 180 s). Without the command the plugin is skipped.
- `run_python` (optional plugin, `plugins/python_sandbox.py`) - the model writes a short Python script and runs it, seeing its output and errors (and fixing and re-running within the same reply if need be): for sums it would otherwise guess at, spreadsheets, charts and file work. It runs in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox: **no network**; the system's programs and its own Python environment read-only; read/write access to the current chat's folder only (its uploads and the files made for it - not your home folder, `.env`, other chats or anything else); 30 seconds (`AGENTIC_SANDBOX_TIMEOUT`), 1 GB of memory (`AGENTIC_SANDBOX_MEMORY_MB`) and 50 MB files at most. Files a script saves in the chat's folder are sent to the user like any created document. **Owner only:** offered only to requests with `"owner": true` - which agentic-gateway sets for `GATEWAY_OWNERS`' messages, and the web UI for its own (it's on this machine). Setup: `sudo apt install bubblewrap`, then the packages scripts can use (pandas, numpy, matplotlib, openpyxl), in their own environment separate from the harness's: `python3 -m venv sandbox-venv && sandbox-venv/bin/pip install -r requirements-sandbox.txt` (without it, scripts get plain Python). Without bubblewrap the plugin is skipped.

Additional environment variables used by the built-in plugins:

- `AGENTIC_FILES_DIR`: sandbox directory for `read_file`/`write_file`/`list_files`, uploads and created files (default `workspace/`).
- `AGENTIC_SANDBOX_VENV`: the Python environment `run_python` scripts use (default `sandbox-venv/`).
- `BRAVE_API_KEY`: enables `web_search`.
- `AGENTIC_READ_MAX_CHARS`: how much text `read_document` returns per call (default `8000`).
- `AGENTIC_MAX_UPLOAD_BYTES`: largest file `POST /v1/files` accepts (default 25 MB).
- `AGENTIC_FETCH_MAX_CHARS`: max characters of page text `fetch_url` returns (default `8000`, applied after HTML-to-text conversion). The whole result goes into the model's context, so keep it well inside your model's window.

Voice messages (optional plugin)

`plugins/voice.py` adds two endpoints (via a `register_routes(app)` hook) rather than model tools. It's optional: its libraries aren't in `requirements.txt`. To enable it:

```bash
pip install -r requirements-voice.txt
```

Without them the plugin is skipped at startup (`[plugins] failed to load 'plugins.voice'`), the endpoints don't exist, and everything else works as normal. `GET /v1/status` lists the server's endpoints, so a client can tell whether voice is there.

- `POST /v1/transcribe` - an audio file as the raw request body (any common format - Signal voice notes are AAC) returns `{"text", "language", "duration"}`, transcribed on this machine's CPU with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). The model (`AGENTIC_WHISPER_MODEL`, default `small`) loads on the first voice message after a start - and the very first time, downloads from Hugging Face into `~/.cache/huggingface` (`small` is about 480 MB). Transcriptions run one at a time, since each already uses every CPU core.
- `POST /v1/speak` - `{"text": ...}` returns the text spoken aloud as `.m4a` audio (`audio/mp4`, the format of a Signal voice note), using [Piper](https://github.com/OHF-Voice/piper1-gpl) - about half a second for a few sentences on a modest CPU. Markdown is read sensibly (markers dropped, code and links summarised as "shown in the message") and text past `AGENTIC_TTS_MAX_CHARS` (default 1500) is cut at a sentence end. The voice (`AGENTIC_TTS_VOICE`, default `en_GB-cori-medium` - any [Piper voice](https://huggingface.co/rhasspy/piper-voices) name) downloads on first use (~60 MB) into `AGENTIC_TTS_DIR` (default `~/.cache/piper-voices`).

agentic-gateway uses both to understand Signal voice messages and answer them with one.

Browser protection

The server listens on 127.0.0.1 only, but your web browser runs on the same machine - so without care, any website you visit could talk to it: a cross-site POST could repoint `/v1/connect` at an attacker's server (sending every later conversation there) or plant files, and a DNS-rebinding site could read responses, workspace files included. So requests must be addressed to `127.0.0.1`/`localhost` (Starlette's `TrustedHostMiddleware`), and any request carrying a browser `Origin` header must come from one of those same hosts. The built-in web UI and non-browser clients like agentic-gateway are unaffected. If you reach the server by another name, add it to `AGENTIC_ALLOWED_HOSTS`.

Separate chats

A request can say which chat it's for with the standard `user` field (agentic-gateway sends one per Signal chat, e.g. `signal:group:<id>`). Each chat then gets its own folder inside the sandbox - `chats/<hash of its id>/`, so folder names don't contain phone numbers - and every file tool works only in there: its uploads, the documents made for it, and its `memory` notes. One chat can't read another's files or memories, even with `read_file` and a `../` path. Requests without `user` (the web UI - the machine's owner) work in the sandbox root, which contains `chats/`, so the owner can still see everything.

Files in and out

- **Out:** a plugin marks a file it created for the user by including `"attachment": "<path in the sandbox>"` in its result (`create_file` does; `write_file` doesn't - it's for the model's own working files). Every file marked during a request is listed in the response as an extra top-level field, which OpenAI-compatible clients simply ignore:

  ```json
  "attachments": [{"filename": "deck.pptx", "path": "deck.pptx", "url": "/v1/files/deck.pptx", "size": 30880, "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}]
  ```

  On the streaming path it arrives as one extra `data: {"attachments": [...]}` event just before `[DONE]`. `GET /v1/files/{path}` downloads it. The web UI shows a "Download ..." link under the reply, and [agentic-gateway](https://github.com/domtowers-rgb/agentic-gateway) sends the file over Signal as an attachment.
- If the model makes the same document twice in one request (same tool, same title - e.g. rebuilding a presentation under a new filename), only the last version is listed; documents with different titles are all kept.
- **In:** `POST /v1/files?filename=report.pdf` (plus `&user=<chat id>` to put it in that chat's folder) with the file as the raw request body saves it into `uploads/` in the sandbox (never overwriting: a clashing name gets `-2`, `-3`...) and returns `{"path": "uploads/report.pdf", ...}` - which a client then mentions in its message, so the model can `read_document` it. agentic-gateway does this for files sent over Signal.

Both endpoints serve only the sandbox directory, with the same local-only trust model as the file plugins themselves (the server listens on 127.0.0.1 only; anything that can reach it could already ask `read_file` for the same content).

Audit log

Every tool call (name, arguments, result - each truncated to 500 characters) is appended as one JSON line to `AGENTIC_AUDIT_LOG` (default `audit.log`), since tool calls execute automatically with no approval step. Local-only, never sent anywhere, same trust model as `workspace/` - but it does contain whatever the tools were called with, so treat it as sensitive.

Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Runs on push/PR via GitHub Actions (`.github/workflows/tests.yml`). The voice and podcast tests skip themselves where the optional voice libraries (`requirements-voice.txt`) aren't installed - as in CI. A couple of `fetch_url` tests hit a real public URL (`example.com`) and skip themselves if the network is unavailable rather than failing.

Performance with local models

The biggest lever for local model speed - quantization, GPU layer offload, context size - lives in the model server (LM Studio, Ollama, llama.cpp), not this harness. What the harness does control:

- **Thinking on/off**: a request with `"thinking": false` asks a thinking model (Qwen, Nemotron, Gemma...) to answer without reasoning first - often several times quicker for everyday chat - by passing `enable_thinking` to its chat template (llama.cpp and LM Studio both do; a model without the switch ignores it). `true` turns it on; leaving it out keeps the model's default. The web UI has a toggle for it.
- **Speed figures**: with llama.cpp, each response (and the final chunk of a streamed one) carries its `timings` - including `predicted_per_second`, the generation speed - which the web UI shows as a tag on each reply.
- **Prompt-prefix reuse**: a local server (llama.cpp, LM Studio) reuses the work it did on the start of the last prompt when the next one begins the same way - which is most of the speed of a long conversation. So the harness keeps the start stable: the tool list is the same on every request in a chat (owner-only tools are listed whoever speaks, and refused at call time for anyone else), today's date changes once a day, and history over budget is trimmed in blocks of 16 messages rather than one at a time, so the cut point stays put for several turns. Don't toggle plugins mid-conversation (the web UI applies changes to new chats only for this reason).
- **`AGENTIC_DEFAULT_MAX_TOKENS`**: see above - too low wastes decode time on responses that get cut off before finishing.
- **`SOUL.md`**: adding an instruction like "keep reasoning brief" measurably cuts `reasoning_content` token usage on models that do extended thinking - worth trying if your model over-analyzes simple requests.
- **Optional plugins can be removed by deleting their file** - `voice.py`, `podcast.py`, `council.py`, `reminders.py`, `python_sandbox.py`, `ask_claude.py`, `stay_silent.py`, `react.py`, `read_chat.py`, `take_to_dm.py` in `plugins/` - which also takes their tool definitions out of every prompt (they come to about 1,900 tokens altogether; `ask_council`, for one, can't work against a single-model llama.cpp server).
- **Trim your plugin set** in the settings (⚙) panel for everyday chat - every enabled plugin adds to the tool definitions sent with every request, whether or not it's used (cached by the server after the first, but still read once and still taking up context).
