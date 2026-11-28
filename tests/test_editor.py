import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from agents import audio_design, editor_ffmpeg, host, media_dispatcher
from agents.editor_ffmpeg import EditorFFmpeg, plan_scene_durations
from agents.qa_judge import QAJudgeReport

FFMPEG = shutil.which("ffmpeg")


def _ffmpeg_has(kind: str, name: str) -> bool:
    if not FFMPEG:
        return False
    out = subprocess.run([FFMPEG, "-hide_banner", f"-{kind}"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines() if len(line.split()) > 1)


needs_ffmpeg = pytest.mark.skipif(
    not (_ffmpeg_has("filters", "ass") and _ffmpeg_has("filters", "zoompan") and _ffmpeg_has("encoders", "libx264")),
    reason="ffmpeg with libass, zoompan and libx264 is required",
)


def _sine_wav(path: Path, seconds: float, rate: int = 24000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate))
        )
        w.writeframes(frames)


def _scene_image(path: Path, hue: int):
    img = Image.new("RGB", (1080, 1920), (hue, 40, 90))
    draw = ImageDraw.Draw(img)
    for i in range(0, 1920, 60):           # detail, so a zoom visibly moves pixels
        draw.line([(0, i), (1080, i + 300)], fill=(255, 255 - hue, i % 255), width=6)
        draw.ellipse([i % 900, i, i % 900 + 120, i + 120], outline=(255, 255, 255), width=5)
    img.save(path, quality=92)


