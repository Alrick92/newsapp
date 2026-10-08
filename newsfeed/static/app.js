"use strict";

const PAGE_SIZE = 60;
const HOURS = [6, 12, 24, 72, 168, 720, 2160]; // the API caps this at the retention window
const hoursLabel = (h) => (h % 24 === 0 && h > 72 ? `${h / 24}d` : `${h}h`);
const CAT_COLOR = { world: "var(--cat-world)", politics: "var(--cat-politics)", ai: "var(--cat-ai)", tech: "var(--cat-tech)", security: "var(--cat-security)", economy: "var(--cat-economy)",
  local: "var(--cat-local)", blogs: "var(--cat-blogs)" };
const FIRST_PARTY = new Set(["official-lab", "vendor-security", "government-advisory", "institutional"]);
const EXT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>';
const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z"/></svg>';
const VIEWS = [["trending", "Trending"], ["latest", "Latest"], ["starred", "Starred"]];

const $ = (sel) => document.querySelector(sel);
const state = { view: "trending", category: "", region: "", q: "", hours: 72, sources: new Set(), sort: "newest",
  hasImage: false, unreadOnly: false, offset: 0 };
let meta = null;
let trendingTimer = null;
let counts = null; // per-category totals for the current filters, once loaded
let unreadCounts = null; // per-category unread counts for the current filters
let trendingData = null;
let selected = -1; // index of the keyboard-selected card in the list

// ---- helpers ---------------------------------------------------------------

function timeAgo(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function displayUrl(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.length > 1 ? u.pathname.replace(/\/$/, "") : "";
    return u.hostname.replace(/^www\./, "") + path;
  } catch { return url; }
}

function safeHref(url) {
  return /^https?:\/\//i.test(url || "") ? url : "#";
}

