"""Trending stories: cluster the window's headlines into multi-source events.

With ANTHROPIC_API_KEY (or another Anthropic credential) set, Claude clusters
and ranks the stories and writes a short brief for each. Without one, a
keyword-overlap heuristic produces the same shape so the tab still works.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .feeds import CATEGORIES, PUBLISHER
from .items import Item
from .store import Store

log = logging.getLogger(__name__)

MODEL = os.environ.get("NEWSFEED_MODEL", "claude-opus-5-5")
MAX_INPUT_ITEMS = int(os.environ.get("NEWSFEED_TRENDING_MAX_ITEMS", "400"))
MIN_INTERVAL = int(os.environ.get("NEWSFEED_TRENDING_MINUTES", "30")) * 60
MAX_STORIES = 12

Category = Literal["world", "ai", "tech", "security"]


class TrendingStory(BaseModel):
    headline: str = Field(description="Neutral, specific headline for the event, under 90 characters")
    summary: str = Field(description="Two or three sentences on what happened, drawn only from the listed items")
    why_trending: str = Field(description="One sentence on why this is gaining attention (breadth of coverage, escalation, impact)")
    category: Category
    item_ids: list[int] = Field(description="Item numbers from the input that cover this event")
    momentum: int = Field(description="1-100: how strongly this story is trending right now")


class TrendingResult(BaseModel):
    stories: list[TrendingStory]


SYSTEM_PROMPT = f"""You are the trending-desk editor for a news aggregator that shows the last 72 hours of \
headlines from {len(CATEGORIES)} sections: world & politics, AI, technology, and security.

You receive numbered items (age in hours, source, source class, section, title, snippet). Identify the \
events that are trending: the same underlying story covered by several independent outlets, a story \
escalating across recent hours, or a single report with clearly outsized impact (an actively exploited \
zero-day, a major model release, a war or election development).

Group items about the same event together, even when headlines are worded differently. Rank by momentum: \
independent coverage counts most, then recency, then impact. Items whose source class is official-lab, \
vendor-security, government-advisory or institutional are first-party: they make a story more \
authoritative but do not count as independent corroboration.

Return at most {MAX_STORIES} stories, strongest first, spread across sections where the news supports it. \
Write headlines and summaries only from what the items say; do not add facts, figures or speculation. \
Skip routine items that nothing else echoes."""


def _render_items(items: list[Item], now: float) -> str:
    lines = []
    for n, item in enumerate(items):
        age = (now - item.published) / 3600
        snippet = item.description[:160]
        lines.append(f"[{n}] {age:.0f}h | {item.source} ({item.source_class}) | {item.category} | {item.title} — {snippet}")
    return "\n".join(lines)


def _story_payload(headline: str, summary: str, why: str, category: str, momentum: int,
                   members: list[Item]) -> dict:
    members = sorted(members, key=lambda i: -i.published)
    independent = {i.source for i in members if i.source_class == PUBLISHER}
    return {
        "headline": headline,
        "summary": summary,
        "why_trending": why,
        "category": category,
        "momentum": max(1, min(100, momentum)),
        "image": next((i.image for i in members if i.image), None),
        "source_count": len({i.source for i in members}),
        "independent_sources": len(independent),
        "latest": members[0].published,
        "items": [{"id": i.id, "title": i.title, "url": i.url, "source": i.source,
                   "source_class": i.source_class, "published": i.published} for i in members],
    }


async def claude_trending(items: list[Item], now: float, client: anthropic.AsyncAnthropic) -> list[dict]:
    response = await client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Current items ({len(items)}):\n\n{_render_items(items, now)}"}],
        output_format=TrendingResult,
        output_config={"effort": "medium"},
        # Re-run on a substitute model if a safety classifier declines (e.g. on
        # cyber-heavy security headlines) instead of returning nothing.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"trending request ended with stop_reason={response.stop_reason}")

    stories = []
    for story in response.parsed_output.stories[:MAX_STORIES]:
        members = [items[n] for n in dict.fromkeys(story.item_ids) if 0 <= n < len(items)]
        if members:
            stories.append(_story_payload(story.headline, story.summary, story.why_trending,
                                          story.category, story.momentum, members))
    stories.sort(key=lambda s: -s["momentum"])
    return stories


# -- heuristic fallback -----------------------------------------------------

_STOP = set("""a an the and or but of to in on for with at by from as is are was were be been has have had
it its this that these those after over into amid about says say said new will can could would may might
not no more than up out off what who how why when where which their his her they them we you your our us
first last year years week day days report reports update updates live latest news""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-']{2,}")