def _frame(video: Path, t: float, out: Path) -> Image.Image:
    subprocess.run([FFMPEG, "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)],
                   capture_output=True, check=True)
    return Image.open(out).convert("L")


def _diff(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


ASS_TEXT = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,360,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,Test caption line\n"
)


def _pose(path: Path, mouth: int, color=(240, 190, 150)):
    img = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([80, 40, 320, 300], fill=color + (255,))
    draw.rectangle([120, 300, 280, 600], fill=(40, 90, 200, 255))
    draw.ellipse([170, 210, 230, 210 + mouth], fill=(120, 20, 20, 255))
    img.save(path)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """Renders, music, effects and character art all live in tmp_path."""
    monkeypatch.setattr(editor_ffmpeg, "RENDERS_DIR", tmp_path / "renders")
    (tmp_path / "renders").mkdir()
    monkeypatch.setattr(audio_design, "BGM_DIR", tmp_path / "bgm")
    monkeypatch.setattr(audio_design, "SFX_DIR", tmp_path / "sfx")
    monkeypatch.setattr(audio_design, "GENERATED_SFX_DIR", tmp_path / "sfx_generated")
    monkeypatch.setattr(host, "CHARACTER_DIR", tmp_path / "character")
    monkeypatch.setattr(host, "LEGACY_AVATAR", tmp_path / "no_avatar.png")
    return tmp_path


def test_scene_durations_follow_narration_segments():
    durations = plan_scene_durations([2.0, 3.5, 1.5], 3, target_duration=7.5)
    assert durations == [2.0, 3.5, 2.0]            # last scene absorbs the 0.5s tail
    assert sum(durations) == pytest.approx(7.5)


def test_scene_durations_fall_back_to_even_split():
    assert plan_scene_durations([9.0], 3, target_duration=9.0) == [3.0, 3.0, 3.0]


def test_ass_timestamp_rounding():
    assert EditorFFmpeg._format_ass_timestamp(59.999) == "0:01:00.00"
    assert EditorFFmpeg._format_ass_timestamp(3.456) == "0:00:03.46"


@needs_ffmpeg
def test_render_keeps_moving_in_every_scene_and_cuts_on_time(studio):
    tmp_path = studio
    editor = EditorFFmpeg()
    durations = [3.0, 4.0, 3.5]
    frames = []
    for i, dur in enumerate(durations):
        p = tmp_path / f"scene_{i}.jpg"
        _scene_image(p, hue=60 + i * 80)
        frames.append((p, dur))
    voice = tmp_path / "voice.wav"
    _sine_wav(voice, 10.0)
    ass = tmp_path / "captions.ass"
    ass.write_text(ASS_TEXT, encoding="utf-8")
    out = tmp_path / "out.mp4"
    editor.render_video(frames, voice, ass, out, target_duration=10.5)

    duration = editor.qa_judge.probe_media_duration(out)
    assert duration == pytest.approx(10.5, abs=0.15)
    assert _frame(out, 5.0, tmp_path / "f.jpg").size == (1080, 1920)

    # The old filter stopped zooming ~2.5s into the video. Scene 3 runs 7.0s–10.5s:
    # its picture must still be changing between its start and its end.
    early, late = _frame(out, 7.3, tmp_path / "a.jpg"), _frame(out, 10.2, tmp_path / "b.jpg")
    assert _diff(early, late) > 2.0

    # Hard cut exactly where narration segment 1 ends (3.0s).
    before, after = _frame(out, 2.9, tmp_path / "c.jpg"), _frame(out, 3.1, tmp_path / "d.jpg")
    assert _diff(before, after) > 20.0
    # ...and no cut in the middle of scene 2.
    mid_a, mid_b = _frame(out, 4.9, tmp_path / "e.jpg"), _frame(out, 5.0, tmp_path / "g.jpg")
    assert _diff(mid_a, mid_b) < 10.0


def _mean_volume(path: Path, start: float, seconds: float) -> float:
    result = subprocess.run(
        [FFMPEG, "-ss", f"{start}", "-t", f"{seconds}", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    line = next(l for l in result.stderr.splitlines() if "mean_volume" in l)
    return float(line.split("mean_volume:")[1].split("dB")[0])


@needs_ffmpeg
def test_render_with_music_effects_and_host(studio):
    tmp_path = studio
    editor = EditorFFmpeg()
    frames = []
    for i, dur in enumerate([2.0, 2.0, 2.5]):
        p = tmp_path / f"scene_{i}.jpg"
        _scene_image(p, hue=60 + i * 80)
        frames.append((p, dur))

    # Voice only in the first 3 seconds, so music level can be compared with and without it.
    spoken, silent = tmp_path / "spoken.wav", tmp_path / "silent.wav"
    _sine_wav(spoken, 3.0)
    editor._write_silence(silent, 3.0)
    voice = tmp_path / "voice.wav"
    editor._concat_wavs([spoken, silent], voice)

    beds = audio_design.make_placeholder_beds(FFMPEG, seconds=8)
    assert len(beds) == 3 and audio_design.pick_bgm("finance", "prod_x").parent.name == "urgent"
    sfx = audio_design.ensure_sfx(FFMPEG)
    assert set(sfx) == {"whoosh", "hit"} and all(p.exists() for p in sfx.values())

    (tmp_path / "character").mkdir()
    _pose(tmp_path / "character" / "idle.png", mouth=6)
    _pose(tmp_path / "character" / "talk.png", mouth=50)
    spans = [host.SpeechSpan(0.0, 3.0, "hook", True), host.SpeechSpan(3.0, 6.0, "build", False)]
    track = host.build_host_track(voice, spans, 6.5, tmp_path / "work")
    assert track is not None and track.animated

    ass = tmp_path / "captions.ass"
    ass.write_text(ASS_TEXT, encoding="utf-8")

    plain, full = tmp_path / "plain.mp4", tmp_path / "full.mp4"
    editor.render_video(frames, voice, ass, plain, 6.5)
    editor.render_video(
        frames, voice, ass, full, 6.5, bgm_volume=0.3, bgm_path=beds[0],
        host_track=track, sfx=sfx, scene_roles=["hook", "build", "reveal"],
    )
    assert editor.qa_judge.probe_media_duration(full) == pytest.approx(6.5, abs=0.15)

    # Host: the lower-left zone differs from the host-less render; the top-right does not.
    zone = (40, 920, 420, 1440)
    with_host = _frame(full, 1.0, tmp_path / "h1.jpg")
    without = _frame(plain, 1.0, tmp_path / "h0.jpg")
    assert _diff(with_host.crop(zone), without.crop(zone)) > 8.0
    assert _diff(with_host.crop((600, 400, 1000, 900)), without.crop((600, 400, 1000, 900))) < 3.0

    # Music: audible once the voice stops (the plain render is silent there).
    assert _mean_volume(full, 4.2, 1.5) > _mean_volume(plain, 4.2, 1.5) + 15


@needs_ffmpeg
def test_assemble_times_scenes_to_voice_retries_with_new_seeds_and_cleans_up(studio, monkeypatch):
    tmp_path = studio
    editor = EditorFFmpeg()
    seg_lengths = iter([1.5, 2.5, 2.0, 1.0, 1.5] * 2)
    synthesized = []

    async def fake_synthesize(segments, out_dir, playbook):
        paths = []
        for i, seg in enumerate(segments):
            synthesized.append(seg)
            p = out_dir / f"fake_{i:02d}.mp3"
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", f"sine=frequency=300:duration={next(seg_lengths)}",
                            "-c:a", "libmp3lame", str(p)], capture_output=True, check=True)
            paths.append(p)
        return "fake", paths

    monkeypatch.setattr(editor, "_synthesize", fake_synthesize)

    seen_salts, planned = [], {}

    def fake_dispatch(shot_cards, production_id, seed_salt=0):
        seen_salts.append(seed_salt)
        scenes = media_dispatcher.work_dir(production_id) / "scenes"
        scenes.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, _ in enumerate(shot_cards):
            p = scenes / f"shot_{i+1:02d}.jpg"
            _scene_image(p, hue=30 + i * 40 + seed_salt)
            paths.append(p)
        return paths

    monkeypatch.setattr(editor.dispatcher, "dispatch_media", fake_dispatch)

    real_render = editor.render_video

    def spy_render(frame_entries, voice_audio, ass_file, target_mp4, target_duration, **kw):
        planned["durations"] = [d for _, d in frame_entries]
        planned["target"] = target_duration
        planned["roles"] = kw.get("scene_roles")
        planned["captions"] = Path(ass_file).read_text(encoding="utf-8")
        return real_render(frame_entries, voice_audio, ass_file, target_mp4, target_duration, **kw)

    monkeypatch.setattr(editor, "render_video", spy_render)

    def fake_audit(video_path, voice_path, ass_path, scenes_dir, topic):
        assert Path(video_path).exists() and Path(scenes_dir).exists()
        return QAJudgeReport(
            status="FAIL", video_path=str(video_path), topic=topic, video_duration=0, voice_duration=0,
            distinct_scenes_count=5, subtitles_count=5, no_watermarks=False, visuals_match_topic=False,
            no_blank_wireframes=True, captions_clean=True, keyframes_evaluated=[], checks={}, score=0, notes=[],
        )

    monkeypatch.setattr(editor.qa_judge, "audit", fake_audit)

    roles = ["hook", "build", "build", "reveal", "cta"]
    cards = [
        {"narration_cue": f"Yeh line number {w} hai dosto", "tts_text": f"यह line number {w} है दोस्तो",
         "speaker": "narrator", "role": role, "overlay_text": "Test", "badge_text": "UPDATE"}
        for w, role in zip(["ek", "do", "teen", "chaar", "paanch"], roles)
    ]
    script = {"title": "T", "category": "technology", "narration_script_hinglish": "x", "shot_cards": cards}

    path1, report1 = editor.assemble_vertical_short(script, "prod_test", "a.mp4", attempt=1)
    path2, _ = editor.assemble_vertical_short(script, "prod_test", "b.mp4", attempt=2)

    # Pauses from the playbook: build 0.08s, reveal 0.35s, cta 0.15s before the line.
    # A scene runs from its line's start to the next line's start, so the pause
    # before the reveal belongs to the scene before it and the cut lands on the reveal.
    assert planned["durations"] == pytest.approx([1.5 + 0.08, 2.5 + 0.08, 2.0 + 0.35, 1.0 + 0.15, 1.5 + 0.5], abs=0.07)
    assert planned["target"] == pytest.approx(8.5 + 0.66 + 0.5, abs=0.2)
    assert planned["roles"] == roles
    assert planned["captions"].count("Dialogue:") == 10          # 6 words per cue -> 2 caption chunks each
    assert "यह" not in planned["captions"]                         # captions stay Roman

    # Delivery: the hook is spoken faster than the reveal; the voice track reaches the provider.
    first_pass = synthesized[:5]
    assert first_pass[0].pace > first_pass[1].pace > first_pass[3].pace
    assert first_pass[3].pause_before == pytest.approx(0.35) and first_pass[0].pause_before == 0
    assert first_pass[0].tts_text.startswith("यह")

    assert editor.last_render_info["voice_provider"] == "fake"
    assert seen_salts == [0, 1]                                   # retry asks for different images
    assert report1.status == "FAIL" and path1.parent.name == "rejected" and path1.exists()
    assert not media_dispatcher.work_dir("prod_test").exists()    # scratch dir removed
