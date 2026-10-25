"""Running the model's tool calls through the plugin registry - each one
logged to the local audit trail - and noting the files they make for the
user."""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import plugins.file_ops as file_ops

from . import plugins

# Local audit trail of every tool call: what was invoked, with what
# arguments, and what came back. Not sent anywhere - stays on this machine,
# same trust model as workspace/. Useful for both debugging ("why did it do
# that?") and security review, since tool calls execute automatically with
# no approval step.
AUDIT_LOG_FILE = os.environ.get("AGENTIC_AUDIT_LOG", "audit.log")
AUDIT_LOG_MAX_CHARS = 500


def truncate_for_audit(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > AUDIT_LOG_MAX_CHARS:
        return text[:AUDIT_LOG_MAX_CHARS] + "...(truncated)"
    return text


def audit_log(tool_name: str, args: Any, result: Any) -> None:
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": truncate_for_audit(args),
            "result": truncate_for_audit(result),
        }
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        # Never let audit logging itself break a tool call.
        print(f"[audit] failed to write audit log entry: {exc}")


def call_tool(fname: str, args_text: Any):
    """Run one tool call, returning (args, result). Any failure - an
    unknown tool, unparseable arguments, or the plugin itself raising -
    becomes an {"error": ...} result rather than an exception, so it goes
    back to the model as that call's tool result (which it can recover
    from: retry with fixed arguments, try something else, or explain)
    instead of aborting the whole request."""
    try:
        args = json.loads(args_text) if isinstance(args_text, str) else args_text
    except json.JSONDecodeError as exc:
        return args_text, {"error": f"arguments were not valid JSON: {exc}"}

    plugin = plugins.registry.get(fname)
    if not plugin:
        return args, {"error": f"unknown function: {fname}"}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return args, {"error": "arguments must be a JSON object of named parameters"}

    # Called exactly once. (It used to be retried with the arguments as a
    # single dict on any TypeError - but a TypeError can come from inside
    # the plugin after it has already written a file or saved a memory,
    # and the retry then did it twice.) A wrong or missing argument name
    # comes back to the model as a TypeError message it can act on.
    try:
        return args, plugin["callable"](**args)
    except Exception as exc:
        return args, {"error": f"{fname} failed: {type(exc).__name__}: {exc}"}


class ToolResults(list):
    """What run_tool_calls returns: the (key, path) attachments - and
    end_turn, set when a tool asked for the turn to end there, with nothing
    sent (stay_silent does) or with just an emoji reaction (react sets
    reaction)."""
    end_turn = False
    reaction = None


def run_tool_calls(
    messages: List[Dict[str, Any]], tool_calls: List[Dict[str, Any]], assistant_content=None, sandbox=None, chat=None,
) -> List[tuple]:
    """Execute tool_calls via the plugin registry, appending the assistant and
    tool-result messages to `messages` in place. Blocking (plugins are plain
    sync functions, some doing network I/O) - callers run it in a thread.
    File plugins work in `sandbox` (the request's chat folder; the root
    sandbox if None) - see plugins/file_ops.py - for `chat` (the request's
    chat id, if any).

    Returns (key, path) for each file the tools created for the user: a
    plugin marks one by including "attachment": <sandbox-relative path> in
    its result (the document plugins do), or several with "attachments":
    [paths] (run_python does). Callers collect these with
    collect_attachments and pass them on - see files.describe_attachments."""
    messages.append({"role": "assistant", "content": assistant_content, "tool_calls": tool_calls})

    attachments = ToolResults()
    with file_ops.using_sandbox(sandbox or file_ops.SANDBOX_DIR, chat=chat):
        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            fname = fn.get("name")
            args, result = call_tool(fname, fn.get("arguments") or "{}")
            audit_log(fname, args, result)
            messages.append({"role": "tool", "tool_call_id": tool_call.get("id"), "content": json.dumps(result, default=str)})
            if isinstance(result, dict) and isinstance(result.get("attachment"), str):
                path = result["attachment"]
                # Same tool + same title + same format = the same document,
                # remade: seen live, a model built a presentation, then
                # rebuilt it under a new filename in the same request, and
                # both got sent. Different titles (or a docx and a pdf of
                # the same one) are different documents, and all kept.
                title = args.get("title") if isinstance(args, dict) else None
                key = (fname, title, args.get("format")) if title else (fname, path)
                attachments.append((key, path))
            if isinstance(result, dict) and result.get("end_turn") is True:
                attachments.end_turn = True
                if isinstance(result.get("reaction"), str):
                    attachments.reaction = result["reaction"]
            if isinstance(result, dict) and isinstance(result.get("attachments"), list):
                # Several files at once (run_python's): keyed by path, so a
                # file rewritten by a later run is sent once, as it ends up.
                attachments += [((fname, p), p) for p in result["attachments"] if isinstance(p, str)]
    return attachments


def collect_attachments(collected: Dict[tuple, str], new: List[tuple]) -> None:
    """Add run_tool_calls' results to `collected`, a later version of a
    document replacing the earlier one (moving to the end, so files keep
    the order they were finished in)."""
    for key, path in new:
        collected.pop(key, None)
        collected[key] = path
