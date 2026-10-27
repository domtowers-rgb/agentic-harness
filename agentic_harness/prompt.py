"""What the model is sent besides the conversation, and how much of the
conversation fits: the personality, today's date, trimming history to the
model's context - plus the fixed nudges the tool loop adds, and cleaning a
reply of the model's leaked working."""
import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List

from . import clock, model

PERSONALITY_FILE = os.environ.get("AGENTIC_PERSONALITY_FILE", "SOUL.md")
PERSONALITY_MAX_TOKENS = 200

# How much of the conversation history to keep, in the same rough
# chars-per-token heuristic truncate_messages uses. Tune this to roughly
# match your model's actual configured context window.
HISTORY_MAX_TOKENS = int(os.environ.get("AGENTIC_HISTORY_MAX_TOKENS", "2048"))


def load_personality(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return ""
    # Same rough ~4-chars/token heuristic used elsewhere in this file - not
    # exact, but enough to flag "this is probably way over the intended size".
    estimated_tokens = len(text) / 4
    if estimated_tokens > PERSONALITY_MAX_TOKENS:
        print(
            f"[personality] {path} is ~{estimated_tokens:.0f} tokens (estimated), "
            f"over the {PERSONALITY_MAX_TOKENS}-token guideline - consider trimming it. Using it as-is."
        )
    return text


PERSONALITY = load_personality(PERSONALITY_FILE)


# What an image in a message counts as toward the history budget, in
# characters (~4 per token, like everything else here). Vision models
# typically spend a few hundred tokens on an image; counting its base64
# data instead - often millions of characters - would make one photo push
# the entire rest of the conversation out of the budget.
IMAGE_CHARS_ESTIMATE = 1000


def content_chars(message: Dict[str, Any]) -> int:
    content = message.get("content")
    if content is None:
        return 0  # e.g. an assistant turn that only made tool calls
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):  # content parts: text and/or images
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += len(part.get("text") or "")
            elif isinstance(part, dict) and part.get("type") in ("image_url", "input_image", "image"):
                total += IMAGE_CHARS_ESTIMATE
            else:
                total += len(json.dumps(part, default=str))
        return total
    return len(json.dumps(content, default=str))


# Set to 0 to leave the date (and the "check before guessing" line) out.
INCLUDE_GROUNDING = os.environ.get("AGENTIC_INCLUDE_DATE", "1") != "0"


def today() -> str:
    """e.g. "Monday 12 October 2026 (Europe/London)". Date only, on purpose:
    this sits at the very start of the prompt, and anything that changes
    there makes a local server reprocess the whole prompt instead of
    reusing its cache - a date changes once a day, a clock every minute.
    The model has get_current_time for the exact time."""
    now = datetime.now(clock.local_tz())
    return f"{now.strftime('%A')} {now.day} {now.strftime('%B %Y')} ({clock.zone_name()})"


def grounding(tool_names) -> str:
    """Facts a local model can't know, and a nudge against guessing. Local
    models believe it's still the year they were trained, and answer
    confidently from stale or invented memory - one line each helps."""
    if not INCLUDE_GROUNDING:
        return ""
    lines = [f"Today is {today()}."]
    if "web_search" in tool_names:
        lines.append(
            "Your knowledge has a cut-off and can be wrong: for anything current, or a fact you're not sure of, "
            "use web_search rather than guessing - and say when you don't know."
        )
    return " ".join(lines)


def with_personality(messages: List[Dict[str, Any]], extra: str = "") -> List[Dict[str, Any]]:
    """Put PERSONALITY (plus `extra`, e.g. today's date - see grounding)
    first. If the client sent its own leading system message - e.g.
    agentic-gateway explaining who's in a group chat - they're combined
    into one, personality first: that context adds to the personality
    rather than replacing it, and many local chat templates accept only a
    single system message, at the start."""
    preamble = "\n\n".join(part for part in (PERSONALITY, extra) if part)
    if not preamble:
        return messages
    if messages and messages[0].get("role") == "system":
        own = messages[0].get("content")
        if isinstance(own, str):
            combined = f"{preamble}\n\n{own}" if own.strip() else preamble
        else:  # content parts
            combined = [{"type": "text", "text": preamble}, *(own or [])]
        return [{**messages[0], "content": combined}] + messages[1:]
    return [{"role": "system", "content": preamble}] + messages


async def history_budget(backend, model_name, tools, max_tokens) -> int:
    """How many tokens of history to keep: AGENTIC_HISTORY_MAX_TOKENS - but
    if the backend says the model is loaded with a smaller context window
    (llama.cpp and LM Studio do), less, leaving room for the tool definitions, the
    system prompt and the reply. Otherwise a long conversation would
    overflow the model and fail, or be cut off by the server."""
    if not hasattr(backend, "context_length"):
        return HISTORY_MAX_TOKENS
    try:
        context = await asyncio.to_thread(backend.context_length, model_name or model.DEFAULT_MODEL)
    except Exception:
        context = None
    if not context:
        return HISTORY_MAX_TOKENS
    tool_tokens = len(json.dumps(tools)) // 4
    room = context - tool_tokens - max_tokens - 600  # 600: system prompt and margin
    return max(512, min(HISTORY_MAX_TOKENS, room))