// Categories from an imported OPML have no colour of their own: derive a stable one.
function catColor(cat) {
  if (CAT_COLOR[cat]) return CAT_COLOR[cat];
  let h = 0;
  for (const ch of cat || "") h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 45%)`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v; // only ever used with constant icon markup
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

// Search snippets arrive with \x02…\x03 around matched words; build them as text, never HTML.
function highlighted(snippet) {
  const out = document.createDocumentFragment();
  snippet.split("\x02").forEach((part, i) => {
    if (i === 0) { out.append(part); return; }
    const [hit, rest = ""] = part.split("\x03");
    out.append(el("mark", { text: hit }), rest);
  });
  return out;
}

function placeholder(category, label) {
  return el("div", { class: "placeholder", style: `--c:${catColor(category)}`, text: label });
}

function media(container, image, category, label) {
  container.replaceChildren();
  if (image) {
    const img = el("img", { src: image, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("error", () => container.replaceChildren(placeholder(category, label)), { once: true });
    container.append(img);
  } else {
    container.append(placeholder(category, label));
  }
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

let toastTimer = null;
function toast(text, undo) {
  const box = $("#toast");
  $("#toast-text").textContent = text;
  const btn = $("#toast-undo");
  btn.hidden = !undo;
  btn.onclick = undo ? () => { box.hidden = true; undo(); } : null;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 7000);
}

// ---- URL state -------------------------------------------------------------

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const legacyTab = p.get("tab"); // links from before the category dropdown
  const view = p.get("view");
  state.view = VIEWS.some(([id]) => id === view) ? view : legacyTab && legacyTab !== "trending" ? "latest" : "trending";
  const cat = p.get("cat") || legacyTab;
  state.category = meta?.categories[cat] ? cat : "";
  const region = p.get("region") || p.get("state"); // "state" is the pre-Politics name
  state.region = meta?.regions?.[state.category]?.[region] ? region : "";
  state.q = p.get("q") || "";
  state.hours = allowedHours().includes(+p.get("hours")) ? +p.get("hours") : 72;
  state.sources = new Set((p.get("sources") || "").split(",").filter(Boolean));
  state.sort = ["source", "relevance"].includes(p.get("sort")) ? p.get("sort") : "newest";
  state.hasImage = p.get("img") === "1";
  state.unreadOnly = p.get("unread") === "1";
}

function writeHash() {
  const p = new URLSearchParams();
  if (state.view !== "trending") p.set("view", state.view);
  if (state.category) p.set("cat", state.category);
  if (state.region) p.set("region", state.region);
  if (state.q) p.set("q", state.q);
  if (state.hours !== 72) p.set("hours", state.hours);
  if (state.sources.size) p.set("sources", [...state.sources].join(","));
  if (state.sort !== "newest") p.set("sort", state.sort);
  if (state.hasImage) p.set("img", "1");
  if (state.unreadOnly) p.set("unread", "1");
  const hash = p.toString();
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname);
}

function filtersActive() {
  return state.category || state.region || state.q || state.hours !== 72 || state.sources.size
    || state.sort !== "newest" || state.hasImage || state.unreadOnly;
}

// ---- chrome ----------------------------------------------------------------

function renderTabs() {
  const unread = unreadCounts ? Object.values(unreadCounts).reduce((a, b) => a + b, 0) : null;
  const badge = { latest: unread, starred: meta?.starred || null };
  $("#tabs").replaceChildren(...VIEWS.map(([id, label]) => {
    const btn = el("button", { class: "tab", role: "tab", type: "button", "aria-selected": String(state.view === id) });
    btn.append(label);
    if (badge[id] != null && (id !== "latest" || state.view !== "starred")) {
      btn.append(el("span", { class: "tab__count", text: String(badge[id]),
        title: id === "latest" ? "Unread stories for the current filters" : "Starred stories" }));
    }
    btn.addEventListener("click", () => { state.view = id; state.offset = 0; writeHash(); render(); });
    return btn;
  }));
}

// Feed ids for the chosen region (a state in Local, US/International in
// Politics); null when the category has no regions or none is chosen.
function regionSources() {
  if (!state.region || !meta.regions?.[state.category]) return null;
  return new Set(meta.sources
    .filter((s) => s.category === state.category && s.region === state.region).map((s) => s.id));
}

function renderRegion() {
  const wrap = $("#region-wrap");
  const regions = meta.regions?.[state.category];
  wrap.hidden = !regions;
  if (!regions) return;
  const select = $("#region");
  select.replaceChildren(
    el("option", { value: "", text: meta.region_all_labels?.[state.category] || "All regions" }),
    ...Object.entries(regions).map(([code, name]) => el("option", { value: code, text: name })));
  select.value = state.region;
}

// Category dropdown: unread counts in the reader views, story counts in Trending.
function renderCategory() {
  const select = $("#category");
  let tally = unreadCounts;
  let suffix = " unread";
  if (state.view === "trending" && trendingData) {
    tally = {};
    for (const s of trendingData.stories) tally[s.category] = (tally[s.category] || 0) + 1;
    suffix = "";
  } else if (state.view === "starred") {
    tally = counts;
    suffix = "";
  }
  const label = (text, n) => (tally && n ? `${text} (${n}${suffix})` : text);
  const total = tally ? Object.values(tally).reduce((a, b) => a + b, 0) : 0;
  select.replaceChildren(
    el("option", { value: "", text: label("All categories", total) }),
    ...Object.entries(meta.categories).map(([id, name]) => el("option", { value: id, text: label(name, tally?.[id]) })));
  select.value = state.category;
  select.style.setProperty("--cat", state.category ? catColor(state.category) : "var(--accent)");
}

function allowedHours() {
  return HOURS.filter((h) => !meta || h <= meta.retention_days * 24);
}

function renderHours() {
  $("#hours").replaceChildren(...allowedHours().map((h) => {
    const b = el("button", { type: "button", role: "radio", "aria-checked": String(state.hours === h), text: hoursLabel(h) });
    b.addEventListener("click", () => { state.hours = h; state.offset = 0; changed(); });
    return b;
  }));
}

function renderSourcesPanel() {
  const panel = $("#sources-panel");
  const nodes = [];
  for (const [cat, label] of Object.entries(meta.categories)) {
    nodes.push(el("div", { class: "dropdown__group", text: label }));
    for (const src of meta.sources.filter((s) => s.category === cat)) {
      const box = el("input", { type: "checkbox", value: src.id, checked: state.sources.has(src.id) });
      box.addEventListener("change", () => {
        box.checked ? state.sources.add(src.id) : state.sources.delete(src.id);
        state.offset = 0;
        changed();
      });
      nodes.push(el("label", {}, box, src.name));
    }
  }
  panel.replaceChildren(...nodes);
}

function syncControls() {
  $("#q").value = state.q;
  $("#sort-relevance").hidden = !state.q;
  $("#sort").value = state.sort;
  $("#has-image").checked = state.hasImage;
  $("#unread-only").checked = state.unreadOnly;
  $("#clear").hidden = !filtersActive();
  $("#filters").classList.toggle("is-trending", state.view === "trending");
  $("#filters").classList.toggle("is-starred", state.view === "starred");
  $("#keys").hidden = state.view === "trending";
  renderCategory();
  renderRegion();
  const n = state.sources.size;
  $("#sources-label").textContent = n === 0 ? "All sources" : n === 1
    ? meta.sources.find((s) => state.sources.has(s.id))?.name ?? "1 source" : `${n} sources`;
  renderHours();
}

function renderMeta() {
  const updated = meta.last_refresh ? `updated ${timeAgo(meta.last_refresh)}` : "waiting for first poll";
  $("#meta").textContent = `${meta.total.toLocaleString()} stories in the last ${meta.window_hours} hours · ${updated}`;
  const parts = [];
  if (meta.stored > meta.total) {
    parts.push(`${meta.stored.toLocaleString()} stories from ${meta.sources.length} feeds are kept for ${meta.retention_days} days; starred stories are kept for good.`);
  }
  if (meta.feed_list_error) parts.push(`Feed list problem: ${meta.feed_list_error}. Using the last good version.`);
  $("#archive").textContent = parts.join(" ");
  $("#demo-badge").hidden = !meta.demo;
}

// ---- reader list -----------------------------------------------------------

function cards() {
  return [...$("#grid").querySelectorAll(".card")];
}

function adjustUnread(category, delta) {
  if (!unreadCounts) return;
  unreadCounts[category] = Math.max(0, (unreadCounts[category] || 0) + delta);
  renderTabs();
  renderCategory();
}

function paintRead(node, item) {
  const read = Boolean(item.read_at);
  node.classList.toggle("is-read", read);
  const btn = node.querySelector(".act--read");
  btn.textContent = read ? "Mark unread" : "Mark read";
  btn.title = read ? "Mark unread (m)" : "Mark read (m)";
}

function paintStar(node, item) {
  const btn = node.querySelector(".act--star");
  const on = Boolean(item.starred_at);
  btn.setAttribute("aria-pressed", String(on));
  btn.replaceChildren();
  btn.insertAdjacentHTML("beforeend", STAR);
  btn.append(on ? "Starred" : "Star");
  btn.title = on ? "Remove star (s)" : "Star (s)";
}

async function setRead(node, item, read) {
  if (Boolean(item.read_at) === read) return;
  const before = item.read_at;
  item.read_at = read ? Date.now() / 1000 : null;
  paintRead(node, item);
  adjustUnread(item.category, read ? -1 : 1);
  try {
    await post(`/api/items/${encodeURIComponent(item.id)}/read`, { read });
  } catch (err) {
    item.read_at = before;
    paintRead(node, item);
    adjustUnread(item.category, read ? 1 : -1);
    toast(`Couldn't save: ${err.message}`);
  }
}