def _tokens(item: Item) -> set[str]:
    return {w.lower().strip("'") for w in _WORD.findall(item.title)} - _STOP


def heuristic_trending(items: list[Item], now: float) -> list[dict]:
    """Greedy keyword-overlap clustering; keeps clusters covered by 2+ sources."""
    doc_freq = Counter(t for i in items for t in _tokens(i))
    clusters: list[tuple[set[str], list[Item]]] = []
    for item in sorted(items, key=lambda i: -i.published):
        toks = {t for t in _tokens(item) if doc_freq[t] < max(8, len(items) // 10)}
        if not toks:
            continue
        best, best_score = None, 0.0
        for cluster in clusters:
            shared = toks & cluster[0]
            score = len(shared) / min(len(toks), len(cluster[0]))
            if len(shared) >= 2 and score > best_score:
                best, best_score = cluster, score
        if best and best_score >= 0.34:
            best[0].update(toks)
            best[1].append(item)
        else:
            clusters.append((set(toks), [item]))

    stories = []
    for _, members in clusters:
        sources = {i.source for i in members}
        if len(sources) < 2:
            continue
        newest = max(i.published for i in members)
        recency = max(0.0, 1 - (now - newest) / (72 * 3600))
        momentum = int(min(100, 18 * len(sources) + 4 * len(members) + 30 * recency))
        lead = min(members, key=lambda i: (i.source_class != PUBLISHER, -len(i.description)))
        category = Counter(i.category for i in members).most_common(1)[0][0]
        stories.append(_story_payload(
            lead.title, lead.description,
            f"Covered by {len(sources)} sources across {len(members)} stories.",
            category, momentum, members))
    stories.sort(key=lambda s: -s["momentum"])
    return stories[:MAX_STORIES]


class TrendingEngine:
    def __init__(self, store: Store):
        self.store = store
        self.stories: list[dict] = []
        self.mode = "none"  # "claude" | "heuristic" | "none"
        self.generated_at: float | None = None
        self.error: str | None = None
        self.running = False
        self._version = -1
        self._client: anthropic.AsyncAnthropic | None = None
        self._ai_disabled = os.environ.get("NEWSFEED_DISABLE_AI") == "1"

    def _get_client(self) -> anthropic.AsyncAnthropic | None:
        if self._ai_disabled:
            return None
        if self._client is None:
            client = anthropic.AsyncAnthropic()
            # Constructing succeeds without credentials; the first call would fail.
            if client.api_key or client.auth_token or client.credentials:
                self._client = client
            else:
                log.info("no Anthropic credentials found; trending uses the keyword heuristic")
                self._ai_disabled = True
        return self._client

    def stale(self) -> bool:
        if self.store.version == self._version:
            return False
        return self.generated_at is None or time.time() - self.generated_at >= MIN_INTERVAL

    async def update(self, force: bool = False) -> None:
        if self.running or not (force or self.stale()):
            return
        self.running = True
        version = self.store.version
        now = time.time()
        items = self.store.query(now=now)[:MAX_INPUT_ITEMS]
        try:
            client = self._get_client()
            if client and items:
                try:
                    self.stories = await claude_trending(items, now, client)
                    self.mode, self.error = "claude", None
                except (anthropic.AnthropicError, RuntimeError) as exc:
                    log.warning("Claude trending failed, using heuristic: %s", exc)
                    self.error = str(exc)[:300]
                    self.stories, self.mode = heuristic_trending(items, now), "heuristic"
            else:
                self.stories, self.mode = heuristic_trending(items, now), "heuristic"
            self.generated_at, self._version = time.time(), version
        finally:
            self.running = False

    def snapshot(self) -> dict:
        return {"mode": self.mode, "model": MODEL if self.mode == "claude" else None,
                "generated_at": self.generated_at, "running": self.running,
                "error": self.error, "stories": self.stories}
