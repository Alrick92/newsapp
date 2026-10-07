# Newsfeed

A news aggregator for the last 72 hours of world, AI, technology and security
headlines. An AI model (Claude, Ollama, or any OpenAI-compatible endpoint)
groups the window's stories into trending events. The UI
shows card grids (image, title, description, URL) with a category dropdown,
in an "Ink & Teal" newsroom palette with Lora and Poppins type.

```bash
git clone https://github.com/Alrick92/newsapp.git && cd newsapp
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # optional; or see "AI providers" below
python -m newsfeed                       # http://127.0.0.1:8000
python -m newsfeed --demo                # offline, fictional sample stories
```

## Docker Compose (behind a load balancer)

The container publishes **no ports**: there is no `ports:` or `expose:` in
`compose.yaml` and no `EXPOSE` in the image. The app listens on port 8000
only inside a Docker network it shares with your load balancer.

```bash
cd newsapp
cp .env.example .env            # pick an AI provider (optional)
docker network create lb        # once; or set LB_NETWORK to your LB's network
docker compose up -d --build
```

Point the load balancer at **`http://newsfeed:8000`** on that network. Use
`GET /healthz` as its health check.

- **Load balancer setup:** the load balancer must be a container (Traefik,
  nginx, HAProxy, Caddy, …) attached to the same network, which is `lb`
  unless you set `LB_NETWORK`. A load balancer outside Docker, such as a cloud
  load balancer, can't reach the container without a published port.
- **Visitor addresses:** `X-Forwarded-For` / `X-Forwarded-Proto` from the load
  balancer are trusted, so logs show real client addresses. To trust only
  your load balancer, set `FORWARDED_ALLOW_IPS` to its address.
- **Data:** the SQLite database is in the `newsfeed-data` volume, so it
  survives `docker compose down` / `up` and image rebuilds.
  `docker compose down -v` deletes it.
- **Replicas:** keep one replica. The feed poller runs inside the app, so a
  second copy would poll every publisher twice and keep its own database.
- **Hardening:** the container runs as a non-root user with a read-only
  filesystem, all Linux capabilities dropped, a Docker health check and
  rotated logs.
- **Network access:** the container needs outbound HTTPS to the feed sites,
  plus your AI endpoint (`api.anthropic.com`, your `OPENAI_BASE_URL`, or
  the bundled Ollama, which also needs internet access to pull models).

## What it does

- **26 feeds.** The 15-feed production bundle from the RSS research brief,
  plus Microsoft Security, CISA, UN News, NPR, The Verge, WIRED (AI and
  Security), BBC Technology, IEEE Spectrum, Hugging Face and Krebs. They are
  listed in [`newsfeed/feeds.py`](newsfeed/feeds.py).
- **Polite polling.** Each feed polls on its own cadence (5, 15 or 30
  minutes, per the brief) and sends `ETag` / `If-Modified-Since`. A `304`
  counts as success. Send a descriptive `User-Agent` by setting
  `NEWSFEED_USER_AGENT` to your own contact URL or email.
- **72-hour view, 30-day archive.** The page shows the last 72 hours by
  default. Stories are kept in SQLite for 30 days (`NEWSFEED_RETENTION_DAYS`)
  and deleted automatically after that, so the file stays small.
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
  both views. Latest also has search, a time window (6h, 12h, 24h, 72h, 7d, 30d), sources
  (multi-select), sort order and "with images only". All filters are stored
  in the URL hash, so a filtered view can be shared as a link.
- **Trending tab.** The configured AI model clusters the newest headlines
  (400 for Claude, 150 for other providers) into at most 12 events. Each event gets a
  headline, summary, "why it's trending", a momentum score and links to
  every source.
  - First-party sources (labs, vendors, government, institutions) are labelled
    and not counted as independent corroboration.
  - Trending regenerates at most every 30 minutes, and only when the item set
    has changed (`NEWSFEED_TRENDING_MINUTES`).
  - With no AI configured, or if a request fails, a keyword-overlap clusterer
    produces the same layout. The UI shows which model produced the stories.

## AI providers

Choose one with `NEWSFEED_AI_PROVIDER` in `.env`. All three return the same
JSON, checked against one schema, and any failure falls back to keyword
clustering. The reason is shown in `GET /api/trending` under `error`.

