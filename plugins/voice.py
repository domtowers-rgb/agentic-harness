"""Optional voice plugin: speech-to-text and text-to-speech endpoints.

  POST /v1/transcribe  audio file as the raw body -> {"text", "language", "duration"}
  POST /v1/speak       {"text": ...}              -> the text spoken, as .m4a audio

Both run on this machine's CPU: transcription with faster-whisper, speech
with Piper. agentic-gateway uses them to understand Signal voice messages
and answer them with one.

Optional: its libraries are in requirements-voice.txt, not
requirements.txt. Without them installed this plugin is skipped at
startup (logged as "[plugins] failed to load 'plugins.voice'"), the two
endpoints don't exist, and everything else works as before.

Unlike the other plugins it adds HTTP endpoints rather than model tools,
via the loader's register_routes(app) hook.
"""
import asyncio
import importlib.util
import io
import os
import re
import threading
from pathlib import Path

# Fail at import - so the loader skips this plugin - if a library is
# missing, without importing the heavy ones yet: they load on first use.
_MISSING = [dep for dep in ("numpy", "av", "faster_whisper", "piper") if importlib.util.find_spec(dep) is None]
if _MISSING:
    raise ImportError(f"voice libraries not installed ({', '.join(_MISSING)}) - pip install -r requirements-voice.txt")

from fastapi import HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

MAX_AUDIO_BYTES = int(os.environ.get("AGENTIC_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
# Speech-to-text model size: tiny, base, small, medium, large-v3 (or *.en).
WHISPER_MODEL = os.environ.get("AGENTIC_WHISPER_MODEL", "small")
SAMPLE_RATE = 16000  # what Whisper expects
# Text-to-speech: any Piper voice name; downloaded on first use (~60 MB).
TTS_VOICE = os.environ.get("AGENTIC_TTS_VOICE", "en_GB-cori-medium")
TTS_DIR = Path(os.environ.get("AGENTIC_TTS_DIR") or Path.home() / ".cache" / "piper-voices")
# Longer replies are cut short in speech (the full text is still sent
# alongside), at a sentence end where possible.
MAX_SPOKEN_CHARS = int(os.environ.get("AGENTIC_TTS_MAX_CHARS", "1500"))

_whisper = None
_piper = None
# One of each at a time: each already uses every CPU core, so running two
# at once would only make both slower (and double the memory).
_whisper_lock = threading.Lock()
_piper_lock = threading.Lock()


# --- speech to text ---------------------------------------------------------

def _decode(audio: bytes):
    """Any common audio format -> 16 kHz mono float32 samples, via PyAV.

    Done here rather than by passing the file to faster-whisper, whose own
    decoder (faster-whisper 1.2.1) calls av.open() with an option PyAV 19
    removed - so with current versions of the two, every voice message
    failed with "unexpected keyword argument 'metadata_errors'"."""
    import av
    import numpy as np

    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    with av.open(io.BytesIO(audio)) as container:
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray() for f in resampler.resample(frame)]
    chunks += [f.to_ndarray() for f in resampler.resample(None)]  # flush what's buffered
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=1).reshape(-1).astype(np.float32) / 32768.0


def transcribe(audio: bytes) -> dict:
    """Transcribe audio in any common format (Signal voice notes are AAC
    in an .m4a container). Blocking - call it in a thread. The model loads
    on first use (and the very first time, downloads from Hugging Face into
    ~/.cache/huggingface - ~480 MB for "small")."""
    global _whisper
    samples = _decode(audio)
    duration = round(len(samples) / SAMPLE_RATE, 1)
    if duration < 0.1:
        return {"text": "", "language": None, "duration": duration}
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        # vad_filter skips silence, which also stops Whisper inventing text
        # for quiet stretches - a known failure mode on near-silent audio.
        segments, info = _whisper.transcribe(samples, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments).strip()
    return {"text": text, "language": info.language, "duration": duration}


# --- text to speech ---------------------------------------------------------

def speakable(text: str) -> str:
    """Reply text as it should be read aloud: markdown markers dropped,
    code blocks and links summarised rather than spelled out, long text
    cut at a sentence end."""
    text = re.sub(r"```.*?```", " (code shown in the message) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [text](url) -> text
    # (Trailing punctuation stays - it's the end of the sentence, not the link.)
    text = re.sub(r"https?://\S+?(?=[.,!?;:)]*(?:\s|$))", "(link in the message)", text)
    # Emphasis markers - not touching snake_case_names or "2 * 3 * 4".
    text = re.sub(r"(?<!\w)(\*\*|__|~~|\*|_)(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)  # headings
    text = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", text, flags=re.M)  # list markers
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = (cut[:end + 1] if end > MAX_SPOKEN_CHARS // 2 else cut) + " The rest is in the message."
    return text


def _encode_m4a(samples, rate: int) -> bytes:
    import av
    import numpy as np

    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        stream = container.add_stream("aac", rate=rate)
        stream.layout = "mono"
        for start in range(0, len(samples), 1024):
            frame = av.AudioFrame.from_ndarray(
                samples[start:start + 1024].astype(np.float32).reshape(1, -1), format="flt", layout="mono",
            )
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def speak(text: str) -> bytes:
    """Reply text -> spoken .m4a audio (the format of a Signal voice note).
    Blocking - call it in a thread. Raises ValueError if there's nothing
    to say."""
    global _piper
    import numpy as np

    spoken = speakable(text)
    if not spoken:
        raise ValueError("nothing to say")
    with _piper_lock:
        if _piper is None:
            from piper import PiperVoice
            from piper.download_voices import download_voice
            model_path = TTS_DIR / f"{TTS_VOICE}.onnx"
            if not model_path.exists():
                TTS_DIR.mkdir(parents=True, exist_ok=True)
                download_voice(TTS_VOICE, TTS_DIR)
            _piper = PiperVoice.load(model_path)
        chunks = list(_piper.synthesize(spoken))
    if not chunks:
        raise ValueError("nothing to say")
    samples = np.concatenate([chunk.audio_float_array for chunk in chunks])
    return _encode_m4a(samples, chunks[0].sample_rate)


# --- endpoints --------------------------------------------------------------

async def transcribe_audio(request: Request):
    """Transcribe a voice message sent as the raw request body. (Not
    OpenAI's /v1/audio/transcriptions: that's a multipart form, which would
    need another dependency.)"""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="no audio in the request body")
    if len(body) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large (max {MAX_AUDIO_BYTES} bytes)")
    try:
        return JSONResponse(content=await asyncio.to_thread(transcribe, body))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"couldn't transcribe that audio: {exc}")


async def speak_text(request: Request):
    """{"text": ...} -> the text spoken aloud, as audio/mp4 (.m4a)."""
    body = await request.json()
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        audio = await asyncio.to_thread(speak, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"speech failed: {exc}")
    return Response(content=audio, media_type="audio/mp4")


def register_routes(app):
    app.add_api_route("/v1/transcribe", transcribe_audio, methods=["POST"])
    app.add_api_route("/v1/speak", speak_text, methods=["POST"])
