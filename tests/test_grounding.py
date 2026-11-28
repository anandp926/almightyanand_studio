import pytest

from agents import script_director
from agents.script_director import (
    ScriptDirector,
    ScriptGroundingError,
    build_short_description,
    extract_numbers,
    load_affiliate_links,
    verify_grounding,
)

SOURCE = (
    "The Reserve Bank of India on Monday raised the per-transaction UPI limit for tax payments "
    "to Rs 5 lakh from Rs 1 lakh. The change takes effect immediately for all member banks. "
    "Banks have been told to update their systems and inform customers about the new limit. "
    "The central bank said the move is meant to make larger tax payments easier for individuals."
)
TITLE = "RBI raises UPI limit for tax payments"


def _script(**overrides):
    cues = [
        {"overlay_text": "UPI Limit", "badge_text": "UPDATE", "speaker": "narrator",
         "visual_prompt": "a glowing phone on a desk", "narration_cue": f"Yeh script ki line {word} hai."}
        for word in ["ek", "do", "teen", "chaar", "paanch", "chheh", "saat"]
    ]
    data = {
        "long_form_title": "RBI ka naya UPI rule",
        "long_form_summary_hinglish": "RBI ne tax payments ke liye UPI limit badha di hai.",
        "short_01_title": "UPI limit badh gayi! #Shorts",
        "short_01_hook_hinglish": "Agar aap UPI se tax bharte ho, to yeh suno...",
        "short_01_script_hinglish": "Agar aap UPI se tax bharte ho, to yeh suno... RBI ne limit 5 lakh kar di hai, pehle 1 lakh thi.",
        "short_01_suspense_ending": "Aap kya sochte ho?",
        "claims": [
            {"claim": "Limit raised to 5 lakh", "source_quote": "raised the per-transaction UPI limit for tax payments to Rs 5 lakh from Rs 1 lakh"},
            {"claim": "Effective immediately", "source_quote": "The change takes effect immediately for all member banks"},
        ],
        "scene_cues": cues,
    }
    data.update(overrides)
    return data


def test_extract_numbers_normalises_separators():
    assert extract_numbers("Rs 1,00,000 and 2.5% by 2026.") == {"100000", "2.5", "2026"}


def test_grounded_script_passes():
    assert verify_grounding(_script(), SOURCE, TITLE) == []


def test_invented_figure_is_caught():
    bad = _script(short_01_script_hinglish="RBI ne limit 5 lakh kar di... aur 90% log yeh nahi jaante!")
    problems = verify_grounding(bad, SOURCE, TITLE)
    assert any("90" in p for p in problems)


def test_invented_figure_in_overlay_is_caught():
    bad = _script()
    bad["scene_cues"][0]["overlay_text"] = "10 Lakh Limit"
    assert any("10" in p for p in verify_grounding(bad, SOURCE, TITLE))


def test_fabricated_quote_is_caught():
    bad = _script(claims=[
        {"claim": "x", "source_quote": "RBI will fine every bank that fails to comply with this rule"},
        {"claim": "y", "source_quote": "The change takes effect immediately for all member banks"},
    ])
    problems = verify_grounding(bad, SOURCE, TITLE)
    assert any("not verbatim" in p for p in problems)
    assert any("only 1 verified claims" in p for p in problems)


def test_missing_claims_fail():
    assert verify_grounding(_script(claims=[]), SOURCE, TITLE)


def test_quote_match_ignores_case_and_punctuation():
    ok = _script(claims=[
        {"claim": "x", "source_quote": "the change takes effect IMMEDIATELY, for all member banks."},
        {"claim": "y", "source_quote": "Banks have been told to update their systems"},
    ])
    assert verify_grounding(ok, SOURCE, TITLE) == []


def _topic(**kw):
    base = {"title": TITLE, "category": "finance", "source": "test", "source_url": "https://x.test/a", "source_context": SOURCE}
    base.update(kw)
    return base


def test_package_requires_source_material():
    director = ScriptDirector()
    with pytest.raises(ScriptGroundingError):
        director.build_transformative_package(_topic(source_context=""))


def test_package_built_when_script_is_grounded(monkeypatch):
    director = ScriptDirector()
    prompts = []
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: prompts.append(prompt) or _script())
    pkg = director.build_transformative_package(_topic())
    assert pkg.grounded is True
    assert len(pkg.verified_claims) == 2
    assert SOURCE in prompts[0]                      # the article text reaches the model
    assert director.memory.is_topic_seen(TITLE)[0]   # recorded for de-duplication


