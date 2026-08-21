from typing import List, Optional, Dict, Any, Iterator
import os
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

DEFAULT_MODEL = os.environ.get("AGENTIC_DEFAULT_MODEL", "gpt-4o-mini")


class BaseModel:
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Dict:
        raise NotImplementedError()

    def chat_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None, **kwargs) -> Iterator[Dict]:
        # default: return single final response as one chunk
        yield self.chat(messages=messages, tools=tools, **kwargs)

    def list_models(self) -> List[str]:
        return []


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

        for event in self._client.chat.completions.create(stream=True, **params):
            yield event.model_dump()

    def list_models(self) -> List[str]:
        resp = self._client.models.list()
        return sorted(m.id for m in resp.data)


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
