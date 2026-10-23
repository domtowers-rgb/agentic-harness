# Settings from .env, loaded before anything below (or a plugin/channel)
# reads them. Only when started as the service - never when imported by
# the tests, which must not pick up someone's real .env.
if __name__ == "__main__":
    from agentic_harness.config import load_env_file
    load_env_file()

import asyncio
import json
import os
import queue
import threading
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterator, List
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import files, model, plugins, prompt, tools

app = FastAPI(title="Agentic LLM Harness")

# The server only listens on this machine - but the user's web browser is
# on this machine too, and would otherwise let any website they visit talk
# to it: a "simple" cross-site POST (Content-Type text/plain, no preflight)
# could repoint /v1/connect at the attacker's own server - sending every
# later conversation there - or plant files; and a DNS-rebinding site
# (its own hostname resolving to 127.0.0.1) could read responses outright,
# workspace files included. Both confirmed against this server before the
# checks below were added. Two checks close them:
#   - the Host header must name this machine (defeats DNS rebinding);
#   - a request carrying an Origin header - which browsers add to
#     cross-site and same-site POSTs alike, unlike non-browser clients
#     such as agentic-gateway - must come from one of those same hosts.
# AGENTIC_ALLOWED_HOSTS adds more hostnames (comma-separated), e.g. if the
# server is reached by a LAN name.
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1"] + [
    h.strip() for h in os.environ.get("AGENTIC_ALLOWED_HOSTS", "").split(",") if h.strip()
]


@app.middleware("http")
async def reject_cross_site_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin is not None and (urlparse(origin).hostname or "") not in ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "cross-site request refused"})
    return await call_next(request)


# Added last so it runs first.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

MAX_TOOL_ITERATIONS = int(os.environ.get("AGENTIC_MAX_TOOL_ITERATIONS", "8"))

# Used whenever a request doesn't specify its own max_tokens (the web UI
# never does). Was hardcoded at 512, which is often not enough room for a
# local "thinking" model to finish its reasoning and still produce an
# answer - every token generated toward a response that then gets cut off
# is wasted decode time, not saved time.
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENTIC_DEFAULT_MAX_TOKENS", "2048"))

# Used whenever a request doesn't set its own temperature (neither the web
# UI nor agentic-gateway does). Was 0, which makes a model pick its single
# likeliest word every time - and its likeliest is the stock assistant
# phrasing ("You're welcome! Let me know if..."), the same way every time.
# 0.7 is the usual setting for chat; tool calls still work fine at it.
DEFAULT_TEMPERATURE = float(os.environ.get("AGENTIC_DEFAULT_TEMPERATURE", "0.7"))

# load plugins at startup
plugins.load_plugins(app=app)
# Uploads and downloads (see files.py).
files.register_routes(app)

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


def _tool_limit_fallback() -> str:
    return f"Sorry - I stopped after {MAX_TOOL_ITERATIONS} rounds of tool calls without reaching an answer."


async def _iter_in_thread(sync_iterable: Iterator[Dict]) -> AsyncIterator[Dict]:
    """Consume a blocking iterator (e.g. a model's HTTP-streaming generator) in a
    background thread, so a slow local model doesn't block the event loop while
    other requests are in flight.

    If the consumer stops early - the client disconnected, e.g. the web UI's
    Stop button - the worker stops at the next item and closes the iterator,
    which closes the model server connection and so ends the generation.
    Before, the worker kept reading until the model finished on its own,
    keeping the GPU busy on a reply nobody would see."""
    q = queue.Queue()
    DONE = object()
    stop = threading.Event()

    def worker():
        try:
            for item in sync_iterable:
                if stop.is_set():
                    break
                q.put(item)
        except Exception as exc:
            q.put(exc)
        finally:
            close = getattr(sync_iterable, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
            q.put(DONE)

    asyncio.get_running_loop().run_in_executor(None, worker)

    try:
        while True:
            item = await asyncio.to_thread(q.get)
            if item is DONE:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stop.set()


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
            ) from exc
        raise HTTPException(status_code=502, detail=f"model backend error: {exc}") from exc


# One load at a time - two overlapping "make this the only model" requests
# would eject each other's models.
_load_lock = asyncio.Lock()


@app.post("/v1/models/load")
async def load_model(request: Request):
    """{"model": id} -> make it the only chat model loaded on the backend:
    every other loaded chat model is ejected first, then it's loaded (if it
    wasn't already). LM Studio only (501 otherwise). Can take a minute or
    more for a big model. Note this affects everything using the backend,
    not just the caller. Returns {"model", "unloaded", "already_loaded",
    "load_time_seconds"}."""
    body = await request.json()
    model_id = body.get("model") if isinstance(body, dict) else None
    if not isinstance(model_id, str) or not model_id.strip():
        raise HTTPException(status_code=400, detail="model is required")
    if not hasattr(model_impl, "load_exclusive"):
        raise HTTPException(status_code=501, detail="loading models is only supported with LM Studio")
    async with _load_lock:
        try:
            result = await asyncio.to_thread(model_impl.load_exclusive, model_id.strip())
        except model.LoadUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no model named {model_id!r}") from None
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"loading {model_id} failed: {exc}") from exc
    return JSONResponse(content=result)