def test_failed_fact_check_retries_with_feedback_then_passes(monkeypatch):
    director = ScriptDirector()
    bad = _script(short_01_script_hinglish="RBI ne limit 5 lakh kar di... 90% log nahi jaante!")
    answers = [bad, _script()]
    prompts = []

    def fake_llm(prompt):
        prompts.append(prompt)
        return answers.pop(0)

    monkeypatch.setattr(director, "call_live_llm", fake_llm)
    pkg = director.build_transformative_package(_topic())
    assert pkg.grounded
    assert "REJECTED BY THE FACT-CHECKER" in prompts[1] and "90" in prompts[1]


def test_script_that_keeps_failing_is_rejected_and_not_recorded(monkeypatch):
    director = ScriptDirector()
    bad = _script(short_01_script_hinglish="RBI ne 90% accounts freeze kar diye!")
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: bad)
    with pytest.raises(ScriptGroundingError):
        director.build_transformative_package(_topic())
    assert director.memory.get_published_count() == 0


# ── Affiliate links ───────────────────────────────────────────────────

def test_no_hardcoded_affiliate_urls_in_module():
    source = open(script_director.__file__, encoding="utf-8").read()
    assert "amzn.to" not in source


def test_placeholder_and_empty_affiliate_values_are_ignored(monkeypatch):
    monkeypatch.setenv("AFFILIATE_TECH_URL", "https://amzn.to/your-tech-tag")
    monkeypatch.setenv("AFFILIATE_BOOKS_URL", "")
    assert load_affiliate_links("technology") == []


def test_real_affiliate_value_is_used(monkeypatch):
    monkeypatch.setenv("AFFILIATE_TECH_URL", "https://amzn.to/3abcDEF")
    links = load_affiliate_links("technology")
    assert links == [{"title": "Tech gear I use", "url": "https://amzn.to/3abcDEF"}]


def test_short_description_and_comment_carry_no_urls(monkeypatch):
    monkeypatch.setenv("AFFILIATE_BROKER_URL", "https://broker.test/ref/anand")
    director = ScriptDirector()
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: _script())
    pkg = director.build_transformative_package(_topic())
    assert pkg.affiliate_links and "http" not in pkg.short_description and "http" not in pkg.top_comment
    assert "channel profile" in pkg.short_description
    assert "https://broker.test/ref/anand" in pkg.long_form_16x9["description"]
    assert "http" not in build_short_description("hook", "finance", has_affiliates=False)


# ── Voice track, series, learned length ───────────────────────────────

from agents.script_director import series_for_category, validate_tts_text


def test_voice_track_must_not_say_more_than_its_caption():
    caption = "RBI ne limit 5 lakh kar di hai"
    assert validate_tts_text("RBI ने limit 5 लाख कर दी है", caption)
    assert validate_tts_text("RBI ने limit ५ लाख कर दी है", caption)                 # Devanagari digit, same figure
    assert not validate_tts_text("RBI ने limit 10 लाख कर दी है", caption)             # a figure the caption lacks
    assert not validate_tts_text("RBI ने limit 5 लाख कर दी है guaranteed", caption)   # an English word the caption lacks
    assert not validate_tts_text("RBI ne limit 5 lakh kar di hai", caption)           # no Devanagari: nothing gained
    assert not validate_tts_text("RBI ने", caption)                                   # far too short
    assert not validate_tts_text("", caption)


def test_package_keeps_valid_voice_lines_and_drops_bad_ones(monkeypatch):
    data = _script()
    data["scene_cues"][0].update(narration_cue="RBI ne limit 5 lakh kar di hai", tts_text="RBI ने limit 5 लाख कर दी है", role="hook")
    data["scene_cues"][1].update(narration_cue="Pehle yeh 1 lakh thi", tts_text="पहले यह 90 लाख थी", role="reveal")
    director = ScriptDirector()
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: data)
    cards = director.build_transformative_package(_topic()).shorts_9x16_variants[0]["shot_cards"]
    assert cards[0]["tts_text"] == "RBI ने limit 5 लाख कर दी है" and cards[0]["role"] == "hook"
    assert cards[1]["tts_text"] == "" and cards[1]["role"] == "reveal"      # invented figure: the caption is spoken instead
    assert cards[-1]["role"] == "cta" and cards[2]["role"] == "build"       # unlabelled scenes get a role by position


TECH_SOURCE = (
    "Google on Tuesday released a new Gemini feature that can summarise long documents inside Google Docs. "
    "The feature is rolling out to free users in India first. It works in Hindi and English. "
    "Google said the tool can make mistakes with tables and asked users to check important figures themselves."
)
TECH_TITLE = "Google Gemini can now summarise documents in Docs"