async function toggleStar(node, item) {
  const on = !item.starred_at;
  item.starred_at = on ? Date.now() / 1000 : null;
  paintStar(node, item);
  try {
    await post(`/api/items/${encodeURIComponent(item.id)}/star`, { starred: on });
    meta.starred = Math.max(0, (meta.starred || 0) + (on ? 1 : -1));
    renderTabs();
  } catch (err) {
    item.starred_at = on ? null : Date.now() / 1000;
    paintStar(node, item);
    toast(`Couldn't save: ${err.message}`);
  }
}

function showSummary(node, text) {
  const box = node.querySelector(".card__summary");
  box.replaceChildren(el("span", { class: "card__summary-label", text: "Summary" }), el("p", { text }));
  box.hidden = false;
  node.querySelector(".act--summary").hidden = true;
}

async function summarize(node, item) {
  const btn = node.querySelector(".act--summary");
  btn.disabled = true;
  btn.textContent = "Summarizing…";
  try {
    const res = await post(`/api/items/${encodeURIComponent(item.id)}/summary`);
    item.summary = res.summary;
    showSummary(node, res.summary);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Summarize";
    toast(err.message);
  }
}

function card(item) {
  const node = $("#card-tpl").content.firstElementChild.cloneNode(true);
  node.dataset.id = item.id;
  node._item = item;
  const href = safeHref(item.url);
  const mediaLink = node.querySelector(".card__media");
  mediaLink.href = href;
  mediaLink.setAttribute("aria-label", item.title);
  media(mediaLink, item.image, item.category, item.source);

  const chip = node.querySelector(".chip");
  chip.textContent = item.source;
  chip.style.setProperty("--c", catColor(item.category));
  if (FIRST_PARTY.has(item.source_class)) chip.title = `First-party source (${item.source_class})`;
  const time = node.querySelector("time");
  time.dateTime = new Date(item.published * 1000).toISOString();
  time.textContent = timeAgo(item.published);
  time.title = new Date(item.published * 1000).toLocaleString();

  const title = node.querySelector(".card__title a");
  title.href = href;
  title.textContent = item.title;
  const desc = node.querySelector(".card__desc");
  if (item.snippet) desc.replaceChildren(highlighted(item.snippet));
  else desc.textContent = item.description;
  desc.hidden = !item.snippet && !item.description;

  if (item.also?.length) {
    const also = node.querySelector(".card__also");
    also.append("Also in ");
    item.also.forEach((a, i) => {
      if (i) also.append(", ");
      also.append(el("a", { href: safeHref(a.url), target: "_blank", rel: "noopener noreferrer", text: a.source }));
    });
    also.hidden = false;
  }

  const url = node.querySelector(".card__url");
  url.href = href;
  url.append(el("span", { text: displayUrl(item.url) }));
  url.insertAdjacentHTML("beforeend", EXT);

  // Opening the article marks it read.
  for (const a of [title, mediaLink, url]) a.addEventListener("click", () => setRead(node, item, true));
  node.querySelector(".act--read").addEventListener("click", () => setRead(node, item, !item.read_at));
  node.querySelector(".act--star").addEventListener("click", () => toggleStar(node, item));
  const sumBtn = node.querySelector(".act--summary");
  if (item.summary) showSummary(node, item.summary);
  else if (item.summarizable) {
    sumBtn.hidden = false;
    sumBtn.addEventListener("click", () => summarize(node, item));
  }
  node.addEventListener("click", () => select(cards().indexOf(node), false));
  paintRead(node, item);
  paintStar(node, item);
  return node;
}