| Provider | Settings | Notes |
|---|---|---|
| `anthropic` (default when `ANTHROPIC_API_KEY` is set) | `ANTHROPIC_API_KEY`; `NEWSFEED_MODEL` defaults to `claude-opus-5-5` | Structured outputs. Server-side refusal fallback re-runs a declined request on a substitute model |
| `openai` | `NEWSFEED_MODEL` (required), `OPENAI_BASE_URL` (default `https://api.openai.com/v1`), `OPENAI_API_KEY` (optional for local servers) | Any OpenAI-compatible `/chat/completions`: OpenAI, vLLM, LM Studio, LiteLLM, llama.cpp server, Together, Groq… |
| `ollama` | `NEWSFEED_MODEL` (required, a model you've pulled), `OLLAMA_BASE_URL` (default `http://ollama:11434` in Compose) | Uses Ollama's own `/api/chat` with a JSON-schema `format` |
| `none` | — | Keyword clustering only |

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
- **Bundled Ollama:** Compose includes an optional Ollama service on a private
  network with no published ports:

  ```bash
  # .env: NEWSFEED_AI_PROVIDER=ollama  NEWSFEED_MODEL=llama3.1:8b
  docker compose --profile ollama up -d
  docker compose exec ollama ollama pull llama3.1:8b
  ```

  For a GPU, uncomment the `deploy.resources` block in `compose.yaml`
  (needs the NVIDIA Container Toolkit).
- **Ollama on the Docker host:** use
  `OLLAMA_BASE_URL=http://host.docker.internal:11434`.
- **Speed:** local models are slow. Requests time out after 600 s for Ollama
  and 300 s for OpenAI-compatible endpoints (`NEWSFEED_AI_TIMEOUT`). Lower
  `NEWSFEED_TRENDING_MAX_ITEMS` if requests time out or replies are cut off.

## Storage

Everything lives in one SQLite file, `data/newsfeed.db`. Requests read only
the rows they need, so stories aren't held in memory.

| Saved | Effect after a restart |
|---|---|
| Stories (title, summary, URL, image, source, time) | Nothing to rebuild; trending and search continue at once |
| Feed caching details (`ETag` / `Last-Modified`), last success and error | First poll is a cheap "not modified" check, not a full download |
| Which articles already had an image lookup | Image lookups aren't repeated |
| Latest trending result | No extra AI call on restart |

- **Purge:** every refresh deletes stories older than the retention window and
  hands the space back to the disk.
- **Duplicate headlines:** identical headlines within 48 hours of each other
  count as one story; recurring titles further apart are kept separately.
- **Upgrading:** an `items.json` snapshot from an earlier version is imported
  once on startup, then renamed to `items.json.imported`.
- **Deploying:** keep `data/` on a persistent volume, and run a single server
  process, since the poller runs inside it.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/items?category=&sources=a,b&q=&hours=&has_image=&sort=newest\|source&limit=&offset=` | Filtered items plus per-section counts |
| `GET /api/trending` | Cached trending stories (starts a regeneration if stale) |
| `POST /api/trending/refresh` | Regenerate trending now |
| `POST /api/refresh` | Poll every feed now |
| `GET /api/feeds` | Per-feed health: last success, last error, stories in the last 72h |
| `GET /healthz` | Health check for the load balancer and Docker |
| `GET /api/meta` | Sections, sources, 72h and 30-day totals, last refresh |

## Configuration

| Variable | Default | |
|---|---|---|
| `NEWSFEED_AI_PROVIDER` | `anthropic` if a key is set, else none | `anthropic`, `openai`, `ollama` or `none` |
| `NEWSFEED_MODEL` | `claude-opus-5-5` (anthropic) | Model for trending; required for `openai` / `ollama` |
| `ANTHROPIC_API_KEY` | — | Claude credentials |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | `https://api.openai.com/v1` / — | OpenAI-compatible endpoint |
| `NEWSFEED_AI_JSON_MODE` | `json_schema` | First JSON mode to try with `openai` (`json_object`, `none`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` (`http://ollama:11434` in Compose) | Ollama server |
| `NEWSFEED_OLLAMA_NUM_CTX` | `32768` | Context window requested from Ollama |
| `NEWSFEED_AI_TIMEOUT` | `300` / `600` (Ollama) | Seconds per AI request (Anthropic uses the SDK default) |
| `NEWSFEED_TRENDING_MINUTES` | `30` | Minimum gap between regenerations |
| `NEWSFEED_TRENDING_MAX_ITEMS` | `400` (Claude) / `150` | Newest items sent to the model |
| `NEWSFEED_DISABLE_AI` | — | `1` forces the keyword clusterer (same as provider `none`) |
| `NEWSFEED_DATA_DIR` | `data` | Folder for `newsfeed.db` |
| `NEWSFEED_RETENTION_DAYS` | `30` | Days to keep stories before purging |
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
