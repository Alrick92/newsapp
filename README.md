# Newsfeed

A personal news reader and aggregator. It polls your feeds (listed in an OPML
file, with a Feedly export imported on first run), keeps 90 days of stories
in SQLite, and gives you unread counts, stars, full-text search and keyboard
navigation. An AI model (Claude, a remote Ollama, or any OpenAI-compatible
endpoint) groups the last 72 hours into trending stories and can summarize an
article on request. There are no accounts and no telemetry; the page loads
nothing from outside services.

```bash
git clone https://github.com/Alrick92/newsapp.git && cd newsapp
pip install -r requirements.txt
cp ~/Downloads/feedly.opml data/import.opml   # optional: your Feedly export, merged on first run
python -m newsfeed                            # http://127.0.0.1:8000
python -m newsfeed --demo                     # offline, fictional sample stories
```

It's built for one person. With no accounts, anyone who can open the site
shares your read and starred state and can use your AI summaries, so keep it
private: see [Keep it private](#keep-it-private).

## Reading

- **Tabs:** Trending (AI-grouped stories from the last 72 hours), Latest (all
  stories, with the unread count) and Starred (everything you've starred, of
  any age).
- **Filters:** category dropdown with unread counts per category (Local and
  Politics add a state or US/International dropdown), search, time window
  (6 hours to 90 days), sources, Unread only, With images. Filters are kept
  in the link, so a view can be bookmarked.
- **Cards:** Mark read / Mark unread, Star, and Summarize. Opening a story
  marks it read. When several feeds carry the same story it's stored once and
  shows "Also in" links to the others, so it has one read state.
- **Search** covers titles and the full text each feed provides (SQLite
  FTS5). Results come best match first, with the matching words highlighted;
  the last word matches as a prefix, so results appear as you type.
- **Summaries:** the Summarize button appears only when an AI provider is set
  up and the feed gives real body text (at least 600 characters; teasers are
  skipped). Only the feed's own text is sent; article pages are never
  fetched. Each summary is saved, so asking again is free, and new summaries
  are limited to 30 an hour (`NEWSFEED_SUMMARIES_PER_HOUR`).

| Key | Action |
|---|---|
| `j` / `k` | Next / previous story (`j` on the last one loads more) |
| `m` | Mark read / unread |
| `s` | Star / unstar |
| `o` or `Enter` | Open the story in a new tab (marks it read) |
| `/` | Search |
| `Shift` + `A` | Mark everything in the current view read (with Undo) |
| `Esc` | Leave the search box |

## Install as an app

Newsfeed is a Progressive Web App: install it and it opens in its own window
with its own icon, starts instantly, and keeps working offline.

- **Android (Chrome), Windows/macOS/Linux (Chrome, Edge):** use the
  **Install** button in the header, or the install icon in the address bar.
- **iPhone and iPad (Safari):** Share → **Add to Home Screen**.
- **Mac (Safari 17+):** File → **Add to Dock**.

Long-press (or right-click) the icon for shortcuts to Latest, Unread and
Starred.

**Offline:** the app itself and the last version of each list you opened are
saved on the device. Offline, a banner says when those stories were saved.
Marking stories read or starring them still works: the changes are kept on
the device and sent when you're back online. Article links, summaries,
search and new stories need a connection. Publisher images aren't saved, so
offline cards show their placeholder.

**Updates:** a deploy is picked up on the next visit, and the app offers
**Reload** to switch to the new version.

Installing needs HTTPS, which your load balancer provides (it also works on
`http://localhost`). Behind a password, the app sends your credentials when it
loads its manifest, so installing works the same way.

## Feeds (OPML)

The feed list is an OPML file: folders become categories, and a folder inside
a folder becomes a region (the states in Local, US/International in
Politics). The app ships 71 curated feeds in `newsfeed/feeds.opml`.

- **Where it lives:** on first run the bundled list is copied to
  `data/feeds.opml` (in Docker, `/data/feeds.opml` in the `newsfeed-data`
  volume). That copy is the one the app reads; set `NEWSFEED_OPML` to use
  another path.
- **Importing from Feedly:** in Feedly, go to *Organize → Export OPML*. Save
  the file as `data/import.opml` (Docker:
  `docker compose cp feedly.opml newsfeed:/data/import.opml`). On the next
  start it's merged in and renamed `import.opml.imported`, so it applies
  once. Feeds you already have (matched by address) aren't added twice.
  Feedly folders keep their names; one named like an existing category (e.g.
  "Tech") joins it. Imported feeds are checked every 30 minutes.
- **Adding or removing feeds:** edit `data/feeds.opml` and save. Changes are
  picked up within a minute, without a restart. If the file has a mistake,
  the app keeps the last good list and shows the problem in the footer. In
  Docker:

  ```bash
  docker compose cp newsfeed:/data/feeds.opml .   # edit feeds.opml, then:
  docker compose cp feeds.opml newsfeed:/data/feeds.opml
  ```

  Optional attributes in the `nf:` namespace (ignored by other readers):
  `nf:poll` (minutes between checks, minimum 5), `nf:class` (`publisher`,
  `official-lab`, `vendor-security`, `government-advisory`, `institutional` or
  `community/blog`; non-publishers don't count as independent coverage in
  trending), `nf:id`, and on folders `nf:key`, `nf:region` and `nf:allLabel`.
- **Exporting:** open `/export.opml` in the browser, or run
  `python -m newsfeed export-opml my-feeds.opml`
  (Docker: `docker compose exec newsfeed python -m newsfeed export-opml > my-feeds.opml`).
  The file imports into Feedly, Inoreader, NetNewsWire and other readers.

## Keep it private

The app has no login. Restrict the site at your load balancer, for example
with a password:

- **Caddy:**

  ```
  news.example.com {
      basic_auth {
          you $2a$14$...   # caddy hash-password
      }
      reverse_proxy newsfeed:8000
  }
  ```

- **nginx:** `auth_basic "Newsfeed"; auth_basic_user_file /etc/nginx/htpasswd;`
  in the `location` that proxies to `http://newsfeed:8000`.
- **Traefik:** a `basicauth` middleware on the router, for example the label
  `traefik.http.middlewares.news-auth.basicauth.users=you:$$apr1$$...`.

A VPN (Tailscale, WireGuard) or an IP allowlist works as well. Leave
`/healthz` reachable for the load balancer's health check if it needs it.

## Docker Compose (behind a load balancer)

The container publishes **no ports**: there is no `ports:` or `expose:` in
`compose.yaml` and no `EXPOSE` in the image. The app listens on port 8000
only on an **internal** Docker network (`lb`) that it shares with your load
balancer. Docker gives an internal network no route to or from the outside,
so the load balancer is the only way in. Outbound traffic to the feed sites
and your AI endpoint uses a second private network (`egress`), which accepts
no inbound connections.

```bash
cd newsapp
cp .env.example .env            # pick an AI provider; set LB_NETWORK if your LB has a network
docker compose up -d --build
```

Point the load balancer at **`http://newsfeed:8000`** on that network. Use
`GET /healthz` as its health check.

- **Load balancer setup:** the load balancer must be a container (Traefik,
  nginx, HAProxy, Caddy, …) attached to the same network, which is `lb`
  unless you set `LB_NETWORK`. If that network doesn't exist yet, `up`
  creates it as an internal network. If it does exist (for example the
  network your proxy or hosting panel already uses), the app joins it and that
  network keeps its own settings. A load balancer outside Docker, such as a cloud
  load balancer, can't reach the container without a published port.
- **Visitor addresses:** `X-Forwarded-For` / `X-Forwarded-Proto` from the load
  balancer are trusted, so logs show real client addresses. To trust only
  your load balancer, set `FORWARDED_ALLOW_IPS` to its address.
- **Data:** the SQLite database and `feeds.opml` are in the
  `newsfeed-data` volume (`/data` in the container), so they survive
  `docker compose down` / `up` and image rebuilds. `docker compose down -v`
  deletes them.
- **Replicas:** keep one replica. The feed poller runs inside the app, so a
  second copy would poll every publisher twice and keep its own database.
- **Hardening:** the container runs as a non-root user with a read-only
  filesystem, all Linux capabilities dropped, a Docker health check and
  rotated logs.
- **Network access:** the container needs outbound access to the feed sites
  and to your AI endpoint: `api.anthropic.com`, your `OPENAI_BASE_URL`, or
  your `OLLAMA_BASE_URL`. No AI service runs in this stack.

## The bundled feeds

- **71 feeds** in [`newsfeed/feeds.opml`](newsfeed/feeds.opml): the 15-feed
  production bundle from the RSS research brief, plus Microsoft Security,
  CISA, UN News, NPR, The Verge, WIRED (AI and Security), BBC Technology,
  IEEE Spectrum, Hugging Face and Krebs, and the groups below.
- **Economy.** BBC Business, The Guardian Economics, CNBC Economy, NPR
  Economy, DW Business and The Economist (finance & economics), plus the
  Federal Reserve and the European Central Bank as first-party sources:
  their own announcements, so they add authority to a story but don't count
  as independent coverage.
- **Politics, US and international.** Picking Politics shows a second
  dropdown: US, International, or both.
  - US: NPR Politics, PBS NewsHour, The New York Times, The Guardian US,
    The Hill, Roll Call (Congress), SCOTUSblog (Supreme Court)
  - International: BBC Politics, The Guardian Politics, POLITICO Europe,
    CBC Politics (Canada), Foreign Policy
- **Local, by state.** Picking Local shows a second dropdown to narrow it to
  one state (the choice is kept in the link, e.g. `#view=latest&cat=local&region=PA`).
  - Georgia: Georgia Recorder, Rough Draft Atlanta, Atlanta Civic Circle
  - Maryland: Maryland Matters, The Baltimore Sun, Baltimore Brew
  - Pennsylvania: Pennsylvania Capital-Star, Spotlight PA, Billy Penn, PublicSource
  - Virginia: Virginia Mercury, Cardinal News
  - West Virginia: West Virginia Watch, Mountain State Spotlight, WV MetroNews

  To add a state, add a folder inside Local in `data/feeds.opml`, e.g.
  `<outline text="Ohio" nf:region="OH">`, and put its feeds in it.
- **Blogs.** Independent writers on tech and AI (Simon Willison, One Useful
  Thing, Stratechery's free posts, Benedict Evans, Daring Fireball), security
  (Schneier on Security, Troy Hunt) and economics (Calculated Risk, Marginal
  Revolution, Noahpinion). Local stories and blogs count as independent
  sources when AI picks trending stories.
- **Polite polling.** Each feed polls on its own cadence (5, 15 or 30
  minutes, per the brief) and sends `ETag` / `If-Modified-Since`. A `304`
  counts as success. Send a descriptive `User-Agent` by setting
  `NEWSFEED_USER_AGENT` to your own contact URL or email.
- **Links are cleaned** before comparing (UTM and other tracking parameters,
  `www.` and trailing slashes removed), so reposts are recognized; see
  [Storage](#storage) for how duplicates are handled.
- **Images.** Taken from `media:content` (widest wins), `media:thumbnail`,
  image enclosures, or the first `<img>` in the summary. For items with no
  image, the server fetches the article's `og:image` for up to 40 items per
  refresh (`NEWSFEED_OG_IMAGE_BUDGET`). Cards with no image get a placeholder
  in the category's colour.
- **Views and filters.** Two tabs, Trending and Latest. A category dropdown
  (All, World, Politics, AI, Technology, Security, Economy, Local, Blogs) with counts works in
  both views. Latest also has search, a time window (6h, 12h, 24h, 72h, 7d, 30d), sources
  (multi-select), sort order and "with images only". All filters are stored
  in the URL hash, so a filtered view can be shared as a link.
- **Trending tab.** The configured AI model clusters the newest headlines
  (400 for Claude, 150 for other providers) into at most 12 events. Each event gets a
  headline, summary, "why it's trending", a momentum score and links to
  every source.
  - First-party sources (labs, vendors, government, institutions) are labelled
    and not counted as independent corroboration.
  - AI requests are throttled to **one every 4 hours**
    (`NEWSFEED_AI_REFRESH_HOURS`), whatever triggers them: the schedule, the
    Regenerate button or the header's Refresh button. A failed request still
    counts. The time of the last request is saved, so restarts don't reset
    it. Changing the provider or model lifts the wait, so a corrected setting
    takes effect at once. While throttled, the tab keeps the last stories, the
    Regenerate button is disabled, and the page shows when the next AI update
    is allowed. A run also needs new stories since the last one.
  - Without AI, keyword clustering is free and reruns every 30 minutes
    (`NEWSFEED_TRENDING_MINUTES`).
  - With no AI configured, or if a request fails, a keyword-overlap clusterer
    produces the same layout. The UI shows which model produced the stories.

## AI providers

Choose one with `NEWSFEED_AI_PROVIDER` in `.env`. All three return the same
JSON, checked against one schema, and any failure falls back to keyword
clustering. The reason is shown in `GET /api/trending` under `error`.

| Provider | Settings | Notes |
|---|---|---|
| `anthropic` (default when `ANTHROPIC_API_KEY` is set) | `ANTHROPIC_API_KEY`; `NEWSFEED_MODEL` defaults to `claude-opus-5-5` | Structured outputs. Server-side refusal fallback re-runs a declined request on a substitute model |
| `openai` | `OPENAI_BASE_URL` (default `https://api.openai.com/v1`), `OPENAI_API_KEY` (optional), `NEWSFEED_MODEL` (required) | Any OpenAI-compatible `/chat/completions`: OpenAI, LiteLLM and other gateways, vLLM, LM Studio, llama.cpp server, Together, Groq, OpenRouter… |
| `ollama` | `OLLAMA_BASE_URL` (required), `OLLAMA_API_KEY` (optional), `NEWSFEED_MODEL` (required, pulled on that server) | A remote Ollama server, called through its own `/api/chat` |
| `none` | — | Keyword clustering only |

Example `.env` entries:

```bash
# OpenAI-compatible endpoint
NEWSFEED_AI_PROVIDER=openai
OPENAI_BASE_URL=https://llm.example.com/v1     # up to and including /v1
OPENAI_API_KEY=sk-...                          # sent as Authorization: Bearer
NEWSFEED_MODEL=meta-llama/Llama-3.1-70B-Instruct

# Remote Ollama
NEWSFEED_AI_PROVIDER=ollama
OLLAMA_BASE_URL=https://ollama.example.com     # server root; a trailing /v1 is ignored
OLLAMA_API_KEY=                                # only if a proxy in front checks a Bearer token
NEWSFEED_MODEL=llama3.1:8b
```

**OpenAI-compatible endpoints** don't all support the same JSON features.
The app first asks for a strict JSON schema (`response_format: json_schema`).
If the endpoint rejects that with a 400, it retries with `json_object`, then
with plain text. The prompt always includes the schema, and the reply is
validated either way. The first mode that works is remembered. Set
`NEWSFEED_AI_JSON_MODE` to start lower.

**Ollama** is called through its own API rather than its OpenAI-compatible
`/v1`. Ollama's default context is only a few thousand tokens, and the `/v1`
API can't raise it, so a long headline list would be silently cut off. The
app requests `NEWSFEED_OLLAMA_NUM_CTX` tokens (default 32768). The prompt for
150 headlines is about 12k tokens.

- **Model choice:** use an instruction-tuned model of about 8B parameters or
  more, e.g. `llama3.1:8b`, `qwen2.5:14b` or `mistral-nemo`. Smaller models
  often return valid JSON but group stories poorly.
- **Reaching the server:** the Ollama server must accept connections from
  the app's host. By default Ollama listens on localhost only; on the Ollama
  machine, set `OLLAMA_HOST=0.0.0.0` or put a reverse proxy in front of it.
  Ollama has no authentication of its own, so expose it only on a private
  network, or behind a proxy that checks a token (set `OLLAMA_API_KEY` to
  that token).
- **Speed:** local models are slow. Requests time out after 600 s for Ollama
  and 300 s for OpenAI-compatible endpoints (`NEWSFEED_AI_TIMEOUT`). Lower
  `NEWSFEED_TRENDING_MAX_ITEMS` if requests time out or replies are cut off.

## Storage

Everything lives in one SQLite file: `data/newsfeed.db` when run directly, or
`/data/newsfeed.db` inside the `newsfeed-data` Docker volume. Requests read
only the rows they need, so stories aren't held in memory.

| Saved | Kept |
|---|---|
| Stories: title, feed summary, full feed text (for search and summaries), link, image address, source, time | 90 days (`NEWSFEED_RETENTION_DAYS`) |
| Read and starred state, saved summaries | With the story; starred stories are kept for good |
| Feed caching details (`ETag` / `Last-Modified`), last success and error | Always; the first poll after a restart is a cheap "not modified" check |
| Latest trending result, last AI request time | Always; a restart doesn't trigger an extra AI call |

- **Pruning:** once a night (first check after 03:00 server time,
  `NEWSFEED_PRUNE_HOUR`) stories older than 90 days are deleted, except
  starred ones, and the space is returned to the disk.
- **Duplicates:** a story is matched by the feed's GUID, then its link, then
  an identical headline within 48 hours. A copy from another feed is recorded
  as an extra source of the stored story ("Also in"), and still counts as a
  separate outlet for trending.
- **Upgrades:** the database records its schema version and upgrades itself
  in place on start, keeping your stories and read state. An `items.json`
  snapshot from the earliest version is imported once.
- **Backup:** copy the database safely while the app runs:

  ```bash
  docker compose exec newsfeed python -c "import sqlite3; s=sqlite3.connect('/data/newsfeed.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
  docker compose cp newsfeed:/data/backup.db ./newsfeed-backup.db
  ```

- **Deploying:** keep the data folder on a persistent volume, and run a single
  server process, since the poller runs inside it.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/items?category=&sources=a,b&q=&hours=&has_image=&unread=&starred=&sort=newest\|source\|relevance&limit=&offset=` | Filtered items with read/starred state, plus total and unread counts per category; `q` is full-text search |
| `POST /api/items/{id}/read` | Body `{"read": true\|false}` |
| `POST /api/items/read` | Body `{"ids": [...], "read": true\|false}`; set many at once |
| `POST /api/items/mark-all-read?…` | Mark everything matching the list filters read; returns the ids, for undo |
| `POST /api/items/{id}/star` | Body `{"starred": true\|false}` |
| `POST /api/items/{id}/summary` | Summarize from the feed text (saved; 404 without a provider, 422 for teasers, 429 over the hourly limit) |
| `GET /export.opml` | The feed list as OPML |
| `GET /manifest.webmanifest`, `GET /sw.js` | App manifest and service worker (installable app, offline) |
| `GET /api/trending` | Cached trending stories (starts a regeneration if stale) |
| `POST /api/trending/refresh` | Regenerate trending now |
| `POST /api/refresh` | Poll every feed now |
| `GET /api/feeds` | Per-feed health: last success, last error, stories in the last 72h |
| `GET /healthz` | Health check for the load balancer and Docker |
| `GET /api/meta` | Categories, regions, sources, totals, starred count, whether summaries are on, feed-list errors |

## Configuration

| Variable | Default | |
|---|---|---|
| `NEWSFEED_AI_PROVIDER` | `anthropic` if a key is set, else none | `anthropic`, `openai`, `ollama` or `none` |
| `NEWSFEED_MODEL` | `claude-opus-5-5` (anthropic) | Model for trending; required for `openai` / `ollama` |
| `ANTHROPIC_API_KEY` | — | Claude credentials |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | `https://api.openai.com/v1` / — | OpenAI-compatible endpoint |
| `NEWSFEED_AI_JSON_MODE` | `json_schema` | First JSON mode to try with `openai` (`json_object`, `none`) |
| `OLLAMA_BASE_URL` / `OLLAMA_API_KEY` | — (required for `ollama`) / — | Remote Ollama server; key is sent as a Bearer token |
| `NEWSFEED_OLLAMA_NUM_CTX` | `32768` | Context window requested from Ollama |
| `NEWSFEED_AI_TIMEOUT` | `300` / `600` (Ollama) | Seconds per AI request (Anthropic uses the SDK default) |
| `NEWSFEED_AI_REFRESH_HOURS` | `4` | Minimum gap between AI requests (all triggers, survives restarts) |
| `NEWSFEED_TRENDING_MINUTES` | `30` | Minimum gap between keyword-clustering runs (no AI) |
| `NEWSFEED_TRENDING_MAX_ITEMS` | `400` (Claude) / `150` | Newest items sent to the model |
| `NEWSFEED_DISABLE_AI` | — | `1` forces the keyword clusterer (same as provider `none`) |
| `NEWSFEED_DATA_DIR` | `data` | Folder for `newsfeed.db`, `feeds.opml` and `import.opml` |
| `NEWSFEED_OPML` | `<data dir>/feeds.opml` | The feed list the app reads |
| `NEWSFEED_RETENTION_DAYS` | `90` | Days to keep stories (starred are kept for good) |
| `NEWSFEED_PRUNE_HOUR` | `3` | Hour (server local time) after which the nightly prune runs |
| `NEWSFEED_SUMMARIES_PER_HOUR` | `30` | New article summaries allowed per hour |
| `NEWSFEED_SUMMARY_MIN_CHARS` | `600` | Feed text needed before a story gets a Summarize button |
| `NEWSFEED_OG_IMAGE_BUDGET` | `40` | og:image lookups per refresh (`0` disables) |
| `NEWSFEED_USER_AGENT` | generic | Identify your deployment to publishers |
| `HOST` / `PORT` | `127.0.0.1` / `8000` (`0.0.0.0` in Docker) | Bind address |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (`*` in Compose) | Proxies whose `X-Forwarded-*` headers are trusted |

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

**Type:** Lora for headlines and Poppins for the interface, served by the app
itself from `newsfeed/static/fonts` (SIL Open Font License), so the page makes
no requests to Google or anyone else. Light and dark themes are included.

## Content use

Each story's title, feed summary, image address and link are shown. The full
text a feed provides is stored only for search and summaries; it's never
shown in full, and article pages are never scraped. Some publishers restrict use
of their feeds (for example, TechCrunch's RSS terms), so check each
publisher's terms before deploying publicly.

## Tests

```bash
pip install pytest
python -m pytest
```
