import hashlib
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

# The root sandbox. Requests that don't say which chat they're for (the
# web UI - the machine's owner) work directly in here; each chat that does
# (the "user" field of a chat request - agentic-gateway sends one per
# Signal chat) gets its own folder under chats/, so its files, uploads and
# memory can't be seen from any other chat. The root contains chats/, so
# the owner can still see everything.
SANDBOX_DIR = Path(os.environ.get("AGENTIC_FILES_DIR", "workspace")).resolve()
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
CHATS_DIR = "chats"

# The sandbox the current request's tools work in - set around each batch
# of tool calls by agentic_harness/main.py (using_sandbox), read by every
# file-touching plugin via sandbox_dir(). A ContextVar so concurrent
# requests for different chats can't see each other's.
_current_sandbox = ContextVar("current_sandbox", default=None)

MAX_READ_CHARS = 200_000
MAX_WRITE_CHARS = 200_000


def safe_output_name(name: str, extension: str, default: str) -> str:
    """A safe filename for a file a plugin creates: letters, digits,
    spaces (as underscores), _ and - only, at most 80 characters, ending
    in `extension` (e.g. ".pdf"). The extension is dropped *before*
    stripping unsafe characters - otherwise its dot goes too, and
    "notes.pdf" becomes "notespdf.pdf"."""
    name = name or ""
    if name.lower().endswith(extension):
        name = name[:-len(extension)]
    name = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip().replace(" ", "_")
    return (name[:80] or default) + extension


def sandbox_dir() -> Path:
    """The current request's sandbox (see using_sandbox) - the root one if
    none is set."""
    return _current_sandbox.get() or SANDBOX_DIR


def chat_sandbox(chat_id) -> Path:
    """The sandbox for one chat: chats/<hash of its id> under the root, or
    the root itself for no chat id. Hashed so folder names don't contain
    phone numbers or group ids."""
    if not chat_id:
        return SANDBOX_DIR
    digest = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16]
    return SANDBOX_DIR / CHATS_DIR / digest


@contextmanager
def using_sandbox(path: Path):
    """Make `path` the sandbox for file plugins called inside this block."""
    path.mkdir(parents=True, exist_ok=True)
    token = _current_sandbox.set(path)
    try:
        yield path
    finally:
        _current_sandbox.reset(token)


def resolve_in(base: Path, path: str) -> Path:
    candidate = (base / path).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("path escapes the sandbox directory")
    return candidate


def _resolve_safe(path: str) -> Path:
    return resolve_in(sandbox_dir(), path)


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
    return {"status": "written", "path": str(target.relative_to(sandbox_dir()))}


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
        "description": "Read a text file from the working directory. Cannot access anything outside it.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the sandbox directory"}},
            "required": ["path"],
        },
    })
    registry.register("write_file", write_file, {
        "name": "write_file",
        "description": "Write text to a file in the working directory, creating it (and parent folders) if needed. Cannot access anything outside it.",
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
        "description": "List files and folders in a directory within the working directory.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the sandbox directory, defaults to '.'"}},
            "required": [],
        },
    })
