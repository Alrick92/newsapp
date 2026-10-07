# Newsfeed

A news aggregator for the last 72 hours of world, AI, technology and security
headlines. Claude groups the window's stories into trending events. The UI
shows card grids (image, title, description, URL) with a category dropdown,
in an "Ink & Teal" newsroom palette with Lora and Poppins type.

```bash
cd newsfeed
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # optional; enables Claude trending
python -m newsfeed                       # http://127.0.0.1:8000
python -m newsfeed --demo                # offline, fictional sample stories
```

## What it does

- **26 feeds.** The 15-feed production bundle from the RSS research brief,
  plus Microsoft Security, CISA, UN News, NPR, The Verge, WIRED (AI and
  Security), BBC Technology, IEEE Spectrum, Hugging Face and Krebs. They are
  listed in [`newsfeed/feeds.py`](newsfeed/feeds.py).
- **Polite polling.** Each feed polls on its own cadence (5, 15 or 30
  minutes, per the brief) and sends `ETag` / `If-Modified-Since`. A `304`
  counts as success. Send a descriptive `User-Agent` by setting
  `NEWSFEED_USER_AGENT` to your own contact URL or email.
- **72-hour window.** Items older than 72 hours are dropped and pruned.
- **Deduplication.** URLs are canonicalized: UTM and other tracking
  parameters, `www.` and trailing slashes are stripped. Identical headlines
  from different feeds collapse to the earliest copy.
- **Images.** Taken from `media:content` (widest wins), `media:thumbnail`,
  image enclosures, or the first `<img>` in the summary. For items with no
  image, the server fetches the article's `og:image` for up to 40 items per
  refresh (`NEWSFEED_OG_IMAGE_BUDGET`). Cards with no image get a placeholder
  in the category's colour.
- **Views and filters.** Two tabs, Trending and Latest. A category dropdown
  (All, World & Politics, AI, Technology, Security) with counts works in
  both views. Latest also has search, a time window (6/12/24/48/72h), sources
  (multi-select), sort order and "with images only". All filters are stored
  in the URL hash, so a filtered view can be shared as a link.
- **Trending tab.** Claude (`claude-opus-5-5`, structured output) clusters
  up to 400 recent headlines into at most 12 events. Each event gets a
  headline, summary, "why it's trending", a momentum score and links to
  every source.
  - First-party sources (labs, vendors, government, institutions) are labelled
    and not counted as independent corroboration.
  - Trending regenerates at most every 30 minutes, and only when the item set
    has changed (`NEWSFEED_TRENDING_MINUTES`).
  - The request opts into server-side refusal fallbacks
    (`fallbacks: "default"`), so security-heavy headlines that trip a safety
    classifier are re-run on a substitute model instead of returning nothing.
  - With no API key, or if the call fails, a keyword-overlap clusterer
    produces the same layout. The UI labels which mode produced the stories.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/items?category=&sources=a,b&q=&hours=&has_image=&sort=newest\|source&limit=&offset=` | Filtered items plus per-section counts |
| `GET /api/trending` | Cached trending stories (starts a regeneration if stale) |
| `POST /api/trending/refresh` | Regenerate trending now |
| `POST /api/refresh` | Poll every feed now |
| `GET /api/feeds` | Per-feed health: last success, last error, items in window |
| `GET /api/meta` | Sections, sources, totals, last refresh |

## Configuration

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables Claude trending |
| `NEWSFEED_MODEL` | `claude-opus-5-5` | Model for trending |
| `NEWSFEED_TRENDING_MINUTES` | `30` | Minimum gap between regenerations |
| `NEWSFEED_TRENDING_MAX_ITEMS` | `400` | Newest items sent to the model |
| `NEWSFEED_DISABLE_AI` | — | `1` forces the keyword clusterer |
| `NEWSFEED_DATA_DIR` | `data` | Snapshot location; restarts reload from it |
| `NEWSFEED_OG_IMAGE_BUDGET` | `40` | og:image lookups per refresh (`0` disables) |
| `NEWSFEED_USER_AGENT` | generic | Identify your deployment to publishers |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |

## Style

**Palette: Ink & Teal**, a cool newsroom scheme. All colours are CSS tokens at
the top of `static/styles.css`, so swapping the palette means editing one block.

| Role | Light | Dark |
|---|---|---|
| Background | `#f6f8f9` paper | `#0c141d` |
| Surface | `#ffffff` | `#132030` |
| Text | `#0f1b2a` ink | `#eaf0f5` |
| Secondary text | `#33475b` / `#5f6f80` | `#b4c2cf` / `#8496a7` |
| Accent (links, active tab, momentum) | `#0e7c72` teal | `#2bb3a3` |

Each section has its own colour, used for the category dot, card chips and
image placeholders: World `#2e64a8` blue, AI `#7a5af0` violet, Technology
`#d9922e` amber, Security `#d1495b` crimson.

**Type:** Lora for headlines and Poppins for the interface, both loaded from
Google Fonts. Light and dark themes are included.

## Content use

Only the title, the feed's summary, the image URL and the link are stored and
shown. Full article text is never republished. Some publishers restrict use
of their feeds (for example, TechCrunch's RSS terms), so check each
publisher's terms before deploying publicly.

## Tests

```bash
pip install pytest
python -m pytest
```
