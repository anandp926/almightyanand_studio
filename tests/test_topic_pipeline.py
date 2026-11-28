import pytest

from agents import topic_pipeline
from agents.script_director import ScriptGroundingError
from agents.topic_pipeline import NoPublishableTopicError, select_grounded_package
from agents.trend_scout import NoFreshTopicError, TopicCandidate, TrendScout


class FakeMemory:
    def __init__(self):
        self.recorded = []

    def record_topic(self, **kw):
        self.recorded.append(kw)


class FakeDirector:
    def __init__(self, fail_titles=()):
        self.fail_titles = set(fail_titles)
        self.memory = FakeMemory()
        self.calls = []

    def build_transformative_package(self, topic_data, chosen_style="x", allow_ungrounded=False):
        self.calls.append((topic_data, allow_ungrounded))
        if topic_data["title"] in self.fail_titles:
            raise ScriptGroundingError("fact-check failed")
        return {"pkg_for": topic_data["title"], "context": topic_data["source_context"]}


class FakeScout:
    match_pillar = staticmethod(TrendScout.match_pillar)

    def __init__(self, candidates=None, error=None):
        self._candidates, self._error = candidates or [], error

    def discover_live_candidates(self, limit=None):
        if self._error:
            raise self._error
        return self._candidates


def _cand(title, url):
    return TopicCandidate(title=title, source="feed", source_url=url, category="technology", direct_link=True)


@pytest.fixture
def sources(monkeypatch):
    texts = {}
    monkeypatch.setattr(topic_pipeline, "build_source_context", lambda title, summary="", source_url="", fetch=True: texts.get(source_url, ""))
    return texts


def test_skips_ungroundable_and_rejected_candidates(sources):
    sources["https://a.test"] = "short"           # not enough text
    sources["https://b.test"] = "b" * 900         # enough text, but fails the fact-check
    sources["https://c.test"] = "c" * 900         # good
    scout = FakeScout([_cand("A", "https://a.test"), _cand("B", "https://b.test"), _cand("C", "https://c.test")])
    director = FakeDirector(fail_titles={"B"})

    candidate, pkg = select_grounded_package(scout, director, style="commentary_broll")

    assert candidate.title == "C" and pkg["pkg_for"] == "C"
    assert [c[0]["title"] for c in director.calls] == ["B", "C"]          # A never reached the LLM
    assert [r["title"] for r in director.memory.recorded] == ["B"]        # B is remembered as rejected
    assert all(allow is False for _, allow in director.calls)


def test_nothing_groundable_means_no_video(sources):
    scout = FakeScout([_cand("A", "https://a.test")])
    with pytest.raises(NoPublishableTopicError):
        select_grounded_package(scout, FakeDirector(), style="s")


def test_empty_scout_means_no_video(sources):
    with pytest.raises(NoPublishableTopicError):
        select_grounded_package(FakeScout(error=NoFreshTopicError("none")), FakeDirector(), style="s")


def test_manual_topic_needs_a_source(sources):
    with pytest.raises(NoPublishableTopicError):
        select_grounded_package(FakeScout(), FakeDirector(), style="s", override_topic="My topic")


def test_manual_topic_with_source_is_grounded(sources):
    sources["https://m.test"] = "m" * 900
    director = FakeDirector()
    candidate, pkg = select_grounded_package(
        FakeScout(), director, style="s", override_topic="RBI UPI update", source_url="https://m.test"
    )
    assert candidate.source == "manual_override" and candidate.category == "finance"
    assert pkg["context"] == "m" * 900
