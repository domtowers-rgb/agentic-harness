"""The optional podcast plugin. Skipped where the voice libraries it
builds on (requirements-voice.txt) aren't installed - e.g. in CI."""
import pytest

np = pytest.importorskip("numpy", reason="voice libraries not installed (requirements-voice.txt)")
podcast = pytest.importorskip("plugins.podcast", reason="voice libraries not installed (requirements-voice.txt)", exc_type=ImportError)

import plugins.file_ops as file_ops  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "SANDBOX_DIR", tmp_path)
    return tmp_path


class TestPodcast:
    @pytest.fixture
    def voices(self, monkeypatch):
        """Stand-in voices: 0.5 s of a different tone per host, at
        different sample rates, recording what each was asked to say."""
        said = []

        def fake_synthesize(text, voice=None):
            said.append((voice, text))
            rate = 22050 if voice == podcast.VOICES[0] else 16000
            return (0.2 * np.sin(np.arange(rate // 2) * (0.05 if rate == 22050 else 0.09))).astype(np.float32), rate

        monkeypatch.setattr(podcast, "synthesize", fake_synthesize)
        return said

    SCRIPT = [
        {"host": 1, "text": "Welcome to **History Bites**!"},
        {"host": 2, "text": "Today: the Battle of Hastings, 1066."},
        {"host": 1, "text": "Let's dive in."},
    ]

    def test_voices_each_host_and_saves_one_audio_file(self, voices, sandbox):
        result = podcast.create_podcast("History Bites: 1066", self.SCRIPT)
        assert result["path"] == result["attachment"] == "History_Bites_1066.m4a"
        assert result["lines"] == 3 and "sent to the user automatically" in result["note"]
        # Host 1 and 2 got their own voices; markdown was read sensibly.
        assert voices == [
            (podcast.VOICES[0], "Welcome to History Bites!"),
            (podcast.VOICES[1], "Today: the Battle of Hastings, 1066."),
            (podcast.VOICES[0], "Let's dive in."),
        ]
        data = (sandbox / "History_Bites_1066.m4a").read_bytes()
        assert data[4:8] == b"ftyp"
        # 3 lines x 0.5 s + 3 pauses, all at one sample rate.
        assert result["duration_seconds"] == round(3 * 0.5 + 3 * podcast.PAUSE_SECONDS)

    def test_bad_scripts_are_explained(self, voices):
        assert "title is required" in podcast.create_podcast("", self.SCRIPT)["error"]
        assert "list of" in podcast.create_podcast("T", [])["error"]
        assert "line 1" in podcast.create_podcast("T", [{"host": 1, "text": "hi"}, {"host": 3, "text": "x"}])["error"]
        assert "too long" in podcast.create_podcast("T", [{"host": 1, "text": "x"}] * (podcast.MAX_LINES + 1))["error"]

    def test_is_registered_as_a_tool(self, client):
        names = {p["name"] for p in client.get("/v1/plugins").json()["plugins"]}
        assert "create_podcast" in names
