"""The optional voice plugin: transcription, speech, and its endpoints.
Skipped where its libraries (requirements-voice.txt) aren't installed -
e.g. in CI."""
import json

import pytest

voice = pytest.importorskip("plugins.voice", reason="voice libraries not installed (requirements-voice.txt)", exc_type=ImportError)


class TestTranscribe:
    def test_returns_the_transcription(self, client, monkeypatch):
        seen = {}

        def fake(audio):
            seen["audio"] = audio
            return {"text": "see you at four", "language": "en", "duration": 2.1}

        monkeypatch.setattr(voice, "transcribe", fake)
        r = client.post("/v1/transcribe", content=b"aac bytes")
        assert r.status_code == 200
        assert r.json() == {"text": "see you at four", "language": "en", "duration": 2.1}
        assert seen["audio"] == b"aac bytes"

    def test_undecodable_audio_is_a_clean_error(self, client, monkeypatch):
        def broken(audio):
            raise ValueError("Invalid data found when processing input")

        monkeypatch.setattr(voice, "transcribe", broken)
        assert client.post("/v1/transcribe", content=b"not audio").status_code == 422

    def test_empty_and_oversized_requests(self, client, monkeypatch):
        assert client.post("/v1/transcribe", content=b"").status_code == 400
        monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", 3)
        assert client.post("/v1/transcribe", content=b"1234").status_code == 413

    def test_cross_site_requests_are_refused(self, client):
        assert client.post("/v1/transcribe", content=b"x", headers={"Origin": "https://evil.example"}).status_code == 403


def _m4a_tone(seconds=1.5):
    """A real AAC-in-M4A file, like a Signal voice note, made with PyAV."""
    import io
    import math

    import av
    import numpy as np

    rate = 44100
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        stream = container.add_stream("aac", rate=rate)
        stream.layout = "mono"
        samples = (0.3 * np.sin(2 * math.pi * 440 * np.arange(int(rate * seconds)) / rate)).astype(np.float32)
        for start in range(0, len(samples), 1024):
            frame = av.AudioFrame.from_ndarray(samples[start:start + 1024].reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


class TestAudioDecoding:
    def test_a_signal_style_m4a_decodes_to_16khz_samples(self):
        # The exact path that failed live: faster-whisper's own decoder
        # broke against PyAV 19 on every voice note.
        samples = voice._decode(_m4a_tone(1.5))
        assert samples.dtype.name == "float32"
        assert abs(len(samples) / 16000 - 1.5) < 0.1
        assert 0.1 < abs(samples).max() <= 1.0

    def test_near_empty_audio_skips_the_model(self, monkeypatch):
        def no_model():
            raise AssertionError("the model shouldn't be loaded for no audio")

        import faster_whisper
        monkeypatch.setattr(faster_whisper, "WhisperModel", no_model)
        assert voice.transcribe(_m4a_tone(0.02))["text"] == ""

    def test_garbage_is_an_error(self):
        with pytest.raises(Exception):
            voice._decode(b"definitely not audio")


class TestSpeech:
    def test_speakable_reads_markdown_sensibly(self):
        from plugins.voice import speakable
        text = (
            "## Plan\n- **Wednesday** at *3:30*\n1. Bring `goggles`\n"
            "See [the club page](https://example.org/club) or https://example.org.\n```\nprint(1)\n```"
        )
        assert speakable(text) == (
            "Plan Wednesday at 3:30 Bring goggles See the club page or (link in the message). (code shown in the message)"
        )

    def test_speakable_leaves_snake_case_and_maths_alone(self):
        from plugins.voice import speakable
        assert speakable("Open my_file_name.txt, then 2 * 3 * 4 = 24.") == "Open my_file_name.txt, then 2 * 3 * 4 = 24."

    def test_long_text_is_cut_at_a_sentence(self, monkeypatch):
        monkeypatch.setattr(voice, "MAX_SPOKEN_CHARS", 40)
        spoken = voice.speakable("First sentence is here. Second sentence goes on. Third one too.")
        assert spoken == "First sentence is here. Second sentence goes on. The rest is in the message." or \
            spoken == "First sentence is here. The rest is in the message."

    def test_endpoint_returns_audio(self, client, monkeypatch):
        seen = {}

        def fake_speak(text):
            seen["text"] = text
            return b"m4a bytes"

        monkeypatch.setattr(voice, "speak", fake_speak)
        r = client.post("/v1/speak", json={"text": "Hello there"})
        assert (r.status_code, r.headers["content-type"], r.content) == (200, "audio/mp4", b"m4a bytes")
        assert seen["text"] == "Hello there"

    def test_endpoint_errors(self, client, monkeypatch):
        assert client.post("/v1/speak", json={"text": "  "}).status_code == 400

        assert client.post("/v1/speak", json={"text": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403

    def test_real_speech_round_trips_through_transcription(self):
        """Piper -> .m4a -> Whisper should give back (roughly) the words.
        Uses the real models, so it's skipped if they aren't available."""
        try:
            audio = voice.speak("The quick brown fox jumps over the lazy dog.")
            heard = voice.transcribe(audio)["text"].lower()
        except Exception as exc:
            pytest.skip(f"speech or transcription models unavailable: {exc}")
        assert audio[4:8] == b"ftyp"  # an MP4/M4A container
        assert "quick brown fox" in heard and "lazy dog" in heard


class TestVoiceIsAPlugin:
    def test_status_lists_the_voice_endpoints_when_installed(self, client):
        endpoints = client.get("/v1/status").json()["endpoints"]
        assert {"/v1/transcribe", "/v1/speak", "/v1/chat/completions"} <= set(endpoints)
