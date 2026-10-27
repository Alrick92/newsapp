"""Coverage by political lean, from the `nf:bias` rating on each feed.

Ratings live in the OPML, not on stored items, so editing a rating changes
every coverage bar on the next request. A source counts once per story
however many of its articles the story has; unrated sources are counted
separately and never assigned a side.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from .model import BIASES, Feed

SIDES = ("left", "center", "right")
SIDE_OF = {"left": "left", "lean-left": "left", "center": "center", "lean-right": "right", "right": "right"}

# A story is a blindspot when at least BLINDSPOT_MIN_RATED rated sources cover
# it and at least BLINDSPOT_SHARE of them sit on one side, leaving the other
# side's readers likely to miss it.
BLINDSPOT_MIN_RATED = 3
BLINDSPOT_SHARE = 0.75


def tally(source_ids: Iterable[str], feeds: Mapping[str, Feed]) -> dict:
    """Counts per bias rating and per side for a set of feed ids (duplicates count once)."""
    ratings = Counter(feeds[sid].bias if sid in feeds else None for sid in set(source_ids))
    by_bias = {b: ratings.get(b, 0) for b in BIASES}
    sides = {side: 0 for side in SIDES}
    for bias, n in by_bias.items():
        sides[SIDE_OF[bias]] += n
    return {"by_bias": by_bias, "sides": sides, "rated": sum(by_bias.values()), "unrated": ratings.get(None, 0)}


def blindspot(sides: Mapping[str, int]) -> str | None:
    """The side that barely covers a story ("left" or "right"), or None.

    Only a lopsided split between left and right counts: a story covered
    mostly by center outlets isn't hidden from either side.
    """
    rated = sum(sides.values())
    if rated < BLINDSPOT_MIN_RATED:
        return None
    if sides["left"] >= BLINDSPOT_SHARE * rated:
        return "right"
    if sides["right"] >= BLINDSPOT_SHARE * rated:
        return "left"
    return None


def story_coverage(story: dict, feeds: Mapping[str, Feed]) -> dict:
    """The coverage block for one trending story (its links carry source_id)."""
    counts = tally((link.get("source_id") for link in story["items"]), feeds)
    return {**counts, "blindspot": blindspot(counts["sides"])}


def with_coverage(stories: list[dict], feeds: Mapping[str, Feed]) -> list[dict]:
    return [{**s, "coverage": story_coverage(s, feeds)} for s in stories]


def reading_profile(read: Mapping[str, int], published: Mapping[str, int], feeds: Mapping[str, Feed]) -> dict:
    """What you read vs what your feeds published, by side, from per-feed article counts."""
    def by_side(counts: Mapping[str, int]) -> dict:
        out = {side: 0 for side in SIDES} | {"unrated": 0}
        for sid, n in counts.items():
            bias = feeds[sid].bias if sid in feeds else None
            out[SIDE_OF[bias] if bias else "unrated"] += n
        return out

    return {"read": by_side(read), "published": by_side(published)}
