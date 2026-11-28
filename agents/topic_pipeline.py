#!/usr/bin/env python3
"""
topic_pipeline.py — Topic selection + grounded scripting, shared by the
orchestrator and the CEO planner.

Walks the ranked candidates until one has enough source text and yields a
script that passes the fact-check. Raises NoPublishableTopicError when none
does; callers skip the cycle rather than publish something unverified.
"""

import logging
from typing import Optional, Tuple

from agents.script_director import ProductionScriptPackage, ScriptDirector, ScriptGroundingError
from agents.settings import env_int
from agents.source_fetcher import build_source_context, is_sufficient, min_source_chars
from agents.trend_scout import NoFreshTopicError, TopicCandidate, TrendScout

log = logging.getLogger("topic_pipeline")


class NoPublishableTopicError(RuntimeError):
    """No candidate could be grounded and scripted this cycle."""


def _manual_candidate(scout: TrendScout, title: str, source_url: Optional[str]) -> TopicCandidate:
    category, _ = scout.match_pillar(title)
    return TopicCandidate(
        title=title,
        source="manual_override",
        source_url=source_url or "",
        category=category or "technology",
        direct_link=bool(source_url),
    )


def select_grounded_package(
    scout: TrendScout,
    director: ScriptDirector,
    style: str,
    override_topic: Optional[str] = None,
    source_url: Optional[str] = None,
    allow_ungrounded: bool = False,
    prefer_category: Optional[str] = None,
) -> Tuple[TopicCandidate, ProductionScriptPackage]:
    """
    Returns (candidate, script package).

    Manual runs (`override_topic`) need `source_url`, or `allow_ungrounded=True`
    for a deliberately generic, figure-free script. Automated runs never pass
    `allow_ungrounded`.

    `prefer_category` moves candidates of that pillar to the front (used to
    rotate series across the day's slots); other pillars remain as fallback.
    """
    max_fetches = env_int("MAX_SOURCE_FETCHES", 12)
    max_script_attempts = env_int("MAX_SCRIPT_CANDIDATES", 3)

    if override_topic:
        candidate = _manual_candidate(scout, override_topic, source_url)
        context = build_source_context(candidate.title, source_url=candidate.source_url) if source_url else ""
        if source_url and not is_sufficient(context):
            raise NoPublishableTopicError(
                f"Only {len(context)} chars of text at {source_url}; need {min_source_chars()}."
            )
        if not context and not allow_ungrounded:
            raise NoPublishableTopicError(
                "A manual topic needs --source-url so the script can be fact-checked "
                "(or --allow-ungrounded for a generic, figure-free script)."
            )
        pkg = director.build_transformative_package(
            _topic_data(candidate, context), chosen_style=style, allow_ungrounded=allow_ungrounded
        )
        return candidate, pkg

    try:
        candidates = scout.discover_live_candidates()
    except NoFreshTopicError as e:
        raise NoPublishableTopicError(str(e)) from e

    if prefer_category:
        # Stable: keeps the scout's ranking inside each group.
        candidates = sorted(candidates, key=lambda c: c.category != prefer_category)

    fetches = 0
    script_attempts = 0
    for candidate in candidates:
        if fetches >= max_fetches or script_attempts >= max_script_attempts:
            break
        fetches += 1
        context = build_source_context(candidate.title, candidate.summary, candidate.source_url)
        if not is_sufficient(context):
            log.info(f"⏭ Not enough source text ({len(context)} chars) for '{candidate.title}'")
            continue

        script_attempts += 1
        try:
            pkg = director.build_transformative_package(_topic_data(candidate, context), chosen_style=style)
        except ScriptGroundingError as e:
            log.warning(f"⏭ {e}")
            # Remember the topic so it is not re-scripted (and re-rejected) every cycle.
            director.memory.record_topic(
                title=candidate.title,
                source=candidate.source,
                source_url=candidate.source_url,
                category=candidate.category,
                angle="rejected",
                visual_style=style,
                status="rejected_fact_check",
            )
            continue
        log.info(f"✨ Topic locked: '{candidate.title}' ({candidate.source})")
        return candidate, pkg

    raise NoPublishableTopicError(
        f"No candidate passed grounding ({len(candidates)} fresh, {fetches} sources fetched, "
        f"{script_attempts} scripts attempted)."
    )


def _topic_data(candidate: TopicCandidate, context: str) -> dict:
    return {
        "title": candidate.title,
        "category": candidate.category,
        "source": candidate.source,
        "source_url": candidate.source_url,
        "source_context": context,
    }
