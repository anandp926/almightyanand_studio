import json
from pathlib import Path

import pytest

from agents import orchestrator
from agents.qa_judge import QAJudge, VISION_KEYS


def _keyframes(tmp_path, n=4):
    paths = []
    for i in range(n):
        p = tmp_path / f"k{i}.jpg"
        p.write_bytes(b"\xff\xd8fake")
        paths.append(p)
    return paths


def test_vision_audit_fails_closed_without_four_keyframes(tmp_path):
    result, notes = QAJudge().run_vision_audit(_keyframes(tmp_path, 3), "topic")
    assert all(result[k] is False for k in VISION_KEYS)


def test_vision_audit_fails_closed_when_no_model_is_reachable(tmp_path, monkeypatch):
    judge = QAJudge()
    judge.gemini_key = ""

    def down(keyframes, rubric):
        raise OSError("connection refused")

    monkeypatch.setattr(judge, "_vision_via_omniroute", down)
    result, _ = judge.run_vision_audit(_keyframes(tmp_path), "topic")
    assert all(result[k] is False for k in VISION_KEYS)


def test_router_reply_without_all_images_is_rejected(tmp_path, monkeypatch):
    judge = QAJudge()
    judge.gemini_key = ""

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            verdict = json.dumps({k: True for k in VISION_KEYS})
            return json.dumps({"images_forwarded": 0, "choices": [{"message": {"content": verdict}}]}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: FakeResp())
    result, _ = judge.run_vision_audit(_keyframes(tmp_path), "topic")
    assert all(result[k] is False for k in VISION_KEYS)      # "all true" about unseen frames must not pass


def test_only_explicit_true_passes_the_vision_checks(tmp_path, monkeypatch):
    judge = QAJudge()
    monkeypatch.setattr(judge, "probe_media_duration", lambda p: 30.0)
    monkeypatch.setattr(judge, "extract_keyframes", lambda v, o, d: [])
    partial = {"no_watermarks": True, "visuals_match_topic": "yes", "no_blank_wireframes": True}   # one missing, one non-bool
    monkeypatch.setattr(judge, "run_vision_audit", lambda k, t: (partial, []))
    report = judge.audit(tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "c.ass", tmp_path / "scenes", "t")
    assert report.status == "FAIL"
    assert report.checks["no_watermarks"] is True
    assert report.checks["visuals_match_topic"] is False and report.checks["captions_clean"] is False


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "QUEUE_FILE", tmp_path / "publish_queue.json")
    return tmp_path


def _item(path: Path):
    return {"video_path": str(path), "title": "T #Shorts", "description": "d", "top_comment": "c",
            "production_id": "p", "topic": "topic", "category": "finance", "style": "commentary_broll", "hook": "h"}


def test_auto_publish_off_leaves_queue_untouched(queue, monkeypatch):
    video = queue / "a.mp4"
    video.write_bytes(b"x")
    orchestrator._save_queue([_item(video)])
    monkeypatch.setenv("AUTO_PUBLISH", "false")
    monkeypatch.setattr(orchestrator, "_publish_item", lambda *a, **kw: pytest.fail("must not publish"))
    orchestrator.publish_next_queued_slot("slot")
    assert len(orchestrator._load_queue()) == 1


def test_failed_upload_is_requeued_and_missing_render_dropped(queue, monkeypatch):
    video = queue / "a.mp4"
    video.write_bytes(b"x")
    orchestrator._save_queue([_item(queue / "gone.mp4"), _item(video)])

    def fail(item, slot_label, memory=None):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(orchestrator, "_publish_item", fail)
    orchestrator.publish_next_queued_slot("slot")
    remaining = orchestrator._load_queue()
    assert [Path(i["video_path"]).name for i in remaining] == ["a.mp4"]


