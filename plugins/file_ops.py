import os
from pathlib import Path

SANDBOX_DIR = Path(os.environ.get("AGENTIC_FILES_DIR", "workspace")).resolve()
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

MAX_READ_CHARS = 200_000
MAX_WRITE_CHARS = 200_000


def _resolve_safe(path: str) -> Path:
    candidate = (SANDBOX_DIR / path).resolve()
    if candidate != SANDBOX_DIR and SANDBOX_DIR not in candidate.parents:
        raise ValueError("path escapes the sandbox directory")
    return candidate


def read_file(path: str):
    try:
        target = _resolve_safe(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    data = target.read_text(encoding="utf-8", errors="replace")
    return {"content": data[:MAX_READ_CHARS], "truncated": len(data) > MAX_READ_CHARS}


def write_file(path: str, content: str):
    try:
        target = _resolve_safe(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if len(content) > MAX_WRITE_CHARS:
        return {"error": f"content too large (max {MAX_WRITE_CHARS} characters)"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "written", "path": str(target.relative_to(SANDBOX_DIR))}


def list_files(path: str = "."):
    try:
        target = _resolve_safe(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not target.is_dir():
        return {"error": f"no such directory: {path}"}
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return {"entries": entries}


def register(registry):
    registry.register("read_file", read_file, {
        "name": "read_file",
        "description": f"Read a text file from the sandboxed working directory ({SANDBOX_DIR}). Cannot access anything outside it.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the sandbox directory"}},
            "required": ["path"],
        },
    })
    registry.register("write_file", write_file, {
        "name": "write_file",
        "description": f"Write text to a file in the sandboxed working directory ({SANDBOX_DIR}), creating it (and parent folders) if needed. Cannot access anything outside it.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the sandbox directory"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
    })
    registry.register("list_files", list_files, {
        "name": "list_files",
        "description": f"List files and folders in a directory within the sandboxed working directory ({SANDBOX_DIR}).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the sandbox directory, defaults to '.'"}},
            "required": [],
        },
    })