def _tech_script(speakers=None):
    data = _script(
        short_01_title="Gemini ab documents padhega! #Shorts",
        short_01_hook_hinglish="Lambe documents padhne se thak gaye ho?",
        short_01_script_hinglish="Lambe documents padhne se thak gaye ho? Gemini ab Docs mein summary bana deta hai.",
        claims=[
            {"claim": "Summarises documents in Docs", "source_quote": "can summarise long documents inside Google Docs"},
            {"claim": "Hindi and English", "source_quote": "It works in Hindi and English"},
        ],
    )
    for cue, speaker in zip(data["scene_cues"], speakers or ["host"] * 7):
        cue["speaker"] = speaker
    return data


def _tech_topic(**kw):
    base = {"title": TECH_TITLE, "category": "technology", "source": "test", "source_url": "https://x.test/g", "source_context": TECH_SOURCE}
    base.update(kw)
    return base


def test_characters_format_and_learned_length_reach_the_prompt(monkeypatch):
    from agents import playbook

    pb = playbook.load_playbook()
    pb["pillars"]["technology"]["script_words_target"] = 65
    playbook.save_learned_state(pb)

    director = ScriptDirector()
    prompts = []
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: prompts.append(prompt) or _tech_script())
    pkg = director.build_transformative_package(_tech_topic())
    prompt = prompts[0]
    assert pkg.series_id == "ai_ki_duniya" and pkg.format_id == "jugaad_test"      # first format of the rotation
    assert 'HOST "Mighty"' in prompt and 'SIDEKICK "Shanky"' in prompt
    assert "Never give financial, investment, legal, medical or political advice" in prompt
    assert "FORMAT FOR THIS EPISODE (Comic dialogue)" in prompt
    assert '"speaker": "host" or "sidekick"' in prompt and "ALLOWED CHARACTER SPEAKERS" not in prompt
    assert "57-70 word" in prompt
    assert "#AIKiDuniya" in pkg.short_description and "AI Ki Duniya" in pkg.short_description
    assert '"tts_text"' in prompt and '"role"' in prompt


def test_formats_rotate_so_consecutive_shorts_differ(monkeypatch):
    from agents.script_director import pick_format

    series = series_for_category("technology")
    ids = [pick_format(series, i)["id"] for i in range(5)]
    assert ids == ["jugaad_test", "chai_explain", "myth_vs_sach", "kya_badla", "jugaad_test"]

    director = ScriptDirector()
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: _tech_script())
    first = director.build_transformative_package(_tech_topic())
    second = director.build_transformative_package(_tech_topic(title="A different Gemini headline about Docs"))
    assert (first.format_id, second.format_id) == ("jugaad_test", "chai_explain")


def test_dialogue_format_maps_host_and_sidekick_to_their_voices(monkeypatch):
    director = ScriptDirector()
    speakers = ["host", "sidekick", "host", "sidekick", "host", "sidekick", "host"]
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: _tech_script(speakers))
    cards = director.build_transformative_package(_tech_topic()).shorts_9x16_variants[0]["shot_cards"]
    assert [c["speaker"] for c in cards] == ["narrator", "skeptic_friend"] * 3 + ["narrator"]


def test_dialogue_format_is_made_a_dialogue_when_the_llm_uses_one_voice(monkeypatch):
    director = ScriptDirector()
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: _tech_script())
    cards = director.build_transformative_package(_tech_topic()).shorts_9x16_variants[0]["shot_cards"]
    assert [c["speaker"] for c in cards].count("skeptic_friend") == 2 and cards[0]["speaker"] == "narrator"


def test_solo_format_keeps_every_line_with_the_host(monkeypatch):
    from agents.script_director import pick_format

    director = ScriptDirector()
    solo = pick_format(series_for_category("technology"), 1)          # chai_explain
    monkeypatch.setattr(director, "call_live_llm", lambda prompt: _tech_script(["host", "sidekick"] * 3 + ["host"]))
    pkg = director.build_transformative_package(_tech_topic(format=solo))
    assert pkg.format_id == "chai_explain"
    assert {c["speaker"] for c in pkg.shorts_9x16_variants[0]["shot_cards"]} == {"narrator"}


def test_series_lookup_and_pillars():
    from agents.script_director import enabled_pillars

    assert series_for_category("technology")["id"] == "ai_ki_duniya"
    assert series_for_category("finance") is None
    assert enabled_pillars() == ["technology"]
