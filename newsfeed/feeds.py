"""Feed catalogue.

Sources and polling tiers come from the "Best Open RSS Feeds" research brief:
the 15-feed production bundle plus the recommended add-ons. Each feed carries
a category (the UI tab it lands in) and a source class, so first-party
announcements are never counted as independent corroboration when ranking
trending stories.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = {
    "world": "World & Politics",
    "ai": "AI",
    "tech": "Technology",
    "security": "Security",
    "economy": "Economy",
}

# Source classes, per the brief's automation notes.
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


FEEDS: tuple[Feed, ...] = (
    # World & politics
    Feed("guardian-world", "The Guardian", "https://www.theguardian.com/world/rss", "world", poll_minutes=5),
    Feed("bbc-world", "BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml", "world", poll_minutes=5),
    Feed("aljazeera", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "world", poll_minutes=5),
    Feed("dw-world", "DW", "https://rss.dw.com/rdf/rss-en-world", "world", poll_minutes=5),
    Feed("france24", "France 24", "https://www.france24.com/en/monde/rss", "world", poll_minutes=5),
    Feed("npr-world", "NPR", "https://feeds.npr.org/1004/rss.xml", "world"),
    Feed("un-news", "UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "world", INSTITUTIONAL, 30),
    # AI
    Feed("techcrunch-ai", "TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", "ai"),
    Feed("ars-ai", "Ars Technica", "https://arstechnica.com/ai/feed/", "ai"),
    Feed("wired-ai", "WIRED", "https://www.wired.com/feed/tag/ai/latest/rss", "ai"),
    Feed("mit-tr-ai", "MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed", "ai", poll_minutes=30),
    Feed("openai", "OpenAI", "https://openai.com/news/rss.xml", "ai", OFFICIAL_LAB, 30),
    Feed("deepmind", "Google DeepMind", "https://deepmind.google/blog/rss.xml", "ai", OFFICIAL_LAB, 30),
    Feed("huggingface", "Hugging Face", "https://huggingface.co/blog/feed.xml", "ai", OFFICIAL_LAB, 30),
    # Technology
    Feed("verge-tech", "The Verge", "https://www.theverge.com/rss/tech/index.xml", "tech"),
    Feed("bbc-tech", "BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "tech"),
    Feed("ieee-spectrum", "IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss", "tech", poll_minutes=30),
    # Security
    Feed("bleepingcomputer", "BleepingComputer", "https://www.bleepingcomputer.com/feed/", "security", poll_minutes=5),
    Feed("thehackernews", "The Hacker News", "https://thehackernews.com/feeds/posts/default?alt=rss", "security", poll_minutes=5),
    Feed("darkreading", "Dark Reading", "https://www.darkreading.com/rss.xml", "security", poll_minutes=5),
    Feed("wired-security", "WIRED Security", "https://www.wired.com/feed/category/security/rss", "security"),
    Feed("google-security", "Google Security Blog", "https://security.googleblog.com/feeds/posts/default", "security", VENDOR_SECURITY, 30),
    Feed("ms-security", "Microsoft Security", "https://www.microsoft.com/en-us/security/blog/feed/", "security", VENDOR_SECURITY, 30),
    Feed("sans-isc", "SANS ISC", "https://isc.sans.edu/rssfeed_full.xml", "security", COMMUNITY, 30),
    Feed("cisa", "CISA", "https://www.cisa.gov/cybersecurity-advisories/all.xml", "security", GOVERNMENT, 30),
    Feed("krebs", "KrebsOnSecurity", "https://krebsonsecurity.com/feed/", "security", COMMUNITY, 30),
    # Economy: newsroom business/economics desks, plus central banks as
    # first-party (institutional) sources for rate decisions and policy.
    Feed("bbc-business", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "economy"),
    Feed("guardian-economics", "The Guardian Economics", "https://www.theguardian.com/business/economics/rss", "economy"),
    Feed("cnbc-economy", "CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "economy"),
    Feed("npr-economy", "NPR Economy", "https://feeds.npr.org/1017/rss.xml", "economy"),
    Feed("dw-business", "DW Business", "https://rss.dw.com/rdf/rss-en-bus", "economy"),
    Feed("economist-finance", "The Economist", "https://www.economist.com/finance-and-economics/rss.xml", "economy", poll_minutes=30),
    Feed("federal-reserve", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "economy", INSTITUTIONAL, 30),
    Feed("ecb", "European Central Bank", "https://www.ecb.europa.eu/rss/press.html", "economy", INSTITUTIONAL, 30),
)

FEEDS_BY_ID = {f.id: f for f in FEEDS}
