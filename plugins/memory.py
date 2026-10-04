import json

from plugins.file_ops import _resolve_safe

MEMORY_FILE = "memory.json"
MAX_ENTRIES = 200
MAX_VALUE_CHARS = 2000


def _load_memory() -> dict:
    path = _resolve_safe(MEMORY_FILE)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_memory(data: dict) -> None:
    path = _resolve_safe(MEMORY_FILE)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remember(key: str, value: str):
    if not key:
        return {"error": "key is required"}
    if len(value or "") > MAX_VALUE_CHARS:
        return {"error": f"value too large (max {MAX_VALUE_CHARS} characters)"}
    data = _load_memory()
    if key not in data and len(data) >= MAX_ENTRIES:
        return {"error": f"memory is full (max {MAX_ENTRIES} entries) - forget something first"}
    data[key] = value
    _save_memory(data)
    return {"status": "saved", "key": key}


def recall(key: str = None):
    data = _load_memory()
    if not key:
        return {"memories": data}
    if key not in data:
        return {"error": f"no memory saved under {key!r}"}
    return {"key": key, "value": data[key]}


def forget(key: str):
    data = _load_memory()
    if key not in data:
        return {"error": f"no memory saved under {key!r}"}
    del data[key]
    _save_memory(data)
    return {"status": "forgotten", "key": key}


def register(registry):
    registry.register("remember", remember, {
        "name": "remember",
        "description": "Save a small piece of information under a short key, persisted across conversations in this chat.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "A short label to save this under, e.g. 'favorite_color'"},
                "value": {"type": "string", "description": "The information to remember"},
            },
            "required": ["key", "value"],
        },
    })
    registry.register("recall", recall, {
        "name": "recall",
        "description": "Retrieve a previously remembered value by key. Omit the key to list everything remembered.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "The label to look up. Omit to list everything."}},
            "required": [],
        },
    })
    registry.register("forget", forget, {
        "name": "forget",
        "description": "Delete a previously remembered value by key.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "The label to delete"}},
            "required": ["key"],
        },
    })
