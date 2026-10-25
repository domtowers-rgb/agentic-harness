"""Optional ask_claude plugin: when the user says "ask Claude...", the model
can put a question to Claude (Anthropic's model) through the Claude Code
command line on this machine, and relay the answer.

Uses the `claude` command signed in to your Claude account (`claude auth
login`), so a question counts toward your plan's usage like one asked in
the app - no API key, no per-request charge. Run as a plain question and
answer: Claude's own tools are all off (no files, no shell, no MCP
servers, no saved session), in an empty folder, with a minimal environment
(none of the harness's settings or keys), and the question goes over
stdin, not the command line, so other processes can't see it.

What leaves the machine is only the question the model writes - not the
chat's history or notes - and only when the user asks for Claude: the tool
says so, and it's owner-only (see agentic_harness/plugins.py).

Without a `claude` command (AGENTIC_CLAUDE_PATH, else ~/.local/bin/claude
or one on PATH) this plugin is skipped. To remove it, delete this file.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CLAUDE = os.path.expanduser(
    os.environ.get("AGENTIC_CLAUDE_PATH") or shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
)
if not os.path.isfile(CLAUDE):
    raise ImportError("no Claude Code command line (claude) found - ask_claude is unavailable")

MODEL = os.environ.get("AGENTIC_CLAUDE_MODEL", "")  # "" = Claude Code's default; e.g. "opus"
TIMEOUT_SECONDS = int(os.environ.get("AGENTIC_CLAUDE_TIMEOUT", "180"))
MAX_QUESTION_CHARS = 20_000
CONSULTED = (
    "You're being consulted by another AI assistant on its user's behalf; your answer will be passed on to them. "
    "You can't ask follow-up questions, so state any assumption you make. Answer clearly, without padding."
)


def _command() -> list:
    cmd = [
        CLAUDE, "-p", "--output-format", "json",
        "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
        "--append-system-prompt", CONSULTED,
    ]
    return cmd + ["--model", MODEL] if MODEL else cmd


def ask_claude(question: str):
    if not isinstance(question, str) or not question.strip():
        return {"error": "question is required"}
    if len(question) > MAX_QUESTION_CHARS:
        return {"error": f"question is too long (max {MAX_QUESTION_CHARS} characters)"}
    env = {"HOME": str(Path.home()), "PATH": f"/usr/bin:/bin:{os.path.dirname(CLAUDE)}", "LANG": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="ask-claude-") as empty:
        try:
            done = subprocess.run(
                _command(), input=question, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=empty, env=env,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"Claude didn't answer within {TIMEOUT_SECONDS} seconds"}
    try:
        data = json.loads(done.stdout)
    except ValueError:
        detail = (done.stderr or done.stdout or "").strip()[-500:]
        return {"error": f"couldn't get an answer from Claude: {detail or 'no output'}"}
    if data.get("is_error"):
        # e.g. signed out, or the plan's usage limit reached
        return {"error": f"Claude couldn't answer: {data.get('result') or 'unknown error'}"}
    models = ", ".join(data.get("modelUsage") or {}) or "Claude"
    return {"answer": data.get("result") or "", "from": models, "note": "Tell the user this answer came from Claude."}


def register(registry):
    registry.register("ask_claude", ask_claude, {
        "name": "ask_claude",
        "description": (
            "Ask Claude, a more capable AI, a question - only when the user asks you to ask Claude. It sees nothing "
            "else from this chat, so write a complete question with just the details it needs. Say its answer is "
            "from Claude."
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    }, owner_only=True)