@app.get("/v1/models")
async def list_models():
    """The backend's models, OpenAI-style. Where the backend can say (LM
    Studio), each entry also carries "loaded" and "type" - extra fields
    OpenAI-compatible clients ignore; agentic-gateway's /model uses them
    to offer only loaded chat models."""
    try:
        ids = await asyncio.to_thread(model_impl.list_models)
    except Exception:
        ids = []
    details = None
    if hasattr(model_impl, "model_details"):
        try:
            details = await asyncio.to_thread(model_impl.model_details)
        except Exception:
            details = None
    data = [{"id": i, "object": "model", **((details or {}).get(i) or {})} for i in ids]
    return JSONResponse(content={"object": "list", "data": data})


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
    """Which backend is in use, plus the /v1 endpoints this server offers -
    so a client can tell whether an optional plugin's endpoints (e.g. the
    voice plugin's /v1/transcribe and /v1/speak) are there."""
    endpoints = sorted({route.path for route in app.routes if getattr(route, "path", "").startswith("/v1/")})
    return JSONResponse(content={"backend": MODEL_BACKEND, "base_url": CURRENT_BASE_URL, "endpoints": endpoints})


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
        raise HTTPException(status_code=400, detail=f"could not connect to {base_url}: {exc}") from exc

    model_impl = candidate
    MODEL_BACKEND = "openai"
    CURRENT_BASE_URL = base_url
    return JSONResponse(content={"status": "connected", "base_url": base_url, "models": ids})


@dataclass
class ChatRequest:
    """A chat request, ready for the model: history trimmed to fit, the
    personality and date in front, and the plugin tools added."""
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    model: str
    temperature: float
    max_tokens: int
    sandbox: Any  # the chat's own folder - its file tools, uploads and memory live there
    chat: str = None  # the chat's id (the standard "user" field), if the client sent one

    def model_args(self, **extra) -> Dict[str, Any]:
        return {
            "messages": self.messages, "tools": self.tools, "model": self.model,
            "temperature": self.temperature, "max_tokens": self.max_tokens, **extra,
        }


async def _prepare(body: Dict[str, Any]) -> ChatRequest:
    tools_ = list(body.get("tools") or [])
    model_name = body.get("model")
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
    existing_names = {t.get("function", {}).get("name") for t in tools_}
    for tool in _PLUGIN_TOOLS:
        name = tool["function"]["name"]
        if name in existing_names:
            continue
        if enabled_plugins is not None and name not in enabled_plugins:
            continue
        tools_.append(tool)

    # Trim history to fit: AGENTIC_HISTORY_MAX_TOKENS, but never more than
    # the model was actually loaded with room for (see history_budget).
    budget = await prompt.history_budget(model_impl, model_name, tools_, max_tokens)
    messages = prompt.truncate_messages(body.get("messages") or [], max_tokens=budget)

    # Personality system prompt (plus today's date - see grounding): a
    # small, fixed addition on top of the (already-trimmed) conversation
    # budget, so it's never at risk of being trimmed away itself.
    messages = prompt.with_personality(messages, prompt.grounding({t["function"]["name"] for t in tools_ if "function" in t}))

    return ChatRequest(
        messages=messages, tools=tools_, model=model_name, temperature=DEFAULT_TEMPERATURE if body.get("temperature") is None else body["temperature"],
        max_tokens=max_tokens,
        # Which chat this is for (the standard "user" field).
        sandbox=files.file_ops.chat_sandbox(files.chat_id(body.get("user"))), chat=files.chat_id(body.get("user")),
    )


def _ask_for_the_answer(messages: List[Dict[str, Any]], notes: str) -> None:
    """The reply was only the model's working (see prompt.clean_reply):
    ask once more, for the answer."""
    messages += [{"role": "assistant", "content": notes}, prompt.NOTES_ONLY_NUDGE]


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    req = await _prepare(body)
    if body.get("stream", False):
        return StreamingResponse(_stream(req), media_type="text/event-stream")
    return JSONResponse(content=await _complete(req))