def test_published_video_is_recorded_for_per_video_analytics(queue, monkeypatch):
    from agents.memory_store import MemoryStore
    from agents.publisher import PublishRecord, Publisher

    video = queue / "a.mp4"
    video.write_bytes(b"x")
    seen = {}

    def fake_publish(self, video_path, title, description, top_comment="", tags=None, slot_label="", publish_at=None):
        seen.update(top_comment=top_comment, description=description)
        return PublishRecord("YouTube Shorts (9:16)", "Almighty Anand", title, "PUBLISHED", slot_label, 0, True,
                             "https://youtube.com/shorts/vid123", "vid123")

    monkeypatch.setattr(Publisher, "publish_youtube_short", fake_publish)
    orchestrator._save_queue([_item(video)])
    orchestrator.publish_next_queued_slot("slot")

    assert orchestrator._load_queue() == []
    with MemoryStore()._get_connection() as conn:
        row = dict(conn.execute("SELECT * FROM published_videos").fetchone())
    assert row["video_id"] == "vid123" and row["category"] == "finance" and row["hook"] == "h"
    assert seen["top_comment"] == "c"


def test_batch_size_matches_publish_slots_and_stops_when_nothing_is_groundable(queue, monkeypatch):
    calls = []

    def cycle(publish_immediately=False, prefer_category=None):
        calls.append(prefer_category)
        if len(calls) == 2:
            raise orchestrator.NoPublishableTopicError("nothing")

    monkeypatch.setattr(orchestrator, "execute_production_cycle", cycle)
    monkeypatch.setenv("SHORTS_PER_DAY", "3")
    orchestrator.run_morning_batch()
    assert len(calls) == 2                      # stopped at the first "no topic", did not hammer the feeds

    calls.clear()
    monkeypatch.setattr(orchestrator, "execute_production_cycle", lambda publish_immediately=False, prefer_category=None: calls.append(prefer_category))
    orchestrator.run_morning_batch()
    assert calls == ["technology"] * 3          # every Short stays on the channel's pillar

    calls.clear()
    monkeypatch.delenv("SHORTS_PER_DAY")
    orchestrator.run_morning_batch()
    assert len(calls) == 1                      # default: one good Short a day


def test_prune_keeps_queued_and_recent_renders(queue, monkeypatch):
    import os
    import time

    renders = queue / "renders"
    (renders / "rejected").mkdir(parents=True)
    monkeypatch.setattr(orchestrator, "RENDERS_DIR", renders)
    old_queued, old_done, fresh, old_rejected = renders / "q.mp4", renders / "done.mp4", renders / "new.mp4", renders / "rejected" / "r.mp4"
    for p in (old_queued, old_done, fresh, old_rejected):
        p.write_bytes(b"x")
    stale = time.time() - 30 * 86400
    for p in (old_queued, old_done, old_rejected):
        os.utime(p, (stale, stale))
    orchestrator._save_queue([_item(old_queued)])

    assert orchestrator.prune_old_artifacts(max_age_days=7) == 2
    assert old_queued.exists() and fresh.exists()
    assert not old_done.exists() and not old_rejected.exists()


# ── Review hold, burst mode, fixed style ──────────────────────────────

def _fake_publisher(monkeypatch, seen):
    from agents.publisher import PublishRecord, Publisher

    def fake_publish(self, video_path, title, description, top_comment="", tags=None, slot_label="", publish_at=None):
        scheduled = publish_at is not None and self.privacy_status == "public"
        seen.append({"title": title, "publish_at": publish_at, "privacy": self.privacy_status, "scheduled": scheduled})
        stamp = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z") if scheduled else ""
        return PublishRecord("YouTube Shorts (9:16)", "Almighty Anand", title, "OK", slot_label, 0, False,
                             f"https://youtube.com/shorts/v{len(seen)}", f"v{len(seen)}", stamp)

    monkeypatch.setattr(Publisher, "publish_youtube_short", fake_publish)


def test_review_required_holds_unapproved_shorts(queue, monkeypatch):
    from agents import review

    seen = []
    _fake_publisher(monkeypatch, seen)
    a, b = queue / "a.mp4", queue / "b.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    first, second = _item(a), _item(b)
    first["title"], second["title"] = "First #Shorts", "Second #Shorts"
    orchestrator._save_queue([first, second])
    monkeypatch.setenv("REVIEW_REQUIRED", "true")

    orchestrator.publish_next_queued_slot("slot")
    assert seen == [] and len(orchestrator._load_queue()) == 2          # nothing approved, nothing published

    assert review.approve("2") == 1
    orchestrator.publish_next_queued_slot("slot")
    assert [s["title"] for s in seen] == ["Second #Shorts"]            # only the approved one went out
    assert [i["title"] for i in orchestrator._load_queue()] == ["First #Shorts"]


