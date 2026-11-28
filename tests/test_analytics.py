import json
from datetime import datetime, timedelta, timezone

import pytest

from agents import playbook
from agents.analytics_reviewer import AnalyticsReviewer, weighted_avd
from agents.memory_store import MemoryStore


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _perf_logs(memory):
    with memory._get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM performance_logs ORDER BY date").fetchall()]


@pytest.fixture
def memory():
    return MemoryStore()


def _reviewer(memory, monkeypatch, metrics=None, error=None):
    reviewer = AnalyticsReviewer(memory=memory)

    def fake_fetch(videos):
        if error:
            raise error
        return {v["video_id"]: metrics[v["video_id"]] for v in videos if v["video_id"] in metrics}

    monkeypatch.setattr(reviewer, "fetch_video_metrics", fake_fetch)
    return reviewer


def test_weighted_avd():
    assert weighted_avd([{"views": 100, "avd_percent": 80.0}, {"views": 300, "avd_percent": 60.0}]) == 65.0
    assert weighted_avd([{"views": 0, "avd_percent": 80.0}]) is None


def test_no_videos_records_nothing(memory, monkeypatch):
    report = _reviewer(memory, monkeypatch, metrics={}).run_daily_review()
    assert report.data_available is False and report.avg_retention_percent is None
    assert _perf_logs(memory) == []
    assert not playbook.STATE_PATH.exists()


def test_api_failure_records_nothing(memory, monkeypatch):
    memory.record_published_video("v1", category="technology", style="commentary_broll", published_at=_iso(4))
    report = _reviewer(memory, monkeypatch, error=RuntimeError("quota")).run_daily_review()
    assert report.data_available is False
    assert _perf_logs(memory) == []          # the old code logged a made-up 70% here
    assert not playbook.STATE_PATH.exists()


def test_low_view_videos_are_not_signal(memory, monkeypatch):
    memory.record_published_video("v1", category="technology", style="commentary_broll", published_at=_iso(4))
    metrics = {"v1": {"views": 12, "watch_minutes": 3.0, "avd_percent": 20.0}}
    report = _reviewer(memory, monkeypatch, metrics=metrics).run_daily_review()
    assert report.data_available is False
    assert _perf_logs(memory) == []          # 12 views at 20% must not count as an underperforming day


def test_too_recent_videos_are_not_reviewed(memory):
    memory.record_published_video("new", published_at=_iso(0.5))
    assert memory.get_videos_for_review(2, 10) == []


def test_measured_videos_drive_style_log_and_their_own_pillar(memory, monkeypatch):
    memory.record_published_video("fin", category="finance", style="commentary_broll", hook="Fin hook", published_at=_iso(4))
    memory.record_published_video("tech", category="technology", style="commentary_broll", hook="Tech hook", published_at=_iso(4))
    metrics = {
        "fin": {"views": 1000, "watch_minutes": 300.0, "avd_percent": 50.0},
        "tech": {"views": 1000, "watch_minutes": 500.0, "avd_percent": 82.0},
    }
    base_before = playbook.BASE_PATH.read_text(encoding="utf-8")
    reviewer = _reviewer(memory, monkeypatch, metrics=metrics)
    report = reviewer.run_daily_review()

    assert report.data_available and report.videos_reviewed == 2 and report.views == 2000
    logs = _perf_logs(memory)
    assert len(logs) == 1 and logs[0]["avg_avd"] == 66.0 and logs[0]["style_preset"] == "commentary_broll"

    # Each pillar learned from its own videos
    assert "decayed_hook_weights" in report.playbook_update["finance"]["changes"]
    assert "promoted_winning_hook" in report.playbook_update["technology"]["changes"]
    merged = playbook.load_playbook()
    assert any(h["pattern"] == "Tech hook" for h in merged["pillars"]["technology"]["hook_priors"])
    assert not any(h["pattern"] == "Fin hook" for h in merged["pillars"]["finance"]["hook_priors"])

    # Learning goes to memory/, the git-tracked baseline is untouched
    assert playbook.STATE_PATH.exists()
    assert playbook.BASE_PATH.read_text(encoding="utf-8") == base_before

    # Running again does not re-apply the same videos
    weights_after_first = json.dumps(playbook.load_playbook()["pillars"], sort_keys=True)
    second = reviewer.run_daily_review()
    assert second.playbook_update == {"updated": False, "reason": "no mature videos pending"}
    assert json.dumps(playbook.load_playbook()["pillars"], sort_keys=True) == weights_after_first
    assert len(_perf_logs(memory)) == 1


def test_pivot_needs_three_different_low_days(memory):
    for day in ("2026-11-20", "2026-11-21"):
        memory.record_style_performance(day, "commentary_broll", 50.0, 500)
    assert memory.check_pivot_needed("commentary_broll")[0] is False
    memory.record_style_performance("2026-11-21", "commentary_broll", 48.0, 900)   # same day re-measured
    assert memory.check_pivot_needed("commentary_broll")[0] is False
    memory.record_style_performance("2026-11-22", "commentary_broll", 55.0, 500)
    pivot, new_style, _ = memory.check_pivot_needed("commentary_broll")
    assert pivot is True and new_style == "origami_breakdown"


