"""Speech-to-text for voice messages, with faster-whisper (Whisper on
CTranslate2), on this machine's CPU.

The model is loaded the first time it's needed, not at startup - the first
voice message after a restart waits a few seconds for that (and, the very
first time, for the model to download from Hugging Face into
~/.cache/huggingface). faster-whisper is optional: without it installed,
transcribe() raises TranscriptionUnavailable and callers say so.
"""
import io
import os
import threading

# Size/accuracy/speed trade-off: tiny, base, small, medium, large-v3 (or
# *.en English-only variants). "small" is a good default for a CPU.
WHISPER_MODEL = os.environ.get("AGENTIC_WHISPER_MODEL", "small")

_model = None
# One transcription at a time: each already uses every CPU core, so
# running two at once would only make both slower (and double the memory).
_lock = threading.Lock()


class TranscriptionUnavailable(Exception):
    pass


def _load():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionUnavailable("faster-whisper isn't installed (pip install faster-whisper)") from exc
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


SAMPLE_RATE = 16000  # what Whisper expects


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
    in an .m4a container). Blocking - call it in a thread. Returns
    {"text", "language", "duration"}."""
    samples = _decode(audio)
    duration = round(len(samples) / SAMPLE_RATE, 1)
    if duration < 0.1:
        return {"text": "", "language": None, "duration": duration}
    with _lock:
        model = _load()
        # vad_filter skips silence, which also stops Whisper inventing text
        # for quiet stretches - a known failure mode on near-silent audio.
        segments, info = model.transcribe(samples, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments).strip()
    return {"text": text, "language": info.language, "duration": duration}