def test_reject_removes_item_and_moves_render(queue, monkeypatch):
    from agents import review

    renders = queue / "renders"
    renders.mkdir()
    monkeypatch.setattr(review, "RENDERS_DIR", renders)
    video = renders / "a.mp4"
    video.write_bytes(b"x")
    orchestrator._save_queue([_item(video)])
    review.reject("1")
    assert orchestrator._load_queue() == []
    assert (renders / "rejected" / "a.mp4").exists() and not video.exists()


def test_upcoming_slots_skip_times_that_are_too_close():
    from datetime import datetime

    now = datetime(2026, 11, 25, 12, 20, tzinfo=orchestrator.IST)       # 12:30 is only 10 minutes away
    slots = orchestrator.upcoming_slot_times(now=now, count=3)
    assert [(w.day, w.hour, w.minute) for w, _ in slots] == [(25, 18, 30), (25, 21, 0), (26, 12, 30)]

    morning = datetime(2026, 11, 25, 7, 15, tzinfo=orchestrator.IST)
    assert [(w.hour, w.minute) for w, _ in orchestrator.upcoming_slot_times(now=morning)] == [(12, 30), (18, 30), (21, 0)]


def test_burst_schedules_every_queued_short_and_records_go_live_time(queue, monkeypatch):
    from agents.memory_store import MemoryStore

    seen = []
    _fake_publisher(monkeypatch, seen)
    items = []
    for name in ("a", "b"):
        video = queue / f"{name}.mp4"
        video.write_bytes(b"x")
        items.append(_item(video))
    orchestrator._save_queue(items)

    assert orchestrator.schedule_queued_uploads() == 2
    assert orchestrator._load_queue() == []
    assert all(s["scheduled"] and s["publish_at"] is not None for s in seen)
    assert seen[0]["publish_at"] < seen[1]["publish_at"]

    with MemoryStore()._get_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM published_videos ORDER BY published_at").fetchall()]
    assert len(rows) == 2
    assert rows[0]["published_at"].startswith(seen[0]["publish_at"].astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M"))
    # Not yet live, so the analytics review does not look at them.
    assert MemoryStore().get_videos_for_review(0, 10) == []


def test_burst_with_review_required_uploads_private_and_unscheduled(queue, monkeypatch):
    seen = []
    _fake_publisher(monkeypatch, seen)
    video = queue / "a.mp4"
    video.write_bytes(b"x")
    orchestrator._save_queue([_item(video)])
    monkeypatch.setenv("REVIEW_REQUIRED", "true")
    assert orchestrator.schedule_queued_uploads() == 1
    assert seen[0]["privacy"] == "private" and seen[0]["scheduled"] is False


def test_style_is_fixed_unless_pivot_is_enabled(monkeypatch):
    from agents.memory_store import MemoryStore

    memory = MemoryStore()
    for day in ("2026-11-20", "2026-11-21", "2026-11-22"):
        memory.record_style_performance(day, "commentary_broll", 40.0, 900)
    memory.record_daily_plan("2026-11-22", "x", ["y"], "commentary_broll")
    assert orchestrator._get_active_style(memory) == "stylised_3d"       # low AVD does not change the look
    monkeypatch.setenv("STYLE_PIVOT_ENABLED", "true")
    assert orchestrator._get_active_style(memory) == "origami_breakdown"


# ── Creator take ──────────────────────────────────────────────────────

def _queued_with_script(queue):
    video = queue / "orig.mp4"
    video.write_bytes(b"x")
    item = _item(video)
    item.update({
        "production_id": "prod_1",
        "description": "Hook line\n\n#Shorts #AI",
        "script_data": {"title": "T", "category": "technology", "narration_script_hinglish": "x", "shot_cards": [
            {"narration_cue": "Hook line", "speaker": "narrator", "role": "hook"},
            {"narration_cue": "Middle line", "speaker": "skeptic_friend", "role": "build"},
            {"narration_cue": "Aap kya sochte ho?", "speaker": "narrator", "role": "cta"},
        ]},
    })
    orchestrator._save_queue([item])
    return video


def _fake_editor(monkeypatch, queue, status="PASS"):
    from agents.qa_judge import QAJudgeReport

    seen = {}

    class FakeEditor:
        last_render_info = {"duration_s": 21.5}

        def __init__(self, dry_run=False):
            pass

        def assemble_vertical_short(self, script_data, production_id, output_filename=None, attempt=1):
            seen.update(script=script_data, production_id=production_id)
            path = queue / output_filename
            path.write_bytes(b"new")
            report = QAJudgeReport(status, str(path), "t", 0, 0, 5, 5, True, True, True, True, [], {}, 10, ["qa note"])
            return path, report

    monkeypatch.setattr(orchestrator, "EditorFFmpeg", FakeEditor)
    return seen


def test_creator_take_is_inserted_before_the_closing_question_and_rerendered(queue, monkeypatch):
    old_video = _queued_with_script(queue)
    seen = _fake_editor(monkeypatch, queue)

    item = orchestrator.apply_creator_take(1, "Mujhe lagta hai yeh students ke liye best hai.", tts_text="मुझे लगता है", audio_path="me.wav")

    cards = seen["script"]["shot_cards"]
    assert [c["role"] for c in cards] == ["hook", "build", "take", "cta"]
    take = cards[2]
    assert take["narration_cue"].startswith("Mujhe lagta hai") and take["badge_text"] == "MY TAKE"
    assert take["speaker"] == "narrator" and take["tts_text"] == "मुझे लगता है" and take["audio_path"] == "me.wav"

    saved = orchestrator._load_queue()[0]
    assert saved["creator_take"].startswith("Mujhe lagta hai") and saved["video_path"] == item["video_path"]
    assert saved["video_path"].endswith("_take.mp4") and not old_video.exists()
    assert saved["description"].split("\n\n")[1] == "Meri rai: Mujhe lagta hai yeh students ke liye best hai."
    assert saved["duration_s"] == 21.5

    # A second take replaces the first instead of stacking.
    orchestrator.apply_creator_take(1, "Nayi rai.")
    assert [c["role"] for c in seen["script"]["shot_cards"]].count("take") == 1
    assert orchestrator._load_queue()[0]["description"].count("Meri rai:") == 1


def test_failed_rerender_leaves_the_original_untouched(queue, monkeypatch):
    old_video = _queued_with_script(queue)
    _fake_editor(monkeypatch, queue, status="FAIL")
    with pytest.raises(RuntimeError):
        orchestrator.apply_creator_take(1, "Meri rai.")
    saved = orchestrator._load_queue()[0]
    assert saved["video_path"] == str(old_video) and old_video.exists() and not saved.get("creator_take")


def test_take_and_format_are_recorded_with_the_published_video(queue, monkeypatch):
    from agents.memory_store import MemoryStore

    seen = []
    _fake_publisher(monkeypatch, seen)
    video = queue / "a.mp4"
    video.write_bytes(b"x")
    item = _item(video)
    item.update({"format": "jugaad_test", "creator_take": "Meri rai.", "series": "ai_ki_duniya"})
    orchestrator._save_queue([item])
    orchestrator.publish_next_queued_slot("slot")
    with MemoryStore()._get_connection() as conn:
        row = dict(conn.execute("SELECT * FROM published_videos").fetchone())
    assert row["format"] == "jugaad_test" and row["has_creator_take"] == 1 and row["series"] == "ai_ki_duniya"


def test_format_performance_ranks_formats_by_retention():
    from agents.memory_store import MemoryStore

    memory = MemoryStore()
    for vid, fmt, views, avd in [("a", "jugaad_test", 1000, 80.0), ("b", "jugaad_test", 3000, 60.0),
                                 ("c", "kya_badla", 500, 75.0), ("d", "chai_explain", 20, 99.0)]:
        memory.record_published_video(vid, format=fmt)
        memory.update_video_metrics(vid, views, 10.0, avd)
    report = memory.format_performance(min_views=100)
    assert [(r["format"], r["avd_percent"]) for r in report] == [("kya_badla", 75.0), ("jugaad_test", 65.0)]
