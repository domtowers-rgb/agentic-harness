"""Optional read_chat plugin: lets the owner's private chat look at the
other chats the gateway keeps - "what happened in the group?".

Reads the gateway's history file (agentic-gateway's GATEWAY_HISTORY_FILE;
the gateway runs on the same machine) rather than asking it, so the
gateway needs no extra endpoint. Owner-only: a group member must never be
able to make the bot read out what someone said in another chat. With no
`chat` it lists the chats; with one, it returns that chat's summary and
latest messages. To remove it, delete this file.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import plugins.file_ops as file_ops

HISTORY_FILE = os.path.expanduser(os.environ.get("AGENTIC_GATEWAY_HISTORY") or str(Path.home() / "agentic-gateway" / "history.json"))
TIMEZONE = os.environ.get("AGENTIC_TIMEZONE") or "UTC"
DEFAULT_MESSAGES = 30
MAX_MESSAGES = 80
MAX_MESSAGE_CHARS = 600


def _load():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _name(key: str, names: dict) -> str:
    if names.get(key):
        return names[key]
    return "group chat" if ":group:" in key else "private chat"


def _when(seconds) -> str:
    try:
        zone = ZoneInfo(TIMEZONE)
    except Exception:
        zone = None
    return datetime.fromtimestamp(seconds, zone).strftime("%d %b %H:%M")


def read_chat(chat: str = "", last: int = DEFAULT_MESSAGES):
    # Owner-only checks who's asking, not where: asked in a group, this
    # would put other people's private chats in front of everyone there.
    if ":group:" in (file_ops.current_chat() or ""):
        return {"error": "read_chat only works in the owner's private chat, not in a group - say so, and offer to look in private"}
    data = _load()
    if data is None:
        return {"error": "the gateway's chat history isn't available"}
    histories = {k: v for k, v in (data.get("histories") or {}).items() if v}
    names = data.get("names") or {}
    if not histories:
        return {"error": "no other chats recorded"}
    if not chat:
        return {"chats": [
            {"chat": _name(key, names), "kind": "group" if ":group:" in key else "private",
             "messages": len(msgs), "last_message": _when(msgs[-1].get("at", 0))}
            for key, msgs in histories.items()
        ]}
    wanted = str(chat).strip().lower()
    matches = [
        key for key in histories
        if wanted in _name(key, names).lower() or wanted in key.lower() or (wanted == "group" and ":group:" in key)
    ]
    if not matches:
        return {"error": f"no chat matches {chat!r} - call with no chat to list them"}
    if len(matches) > 1:
        return {"error": "more than one chat matches: " + ", ".join(_name(k, names) for k in matches)}
    key = matches[0]
    try:
        last = max(1, min(int(last), MAX_MESSAGES))
    except (TypeError, ValueError):
        last = DEFAULT_MESSAGES
    lines = []
    for msg in histories[key][-last:]:
        who = "Niamh (you)" if msg.get("role") == "assistant" else None
        text = (msg.get("content") or "").strip()
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS].rstrip() + "..."
        lines.append(f"[{_when(msg.get('at', 0))}] " + (f"{who}: {text}" if who else text))
    result = {"chat": _name(key, names), "messages": lines}
    summary = (data.get("summaries") or {}).get(key)
    if summary:
        result["earlier_summary"] = summary
    return result


def register(registry):
    registry.register("read_chat", read_chat, {
        "name": "read_chat",
        "description": (
            "Look at one of your OTHER chats - e.g. the group chat, when asked in private what happened there. "
            "Call with no arguments to list the chats, or with chat (a name, or 'group') to read its latest messages. "
            "Only when asked about another chat; don't mention it unprompted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chat": {"type": "string", "description": "the chat's name, or 'group'; empty to list chats"},
                "last": {"type": "integer", "description": f"how many recent messages (default {DEFAULT_MESSAGES}, max {MAX_MESSAGES})"},
            },
        },
    }, owner_only=True)
