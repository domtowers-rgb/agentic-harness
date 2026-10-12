"""Files in and out of the sandbox over HTTP: what a client uploads (e.g. a
document sent over Signal, via agentic-gateway) and what the tools made,
for the client to download."""
import asyncio
import mimetypes
import os
import re
from typing import Any, Dict, List

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import plugins.file_ops as file_ops

# Uploads are saved here, inside the same sandbox the file plugins use -
# so read_document can then read them by the path the upload returns.
UPLOAD_DIR = "uploads"
MAX_UPLOAD_BYTES = int(os.environ.get("AGENTIC_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))


def chat_id(value) -> str:
    """The client's chat id - the standard "user" field of a chat request,
    or ?user= on an upload - or None. agentic-gateway sends one per Signal
    chat; the web UI sends none."""
    if isinstance(value, str) and value.strip() and len(value) <= 256:
        return value.strip()
    return None


def safe_upload_name(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "", stem).strip(" .")[:80] or "upload"
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:10]
    return f"{stem}{ext}".replace(" ", "_")


async def upload_file(request: Request, filename: str = "", user: str = ""):
    """Save the raw request body as a file in the sandbox's uploads/ folder
    and return its workspace path. Raw body + ?filename= rather than a
    multipart form, to avoid an extra dependency (python-multipart) for a
    single-file upload. An existing name gets a numeric suffix rather than
    being overwritten."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large (max {MAX_UPLOAD_BYTES} bytes)")
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large (max {MAX_UPLOAD_BYTES} bytes)")

    # Into the uploading chat's own sandbox (?user=, same id as its chat
    # requests send), so only that chat's tools can read it.
    sandbox = file_ops.chat_sandbox(chat_id(user))
    name = safe_upload_name(filename)
    stem, ext = os.path.splitext(name)
    target = file_ops.resolve_in(sandbox, os.path.join(UPLOAD_DIR, name))
    counter = 1
    while target.exists():
        counter += 1
        target = file_ops.resolve_in(sandbox, os.path.join(UPLOAD_DIR, f"{stem}-{counter}{ext}"))
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, body)
    path = target.relative_to(sandbox).as_posix()
    return JSONResponse(content={"path": path, "filename": target.name, "size": len(body)})


async def download_file(path: str):
    """Download a file from the sandbox - how a client fetches what a tool
    created (see the "attachments" in a chat completion response). Same
    sandbox, and same local-only trust model, as the file plugins
    themselves: anything that can reach this server could already ask
    read_file for the same content."""
    try:
        target = file_ops.resolve_in(file_ops.SANDBOX_DIR, path)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, filename=target.name)


def describe_attachments(paths, sandbox=None) -> List[Dict[str, Any]]:
    """What the client gets for each created file: enough to show it and
    download it. `path` is relative to the chat's sandbox (what the model
    sees); `url` is GET /v1/files/ + its path from the root sandbox. A path
    listed twice is listed once; one that no longer exists is dropped."""
    sandbox = sandbox or file_ops.SANDBOX_DIR
    described = []
    for path in dict.fromkeys(paths):
        try:
            target = file_ops.resolve_in(sandbox, path)
        except ValueError:
            continue
        if not target.is_file():
            continue
        described.append({
            "filename": target.name,
            "path": path,
            "url": f"/v1/files/{target.relative_to(file_ops.SANDBOX_DIR).as_posix()}",
            "size": target.stat().st_size,
            "content_type": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        })
    return described


def register_routes(app) -> None:
    # Added straight to the app (like a plugin's routes), so /v1/status
    # lists them.
    app.post("/v1/files")(upload_file)
    app.get("/v1/files/{path:path}")(download_file)
