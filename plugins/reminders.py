"""Optional reminders plugin: lets the model set, list and cancel reminders
for the chat it's in, which a client then delivers when they're due.

The model can only ever speak when it's spoken to - so without this, "remind
me to book it" got a cheerful promise that could never be kept. With it,
the reminder is stored here against the chat's id (the request's "user"
field), and agentic-gateway collects due ones (GET /v1/reminders/due) and
sends each to its chat, confirming with POST /v1/reminders/{id}/sent - only
then is it removed, so a gateway that's down delivers late, not never.

Chats without an id (the web UI) can't receive a message out of the blue,
so reminders aren't offered there. To remove the feature entirely, delete
this file.
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import plugins.file_ops as file_ops

# Outside the sandbox on purpose: the file tools can't read or edit it.
REMINDERS_FILE = os.environ.get("AGENTIC_REMINDERS_FILE", "reminders.json")
MAX_PER_CHAT = 20
MAX_TEXT_CHARS = 500
# Same time zone as "today's date" in the system prompt.
TIMEZONE = os.environ.get("AGENTIC_TIMEZONE", "")

_lock = threading.Lock()  # tools run in worker threads; the endpoints don't


def _tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(TIMEZONE) if TIMEZONE else datetime.now().astimezone().tzinfo
    except Exception:
        return datetime.now().astimezone().tzinfo


def _load() -> list:
    try:
        with open(REMINDERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        print(f"[reminders] couldn't read {REMINDERS_FILE}: {exc}")
        return []


def _save(reminders: list) -> None:
    tmp_path = f"{REMINDERS_FILE}.tmp"
    # Readable by this account only: they're people's plans.
    with os.fdopen(os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=1)
    os.replace(tmp_path, REMINDERS_FILE)


_RELATIVE = re.compile(r"^in\s+(\d+)\s*(minute|min|hour|hr|day|week)s?$", re.I)
_UNITS = {"minute": "minutes", "min": "minutes", "hour": "hours", "hr": "hours", "day": "days", "week": "weeks"}


def parse_when(when: str, now: datetime = None) -> datetime:
    """'2026-11-13 10:00' (local time; a date alone means 9am) or 'in 2
    hours' -> an aware datetime. Raises ValueError for anything else."""
    now = now or datetime.now(_tz())
    text = " ".join(str(when or "").strip().split())
    match = _RELATIVE.match(text)
    if match:
        return now + timedelta(**{_UNITS[match.group(2).lower()]: int(match.group(1))})
    try:
        due = datetime.fromisoformat(text.replace(" ", "T", 1) if len(text) > 10 else text)
    except ValueError:
        raise ValueError(f"couldn't understand when={when!r} - use e.g. '2026-11-13 10:00' or 'in 2 hours'") from None
    if len(text) <= 10:  # a date alone
        due = due.replace(hour=9, minute=0)
    return due if due.tzinfo else due.replace(tzinfo=_tz())


def _describe(due: datetime) -> str:
    local = due.astimezone(_tz())
    return f"{local.strftime('%A')} {local.day} {local.strftime('%B %Y at %H:%M')}"


def reminder(action: str, when: str = None, text: str = None, id: str = None):  # noqa: A002 - the tool's own name for it
    chat = file_ops.current_chat()
    if not chat:
        return {"error": "Reminders only work in chats that can receive messages (e.g. Signal), not here."}
    action = (action or "").strip().lower()
    with _lock:
        reminders = _load()
        mine = [r for r in reminders if r["chat"] == chat]
        if action == "list":
            return {"reminders": [{"id": r["id"], "when": _describe(datetime.fromisoformat(r["due"])), "text": r["text"]} for r in mine]}
        if action == "cancel":
            if not any(r["id"] == id for r in mine):
                return {"error": f"no reminder {id!r} in this chat - use action 'list' to see their ids"}
            _save([r for r in reminders if r["id"] != id])
            return {"cancelled": id}
        if action != "set":
            return {"error": "action must be 'set', 'list' or 'cancel'"}
        text = " ".join(str(text or "").split())
        if not text:
            return {"error": "text is required: the reminder message itself"}
        if len(text) > MAX_TEXT_CHARS:
            return {"error": f"text is too long (max {MAX_TEXT_CHARS} characters)"}
        if len(mine) >= MAX_PER_CHAT:
            return {"error": f"this chat already has {MAX_PER_CHAT} reminders - cancel one first"}
        try:
            due = parse_when(when)
        except ValueError as exc:
            return {"error": str(exc)}
        if due <= datetime.now(timezone.utc):
            return {"error": f"{_describe(due)} is in the past - check today's date and time (get_current_time)"}
        entry = {"id": uuid.uuid4().hex[:8], "chat": chat, "due": due.astimezone(timezone.utc).isoformat(), "text": text}
        _save(reminders + [entry])
    return {"set": entry["id"], "when": _describe(due), "text": text}


def due_reminders(now: datetime = None) -> list:
    now = now or datetime.now(timezone.utc)
    with _lock:
        return [r for r in _load() if datetime.fromisoformat(r["due"]) <= now]


def mark_sent(reminder_id: str) -> bool:
    with _lock:
        reminders = _load()
        remaining = [r for r in reminders if r["id"] != reminder_id]
        if len(remaining) == len(reminders):
            return False
        _save(remaining)
        return True


def register(registry):
    registry.register("reminder", reminder, {
        "name": "reminder",
        "description": (
            "Set, list or cancel reminders for this chat. A reminder is sent to the chat as a message at the time "
            "given - use it whenever someone asks to be reminded of something (you can't message anyone otherwise)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "list", "cancel"]},
                "when": {"type": "string", "description": "For set: local time like '2026-11-13 10:00', or 'in 2 hours'"},
                "text": {"type": "string", "description": "For set: the reminder message itself, e.g. 'Dom - time to book the Thai place!'"},
                "id": {"type": "string", "description": "For cancel"},
            },
            "required": ["action"],
        },
    })


def register_routes(app):
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    @app.get("/v1/reminders/due")
    async def list_due():
        """Every chat's reminders that are due, for a client to deliver."""
        return JSONResponse(content={"reminders": due_reminders()})

    @app.post("/v1/reminders/{reminder_id}/sent")
    async def sent(reminder_id: str):
        """Delivered: remove it."""
        if not mark_sent(reminder_id):
            raise HTTPException(status_code=404, detail="no such reminder")
        return JSONResponse(content={"removed": reminder_id})
