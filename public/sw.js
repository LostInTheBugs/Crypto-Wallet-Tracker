// Crypto Wallet Tracker — Service Worker (PWA)
// Cache app shell for offline display; network-first for API calls.
var CACHE = "cwt-v1";
var SHELL = ["/", "/index.html", "/manifest.json", "/static/icon-192.png", "/static/icon-512.png"];

self.addEventListener("install", function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(cache) {
      return cache.addAll(SHELL).catch(function(err) {
        console.warn("sw install cache addAll:", err.message);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k) { return k !== CACHE; }).map(function(k) { return caches.delete(k); }));
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function(e) {
  var url = new URL(e.request.url);
  // Network-first for API — never cache sensitive portfolio data
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(
      fetch(e.request).catch(function() {
        return new Response(JSON.stringify({ error: "offline" }), { status: 503, headers: { "Content-Type": "application/json" } });
      })
    );
    return;
  }
  // Cache-first for app shell
  e.respondWith(
    caches.match(e.request).then(function(cached) {
      return cached || fetch(e.request);
    })
  );
});
