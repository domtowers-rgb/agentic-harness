from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from . import model, plugins
import os
import json
import asyncio
import queue
from datetime import datetime, timezone
from typing import List, Dict, Any, AsyncIterator, Iterator

app = FastAPI(title="Agentic LLM Harness")

MAX_TOOL_ITERATIONS = int(os.environ.get("AGENTIC_MAX_TOOL_ITERATIONS", "8"))

# Used whenever a request doesn't specify its own max_tokens (the web UI
# never does). Was hardcoded at 512, which is often not enough room for a
# local "thinking" model to finish its reasoning and still produce an
# answer - every token generated toward a response that then gets cut off
# is wasted decode time, not saved time.
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENTIC_DEFAULT_MAX_TOKENS", "2048"))

# How much of the conversation history to keep, in the same rough
# chars-per-token heuristic _truncate_messages uses. Tune this to roughly
# match your model's actual configured context window.
HISTORY_MAX_TOKENS = int(os.environ.get("AGENTIC_HISTORY_MAX_TOKENS", "2048"))

# load plugins at startup
plugins.load_plugins()

# choose model implementation based on env
MODEL_BACKEND = os.environ.get("AGENTIC_MODEL", "mock")
if MODEL_BACKEND == "openai":
    model_impl = model.OpenAIModel()
    CURRENT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
else:
    model_impl = model.MockModel()
    CURRENT_BASE_URL = ""


# Built once from the plugin registry, which is only populated at startup
# (plugins are never hot-reloaded), so this never needs to be recomputed
# per-request.
_PLUGIN_TOOLS = [{"type": "function", "function": spec} for spec in plugins.registry.all_specs()]

PERSONALITY_FILE = os.environ.get("AGENTIC_PERSONALITY_FILE", "SOUL.md")
PERSONALITY_MAX_TOKENS = 100


def _load_personality(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return ""
    # Same rough ~4-chars/token heuristic used elsewhere in this file - not
    # exact, but enough to flag "this is probably way over the intended size".
    estimated_tokens = len(text) / 4
    if estimated_tokens > PERSONALITY_MAX_TOKENS:
        print(
            f"[personality] {path} is ~{estimated_tokens:.0f} tokens (estimated), "
            f"over the {PERSONALITY_MAX_TOKENS}-token guideline - consider trimming it. Using it as-is."
        )
    return text


PERSONALITY = _load_personality(PERSONALITY_FILE)


def _truncate_messages(messages: List[Dict[str, Any]], max_tokens: int = 1500) -> List[Dict[str, Any]]:
    # very simple heuristic: assume 4 chars per token
    allowed_chars = max_tokens * 4
    total = sum(len(m.get("content", "")) for m in messages)
    if total <= allowed_chars:
        return messages
    # drop oldest until under budget
    out = messages[:]
    while out and sum(len(m.get("content", "")) for m in out) > allowed_chars:
        out.pop(0)
    return out


# Local audit trail of every tool call: what was invoked, with what
# arguments, and what came back. Not sent anywhere - stays on this machine,
# same trust model as workspace/. Useful for both debugging ("why did it do
# that?") and security review, since tool calls execute automatically with
# no approval step.
AUDIT_LOG_FILE = os.environ.get("AGENTIC_AUDIT_LOG", "audit.log")
AUDIT_LOG_MAX_CHARS = 500


def _truncate_for_audit(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > AUDIT_LOG_MAX_CHARS:
        return text[:AUDIT_LOG_MAX_CHARS] + "...(truncated)"
    return text


def _audit_log(tool_name: str, args: Any, result: Any) -> None:
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": _truncate_for_audit(args),
            "result": _truncate_for_audit(result),
        }
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        # Never let audit logging itself break a tool call.
        print(f"[audit] failed to write audit log entry: {exc}")


def _run_tool_calls(messages: List[Dict[str, Any]], tool_calls: List[Dict[str, Any]], assistant_content=None) -> str:
    """Execute tool_calls via the plugin registry, appending the assistant and
    tool-result messages to `messages` in place. Returns an error string if a
    requested tool is unknown, otherwise None."""
    messages.append({"role": "assistant", "content": assistant_content, "tool_calls": tool_calls})

    for tool_call in tool_calls:
        fn = tool_call.get("function", {})
        fname = fn.get("name")
        args_text = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_text) if isinstance(args_text, str) else args_text
        except Exception:
            args = {}

        plugin = plugins.registry.get(fname)
        if not plugin:
            _audit_log(fname, args, {"error": "unknown function"})
            return f"unknown function: {fname}"

        try:
            result = plugin["callable"](**(args or {}))
        except TypeError:
            result = plugin["callable"](args)

        _audit_log(fname, args, result)
        messages.append({"role": "tool", "tool_call_id": tool_call.get("id"), "content": json.dumps(result)})

    return None


