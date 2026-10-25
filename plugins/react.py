"""Optional react plugin: lets the model answer a message with an emoji
reaction instead of a reply - a 👍 on "thanks!", as a person would.

Like stay_silent, calling it ends the turn with no text; the response
carries "reaction": the emoji, for the client to put on the message
(agentic-gateway does, on Signal). The client decides whether to allow it:
agentic-gateway only does for short messages that aren't questions, and
never twice in a row - so it can't stand in for an actual answer. Offered
only to chats (needs_chat). To remove it, delete this file.
"""
EMOJI = ["👍", "❤️", "😂", "😮", "😢", "🙏", "🎉"]


def react(emoji: str):
    if emoji not in EMOJI:
        return {"error": f"emoji must be one of {' '.join(EMOJI)}"}
    return {"end_turn": True, "reaction": emoji}


def register(registry):
    registry.register("react", react, {
        "name": "react",
        "description": (
            "React to the message with an emoji instead of replying - only for a short message that needs no answer, "
            "like thanks, ok or good news. Never for a question or anything needing words."
        ),
        "parameters": {
            "type": "object",
            "properties": {"emoji": {"type": "string", "enum": EMOJI}},
            "required": ["emoji"],
        },
    }, needs_chat=True)
