from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from . import model, plugins
import os
import json
import asyncio
import queue
from typing import List, Dict, Any, AsyncIterator, Iterator

app = FastAPI(title="Agentic LLM Harness")

MAX_TOOL_ITERATIONS = int(os.environ.get("AGENTIC_MAX_TOOL_ITERATIONS", "8"))

# load plugins at startup
plugins.load_plugins()

# choose model implementation based on env
MODEL_BACKEND = os.environ.get("AGENTIC_MODEL", "mock")
if MODEL_BACKEND == "openai":
    model_impl = model.OpenAIModel()
else:
    model_impl = model.MockModel()


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
            return f"unknown function: {fname}"

        try:
            result = plugin["callable"](**(args or {}))
        except TypeError:
            result = plugin["callable"](args)

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


@app.get("/v1/models")
async def list_models():
    try:
        ids = await asyncio.to_thread(model_impl.list_models)
    except Exception:
        ids = []
    return JSONResponse(content={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    tools = body.get("tools")
    stream = body.get("stream", False)
    model_name = body.get("model")
    temperature = body.get("temperature", 0.0)
    max_tokens = body.get("max_tokens", 512)

    # token-frugal trimming
    messages = _truncate_messages(messages, max_tokens=2048)

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
    resp = await asyncio.to_thread(model_impl.chat, messages=messages, tools=tools, model=model_name, temperature=temperature, max_tokens=max_tokens)
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

        resp = await asyncio.to_thread(model_impl.chat, messages=messages, tools=tools, model=model_name, temperature=temperature, max_tokens=max_tokens)
        iterations += 1

    return JSONResponse(content=resp)


# Serve the static chat UI. Mounted after the API route above so that route
# takes priority over the catch-all static handler for the same path space.
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run("agentic_harness.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), reload=True)
