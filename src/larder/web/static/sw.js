// SPDX-License-Identifier: AGPL-3.0-only
// larder service worker. Served at /sw.js so its scope is the whole site.
//
// Static assets are cache-first and page navigations are network-first. The
// worker only keeps a response that is ok, was not redirected and is not
// marked no-store. It never keeps /pair/ or /admin/ URLs, and the precache
// list holds static files only, so the 401 "not set up" page is never stored.
// Bump CACHE when a precached file changes.

const CACHE = "larder-v1";
const PRECACHE = [
  "/static/larder.css",
  "/static/icons/larder-192.png",
  "/static/icons/larder-512.png",
  "/static/icons/larder-maskable-512.png",
];
const NEVER_CACHE = ["/pair/", "/admin/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

function isCacheable(url, response) {
  if (NEVER_CACHE.some((prefix) => url.pathname.startsWith(prefix))) {
    return false;
  }
  if (!response.ok || response.redirected) {
    return false;
  }
  const control = response.headers.get("Cache-Control") || "";
  return !control.toLowerCase().includes("no-store");
}

function store(request, url, response) {
  if (isCacheable(url, response)) {
    const copy = response.clone();
    caches.open(CACHE).then((cache) => cache.put(request, copy));
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }
  if (NEVER_CACHE.some((prefix) => url.pathname.startsWith(prefix))) {
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches
        .match(request)
        .then((hit) => hit || fetch(request).then((response) => store(request, url, response))),
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => store(request, url, response))
        .catch(() => caches.match(request).then((hit) => hit || Response.error())),
    );
  }
});