function listParams() {
  const params = new URLSearchParams({ hours: state.hours, sort: state.sort });
  if (state.category) params.set("category", state.category);
  if (state.q) params.set("q", state.q);
  // A chosen region narrows the source filter to that region's feeds.
  const inRegion = regionSources();
  let sources = [...state.sources];
  if (inRegion) sources = sources.length ? sources.filter((id) => inRegion.has(id)) : [...inRegion];
  if (inRegion && !sources.length) sources = ["none"]; // picked sources are all outside the region
  if (sources.length) params.set("sources", sources.join(","));
  if (state.hasImage) params.set("has_image", "true");
  if (state.view === "starred") params.set("starred", "true");
  return params;
}

async function loadFeed(append = false) {
  const grid = $("#grid");
  if (!append) {
    selected = -1;
    grid.replaceChildren(...Array.from({ length: 6 }, () => el("div", { class: "skeleton" })));
  }
  const params = listParams();
  params.set("limit", PAGE_SIZE);
  params.set("offset", state.offset);
  if (state.unreadOnly && state.view !== "starred") params.set("unread", "true");

  let data;
  try {
    data = await api(`/api/items?${params}`);
  } catch (err) {
    grid.replaceChildren(el("div", { class: "empty" }, el("h3", { text: "Couldn't load stories" }), el("p", { text: String(err.message) })));
    return;
  }
  counts = data.counts;
  unreadCounts = data.unread_counts;
  renderTabs();
  renderCategory();
  const nodes = data.items.map(card);
  if (append) grid.append(...nodes);
  else if (nodes.length) grid.replaceChildren(...nodes);
  else grid.replaceChildren(emptyState());

  const shown = Math.min(state.offset + PAGE_SIZE, data.total);
  const noun = state.view === "starred" ? "starred stories" : state.q ? "matches" : "stories";
  $("#count").textContent = data.total ? `Showing ${shown} of ${data.total} ${noun}` : "";
  $("#more").hidden = shown >= data.total;
}

