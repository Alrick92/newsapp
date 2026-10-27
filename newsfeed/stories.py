"""Story clusters: group every recent item with the others about the same event.

After each poll, items the pass hasn't seen yet are matched to existing
stories by headline keywords, using the same overlap rule as the keyword
fallback in trending.py; an item that matches nothing starts a new story.
Assignments are kept, so a story only grows: earlier groupings never move.
A story is identified by its first item's id (items.story_id).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from .store import Store
from .trending import _STOP, _WORD

log = logging.getLogger(__name__)

# Stories stay open to new items for this long after their first item.
CLUSTER_HOURS = 72
MIN_SHARED = 2  # keywords an item must share with a story
MIN_OVERLAP = 0.34  # shared keywords / the smaller keyword set


_SUFFIXES = ("ation", "ions", "ing", "ion", "ed", "es", "s")


def _stem(word: str) -> str:
    """Crude suffix stripping, so "exploited" and "exploitation" match."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def title_tokens(title: str) -> set[str]:
    return {_stem(w) for w in {w.lower().strip("'") for w in _WORD.findall(title)} - _STOP}


def cluster(store: Store, now: float | None = None) -> int:
    """Assign unclustered items in the window to stories; returns how many were assigned."""
    rows = store.cluster_window(CLUSTER_HOURS, now)
    if not any(r["story_id"] is None for r in rows):
        return 0
    tokens = {r["id"]: title_tokens(r["title"]) for r in rows}
    doc_freq = Counter(t for toks in tokens.values() for t in toks)
    common = max(8, len(rows) // 10)  # words in this many headlines say nothing about the event

    story_tokens: dict[str, set[str]] = defaultdict(set)
    postings: dict[str, set[str]] = defaultdict(set)  # keyword -> stories using it

    def join(story: str, toks: set[str]) -> None:
        story_tokens[story] |= toks
        for t in toks:
            postings[t].add(story)

    for r in rows:
        if r["story_id"] is not None:
            join(r["story_id"], {t for t in tokens[r["id"]] if doc_freq[t] < common})

    assigned: dict[str, str] = {}
    for r in rows:  # oldest first, so a story is named after its first item
        if r["story_id"] is not None:
            continue
        toks = {t for t in tokens[r["id"]] if doc_freq[t] < common}
        shared = Counter(s for t in toks for s in postings[t])
        best, best_score = None, 0.0
        for story, n in shared.items():
            score = n / min(len(toks), len(story_tokens[story]))
            if n >= MIN_SHARED and score > best_score:
                best, best_score = story, score
        story = best if best and best_score >= MIN_OVERLAP else r["id"]
        assigned[r["id"]] = story
        join(story, toks)

    store.set_story_ids(assigned)
    log.info("clustered %d new items into stories", len(assigned))
    return len(assigned)

