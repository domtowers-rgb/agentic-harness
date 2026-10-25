"""Optional run_python plugin: lets the model write and run a short Python
script - for sums it would otherwise guess at, spreadsheets, charts and
file work - inside a bubblewrap sandbox.

What a script can and can't do:
  - no network at all;
  - the system's programs read-only, and its own Python environment
    (sandbox-venv/: pandas, numpy, matplotlib, openpyxl) read-only;
  - read and write only the current chat's own folder (its uploads, the
    files made for it) - not the home folder, the harness's .env, other
    chats or anything else on the machine;
  - TIMEOUT_SECONDS of wall time, MEMORY_MB of memory, files up to 50 MB.
Files a script creates or changes in the chat's folder are sent to the
user (like create_file's documents).

Offered only to the owner (owner_only - see agentic_harness/plugins.py):
agentic-gateway marks requests from GATEWAY_OWNERS, and the web UI, being
on this machine, marks all of its own.

Needs bubblewrap (`sudo apt install bubblewrap`); without it this plugin is
skipped. The packages: `python3 -m venv sandbox-venv && sandbox-venv/bin/pip
install -r requirements-sandbox.txt` - without them, scripts get plain
Python only. To remove the feature entirely, delete this file.
"""
import os
import shutil
import subprocess
from pathlib import Path

import plugins.file_ops as file_ops

BWRAP = shutil.which("bwrap")
if not BWRAP:
    raise ImportError("bubblewrap isn't installed (sudo apt install bubblewrap) - run_python is unavailable")

PROJECT_DIR = Path(__file__).resolve().parent.parent
VENV = Path(os.environ.get("AGENTIC_SANDBOX_VENV") or PROJECT_DIR / "sandbox-venv").expanduser()
TIMEOUT_SECONDS = int(os.environ.get("AGENTIC_SANDBOX_TIMEOUT", "30"))
MEMORY_MB = int(os.environ.get("AGENTIC_SANDBOX_MEMORY_MB", "1024"))
MAX_CODE_CHARS = 50_000
MAX_OUTPUT_CHARS = 6_000
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES_SENT = 5
# Matplotlib's font cache, built once outside the sandbox (it's slow to
# build, and the sandbox can't keep anything between runs).
MPL_CACHE = VENV / "mpl-cache"


def _has_venv() -> bool:
    return (VENV / "bin" / "python").exists()


def _font_cache() -> list:
    if not _has_venv():
        return []
    if not MPL_CACHE.is_dir():
        try:
            subprocess.run(
                [str(VENV / "bin" / "python"), "-c", "import matplotlib.font_manager"],
                env={**os.environ, "MPLCONFIGDIR": str(MPL_CACHE)}, capture_output=True, timeout=180,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(MPL_CACHE.glob("*.json")) if MPL_CACHE.is_dir() else []


def _command(work: Path) -> list:
    limits = [
        "prlimit", f"--as={MEMORY_MB * 1024 * 1024}", f"--fsize={MAX_FILE_BYTES}", f"--cpu={TIMEOUT_SECONDS}",
    ] if shutil.which("prlimit") else []
    cmd = [
        *limits, BWRAP, "--unshare-all", "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
        "--ro-bind-try", "/etc/fonts", "/etc/fonts", "--ro-bind-try", "/etc/localtime", "/etc/localtime",
        "--bind", str(work), "/work", "--chdir", "/work",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--clearenv",
        "--setenv", "HOME", "/tmp", "--setenv", "XDG_CACHE_HOME", "/tmp", "--setenv", "PATH", "/usr/bin",
        "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "OPENBLAS_NUM_THREADS", "1", "--setenv", "MPLBACKEND", "Agg", "--setenv", "MPLCONFIGDIR", "/tmp/mpl",
    ]
    if _has_venv():
        cmd += ["--ro-bind", str(VENV), "/venv"]
        for cache in _font_cache():
            cmd += ["--ro-bind", str(cache), f"/tmp/mpl/{cache.name}"]
        python = "/venv/bin/python"
    else:
        python = "/usr/bin/python3"
    return [*cmd, python, "-"]


def _snapshot(work: Path) -> dict:
    files = {}
    for path in work.rglob("*"):
        rel = path.relative_to(work)
        if path.is_file() and not any(part.startswith((".", "__pycache__")) for part in rel.parts):
            stat = path.stat()
            files[rel.as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return files


def _clip(text: str) -> str:
    text = text or ""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS // 2] + "\n...(output shortened)...\n" + text[-MAX_OUTPUT_CHARS // 2:]


def run_python(code: str):
    if not isinstance(code, str) or not code.strip():
        return {"error": "code is required"}
    if len(code) > MAX_CODE_CHARS:
        return {"error": f"code is too long (max {MAX_CODE_CHARS} characters)"}
    work = file_ops.sandbox_dir()
    work.mkdir(parents=True, exist_ok=True)
    before = _snapshot(work)
    try:
        done = subprocess.run(_command(work), input=code, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        result = {"exit_code": done.returncode, "stdout": _clip(done.stdout), "stderr": _clip(done.stderr)}
    except subprocess.TimeoutExpired as exc:
        result = {
            "error": f"stopped after {TIMEOUT_SECONDS} seconds - make it do less, or work in smaller steps",
            "stdout": _clip(exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout),
        }
    if result.get("exit_code") not in (0, None) and not result["stderr"].strip():
        result["stderr"] = "(the script was stopped - most likely it used too much memory)"
    after = _snapshot(work)
    made = [p for p, info in after.items() if before.get(p) != info]
    if made:
        result["files"] = made
        sendable = [p for p in made if after[p][1] <= MAX_FILE_BYTES][:MAX_FILES_SENT]
        if sendable:
            result["attachments"] = sendable
            result["note"] = "These files will be sent to the user with your reply."
    return result


def register(registry):
    registry.register("run_python", run_python, {
        "name": "run_python",
        "description": (
            "Run a Python script in a sandbox and get its output - for calculations, data, charts and file work. "
            f"No internet; {TIMEOUT_SECONDS} s limit. This chat's files (e.g. uploads/) are in its working folder, and "
            "files it saves there are sent to the user. pandas, numpy, matplotlib and openpyxl are available. "
            "print() what you need to see."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    }, owner_only=True)