async def _iter_in_thread(sync_iterable: Iterator[Dict]) -> AsyncIterator[Dict]:
    """Consume a blocking iterator (e.g. a model's HTTP-streaming generator) in a
    background thread, so a slow local model doesn't block the event loop while
    other requests are in flight."""
    q = queue.Queue()
    DONE = object()

    def worker():
        try:
            for item in sync_iterable:
                q.put(item)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(DONE)

    asyncio.get_event_loop().run_in_executor(None, worker)

    while True:
        item = await asyncio.to_thread(q.get)
        if item is DONE:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def _call_model(**kwargs) -> Dict:
    """Runs model_impl.chat in a thread, same as before, but turns a failure
    into something a caller can act on instead of an opaque 500.

    Specifically: if the call fails and the model it asked for isn't in the
    backend's current model list, that's almost certainly *why* it failed
    (e.g. AGENTIC_DEFAULT_MODEL points at a model that isn't loaded in LM
    Studio) - raise a 404 carrying the requested name and what's actually
    available, so a client (e.g. the Signal gateway) can offer the user a
    choice instead of just relaying "something went wrong" with no way to
    recover short of an operator fixing the server's config. Any other
    failure (backend unreachable, etc.) still becomes a plain error, since
    there's nothing more specific to say about it here.

    Only wraps the non-streaming path - chat_stream()'s errors surface
    inside an already-started SSE response, which is a bigger change to
    handle well; the web UI is the only caller of that path today.
    """
    try:
        return await asyncio.to_thread(model_impl.chat, **kwargs)
    except Exception as exc:
        requested_model = kwargs.get("model") or model.DEFAULT_MODEL
        try:
            available = await asyncio.to_thread(model_impl.list_models)
        except Exception:
            available = None
        if available is not None and requested_model not in available:
            raise HTTPException(
                status_code=404,
                detail={"type": "model_not_found", "model": requested_model, "available_models": available},
            )
        raise HTTPException(status_code=502, detail=f"model backend error: {exc}")


@app.get("/v1/models")
async def list_models():
    try:
        ids = await asyncio.to_thread(model_impl.list_models)
    except Exception:
        ids = []
    return JSONResponse(content={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})


@app.get("/v1/plugins")
async def list_plugins():
    return JSONResponse(content={
        "plugins": [
            {"name": t["function"]["name"], "description": t["function"].get("description", "")}
            for t in _PLUGIN_TOOLS
        ]
    })


@app.get("/v1/status")
async def status():
    return JSONResponse(content={"backend": MODEL_BACKEND, "base_url": CURRENT_BASE_URL})


