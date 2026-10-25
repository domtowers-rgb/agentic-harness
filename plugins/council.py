"""Optional council plugin: put one question to several models at once.

The ask_council tool sends the question to every chat model currently
loaded on the backend (or AGENTIC_COUNCIL_MODELS), in parallel, and
returns each one's answer. The chat's own model then acts as judge in its
reply - comparing them, naming who said what, and giving a verdict. On
LM Studio with LM Link, the members can be on different machines,
answering at the same time.

Triggered by the model when a message starts with /council (or asks for
the council). To remove the feature entirely, delete this file.
"""
import os
from concurrent.futures import ThreadPoolExecutor

# Optional fixed membership (comma-separated model ids); otherwise every
# chat model currently loaded is asked - unloaded ones aren't, as asking
# would make the backend load them.
FIXED_MEMBERS = [m.strip() for m in os.environ.get("AGENTIC_COUNCIL_MODELS", "").split(",") if m.strip()]
MAX_MEMBERS = 5
MEMBER_MAX_TOKENS = 1500  # room for "thinking" models to finish
MEMBER_PROMPT = (
    "You're one member of a panel of AI models asked the same question independently. Give your own honest answer "
    "and your main reason, in under 120 words. Take a clear position if the question asks for one."
)


def _backend():
    # Looked up at call time, not import time: the backend is created after
    # plugins load, and the web UI's Connect can swap it while running.
    from agentic_harness import main as harness

    return harness.model_impl


def _members(backend) -> list:
    if FIXED_MEMBERS:
        return FIXED_MEMBERS[:MAX_MEMBERS]
    details = backend.model_details() if hasattr(backend, "model_details") else None
    loaded = [m for m, d in (details or {}).items() if d.get("loaded") and d.get("type") in ("llm", "vlm")]
    return sorted(loaded)[:MAX_MEMBERS]


def _ask(backend, model_id: str, question: str) -> dict:
    try:
        response = backend.chat(
            messages=[{"role": "system", "content": MEMBER_PROMPT}, {"role": "user", "content": question}],
            model=model_id, temperature=0.7, max_tokens=MEMBER_MAX_TOKENS,
        )
        answer = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"model": model_id, "answer": answer.strip() or "(no answer)"}
    except Exception as exc:
        return {"model": model_id, "answer": f"(couldn't answer: {exc})"}


def ask_council(question: str):
    if not question or not str(question).strip():
        return {"error": "question is required"}
    backend = _backend()
    members = _members(backend)
    if len(members) < 2:
        return {"error": (
            f"The council needs at least two chat models loaded, and only {len(members)} is"
            f"{': ' + members[0] if members else ''}. Load another on the model server (e.g. LM Studio on another linked machine; a llama.cpp server runs just one), "
            "or set AGENTIC_COUNCIL_MODELS."
        )}
    with ThreadPoolExecutor(max_workers=len(members)) as pool:
        answers = list(pool.map(lambda m: _ask(backend, m, str(question)), members))
    return {
        "council": answers,
        "instructions": (
            "You are the judge. In your reply: briefly give each model's position, naming it; say where they agree "
            "and differ; then give your verdict and why."
        ),
    }


def register(registry):
    registry.register("ask_council", ask_council, {
        "name": "ask_council",
        "description": (
            "Ask several AI models the same question at once and get each one's answer, to judge between them. "
            "Use when the message starts with /council or asks for the council."
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    })