# History over budget is trimmed this many messages at a time, counted from
# the start - see truncate_messages.
TRIM_BLOCK = 16


def truncate_messages(messages: List[Dict[str, Any]], max_tokens: int = 1500) -> List[Dict[str, Any]]:
    """Drop the oldest messages until the history fits max_tokens (very
    simple heuristic: ~4 chars per token).

    Dropped in whole blocks of TRIM_BLOCK messages, counted from the start,
    rather than one at a time: trimming just enough meant the conversation
    started one message later on every turn, so the prompt changed at the
    very beginning and a local server re-read all of it, every turn. With
    blocks, the cut stays put for several turns, until the next block has
    to go, and the server can reuse what it has cached in between.

    Never drops the newest message - previously, one long enough to blow
    the budget on its own (a pasted document, say) was dropped along with
    everything else, so the model got no question at all and answered
    nothing in particular. Better to send it over budget and let the
    backend complain if it really doesn't fit. A leading system message is
    kept too, so a client's own system prompt survives a long
    conversation instead of being the first thing trimmed. And the kept
    history never starts with an orphaned tool result whose assistant
    tool-call turn was dropped - backends reject that."""
    allowed_chars = max_tokens * 4
    if sum(content_chars(m) for m in messages) <= allowed_chars:
        return messages

    system = messages[:1] if messages and messages[0].get("role") == "system" else []
    rest = messages[len(system):]
    budget = allowed_chars - sum(content_chars(m) for m in system)
    sizes = [content_chars(m) for m in rest]
    drop = 0
    while drop < len(rest) - 1 and sum(sizes[drop:]) > budget:
        drop = min(drop + TRIM_BLOCK, len(rest) - 1)
    rest = rest[drop:]
    while len(rest) > 1 and rest[0].get("role") == "tool":
        rest.pop(0)
    return system + rest


# Appended once MAX_TOOL_ITERATIONS is used up, for one last round with
# tool_choice="none" - so the model gives a real answer from what it has
# instead of the request ending on a tool-call turn with no text (which a
# client like the Signal gateway sees as an empty reply and sends nothing).
# A user-role message rather than a system one: several local chat
# templates (Qwen's, for one) reject a system message anywhere but first.
#
# The tools stay *listed* in that round (just with tool_choice="none")
# rather than being dropped: tested live, Gemma 4 with no tools listed
# would sometimes still try, writing its raw tool-call syntax out as the
# reply text. With them listed, the backend still parses such an attempt
# into a real tool call, which the caller then replaces with a fallback.
TOOL_LIMIT_NUDGE = {
    "role": "user",
    "content": (
        "Tool calls are no longer available for this request. Do not call or write out any tool calls. "
        "Reply now in plain text with your best answer from the results you already have, "
        "and say what you couldn't finish."
    ),
}


# Field names a model uses for its own working, not for the answer.
_NOTES_FIELDS = {"thinking", "thought", "thoughts", "plan", "planning", "reasoning", "analysis", "notes", "reflection", "scratchpad"}
# ...and for the answer itself, when it wraps both in one JSON object.
_ANSWER_FIELDS = ("response", "answer", "reply", "final_answer", "final", "message", "output", "content", "text")
# Sent (after the notes-only reply) to ask once more for the answer.
NOTES_ONLY_NUDGE = {
    "role": "user",
    "content": "(Now write your actual reply to the message above, in plain text - not JSON, and without your notes or plan.)",
}
# If the second try is notes-only too.
NO_ANSWER_REPLY = "Sorry, I lost my train of thought there - could you ask me again?"

# A thinking model that used its whole reply budget on thinking leaves an
# empty reply (finish_reason "length"); without this the chat sees silence.
OUT_OF_ROOM_REPLY = (
    "Sorry, I ran out of room while thinking that through and never got to an answer. "
    "Try asking for less at once, or switch thinking off (/think off in Signal, or the Think button)."
)


def clean_reply(text):
    """A reply with a model's leaked working removed. Returns the cleaned
    text - or None if it was *only* working, with no answer in it.

    - <think>...</think> blocks are removed (and anything before a stray
      closing </think>), for models whose thinking the server doesn't
      separate out itself.
    - A reply that's a JSON object of working fields ({"thinking": ...,
      "plan": ...}) has its answer field used, if it has one, or else
      counts as no answer. Seen live with an "agent-style" model in a
      Signal group: it replied with just its thinking and plan as JSON -
      and, saved in history, that taught it to keep doing so.
    JSON a user actually asked for (any other fields) is left alone."""
    if not isinstance(text, str):
        return text
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        try:
            data = json.loads(candidate)
        except ValueError:
            return text
        if isinstance(data, dict) and data:
            keys = {k.lower() for k in data}
            for field in _ANSWER_FIELDS:
                value = next((v for k, v in data.items() if k.lower() == field), None)
                if isinstance(value, str) and value.strip() and keys - {field} <= _NOTES_FIELDS:
                    return value.strip()
            if keys <= _NOTES_FIELDS:
                return None
    return text
