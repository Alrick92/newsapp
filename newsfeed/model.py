"""Feed definition and source classes, shared by the feed list and the OPML reader."""

from __future__ import annotations

from dataclasses import dataclass

# Source classes: first-party announcements are never counted as independent
# corroboration when ranking trending stories.
PUBLISHER = "publisher"
OFFICIAL_LAB = "official-lab"
VENDOR_SECURITY = "vendor-security"
GOVERNMENT = "government-advisory"
INSTITUTIONAL = "institutional"
COMMUNITY = "community/blog"

# Political lean of a source, left to right. Feeds without a rating are left
# out of coverage bars (Unrated), never guessed.
BIASES = ("left", "lean-left", "center", "lean-right", "right")
# How reliably a source reports facts, best first; optional, shown as a label.
FACTUALITY = ("very-high", "high", "mixed", "low")


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str
    category: str
    source_class: str = PUBLISHER
    poll_minutes: int = 15
    region: str | None = None  # a region code, for categories split by region
    bias: str | None = None  # one of BIASES, or None when unrated
    factuality: str | None = None  # one of FACTUALITY, or None
