import asyncio

import pytest

from agents import voice
from agents.voice import (
    EdgeProvider,
    SarvamProvider,
    VoiceError,
    VoiceSegment,
    apply_pronunciations,
    build_segments,
    get_providers,
    infer_role,
    synthesize_all,
)

PLAYBOOK = {
    "pillars": {"finance": {"default_speaker": "narrator"}},
    "character_voices": {
        "narrator": {"voice": "hi-IN-MadhurNeural", "rate": "+8%", "pitch": "+2Hz", "sarvam_speaker": "shubh", "pace": 1.1},
        "tech_expert": {"voice": "en-IN-PrabhatNeural", "rate": "+7%", "pitch": "+0Hz", "sarvam_speaker": "aditya", "pace": 1.0},
    },
    "delivery": {"hook": {"pace": 1.1, "pause_before": 0.0}, "reveal": {"pace": 0.9, "pause_before": 0.4}},
}


def _cards():
    return [
        {"narration_cue": "Agar aap UPI use karte ho...", "tts_text": "अगर आप UPI use करते हो...", "speaker": "narrator", "role": "hook"},
        {"narration_cue": "RBI ne naya rule nikala hai.", "tts_text": "RBI ने नया rule निकाला है।", "speaker": "narrator"},
        {"narration_cue": "", "speaker": "narrator"},                                    # no line: no segment
        {"narration_cue": "Limit ab 5 lakh hai.", "tts_text": "Limit अब 5 लाख है।", "speaker": "tech_expert", "role": "reveal"},
        {"narration_cue": "Aap kya sochte ho?", "speaker": "narrator", "role": "nonsense"},
    ]


def test_infer_role_by_position():
    assert [infer_role(i, 6) for i in range(6)] == ["hook", "build", "build", "build", "reveal", "cta"]


def test_segments_get_pace_and_pause_from_role():
    segs = build_segments(_cards(), "finance", PLAYBOOK)
    assert [s.role for s in segs] == ["hook", "build", "reveal", "cta"]     # bad label -> inferred from position
    assert segs[0].pace == pytest.approx(1.21) and segs[0].pause_before == 0.0
    assert segs[2].pace == pytest.approx(0.9) and segs[2].pause_before == pytest.approx(0.4)
    assert segs[3].tts_text == ""


def test_pronunciations_replace_whole_words_only():
    lexicon = {"AI": "ए आई", "UPI": "यू पी आई"}
    assert apply_pronunciations("AI aur UPI, par PAID nahi", lexicon) == "ए आई aur यू पी आई, par PAID nahi"


def test_sarvam_request_uses_voice_track_lexicon_and_role_pace(monkeypatch):
    provider = SarvamProvider("key", lexicon={"UPI": "यू पी आई"})
    sent = {}
    monkeypatch.setattr(provider, "_request", lambda payload: sent.update(payload) or b"RIFFfake")
    seg = build_segments(_cards(), "finance", PLAYBOOK)[0]
    out = provider.synthesize_sync(seg, PLAYBOOK["character_voices"]["narrator"], __import__("pathlib").Path("/tmp/sarvam_test_seg"))
    assert sent["text"] == "अगर आप यू पी आई use करते हो..."
    assert sent["language_code"] == "hi-IN" and sent["model"] == "bulbul:v3"
    assert sent["speaker"] == "shubh" and sent["pace"] == pytest.approx(1.21)
    assert out.suffix == ".wav" and out.read_bytes() == b"RIFFfake"


def test_sarvam_falls_back_to_caption_without_a_voice_track():
    provider = SarvamProvider("key", lexicon={})
    assert provider.text_for(VoiceSegment("narrator", "Aap kya sochte ho?", "")) == "Aap kya sochte ho?"


def test_edge_gives_english_voices_the_roman_line():
    edge = EdgeProvider(lexicon={})
    seg = VoiceSegment("tech_expert", "Limit ab 5 lakh hai - hi sab ko.", "Limit अब 5 लाख है", pace=0.9)
    hindi_voice, english_voice = PLAYBOOK["character_voices"]["narrator"], PLAYBOOK["character_voices"]["tech_expert"]
    assert edge.text_for(seg, hindi_voice) == "Limit अब 5 लाख है"
    assert edge.text_for(seg, english_voice) == "Limit ab 5 lakh hai, hee sab ko."


def test_edge_rate_follows_the_role():
    seg = VoiceSegment("narrator", "x", "", pace=1.1 * 0.9)          # reveal: 10% slower than the base pace
    assert EdgeProvider.rate_for(seg, PLAYBOOK["character_voices"]["narrator"], 1.1) == "-2%"
    seg.pace = 1.1 * 1.1
    assert EdgeProvider.rate_for(seg, PLAYBOOK["character_voices"]["narrator"], 1.1) == "+18%"


def test_provider_choice(monkeypatch):
    assert [p.name for p in get_providers()] == ["edge"]
    monkeypatch.setenv("SARVAM_API_KEY", "k")
    assert [p.name for p in get_providers()] == ["sarvam", "edge"]
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    assert [p.name for p in get_providers()] == ["edge"]
    monkeypatch.setenv("TTS_PROVIDER", "sarvam")
    assert [p.name for p in get_providers()] == ["sarvam"]
    monkeypatch.delenv("SARVAM_API_KEY")
    with pytest.raises(VoiceError):
        get_providers()


class FakeProvider:
    def __init__(self, name, fail_on=None):
        self.name, self.fail_on, self.calls = name, fail_on, 0

    async def synthesize(self, segment, cfg, out_path):
        self.calls += 1
        if self.fail_on == self.calls:
            raise VoiceError("quota")
        path = out_path.with_suffix(".wav")
        path.write_bytes(b"x")
        return path


def test_one_provider_voices_the_whole_video(tmp_path):
    segs = build_segments(_cards(), "finance", PLAYBOOK)
    primary, backup = FakeProvider("sarvam", fail_on=3), FakeProvider("edge")
    name, paths = asyncio.run(synthesize_all(segs, tmp_path, PLAYBOOK, providers=[primary, backup]))
    assert name == "edge" and backup.calls == len(segs)            # redone from the first line, not patched
    assert all(p.name.startswith("edge_seg_") for p in paths)


def test_all_providers_failing_raises(tmp_path):
    segs = build_segments(_cards(), "finance", PLAYBOOK)
    with pytest.raises(VoiceError):
        asyncio.run(synthesize_all(segs, tmp_path, PLAYBOOK, providers=[FakeProvider("sarvam", fail_on=1)]))


def test_your_own_recording_is_used_instead_of_synthesis(tmp_path):
    recording = tmp_path / "my_take.wav"
    recording.write_bytes(b"RIFFmine")
    cards = _cards() + [{"narration_cue": "Meri rai yeh hai.", "speaker": "narrator", "role": "take", "audio_path": str(recording)}]
    segs = build_segments(cards, "finance", PLAYBOOK)
    assert segs[-1].role == "take" and segs[-1].pause_before == pytest.approx(0.30)
    provider = FakeProvider("sarvam")
    name, paths = asyncio.run(synthesize_all(segs, tmp_path / "out", PLAYBOOK, providers=[provider]))
    assert paths[-1] == recording and provider.calls == len(segs) - 1
