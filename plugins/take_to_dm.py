"""Optional take_to_dm plugin: lets the model move a conversation from a
group into a private chat with whoever asked - "can we take this to DMs?".

Calling it doesn't end the turn: the model still replies in the group
(e.g. "sure, messaging you"). The response carries "dm": the opening
message, for the client to send privately to the person who spoke last -
agentic-gateway does, and records it in their private chat's history so
the conversation continues there. The client decides whether to allow it
(never to another AI). Offered only in group chats (needs_group).
To remove it, delete this file.
"""
MAX_CHARS = 1200


def take_to_dm(message: str):
    if not isinstance(message, str) or not message.strip():
        return {"error": "message is required"}
    # Not "sent" yet: the client sends it after the turn, and may decline
    # (e.g. to another AI) - so the model mustn't claim more than this.
    return {"dm": message.strip()[:MAX_CHARS], "note": "It will be sent to them privately once you've replied. Briefly tell the group."}


def register(registry):
    registry.register("take_to_dm", take_to_dm, {
        "name": "take_to_dm",
        "description": (
            "Move this conversation to a private message with the person who just asked - only when they ask to "
            "take it to DMs or want to discuss something privately. `message` is what you send them there: "
            "say what the topic is and carry on from where the group left off. Then tell the group briefly."
        ),
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }, needs_group=True)
