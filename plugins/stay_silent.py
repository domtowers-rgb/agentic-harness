"""Optional stay_silent plugin: lets the model send no reply at all.

Every turn otherwise has to end in some text - so "I'll stay quiet now" was
the only way to be quiet, and in a group with another AI that's just one
more message for it to answer. Calling this ends the turn there, with
nothing sent (any text written alongside the call is dropped too). Offered
only to chats (needs_chat), not the web UI, where a reply is always wanted.
To remove it, delete this file.
"""


def stay_silent():
    return {"end_turn": True}


def register(registry):
    registry.register("stay_silent", stay_silent, {
        "name": "stay_silent",
        "description": (
            "Send no reply at all. Use in a group when nothing needs saying - e.g. another AI keeps chatting with "
            "you in circles, or a message isn't for you. Don't say you're staying quiet; just call this."
        ),
        "parameters": {"type": "object", "properties": {}},
    }, needs_chat=True)
