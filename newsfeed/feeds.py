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
    "world": "World",
    "politics": "Politics",
    "ai": "AI",
    "tech": "Technology",
    "security": "Security",
    "economy": "Economy",
    "local": "Local",
    "blogs": "Blogs",
}

# Source classes, per the brief's automation notes.
PUBLISHER = "publisher"
OFFICIAL_LAB = "official-lab"
VENDOR_SECURITY = "vendor-security"
GOVERNMENT = "government-advisory"
INSTITUTIONAL = "institutional"
COMMUNITY = "community/blog"

# Categories split into regions get a second dropdown in the UI. Every feed in
# such a category carries one of its region codes.
REGIONS = {
    "local": {
        "GA": "Georgia",
        "MD": "Maryland",
        "PA": "Pennsylvania",
        "VA": "Virginia",
        "WV": "West Virginia",
    },
    "politics": {
        "US": "United States",
        "INTL": "International",
    },
}
# Label for the dropdown's "everything" option.
REGION_ALL_LABELS = {"local": "All states", "politics": "US and international"}


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str
    category: str
    source_class: str = PUBLISHER
    poll_minutes: int = 15
    region: str | None = None  # a REGIONS[category] code, for categories split by region


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
    # Politics. US: public broadcasters, papers of record, and specialist desks
    # for Congress and the Supreme Court. International: national public
    # broadcasters and outlets dedicated to politics and foreign policy.
    Feed("npr-politics", "NPR Politics", "https://feeds.npr.org/1014/rss.xml", "politics", region="US"),
    Feed("pbs-politics", "PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/politics", "politics", region="US"),
    Feed("nyt-politics", "The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "politics", region="US"),
    Feed("guardian-us-politics", "The Guardian US", "https://www.theguardian.com/us-news/us-politics/rss", "politics", region="US"),
    Feed("the-hill", "The Hill", "https://thehill.com/homenews/feed/", "politics", region="US"),
    Feed("roll-call", "Roll Call", "https://rollcall.com/feed/", "politics", region="US"),
    Feed("scotusblog", "SCOTUSblog", "https://www.scotusblog.com/feed/", "politics", poll_minutes=30, region="US"),
    Feed("bbc-politics", "BBC Politics", "https://feeds.bbci.co.uk/news/politics/rss.xml", "politics", region="INTL"),
    Feed("guardian-politics", "The Guardian Politics", "https://www.theguardian.com/politics/rss", "politics", region="INTL"),
    Feed("politico-europe", "POLITICO Europe", "https://www.politico.eu/feed/", "politics", region="INTL"),
    Feed("cbc-politics", "CBC Politics", "https://www.cbc.ca/webfeed/rss/rss-politics", "politics", region="INTL"),
    Feed("foreign-policy", "Foreign Policy", "https://foreignpolicy.com/feed/", "politics", poll_minutes=30, region="INTL"),
    # Local, by state: the States Newsroom statehouse outlet, nonprofit and
    # independent newsrooms, and community sites.
    # Georgia
    Feed("georgia-recorder", "Georgia Recorder", "https://georgiarecorder.com/feed/", "local", region="GA"),
    Feed("rough-draft-atlanta", "Rough Draft Atlanta", "https://roughdraftatlanta.com/feed/", "local", region="GA"),
    Feed("atlanta-civic-circle", "Atlanta Civic Circle", "https://atlantaciviccircle.org/feed/", "local", poll_minutes=30, region="GA"),
    # Maryland
    Feed("maryland-matters", "Maryland Matters", "https://marylandmatters.org/feed/", "local", region="MD"),
    Feed("baltimore-sun", "The Baltimore Sun", "https://www.baltimoresun.com/feed/", "local", region="MD"),
    Feed("baltimore-brew", "Baltimore Brew", "https://baltimorebrew.com/feed/", "local", poll_minutes=30, region="MD"),
    # Pennsylvania
    Feed("pa-capital-star", "Pennsylvania Capital-Star", "https://penncapital-star.com/feed/", "local", region="PA"),
    Feed("spotlight-pa", "Spotlight PA", "https://www.spotlightpa.org/feeds/full.xml", "local", region="PA"),
    Feed("billy-penn", "Billy Penn", "https://billypenn.com/feed/", "local", poll_minutes=30, region="PA"),
    Feed("publicsource", "PublicSource", "https://www.publicsource.org/feed/", "local", poll_minutes=30, region="PA"),
    # Virginia
    Feed("virginia-mercury", "Virginia Mercury", "https://virginiamercury.com/feed/", "local", region="VA"),
    Feed("cardinal-news", "Cardinal News", "https://cardinalnews.org/feed/", "local", region="VA"),
    # West Virginia
    Feed("west-virginia-watch", "West Virginia Watch", "https://westvirginiawatch.com/feed/", "local", region="WV"),
    Feed("mountain-state-spotlight", "Mountain State Spotlight", "https://mountainstatespotlight.org/feed/", "local", region="WV"),
    Feed("wv-metronews", "WV MetroNews", "https://wvmetronews.com/feed/", "local", region="WV"),
    # Blogs: independent writers. They post a few times a week at most, so 30 minutes is plenty.
    # Tech and AI
    Feed("simon-willison", "Simon Willison", "https://simonwillison.net/atom/everything/", "blogs", poll_minutes=30),
    Feed("one-useful-thing", "One Useful Thing", "https://www.oneusefulthing.org/feed", "blogs", poll_minutes=30),
    Feed("stratechery", "Stratechery", "https://stratechery.com/feed/", "blogs", poll_minutes=30),
    Feed("benedict-evans", "Benedict Evans", "https://www.ben-evans.com/benedictevans?format=rss", "blogs", poll_minutes=30),
    Feed("daring-fireball", "Daring Fireball", "https://daringfireball.net/feeds/main", "blogs", poll_minutes=30),
    # Security
    Feed("schneier", "Schneier on Security", "https://www.schneier.com/feed/atom/", "blogs", poll_minutes=30),
    Feed("troy-hunt", "Troy Hunt", "https://www.troyhunt.com/rss/", "blogs", poll_minutes=30),
    # Economy and finance
    Feed("calculated-risk", "Calculated Risk", "https://www.calculatedriskblog.com/feeds/posts/default", "blogs", poll_minutes=30),
    Feed("marginal-revolution", "Marginal Revolution", "https://marginalrevolution.com/feed", "blogs", poll_minutes=30),
    Feed("noahpinion", "Noahpinion", "https://www.noahpinion.blog/feed", "blogs", poll_minutes=30),
)

FEEDS_BY_ID = {f.id: f for f in FEEDS}
