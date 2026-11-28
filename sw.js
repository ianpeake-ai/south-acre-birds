// Keeps the app opening offline. Data is always fetched fresh when online.
var SHELL = "southacre-shell-v1";
var MEDIA = "southacre-media-v1";
var SHELL_FILES = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest",
                   "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(SHELL).then(function (c) { return c.addAll(SHELL_FILES); }));
  self.skipWaiting();
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== SHELL && k !== MEDIA; })
                           .map(function (k) { return caches.delete(k); }));
  }));
  self.clients.claim();
});

self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET") return;

  // today.json: network only (the page keeps its own offline copy)
  if (url.pathname.endsWith("/data/today.json")) return;

  // pictures, clips and fonts: cache as they're seen, serve the cache when offline
  var isMedia = url.pathname.indexOf("/data/") !== -1 ||
                url.hostname.indexOf("fonts.g") === 0;
  if (isMedia) {
    e.respondWith(caches.open(MEDIA).then(function (c) {
      return fetch(e.request).then(function (r) {
        if (r.ok || r.type === "opaque") c.put(e.request, r.clone());
        return r;
      }).catch(function () { return c.match(e.request); });
    }));
    return;
  }

  // the app itself: network first so updates arrive, cache when offline
  e.respondWith(fetch(e.request).then(function (r) {
    var copy = r.clone();
    caches.open(SHELL).then(function (c) { c.put(e.request, copy); });
    return r;
  }).catch(function () { return caches.match(e.request); }));
});
