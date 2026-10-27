"""Compare coverage: how left, center and right outlets frame the same story.

The AI provider gets each side's headlines and feed snippets separately (the
side comes from each feed's nf:bias rating; unrated sources are left out) and
describes differences in emphasis and wording, using only that text. A
comparison is saved per set of articles, so asking again is free until more
coverage arrives, and new comparisons are limited to COMPARISONS_PER_HOUR.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections import deque
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIError, Provider
from .bias import SIDE_OF, SIDES
from .model import Feed
from .store import Store

COMPARISONS_PER_HOUR = int(os.environ.get("NEWSFEED_COMPARISONS_PER_HOUR") or 20)
MAX_ITEMS = 40  # articles accepted per request
PER_SIDE = 12  # articles sent to the provider per side

SYSTEM_PROMPT = """You compare how news outlets across the political spectrum cover one story, for a \
personal news reader. You receive the story's headlines and feed snippets grouped by the outlets' lean \
(left, center, right). Use only that text: do not add facts, context or outside knowledge about the \
outlets. Describe framing, not truth: what each side emphasizes, which facts or voices it leads with, \
and notable word choices (quote short phrases from the headlines). Stay neutral and specific; don't say \
which side is right, and don't call coverage biased. When a side has no articles, return an empty \
string for it. Keep each part to one to three sentences."""


class CoverageComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    common: str = Field(description="What outlets on every side report alike, in one or two sentences")
    left: str = Field(description="How left-leaning outlets frame it; empty if none are listed")
    center: str = Field(description="How center outlets frame it; empty if none are listed")
    right: str = Field(description="How right-leaning outlets frame it; empty if none are listed")
    differences: list[str] = Field(description="Two to four specific differences in emphasis or wording")


class CompareError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def sides_of(items: list, feeds: Mapping[str, Feed]) -> dict[str, list[dict]]:
    """Articles per side (each source once per side; extra feeds carrying an item count too)."""
    out: dict[str, list[dict]] = {side: [] for side in SIDES}
    seen: set[tuple[str, str]] = set()
    for item in items:
        carriers = [(item.source_id, item.source), *((a["source_id"], a["source"]) for a in item.also)]
        for source_id, source in carriers:
            feed = feeds.get(source_id)
            if not feed or not feed.bias or (source_id, item.id) in seen:
                continue
            seen.add((source_id, item.id))
            out[SIDE_OF[feed.bias]].append({"source": source, "title": item.title, "snippet": item.description})
    return out


def render(sides: dict[str, list[dict]]) -> str:
    parts = []
    for side in SIDES:
        rows = sides[side][:PER_SIDE]
        lines = [f"- {r['source']}: {r['title']}" + (f" — {r['snippet'][:240]}" if r["snippet"] else "") for r in rows]
        parts.append(f"{side.upper()} ({len(sides[side])} articles)\n" + ("\n".join(lines) or "(none)"))
    return "\n\n".join(parts)


class Comparer:
    def __init__(self, store: Store, provider: Callable[[], Provider | None],
                 feeds: Callable[[], Mapping[str, Feed]]):
        self.store = store
        self._provider = provider
        self._feeds = feeds
        self._recent: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return self._provider() is not None

    async def compare(self, ids: list[str]) -> tuple[dict, bool]:
        """Return (comparison, cached) for the story made of these item ids."""
        provider = self._provider()
        if provider is None:
            raise CompareError(404, "Comparisons are off: no AI provider is configured.")
        items = self.store.items_by_id(list(dict.fromkeys(ids))[:MAX_ITEMS])
        if not items:
            raise CompareError(404, "Those stories are no longer stored.")
        sides = sides_of(items, self._feeds())
        covered = [side for side in SIDES if sides[side]]
        if len(covered) < 2:
            raise CompareError(422, "Only one side of the spectrum covers this story so far, so there's nothing to compare.")

        key = "compare:" + hashlib.sha1(",".join(sorted(i.id for i in items)).encode()).hexdigest()[:20]
        saved = self.store.get_meta(key)
        if saved:
            return saved["result"], True

        now = time.time()
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        if len(self._recent) >= COMPARISONS_PER_HOUR:
            wait = int(3600 - (now - self._recent[0])) // 60 + 1
            raise CompareError(429, f"Comparison limit reached ({COMPARISONS_PER_HOUR} per hour). Try again in {wait} min.")
        self._recent.append(now)

        try:
            result = await provider.generate(SYSTEM_PROMPT, render(sides), CoverageComparison)
        except AIError as exc:
            raise CompareError(502, f"The AI provider couldn't compare this coverage: {exc}") from exc
        out = {**result.model_dump(), "counts": {side: len(sides[side]) for side in SIDES},
               "model": f"{provider.name}:{provider.model}"}
        for side in SIDES:  # never let the model describe a side with no articles
            if not sides[side]:
                out[side] = ""
        self.store.set_meta(key, {"at": now, "result": out})
        return out, False
