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


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str
    category: str
    source_class: str = PUBLISHER
    poll_minutes: int = 15
    region: str | None = None  # a region code, for categories split by region
