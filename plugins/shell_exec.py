import os
import shlex
import subprocess

from plugins.file_ops import sandbox_dir

# Runs arbitrary commands the model chooses to run - unlike the other
# plugins, this has effectively unbounded blast radius (an absolute-path
# command isn't contained by the sandboxed cwd below). Off by default;
# set AGENTIC_ENABLE_SHELL=1 to opt in.
ENABLED = os.environ.get("AGENTIC_ENABLE_SHELL") == "1"

TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 20_000


def run_command(command: str):
    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return {"error": f"could not parse command: {exc}"}
    if not args:
        return {"error": "empty command"}

    cwd = sandbox_dir()
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError:
        return {"error": f"command not found: {args[0]}"}
    except subprocess.TimeoutExpired:
        return {"error": f"command timed out after {TIMEOUT_SECONDS}s"}
    except Exception as exc:
        return {"error": f"failed to run command: {exc}"}

    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:MAX_OUTPUT_CHARS],
        "stderr": proc.stderr[:MAX_OUTPUT_CHARS],
    }


def register(registry):
    if not ENABLED:
        return
    registry.register("run_command", run_command, {
        "name": "run_command",
        "description": (
            "Run a shell command in the working directory. "
            f"Not run through a shell interpreter. Times out after {TIMEOUT_SECONDS}s."
        ),
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The command to run, e.g. 'ls -la'"}},
            "required": ["command"],
        },
    })
