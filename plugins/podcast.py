"""Optional podcast plugin: the model writes a two-host conversation, this
voices each host with a different Piper voice and sends it as one audio
file (.m4a) - in Signal, a playable audio message; in the web UI, a
download link.

Builds on the voice plugin's speech engine, so it needs the same optional
libraries (requirements-voice.txt): without them this plugin is skipped.
To remove the feature entirely, delete this file.
"""
import os

from plugins.file_ops import _resolve_safe, safe_output_name, sandbox_dir
from plugins.voice import _encode_m4a, speakable, synthesize
from plugins.word_document import DELIVERY_NOTE

# Each host's Piper voice and the name the script uses for them.
VOICES = [v.strip() for v in os.environ.get("AGENTIC_PODCAST_VOICES", "en_GB-alba-medium,en_GB-alan-medium").split(",")]
HOSTS = [h.strip() for h in os.environ.get("AGENTIC_PODCAST_HOSTS", "Alba,Alan").split(",")]
MAX_LINES = 80
MAX_SCRIPT_CHARS = 15000
PAUSE_SECONDS = 0.35  # between lines, so it sounds like turn-taking


def _resample(samples, rate: int, target: int):
    """Voices can differ in sample rate; bring a line to the first one's."""
    import numpy as np

    if rate == target:
        return samples
    positions = np.linspace(0, len(samples) - 1, int(len(samples) * target / rate))
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def create_podcast(title: str, script: list, filename: str = None):
    """Voice a two-host script ([{"host": 1|2, "text": ...}, ...]) and save
    it as an .m4a in the chat's sandbox, sent to the user like any created
    file."""
    import numpy as np

    if not title or not isinstance(title, str):
        return {"error": "title is required"}
    if not isinstance(script, list) or not script:
        return {"error": "script must be a list of {host, text} lines"}
    if len(script) > MAX_LINES:
        return {"error": f"script too long (max {MAX_LINES} lines)"}
    if sum(len(str(line.get("text", ""))) for line in script if isinstance(line, dict)) > MAX_SCRIPT_CHARS:
        return {"error": f"script too long (max {MAX_SCRIPT_CHARS} characters)"}

    pieces, rate = [], None
    for i, line in enumerate(script):
        if not isinstance(line, dict) or line.get("host") not in (1, 2) or not str(line.get("text", "")).strip():
            return {"error": f"line {i} must be {{host: 1 or 2, text: ...}}"}
        try:
            samples, line_rate = synthesize(speakable(str(line["text"])), VOICES[line["host"] - 1])
        except ValueError:
            continue  # a line that's only markup - nothing to say
        except Exception as exc:
            return {"error": f"couldn't voice line {i}: {exc}"}
        rate = rate or line_rate
        pieces += [_resample(samples, line_rate, rate), np.zeros(int(rate * PAUSE_SECONDS), dtype=np.float32)]
    if not pieces:
        return {"error": "the script had nothing to say"}

    samples = np.concatenate(pieces)
    try:
        target = _resolve_safe(safe_output_name(filename or title, ".m4a", "podcast"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_encode_m4a(samples, rate))
    except Exception as exc:
        return {"error": f"couldn't save the podcast: {exc}"}

    path = str(target.relative_to(sandbox_dir()))
    return {
        "status": "written", "path": path, "attachment": path,
        "duration_seconds": round(len(samples) / rate), "lines": len(script), "note": DELIVERY_NOTE,
    }


def register(registry):
    registry.register("create_podcast", create_podcast, {
        "name": "create_podcast",
        "description": (
            f"Make a short audio podcast and send it to the user: a lively, natural conversation between two hosts, "
            f"{HOSTS[0]} (host 1) and {HOSTS[1]} (host 2), taking turns. Aim for 2-4 minutes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "script": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"host": {"type": "integer", "enum": [1, 2]}, "text": {"type": "string"}},
                        "required": ["host", "text"],
                    },
                },
            },
            "required": ["title", "script"],
        },
    })