function emptyState() {
  if (state.view === "starred") {
    return el("div", { class: "empty" }, el("h3", { text: "No starred stories" }),
      el("p", { text: "Star a story with its Star button or the s key to keep it here for good." }));
  }
  if (state.unreadOnly && meta.total) {
    return el("div", { class: "empty" }, el("h3", { text: "All caught up" }),
      el("p", { text: "Nothing unread for these filters. Turn off Unread only to see everything." }));
  }
  return el("div", { class: "empty" },
    el("h3", { text: meta.total ? "No stories match" : "No stories yet" }),
    el("p", { text: meta.total ? "Try a wider time window or fewer filters." : "Feeds are being polled. Check back in a minute." }));
}

async function markAllRead() {
  const params = listParams();
  const scope = state.category ? meta.categories[state.category] : "these filters";
  try {
    const { changed: ids } = await post(`/api/items/mark-all-read?${params}`);
    if (!ids.length) { toast("Nothing unread here."); return; }
    loadFeed();
    toast(`Marked ${ids.length} ${ids.length === 1 ? "story" : "stories"} in ${scope} read.`, async () => {
      await post("/api/items/read", { ids, read: false });
      loadFeed();
      toast("Restored as unread.");
    });
  } catch (err) {
    toast(`Couldn't mark read: ${err.message}`);
  }
}

// ---- keyboard --------------------------------------------------------------