# ── Retention curve ───────────────────────────────────────────────────

from agents.analytics_reviewer import dominant_zone, summarize_retention


def _curve(after_hook, late, finish, start=1.0):
    """100-point curve passing through the given levels at 15%, 60% and 95%."""
    points = []
    for i in range(1, 101):
        x = i / 100
        if x <= 0.15:
            y = start + (after_hook - start) * (x - 0.01) / 0.14
        elif x <= 0.60:
            y = after_hook + (late - after_hook) * (x - 0.15) / 0.45
        else:
            y = late + (finish - late) * (x - 0.60) / 0.35
        points.append((x, y))
    return points


def test_retention_summary_finds_the_weak_zone():
    assert summarize_retention(_curve(0.50, 0.45, 0.40))["drop_zone"] == "hook"
    assert summarize_retention(_curve(0.85, 0.45, 0.40))["drop_zone"] == "middle"
    assert summarize_retention(_curve(0.85, 0.75, 0.40))["drop_zone"] == "end"
    assert summarize_retention(_curve(0.90, 0.80, 0.70))["drop_zone"] == "none"
    assert summarize_retention([(0.1, 0.5)]) is None
    assert dominant_zone([{"drop_zone": "middle"}, {"drop_zone": "hook"}, {"drop_zone": "middle"}]) == "middle"
    assert dominant_zone([{"drop_zone": None}]) is None


def test_middle_drop_shortens_script_and_leaves_hooks_alone(memory, monkeypatch):
    memory.record_published_video("fin", category="finance", style="stylised_3d", hook="h", published_at=_iso(4), series="naya_rule")
    metrics = {"fin": {"views": 800, "watch_minutes": 200.0, "avd_percent": 48.0}}
    reviewer = _reviewer(memory, monkeypatch, metrics=metrics)
    monkeypatch.setattr(reviewer, "fetch_retention_curve", lambda video_id: _curve(0.85, 0.45, 0.40))
    before = playbook.load_playbook()
    report = reviewer.run_daily_review()

    changes = report.playbook_update["finance"]["changes"]
    assert "script_words_target_to_75" in changes and "decayed_hook_weights" not in changes
    after = playbook.load_playbook()
    assert after["pillars"]["finance"]["script_words_target"] == 75
    assert after["pillars"]["finance"]["hook_priors"] == before["pillars"]["finance"]["hook_priors"]
    speaker = after["pillars"]["finance"]["default_speaker"]
    assert after["character_voices"][speaker]["pace"] > before["character_voices"][speaker]["pace"]

    with memory._get_connection() as conn:
        row = dict(conn.execute("SELECT * FROM published_videos WHERE video_id='fin'").fetchone())
    assert row["drop_zone"] == "middle" and abs(row["hook_hold"] - 0.85) < 0.02 and row["series"] == "naya_rule"


def test_hook_drop_decays_hooks_and_keeps_length(memory, monkeypatch):
    memory.record_published_video("t", category="technology", style="stylised_3d", published_at=_iso(4))
    reviewer = _reviewer(memory, monkeypatch, metrics={"t": {"views": 800, "watch_minutes": 100.0, "avd_percent": 40.0}})
    monkeypatch.setattr(reviewer, "fetch_retention_curve", lambda video_id: _curve(0.50, 0.45, 0.40))
    report = reviewer.run_daily_review()
    assert report.playbook_update["technology"]["changes"] == ["decayed_hook_weights"]
    assert playbook.load_playbook()["pillars"]["technology"]["script_words_target"] == 80


def test_old_database_is_upgraded_in_place(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE published_videos (video_id TEXT PRIMARY KEY, production_id TEXT, title TEXT, topic TEXT,
        category TEXT, style TEXT, hook TEXT, published_at TEXT NOT NULL, views INTEGER DEFAULT 0,
        watch_minutes REAL DEFAULT 0.0, avd_percent REAL, last_checked TEXT, playbook_applied INTEGER DEFAULT 0)""")
    conn.execute("INSERT INTO published_videos (video_id, published_at) VALUES ('old', '2026-11-20T00:00:00+00:00')")
    conn.commit()
    conn.close()

    store = MemoryStore(db_path=db)
    store.record_published_video("new", series="ai_tool", word_count=72, voice_provider="sarvam", pace=1.08)
    with store._get_connection() as c:
        rows = {r["video_id"]: dict(r) for r in c.execute("SELECT * FROM published_videos").fetchall()}
    assert rows["old"]["series"] is None and rows["new"]["voice_provider"] == "sarvam" and rows["new"]["word_count"] == 72
