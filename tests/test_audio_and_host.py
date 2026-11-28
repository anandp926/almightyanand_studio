import math
import struct
import wave

from PIL import Image

from agents import audio_design, host
from agents.audio_design import build_audio_graph, sfx_times
from agents.host import SpeechSpan, mouth_envelope, plan_frames


def test_voice_only_graph_is_just_loudnorm():
    graph = build_audio_graph(voice_idx=3)
    assert "[3:a]" in graph and "amix" not in graph and graph.endswith("loudnorm=I=-16:TP=-1.5:LRA=11[a]")


def test_music_is_ducked_under_the_voice_and_voice_sets_the_length():
    graph = build_audio_graph(voice_idx=3, bgm_idx=5, bgm_volume=0.12)
    assert "[5:a]" in graph and "volume=0.120" in graph
    assert "[bed][sc]sidechaincompress" in graph
    assert "[vox][duck]amix=inputs=2:duration=first:normalize=0" in graph


def test_effects_are_placed_at_cut_times():
    graph = build_audio_graph(voice_idx=2, whoosh_idx=4, whoosh_times=[1.0, 2.5], hit_idx=5, hit_times=[2.65])
    assert "[4:a]asplit=2[ws0][ws1]" in graph
    assert "adelay=1000:all=1" in graph and "adelay=2500:all=1" in graph and "adelay=2650:all=1" in graph
    assert "amix=inputs=4" in graph


def test_whoosh_before_each_cut_and_hit_on_the_reveal():
    whooshes, hits = sfx_times([0.0, 2.0, 4.5, 7.0], ["hook", "build", "reveal", "cta"])
    assert whooshes == [1.85, 4.35, 6.85] and hits == [4.5]


def test_no_music_files_means_no_music(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_design, "BGM_DIR", tmp_path / "bgm")
    assert audio_design.pick_bgm("finance", "prod_1") is None


def test_music_is_picked_from_the_pillars_mood_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_design, "BGM_DIR", tmp_path / "bgm")
    for mood in ("urgent", "curious"):
        (tmp_path / "bgm" / mood).mkdir(parents=True)
        (tmp_path / "bgm" / mood / f"{mood}_1.mp3").write_bytes(b"x")
    assert audio_design.pick_bgm("finance", "prod_1").name == "urgent_1.mp3"
    assert audio_design.pick_bgm("technology", "prod_1").name == "curious_1.mp3"


def test_sfx_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("SFX_ENABLED", "false")
    assert audio_design.ensure_sfx("ffmpeg") == {}


def _wav(path, pattern, rate=24000):
    """pattern: list of (seconds, amplitude)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for seconds, amp in pattern:
            w.writeframes(b"".join(
                struct.pack("<h", int(amp * math.sin(2 * math.pi * 200 * i / rate))) for i in range(int(seconds * rate))
            ))


def test_mouth_follows_loudness(tmp_path):
    path = tmp_path / "v.wav"
    _wav(path, [(1.0, 9000), (1.0, 0)])
    env = mouth_envelope(path)
    assert len(env) == 24 and all(env[:11]) and not any(env[13:])


def test_host_talks_only_while_a_host_speaker_speaks_and_flaps():
    poses = {"idle": "i", "talk": "t", "reveal": "r", "reveal_talk": "rt"}
    spans = [SpeechSpan(0.0, 1.0, "hook", True), SpeechSpan(1.0, 2.0, "build", False), SpeechSpan(2.0, 3.0, "reveal", True)]
    runs = plan_frames(spans, [True] * 36, poses, 3.0)
    names = [name for name, _ in runs]
    assert abs(sum(sec for _, sec in runs) - 3.0) < 0.01
    first_second = [n for n, sec in runs[:names.index("idle") + 1]]
    assert first_second == ["talk", "idle"]                 # opens, then closes after three windows
    assert runs[0][1] == 3 * host.MOUTH_WINDOW
    assert "reveal_talk" in names and "reveal" in names     # reveal uses its own poses
    # While the contrast voice speaks (1.0–2.0s) the host only listens: one idle run of ~1s.
    assert any(n == "idle" and sec > 0.9 for n, sec in runs)


def test_still_avatar_is_used_when_there_are_no_poses(tmp_path, monkeypatch):
    avatar = tmp_path / "character_ref.png"
    Image.new("RGBA", (300, 400), (200, 100, 50, 255)).save(avatar)
    monkeypatch.setattr(host, "CHARACTER_DIR", tmp_path / "character")
    monkeypatch.setattr(host, "LEGACY_AVATAR", avatar)
    voice = tmp_path / "v.wav"
    _wav(voice, [(1.0, 9000)])
    track = host.build_host_track(voice, [SpeechSpan(0, 1, "hook", True)], 1.5, tmp_path / "work")
    assert track is not None and track.animated is False and "-loop" in track.input_args
    assert track.y == 1440 - 520 and track.x == 40


def test_host_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_ENABLED", "false")
    voice = tmp_path / "v.wav"
    _wav(voice, [(0.5, 9000)])
    assert host.build_host_track(voice, [], 1.0, tmp_path / "work") is None
