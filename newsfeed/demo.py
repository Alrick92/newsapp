"""Fictional sample stories for `--demo` mode (offline UI development).

Every headline here is invented and links to example.com; the UI shows a
"Demo data" badge whenever this mode is on.
"""

from __future__ import annotations

import base64
import hashlib

from .feeds import FEEDS_BY_ID
from .items import Item

_PALETTE = ["#D97757", "#C6613F", "#E3DACC", "#BCD1CA", "#CBCADB", "#EBDBBC", "#141413", "#6A9BCC"]

# (feed id, hours ago, title, description, has image)
_SAMPLES = [
    ("bbc-world", 1, "Coastal nations agree draft framework on shared fisheries patrols",
     "Delegates from eleven countries endorsed a draft text after three days of talks, with a final vote expected next month.", True),
    ("guardian-world", 2, "Fisheries patrol framework clears first hurdle as eleven nations sign draft",
     "The provisional agreement sets out joint patrol zones and a dispute panel, according to officials at the summit.", True),
    ("aljazeera", 3, "Eleven nations back draft fisheries patrol pact at coastal summit",
     "Smaller island states won concessions on catch reporting in the final hours of negotiations.", False),
    ("dw-world", 5, "European ministers debate rail freight subsidies ahead of budget vote",
     "Transport ministers remain split over how quickly to shift freight from road to rail.", True),
    ("france24", 7, "Election officials begin recount in disputed northern district",
     "The recount covers roughly 40,000 ballots and is expected to take four days.", True),
    ("npr-world", 9, "Recount underway in contested northern district race",
     "Both campaigns have sent observers as officials start the manual count.", False),
    ("un-news", 12, "Humanitarian agencies scale up winter shelter appeal",
     "The appeal seeks funding for insulated shelters ahead of the cold season.", True),
    ("techcrunch-ai", 1, "Startup Lumen Labs raises Series B to build on-device speech models",
     "The company says its compact models run fully offline on mid-range phones.", True),
    ("ars-ai", 4, "Hands-on: Lumen Labs' offline speech model holds up on a budget phone",
     "We tested transcription accuracy and latency across noisy and quiet environments.", True),
    ("openai", 6, "Introducing improved tool-use evaluations for agents",
     "A new public benchmark measures how reliably agents complete multi-step tasks with tools.", True),
    ("deepmind", 10, "New research on protein-structure refinement at scale",
     "The paper describes a refinement stage that improves accuracy on difficult targets.", True),
    ("mit-tr-ai", 14, "Why agent benchmarks keep getting harder to trust",
     "Researchers argue that saturated benchmarks hide real-world failure modes.", True),
    ("huggingface", 20, "A practical guide to evaluating small open models",
     "Step-by-step recipes for building task-specific evaluation sets.", False),
    ("wired-ai", 26, "The quiet race to put speech models on every phone",
     "Offline speech models are moving from labs into consumer devices.", True),
    ("verge-tech", 2, "Pixelbook-style laptop revival leaks ahead of autumn event",
     "Leaked images show a slimmer chassis and a larger trackpad.", True),
    ("bbc-tech", 8, "Regulator opens consultation on smartphone repairability scores",
     "Manufacturers would have to display a repair score at the point of sale.", True),
    ("ieee-spectrum", 30, "Solid-state batteries pass a key durability milestone",
     "Test cells retained most of their capacity after thousands of cycles.", True),
    ("bleepingcomputer", 1, "Critical flaw in ExampleVPN gateways exploited in the wild",
     "Attackers are exploiting an authentication bypass to gain admin access; a patch is available.", True),
    ("thehackernews", 2, "ExampleVPN authentication bypass under active exploitation, patch now",
     "Researchers observed mass scanning for vulnerable ExampleVPN gateways within hours of disclosure.", True),
    ("cisa", 3, "CISA adds ExampleVPN gateway authentication bypass to urgent advisories",
     "Agencies are urged to apply vendor updates and review gateway logs for compromise.", False),
    ("darkreading", 6, "Ransomware crew shifts to data-theft-only extortion",
     "Analysts say the group stopped encrypting files and now only threatens leaks.", True),
    ("google-security", 11, "Hardening the open-source supply chain with signed builds",
     "An update on build provenance adoption across popular package ecosystems.", True),
    ("ms-security", 16, "Threat actor targets cloud identity with token replay",
     "Guidance on detecting and mitigating token theft in hybrid identity setups.", True),
    ("sans-isc", 22, "Scanning spike for ExampleVPN gateways seen in honeypots",
     "Our sensors recorded a sharp rise in probes against the vulnerable endpoint.", False),
    ("krebs", 40, "Inside a phishing kit sold on underground forums",
     "A look at how one kit automates credential capture and MFA relay.", True),
    ("wired-security", 50, "How data brokers sell location data despite new rules",
     "An investigation into loopholes in recent privacy regulations.", True),
    ("guardian-world", 60, "Heatwave warnings extended across southern regions",
     "Forecasters say temperatures will remain high into next week.", True),
    ("techcrunch-ai", 66, "Developer tools startup launches code-review agent",
     "The agent posts inline suggestions and explains its reasoning.", True),
]


def _art(seed: str) -> str:
    """Abstract SVG thumbnail in the house palette, as a data URI."""
    h = hashlib.sha1(seed.encode()).digest()
    bg = _PALETTE[h[0] % 6]
    fg = _PALETTE[(h[0] % 6 + 1 + h[1] % 4) % 6]
    dark = "#141413"
    cx, cy, r = 120 + h[2] % 360, 60 + h[3] % 180, 50 + h[4] % 90
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 340">'
        f'<rect width="600" height="340" fill="{bg}"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fg}"/>'
        f'<path d="M0 {260 - h[5] % 60} Q 300 {200 - h[6] % 120} 600 {250 - h[7] % 70} L600 340 L0 340Z" fill="{dark}" opacity=".85"/>'
        f'</svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def demo_items(now: float) -> list[Item]:
    items = []
    for n, (feed_id, hours, title, desc, has_image) in enumerate(_SAMPLES):
        feed = FEEDS_BY_ID[feed_id]
        url = f"https://example.com/{feed_id}/{n}"
        items.append(Item(
            id=hashlib.sha1(url.encode()).hexdigest()[:16], title=title, description=desc, url=url,
            image=_art(title) if has_image else None, source_id=feed.id, source=feed.name,
            category=feed.category, source_class=feed.source_class, published=now - hours * 3600 - n * 97,
        ))
    return items
