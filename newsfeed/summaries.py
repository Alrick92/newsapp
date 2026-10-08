"""Per-article summaries from the configured AI provider.

Only the text the feed itself provides is summarized; the app never fetches
the article page. A summary is saved with the item, so asking again costs
nothing, and new summaries are limited to SUMMARIES_PER_HOUR.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIError, Provider
from .store import Store

# Feeds that only carry a one-line teaser have nothing worth summarizing.
SUMMARY_MIN_CHARS = int(os.environ.get("NEWSFEED_SUMMARY_MIN_CHARS") or 600)
SUMMARIES_PER_HOUR = int(os.environ.get("NEWSFEED_SUMMARIES_PER_HOUR") or 30)
INPUT_LIMIT = 12_000  # characters of feed text sent per request

SYSTEM_PROMPT = """You summarize news articles for a personal news reader. Use only the text you are given; \
do not add facts, context or opinions that aren't in it. Write a neutral summary of two to four sentences \
that covers who did what, when, and why it matters, in plain language."""


class ArticleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="Two to four sentences summarizing the article, using only the given text")


class SummaryError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Summarizer:
    def __init__(self, store: Store, provider: Callable[[], Provider | None]):
        self.store = store
        self._provider = provider
        self._recent: deque[float] = deque()  # times of recent AI requests, for the hourly limit

    @property
    def enabled(self) -> bool:
        return self._provider() is not None

    def can_summarize(self, content_len: int) -> bool:
        return self.enabled and content_len >= SUMMARY_MIN_CHARS

    async def summarize(self, item_id: str) -> tuple[str, bool]:
        """Return (summary, cached)."""
        provider = self._provider()
        if provider is None:
            raise SummaryError(404, "Summaries are off: no AI provider is configured.")
        item = self.store.get(item_id)
        if item is None:
            raise SummaryError(404, "That story is no longer stored.")
        if item.summary:
            return item.summary, True
        if len(item.content) < SUMMARY_MIN_CHARS:
            raise SummaryError(422, "This feed only provides a short excerpt, so there's nothing to summarize.")

        now = time.time()
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        if len(self._recent) >= SUMMARIES_PER_HOUR:
            wait = int(3600 - (now - self._recent[0])) // 60 + 1
            raise SummaryError(429, f"Summary limit reached ({SUMMARIES_PER_HOUR} per hour). Try again in {wait} min.")
        self._recent.append(now)

        user = f"Title: {item.title}\nSource: {item.source}\n\n{item.content[:INPUT_LIMIT]}"
        try:
            result = await provider.generate(SYSTEM_PROMPT, user, ArticleSummary)
        except AIError as exc:
            raise SummaryError(502, f"The AI provider couldn't summarize this: {exc}") from exc
        summary = result.summary.strip()
        self.store.save_summary(item_id, summary, f"{provider.name}:{provider.model}")
        return summary, False