@app.post("/v1/connect")
async def connect(request: Request):
    global model_impl, MODEL_BACKEND, CURRENT_BASE_URL
    body = await request.json()
    base_url = (body.get("base_url") or "").strip()
    api_key = (body.get("api_key") or "").strip() or None
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    try:
        candidate = model.OpenAIModel(api_key=api_key, base_url=base_url)
        ids = await asyncio.to_thread(candidate.list_models)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"could not connect to {base_url}: {exc}")

    model_impl = candidate
    MODEL_BACKEND = "openai"
    CURRENT_BASE_URL = base_url
    return JSONResponse(content={"status": "connected", "base_url": base_url, "models": ids})


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    tools = list(body.get("tools") or [])
    stream = body.get("stream", False)
    model_name = body.get("model")
    temperature = body.get("temperature", 0.0)
    # `.get(..., DEFAULT_MAX_TOKENS)` alone wouldn't fall back if a client
    # sent an explicit `"max_tokens": null` - that key would still be
    # present, so .get() would return None rather than the default,
    # reproducing the exact "no cap, runaway generation" bug this default
    # exists to prevent in the first place.
    max_tokens = body.get("max_tokens") or DEFAULT_MAX_TOKENS

    # Merge in registered plugins as tools, skipping any name the client
    # already supplied explicitly. enabled_plugins, if given, restricts this
    # to that subset of plugin names (used by the web UI's plugin toggles);
    # omitting it means "all loaded plugins".
    enabled_plugins = body.get("enabled_plugins")
    existing_names = {t.get("function", {}).get("name") for t in tools}
    for tool in _PLUGIN_TOOLS:
        name = tool["function"]["name"]
        if name in existing_names:
            continue
        if enabled_plugins is not None and name not in enabled_plugins:
            continue
        tools.append(tool)

    # token-frugal trimming
    messages = _truncate_messages(messages, max_tokens=HISTORY_MAX_TOKENS)

    # Personality system prompt: a small, fixed addition on top of the
    # (already-trimmed) conversation budget, so it's never at risk of being
    # trimmed away itself. Only added if the caller didn't already supply
    # their own system message - theirs wins if given.
    if PERSONALITY and not (messages and messages[0].get("role") == "system"):
        messages = [{"role": "system", "content": PERSONALITY}] + messages

    if stream:
        async def event_stream():
            # Stream events from model_impl.chat_stream, forwarding each chunk as-is.
            # If the model streams tool_calls, accumulate the deltas, execute the
            # tools once the round finishes, and continue streaming the next round
            # in the same SSE response - repeating until a plain response comes
            # back or the iteration cap is hit.
            iterations = 0
            while True:
                tool_call_accum = {}

                stream_iter = model_impl.chat_stream(messages=messages, tools=tools, model=model_name, temperature=temperature, max_tokens=max_tokens)
                async for chunk in _iter_in_thread(stream_iter):
                    try:
                        yield f"data: {json.dumps(chunk)}\n\n"
                    except Exception:
                        # fallback to str
                        yield f"data: {str(chunk)}\n\n"
                        continue

                    choices = chunk.get("choices") or []
                    delta = (choices[0].get("delta") or {}) if choices else {}
                    for tc_delta in delta.get("tool_calls") or []:
                        idx = tc_delta.get("index", 0)
                        slot = tool_call_accum.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                        if tc_delta.get("id"):
                            slot["id"] = tc_delta["id"]
                        fn = tc_delta.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

                if not tool_call_accum or iterations >= MAX_TOOL_ITERATIONS:
                    break

                tool_calls = [
                    {
                        "id": slot["id"] or f"call_{idx}",
                        "type": "function",
                        "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
                    }
                    for idx, slot in sorted(tool_call_accum.items())
                ]
                error = _run_tool_calls(messages, tool_calls)
                if error:
                    yield f"data: {json.dumps({'error': error})}\n\n"
                    break
                iterations += 1

            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Tool-calling loop: keep executing tool calls and re-prompting the model
    # until it returns a plain response or we hit the iteration cap. Runs the
    # blocking model call in a thread so a slow local model doesn't block the
    # event loop.
    resp = await _call_model(messages=messages, tools=tools, model=model_name, temperature=temperature, max_tokens=max_tokens)
    iterations = 0
    while iterations < MAX_TOOL_ITERATIONS:
        choice = resp["choices"][0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            break

        error = _run_tool_calls(messages, tool_calls, message.get("content"))
        if error:
            raise HTTPException(status_code=400, detail=error)

        resp = await _call_model(messages=messages, tools=tools, model=model_name, temperature=temperature, max_tokens=max_tokens)
        iterations += 1

    return JSONResponse(content=resp)


# Serve the static chat UI. Mounted after the API route above so that route
# takes priority over the catch-all static handler for the same path space.
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")


if __name__ == "__main__":
    # Off by default: uvicorn's --reload respawns a worker subprocess, and on
    # at least one real Windows setup that respawn silently used the base
    # interpreter instead of an active venv's - meaning any venv-installed
    # dependency (a plugin's, say) would just vanish with no error. Opt in
    # with AGENTIC_RELOAD=1 for active development, and watch for that
    # failure mode if you do (plugins.py logs a load failure if it happens).
    reload = os.environ.get("AGENTIC_RELOAD") == "1"
    uvicorn.run("agentic_harness.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), reload=reload)