async def _complete(req: ChatRequest) -> Dict[str, Any]:
    # Tool-calling loop: keep executing tool calls and re-prompting the model
    # until it returns a plain response. Once the iteration cap is used up,
    # one last round with tool_choice="none" asks for an answer from what it
    # has (see TOOL_LIMIT_NUDGE). Both the model call and the tools
    # themselves run in a thread, so neither a slow local model nor a slow
    # tool (a web fetch, say) blocks the event loop - and with it every
    # other request.
    resp = await _call_model(**req.model_args())
    iterations = 0
    attachment_paths = {}
    while True:
        message = resp["choices"][0].get("message") or {}
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            break

        if iterations >= MAX_TOOL_ITERATIONS:
            req.messages.append(prompt.TOOL_LIMIT_NUDGE)
            resp = await _call_model(**req.model_args(tool_choice="none"))
            final = resp["choices"][0].get("message") or {}
            if final.get("tool_calls") or not (final.get("content") or "").strip():
                resp["choices"][0]["message"] = {"role": "assistant", "content": _tool_limit_fallback()}
            break

        tools.collect_attachments(attachment_paths, await asyncio.to_thread(tools.run_tool_calls, req.messages, tool_calls, message.get("content"), req.sandbox, req.chat))
        resp = await _call_model(**req.model_args())
        iterations += 1

    # Leaked working (see clean_reply): strip it - or, if the reply was
    # nothing but working, ask once more for the actual answer.
    final = resp["choices"][0].get("message") or {}
    if not final.get("tool_calls") and isinstance(final.get("content"), str):
        cleaned = prompt.clean_reply(final["content"])
        if cleaned is None:
            _ask_for_the_answer(req.messages, final["content"])
            resp = await _call_model(**req.model_args(tool_choice="none"))
            final = resp["choices"][0].get("message") or {}
            cleaned = prompt.clean_reply(final.get("content") or "") or prompt.NO_ANSWER_REPLY
        resp["choices"][0]["message"] = {**final, "role": "assistant", "content": cleaned, "tool_calls": None}

    # Files created by tools this request, as an extra top-level field
    # alongside the standard ones - OpenAI-compatible clients ignore it;
    # agentic-gateway downloads and sends them on.
    attachments = files.describe_attachments(attachment_paths.values(), req.sandbox)
    if attachments:
        resp["attachments"] = attachments
    return resp


def _event(data) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def _stream(req: ChatRequest):
    """The same loop as _complete, streamed: each model chunk is forwarded
    as-is. If the model streams tool_calls, the deltas are accumulated, the
    tools run once the round finishes, and the next round streams in the
    same SSE response - until a plain response comes back or the iteration
    cap is hit.

    A reply with leaked working (see clean_reply) has already been shown
    by the time it's finished, so it's corrected after the fact: a
    "replace_content" event ({"old", "new"}) - not a standard chunk, so
    clients unaware of it skip it - tells the client to swap the text it
    just streamed for the cleaned one."""
    iterations = 0
    attachment_paths = {}
    tool_choice = "auto"
    retried_for_answer = False
    while True:
        if tool_choice == "auto" and iterations >= MAX_TOOL_ITERATIONS:
            req.messages.append(prompt.TOOL_LIMIT_NUDGE)
            tool_choice = "none"
        tool_call_accum = {}
        content = []

        stream_iter = model_impl.chat_stream(**req.model_args(tool_choice=tool_choice))
        # aclosing: if the client goes away mid-stream (the web UI's Stop
        # button), close the model stream right away - see _iter_in_thread.
        async with aclosing(_iter_in_thread(stream_iter)) as chunks:
            async for chunk in chunks:
                try:
                    yield _event(chunk)
                except Exception:
                    # fallback to str
                    yield f"data: {str(chunk)}\n\n"
                    continue

                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}) if choices else {}
                if delta.get("content"):
                    content.append(delta["content"])
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

        if tool_choice == "auto" and tool_call_accum:
            tool_calls = [
                {
                    "id": slot["id"] or f"call_{idx}",
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
                }
                for idx, slot in sorted(tool_call_accum.items())
            ]
            tools.collect_attachments(attachment_paths, await asyncio.to_thread(tools.run_tool_calls, req.messages, tool_calls, None, req.sandbox, req.chat))
            iterations += 1
            continue

        # The final round.
        text = "".join(content)
        if not text:
            if tool_choice == "none":
                fallback = prompt.NO_ANSWER_REPLY if retried_for_answer else _tool_limit_fallback()
                yield _event({"choices": [{"delta": {"content": fallback}, "finish_reason": "stop"}]})
            break
        cleaned = prompt.clean_reply(text)
        if cleaned is None and not retried_for_answer:
            yield _event({"replace_content": {"old": text, "new": ""}})
            _ask_for_the_answer(req.messages, text)
            retried_for_answer, tool_choice = True, "none"
            continue
        cleaned = cleaned or prompt.NO_ANSWER_REPLY
        if cleaned != text:
            yield _event({"replace_content": {"old": text, "new": cleaned}})
        break

    attachments = files.describe_attachments(attachment_paths.values(), req.sandbox)
    if attachments:
        # Not a standard chat-completion chunk - an extra event
        # (no "choices") that clients unaware of it just skip.
        yield _event({"attachments": attachments})
    yield "data: [DONE]\n\n"


# Serve the static chat UI. Mounted after the API routes above so they take
# priority over the catch-all static handler for the same path space.
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
