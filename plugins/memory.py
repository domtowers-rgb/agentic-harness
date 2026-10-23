"""A small persistent notes store for each chat - memory.json in its
sandbox - exposed to the model as one `memory` tool."""
import json
import threading
import time

from plugins.file_ops import _resolve_safe, write_json

MEMORY_FILE = "memory.json"
MAX_ENTRIES = 200
MAX_VALUE_CHARS = 2000

# Tools run in worker threads: without this, two requests saving at once
# (the web UI can send several) could each overwrite the other's change.
_lock = threading.Lock()


def _load_memory() -> dict:
    path = _resolve_safe(MEMORY_FILE)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Kept, not overwritten: the next save would otherwise replace
        # every memory with just the new one.
        aside = path.with_name(f"{MEMORY_FILE}.unreadable-{int(time.time())}")
        try:
            path.rename(aside)
        except OSError:
            pass
        print(f"[memory] {path} couldn't be read ({exc}) - moved aside to {aside.name}, starting afresh")
        return {}


def _save_memory(data: dict) -> None:
    write_json(_resolve_safe(MEMORY_FILE), data)


def remember(key: str, value: str):
    if not key:
        return {"error": "key is required"}
    if len(value or "") > MAX_VALUE_CHARS:
        return {"error": f"value too large (max {MAX_VALUE_CHARS} characters)"}
    with _lock:
        data = _load_memory()
        if key not in data and len(data) >= MAX_ENTRIES:
            return {"error": f"memory is full (max {MAX_ENTRIES} entries) - forget something first"}
        data[key] = value
        _save_memory(data)
    return {"status": "saved", "key": key}


def recall(key: str = None):
    with _lock:
        data = _load_memory()
    if not key:
        return {"memories": data}
    if key not in data:
        return {"error": f"no memory saved under {key!r}"}
    return {"key": key, "value": data[key]}


def forget(key: str):
    with _lock:
        data = _load_memory()
        if key not in data:
            return {"error": f"no memory saved under {key!r}"}
        del data[key]
        _save_memory(data)
    return {"status": "forgotten", "key": key}


def memory(action: str, key: str = None, value: str = None):
    action = (action or "").strip().lower()
    if action == "save":
        return remember(key, value)
    if action == "get":
        return recall(key)
    if action == "delete":
        return forget(key)
    return {"error": "action must be 'save', 'get' or 'delete'"}


def register(registry):
    # One tool rather than three: every tool's definition goes with every
    # request, so this saves ~90 prompt tokens each time.
    registry.register("memory", memory, {
        "name": "memory",
        "description": "Notes kept between conversations: save a fact under a short key, get one (or all, with no key), or delete one.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "get", "delete"]},
                "key": {"type": "string", "description": "e.g. 'favorite_color'"},
                "value": {"type": "string", "description": "For save"},
            },
            "required": ["action"],
        },
    })