function select(index, scroll = true) {
  const list = cards();
  if (!list.length) return;
  selected = Math.max(0, Math.min(index, list.length - 1));
  list.forEach((c, i) => c.classList.toggle("is-selected", i === selected));
  if (scroll) list[selected].scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function selectedCard() {
  return cards()[selected] || null;
}

function onKey(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  if (e.key === "Escape") { $("#sources-panel").hidden = true; $("#sources-btn").setAttribute("aria-expanded", "false"); return; }
  if (state.view === "trending") return;
  const node = selectedCard();
  switch (e.key) {
    case "j": {
      const list = cards();
      if (selected >= list.length - 1 && !$("#more").hidden) {
        $("#more").click(); // load the next page, then move on
      }
      select(selected + 1);
      break;
    }
    case "k": select(selected - 1); break;
    case "m": if (node) setRead(node, node._item, !node._item.read_at); break;
    case "s": if (node) toggleStar(node, node._item); break;
    case "o": case "Enter":
      if (node) { node.querySelector(".card__title a").click(); }
      break;
    case "/": e.preventDefault(); $("#q").focus(); break;
    case "A": if (e.shiftKey) markAllRead(); break;
    default: return;
  }
  e.preventDefault();
}

// ---- trending view ---------------------------------------------------------

function story(s, rank, aiWritten) {
  const lead = rank === 1;
  const m = el("div", { class: "story__media" });
  media(m, s.image, s.category, meta.categories[s.category] || "");
  m.append(el("span", { class: "story__rank", text: String(rank), title: `Rank ${rank} of today's trending stories` }));

  const limit = lead ? 6 : 4;
  const sources = el("ul", { class: "story__sources" }, ...s.items.slice(0, limit).map((it) =>
    el("li", {},
      el("a", { class: "src-title", href: safeHref(it.url), target: "_blank", rel: "noopener noreferrer", text: it.title }),
      el("span", { class: "src-meta" },
        el("span", { class: "src-name", text: it.source }),
        FIRST_PARTY.has(it.source_class) ? el("span", { class: "src-first", text: "first-party", title: "The organization's own announcement; not independent coverage" }) : null,
        el("span", { text: timeAgo(it.published) })))));
  const more = s.items.length - limit;

  const chip = el("span", { class: "chip", text: meta.categories[s.category] || s.category, style: `--c:${catColor(s.category)}` });
  return el("article", { class: `story${lead ? " story--lead" : ""}` }, m,
    el("div", { class: "story__body" },
      el("div", { class: "card__meta" }, chip, el("span", { class: "dot", text: "·" }),
        el("span", { text: `${s.source_count} sources` }), el("span", { class: "dot", text: "·" }),
        el("span", { text: `latest ${timeAgo(s.latest)}` })),
      el("h3", { class: "story__title" },
        el("a", { href: safeHref(s.items[0]?.url), target: "_blank", rel: "noopener noreferrer", text: s.headline })),
      el("p", { class: "story__summary", text: s.summary }),
      // Only an AI writes a real reason; keyword clustering would just restate the source count.
      aiWritten && s.why_trending ? el("p", { class: "story__why", text: s.why_trending }) : null,
      sources,
      more > 0 ? el("p", { class: "src-more", text: `${more} more ${more === 1 ? "source" : "sources"}` }) : null));
}

async function loadTrending(force = false) {
  clearTimeout(trendingTimer);
  const box = $("#trending");
  if (!box.children.length || force) box.replaceChildren(...Array.from({ length: 3 }, () => el("div", { class: "skeleton" })));
  let data;
  try {
    data = force ? await post("/api/trending/refresh") : await api("/api/trending");
  } catch (err) {
    box.replaceChildren(el("div", { class: "empty" }, el("h3", { text: "Couldn't load trending stories" }), el("p", { text: String(err.message) })));
    return;
  }

  trendingData = data;
  renderTrending();
  if (data.running || !data.generated_at) trendingTimer = setTimeout(() => loadTrending(), 4000);
}

function renderTrending() {
  const data = trendingData;
  const box = $("#trending");
  renderCategory();
  const sub = $("#trending-sub");
  sub.replaceChildren();
  if (data.mode === "ai" || data.mode === "claude") {
    const badge = el("span", { class: "badge badge--ai", title: data.model ? `${data.provider} · ${data.model}` : "" });
    const by = data.provider === "anthropic" || data.mode === "claude" ? "Claude" : data.model || "AI";
    badge.append(`Curated by ${by}`);
    sub.append(badge);
  } else if (data.mode === "heuristic") {
    sub.append(el("span", { class: "badge badge--demo", text: "Keyword clustering" }));
  }
  sub.append(data.generated_at ? `Clustered from the last 72 hours · ${timeAgo(data.generated_at)}` : "Analyzing headlines…");

  // AI requests are limited to one per ai_refresh_hours; say when the next one may run.
  const btn = $("#trending-refresh");
  const nextAt = data.next_ai_refresh_at ? new Date(data.next_ai_refresh_at * 1000) : null;
  btn.disabled = Boolean(nextAt) || data.running;
  btn.title = nextAt ? `The AI refreshes at most every ${data.ai_refresh_hours} hours` : "";
  if (nextAt) {
    const when = nextAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    sub.append(el("span", { class: "next-ai", text: `Next AI update after ${when}` }));
  }

  const inRegion = regionSources();
  const stories = data.stories.filter((s) => (!state.category || s.category === state.category)
    && (!inRegion || s.items.some((it) => inRegion.has(it.source_id))));
  const pending = data.running || !data.generated_at;
  if (stories.length) {
    const aiWritten = data.mode === "ai" || data.mode === "claude";
    box.replaceChildren(...stories.map((s, i) => story(s, i + 1, aiWritten)));
  } else if (data.stories.length) {
    box.replaceChildren(el("div", { class: "empty" },
      el("h3", { text: `Nothing trending in ${inRegion ? meta.regions[state.category][state.region] : meta.categories[state.category]}` }),
      el("p", { text: "Pick another category, or check back as coverage builds." })));
  } else {
    box.replaceChildren(el("div", { class: "empty" },
      el("h3", { text: pending ? "Finding trending stories…" : "Nothing trending yet" }),
      el("p", { text: pending ? "This takes a few seconds." : "Stories appear here once several outlets cover the same event." })));
  }
}

// ---- wiring ----------------------------------------------------------------

function render() {
  const trending = state.view === "trending";
  $("#trending-view").hidden = !trending;
  $("#feed-view").hidden = trending;
  syncControls();
  renderTabs();
  if (trending) loadTrending();
  else loadFeed();
}

function changed() {
  writeHash();
  syncControls();
  loadFeed();
}

function wire() {
  let debounce;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      const q = e.target.value.trim();
      // Searching switches to best-match order; clearing the search switches back.
      if (q && !state.q && state.sort === "newest") state.sort = "relevance";
      if (!q && state.sort === "relevance") state.sort = "newest";
      state.q = q;
      state.offset = 0;
      changed();
    }, 220);
  });
  $("#category").addEventListener("change", (e) => {
    state.category = e.target.value;
    state.region = ""; // regions belong to one category
    state.offset = 0;
    writeHash();
    syncControls();
    if (state.view === "trending") { if (trendingData) renderTrending(); } else loadFeed();
  });
  $("#region").addEventListener("change", (e) => {
    state.region = e.target.value;
    state.offset = 0;
    writeHash();
    syncControls();
    if (state.view === "trending") { if (trendingData) renderTrending(); } else loadFeed();
  });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; state.offset = 0; changed(); });
  $("#has-image").addEventListener("change", (e) => { state.hasImage = e.target.checked; state.offset = 0; changed(); });
  $("#unread-only").addEventListener("change", (e) => { state.unreadOnly = e.target.checked; state.offset = 0; changed(); });
  $("#clear").addEventListener("click", () => {
    Object.assign(state, { category: "", region: "", q: "", hours: 72, sort: "newest", hasImage: false, unreadOnly: false, offset: 0 });
    state.sources.clear();
    renderSourcesPanel();
    changed();
  });
  $("#more").addEventListener("click", () => { state.offset += PAGE_SIZE; loadFeed(true); });

  const panel = $("#sources-panel");
  const toggle = $("#sources-btn");
  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
  });
  document.addEventListener("click", (e) => {
    if (!$("#sources-dd").contains(e.target)) { panel.hidden = true; toggle.setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("keydown", onKey);

  $("#theme").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("nf-theme", root.dataset.theme); } catch {}
  });

  $("#refresh").addEventListener("click", async () => {
    const btn = $("#refresh");
    btn.disabled = true;
    btn.classList.add("is-spinning");
    try {
      await post("/api/refresh");
      meta = await api("/api/meta");
      renderMeta();
      render();
    } catch (err) {
      toast(`Refresh failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.classList.remove("is-spinning");
    }
  });
  $("#trending-refresh").addEventListener("click", () => loadTrending(true));
  window.addEventListener("hashchange", () => { readHash(); render(); });
}

async function boot() {
  meta = await api("/api/meta");
  readHash();
  renderMeta();
  renderSourcesPanel();
  wire();
  // Counts come from the item endpoint; fetch once so the Trending view has them too.
  api(`/api/items?limit=1`).then((d) => {
    counts = counts || d.counts;
    unreadCounts = unreadCounts || d.unread_counts;
    renderTabs();
    renderCategory();
  }).catch(() => {});
  render();
  setInterval(async () => {
    try { meta = await api("/api/meta"); renderMeta(); renderTabs(); } catch {}
  }, 60_000);
}

boot().catch((err) => {
  $("#meta").textContent = `Couldn't reach the server: ${err.message}`;
});
