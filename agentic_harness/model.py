from typing import List, Optional, Dict, Any, Iterator
import os

import httpx
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

DEFAULT_MODEL = os.environ.get("AGENTIC_DEFAULT_MODEL", "gpt-4o-mini")


class LoadUnavailable(Exception):
    """The backend can't load/eject models on request (it isn't LM Studio)."""


def _lmstudio_post(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    resp = httpx.post(url, json=body, timeout=timeout)
    if resp.is_error:
        try:
            message = resp.json()["error"]["message"]
        except Exception:
            message = resp.text[:300]
        raise RuntimeError(f"LM Studio: {message}")
    return resp.json()


class BaseModel:
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Dict:
        raise NotImplementedError()

    def chat_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Iterator[Dict]:
        # default: return single final response as one chunk
        yield self.chat(messages=messages, tools=tools, **kwargs)

    def list_models(self) -> List[str]:
        return []

    def model_details(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Extra facts per model id - {"loaded": bool, "type": ...} - where
        the server can say; None where it can't."""
        return None


class OpenAIModel(BaseModel):
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        if OpenAI is None:
            raise RuntimeError("openai package is required for OpenAIModel")
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY") or "not-needed"
        resolved_base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._client = OpenAI(api_key=resolved_key, base_url=resolved_base_url)
        self.base_url = resolved_base_url

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Dict:
        params = {
            "model": kwargs.get("model") or DEFAULT_MODEL,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
        }
        if kwargs.get("max_tokens") is not None:
            params["max_tokens"] = kwargs["max_tokens"]
        if tools:
            params["tools"] = tools
            params["tool_choice"] = kwargs.get("tool_choice", "auto")

        resp = self._client.chat.completions.create(**params)
        return resp.model_dump()

    def chat_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Iterator[Dict]:
        params = {
            "model": kwargs.get("model") or DEFAULT_MODEL,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
        }
        if kwargs.get("max_tokens") is not None:
            params["max_tokens"] = kwargs["max_tokens"]
        if tools:
            params["tools"] = tools
            params["tool_choice"] = kwargs.get("tool_choice", "auto")

        # As a context manager, so closing this generator early (see
        # _iter_in_thread) closes the HTTP response too - which is what tells
        # the model server to stop generating.
        with self._client.chat.completions.create(stream=True, **params) as stream:
            for event in stream:
                yield event.model_dump()

    def list_models(self) -> List[str]:
        resp = self._client.models.list()
        return sorted(m.id for m in resp.data)

    def _lmstudio_root(self) -> Optional[str]:
        if not self.base_url:
            return None
        root = self.base_url.rstrip("/")
        return root[:-len("/v1")] if root.endswith("/v1") else root

    def load_exclusive(self, model_id: str) -> Dict[str, Any]:
        """Make `model_id` the only chat model loaded in LM Studio: eject
        every other loaded chat model first (freeing their memory), then
        load it unless it already is. Embedding models are left alone.
        Uses LM Studio's management API (/api/v1/models, .../load,
        .../unload). Raises LoadUnavailable if the server isn't LM Studio,
        KeyError if it has no such model, RuntimeError if a step fails."""
        root = self._lmstudio_root()
        try:
            listing = httpx.get(f"{root}/api/v1/models", timeout=10) if root else None
            if listing is None or listing.status_code == 404:
                raise LoadUnavailable("loading models is only supported with LM Studio")
            listing.raise_for_status()
            models = listing.json()["models"]
        except LoadUnavailable:
            raise
        except Exception as exc:
            raise LoadUnavailable(f"couldn't reach LM Studio's model API: {exc}") from exc

        target = next((m for m in models if m.get("key") == model_id), None)
        if target is None:
            raise KeyError(model_id)

        unloaded = []
        for m in models:
            if m.get("key") == model_id or m.get("type") not in ("llm", "vlm"):
                continue
            for instance in m.get("loaded_instances") or []:
                _lmstudio_post(f"{root}/api/v1/models/unload", {"instance_id": instance["id"]}, timeout=120)
                unloaded.append(m["key"])

        already_loaded = bool(target.get("loaded_instances"))
        load_seconds = None
        if not already_loaded:
            result = _lmstudio_post(f"{root}/api/v1/models/load", {"model": model_id}, timeout=600)
            load_seconds = result.get("load_time_seconds")
        return {
            "model": model_id, "unloaded": list(dict.fromkeys(unloaded)),
            "already_loaded": already_loaded, "load_time_seconds": load_seconds,
        }

    def model_details(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """For LM Studio: which models are loaded, and each one's type
        ("llm", "vlm", "embeddings"...), from its own REST API
        (/api/v0/models) - the OpenAI-style list it also serves says
        neither. None for any other server, or if it can't be reached."""
        root = self._lmstudio_root()
        if not root:
            return None
        try:
            resp = httpx.get(f"{root}/api/v0/models", timeout=5)
            resp.raise_for_status()
            data = resp.json()["data"]
            return {
                m["id"]: {"loaded": m.get("state") == "loaded", "type": m.get("type")}
                for m in data if isinstance(m, dict) and m.get("id")
            }
        except Exception:
            return None


class MockModel(BaseModel):
    """A tiny, token-frugal mock model for local testing.

    Behavior: echoes last user message and optionally requests a tool call
    if the user message starts with "call:" followed by a tool name.
    """
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Dict:
        last = messages[-1]["content"] if messages else ""
        choice = {"role": "assistant", "content": f"Echo: {last}"}
        # simple tool call request if user asked
        if last.strip().lower().startswith("call:") and tools:
            name = last.split(" ", 1)[0].split(":", 1)[1]
            choice = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": name, "arguments": "{}"},
                    }
                ],
            }
        return {"id": "mock-1", "object": "chat.completion", "choices": [{"message": choice, "finish_reason": None}], "usage": {}}

    def chat_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Iterator[Dict]:
        last = messages[-1]["content"] if messages else ""

        if last.strip().lower().startswith("call:") and tools:
            name = last.split(" ", 1)[0].split(":", 1)[1]
            delta = {
                "choices": [{
                    "delta": {"tool_calls": [
                        {"index": 0, "id": "call_1", "type": "function", "function": {"name": name, "arguments": "{}"}}
                    ]},
                    "finish_reason": "tool_calls",
                }],
                "object": "chat.completion.chunk",
            }
            yield delta
            return

        # simple token-frugal streaming: split echoed content into small chunks
        content = f"Echo: {last}"
        # stream per-word
        parts = content.split()
        for i, p in enumerate(parts):
            delta = {"choices": [{"delta": {"content": (p + (" " if i < len(parts)-1 else ""))}}], "object": "chat.completion.chunk"}
            yield delta
        # final full message
        final = {"id": "mock-1", "object": "chat.completion", "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], "usage": {}}
        yield final

    def list_models(self) -> List[str]:
        return ["mock"]
