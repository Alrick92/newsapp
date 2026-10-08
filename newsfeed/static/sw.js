/* Newsfeed service worker.
 *
 * - The app itself (page, script, styles, fonts, icons) is cached on install,
 *   so the app opens instantly and works offline. __VERSION__ is replaced by
 *   the server with a hash of those files: a new deploy installs a new worker,
 *   which replaces the old cache.
 * - Story lists and trending (GET /api/...) are network-first: always fresh
 *   when online; when offline, the last copy of that exact view is served with
 *   an X-Newsfeed-Offline header so the page can say it's showing saved stories.
 * - Everything else (POSTs such as read/star, OPML export, publisher images)
 *   goes straight to the network and is never cached.
 */
const VERSION = "__VERSION__";
const SHELL = `newsfeed-shell-${VERSION}`;
const DATA = "newsfeed-data-v1";
const DATA_LIMIT = 80; // saved API responses (each distinct view/filter is one entry)

const SHELL_FILES = [
  "/",
  "/static/app.js",
  "/static/styles.css",
  "/manifest.webmanifest",
  "/static/fonts/lora-variable.woff2",
  "/static/fonts/lora-italic-400.woff2",
  "/static/fonts/poppins-400.woff2",
  "/static/fonts/poppins-500.woff2",
  "/static/fonts/poppins-600.woff2",
  "/static/icons/icon-192.png",
  "/static/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL)
      .then((cache) => cache.addAll(SHELL_FILES.map((url) => new Request(url, { cache: "reload", credentials: "same-origin" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("newsfeed-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function trimData() {
  const cache = await caches.open(DATA);
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - DATA_LIMIT)).map((k) => cache.delete(k)));
}

async function networkFirstApi(request) {
  const cache = await caches.open(DATA);
  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(request, response.clone());
      trimData();
    }
    return response;
  } catch {
    const saved = await cache.match(request);
    if (!saved) {
      return new Response(JSON.stringify({ detail: "You're offline, and this view hasn't been saved yet." }),
        { status: 503, headers: { "Content-Type": "application/json" } });
    }
    const headers = new Headers(saved.headers);
    headers.set("X-Newsfeed-Offline", saved.headers.get("Date") || "");
    return new Response(saved.body, { status: saved.status, statusText: saved.statusText, headers });
  }
}

async function page(request) {
  try {
    const response = await fetch(request);
    if (response.ok) (await caches.open(SHELL)).put("/", response.clone());
    return response;
  } catch {
    return (await caches.match("/", { cacheName: SHELL })) || Response.error();
  }
}

async function cacheFirst(request) {
  return (await caches.match(request, { cacheName: SHELL })) || fetch(request);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // publisher images etc.
  if (request.mode === "navigate") { event.respondWith(page(request)); return; }
  if (url.pathname.startsWith("/api/")) { event.respondWith(networkFirstApi(request)); return; }
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(cacheFirst(request));
  }
});
