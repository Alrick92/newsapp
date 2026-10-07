"""Trending stories: cluster the window's headlines into multi-source events.

An AI provider (Claude, an OpenAI-compatible endpoint, or Ollama; see ai.py)
clusters and ranks the stories and writes a short brief for each. With no
provider configured, or when a request fails, a keyword-overlap heuristic
produces the same shape so the tab still works.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIError, Provider, provider_from_env
from .feeds import CATEGORIES, PUBLISHER
from .items import Item
from .store import DISPLAY_HOURS, Store

log = logging.getLogger(__name__)

# AI requests cost money, so at most one per AI_INTERVAL, whatever triggers it
# (schedule, Regenerate button, feed refresh). Keyword clustering is free and
# keeps the shorter HEURISTIC_INTERVAL.
AI_INTERVAL = float(os.environ.get("NEWSFEED_AI_REFRESH_HOURS") or 4) * 3600
HEURISTIC_INTERVAL = int(os.environ.get("NEWSFEED_TRENDING_MINUTES") or 30) * 60
MAX_STORIES = 12

Category = Literal["world", "ai", "tech", "security"]


class TrendingStory(BaseModel):
    # extra="forbid" emits additionalProperties: false, which strict JSON-schema modes require.
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(description="Neutral, specific headline for the event, under 90 characters")
    summary: str = Field(description="Two or three sentences on what happened, drawn only from the listed items")
    why_trending: str = Field(description="One sentence on why this is gaining attention (breadth of coverage, escalation, impact)")
    category: Category
    item_ids: list[int] = Field(description="Item numbers from the input that cover this event")
    momentum: int = Field(description="1-100: how strongly this story is trending right now")


class TrendingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


async def ai_trending(items: list[Item], now: float, provider: Provider) -> list[dict]:
    """Ask the provider to cluster `items`; map its item numbers back to stories."""
    user = f"Current items ({len(items)}):\n\n{_render_items(items, now)}"
    result = await provider.generate(SYSTEM_PROMPT, user, TrendingResult)
    stories = []
    for story in result.stories[:MAX_STORIES]:
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
            "",  # no reason to give beyond the source count the card already shows
            category, momentum, members))
    stories.sort(key=lambda s: -s["momentum"])
    return stories[:MAX_STORIES]


class TrendingEngine:
    def __init__(self, store: Store, provider: Provider | None = None):
        self.store = store
        self.stories: list[dict] = []
        self.mode = "none"  # "ai" | "heuristic" | "none"
        self.provider_name: str | None = None
        self.model: str | None = None
        self.generated_at: float | None = None
        self.error: str | None = None
        self.running = False
        self._version = -1
        self._provider = provider
        self._resolved = provider is not None
        self._config_error: str | None = None
        # {"at": ts, "provider": name, "model": model} of the last AI request sent,
        # successful or not; persisted so a restart can't skip the wait.
        self._last_ai = store.get_meta("trending_ai_last_request")
        saved = store.get_meta("trending")
        if saved:  # survive restarts without paying for a fresh AI call
            self.stories, self.generated_at, self.error = saved["stories"], saved["generated_at"], saved.get("error")
            self.mode = "ai" if saved["mode"] in ("ai", "claude") else saved["mode"]
            self.provider_name = saved.get("provider") or ("anthropic" if saved["mode"] == "claude" else None)
            self.model = saved.get("model")
            self._version = store.version

    def _get_provider(self) -> Provider | None:
        """Resolve NEWSFEED_AI_PROVIDER once; a misconfiguration is reported, not raised."""
        if not self._resolved:
            self._resolved = True
            try:
                self._provider = provider_from_env()
                if self._provider:
                    log.info("trending uses %s (%s)", self._provider.name, self._provider.model)
            except AIError as exc:
                log.error("AI provider misconfigured, using keyword clustering: %s", exc)
                self._config_error = str(exc)
        return self._provider

    def next_ai_at(self) -> float | None:
        """When the next AI request may be sent; None if one may be sent now (or no AI is configured)."""
        provider = self._get_provider()
        last = self._last_ai
        if not provider or not last:
            return None
        if (last.get("provider"), last.get("model")) != (provider.name, provider.model):
            return None  # provider or model changed (e.g. a fixed config): don't make it wait
        due = last["at"] + AI_INTERVAL
        return due if due > time.time() else None

    def stale(self) -> bool:
        if self.store.version == self._version:
            return False
        if self._get_provider():
            return self.next_ai_at() is None
        return self.generated_at is None or time.time() - self.generated_at >= HEURISTIC_INTERVAL

    async def update(self, force: bool = False) -> bool:
        """Regenerate if due. `force` skips the new-stories check but never the AI throttle.

        Returns False when nothing ran (already running, throttled, or not due).
        """
        if self.running:
            return False
        provider = self._get_provider()
        if provider and self.next_ai_at() is not None:
            return False
        if not (force or self.stale()):
            return False
        self.running = True
        version = self.store.version
        now = time.time()
        limit = provider.max_items if provider else 400
        items = self.store.query(now=now, hours=DISPLAY_HOURS, limit=limit)
        try:
            self.error = self._config_error
            self.provider_name = self.model = None
            if provider and items:
                self._last_ai = {"at": now, "provider": provider.name, "model": provider.model}
                self.store.set_meta("trending_ai_last_request", self._last_ai)
                try:
                    self.stories = await ai_trending(items, now, provider)
                    self.mode, self.provider_name, self.model = "ai", provider.name, provider.model
                except AIError as exc:
                    log.warning("%s trending failed, using keyword clustering: %s", provider.name, exc)
                    self.error = f"{provider.name}: {exc}"[:300]
                    self.stories, self.mode = heuristic_trending(items, now), "heuristic"
            else:
                self.stories, self.mode = heuristic_trending(items, now), "heuristic"
            self.generated_at, self._version = time.time(), version
            self.store.set_meta("trending", {"stories": self.stories, "mode": self.mode,
                                             "provider": self.provider_name, "model": self.model,
                                             "generated_at": self.generated_at, "error": self.error})
            return True
        finally:
            self.running = False

    def snapshot(self) -> dict:
        return {"mode": self.mode, "provider": self.provider_name, "model": self.model,
                "generated_at": self.generated_at, "running": self.running,
                "error": self.error, "stories": self.stories,
                "ai_enabled": self._get_provider() is not None,
                "ai_refresh_hours": AI_INTERVAL / 3600,
                "next_ai_refresh_at": self.next_ai_at()}
