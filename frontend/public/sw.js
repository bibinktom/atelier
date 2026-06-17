// Minimal service worker. Atelier intentionally does NOT cache responses:
// chats, file lists, and SSE streams must always reflect fresh server state,
// and a stale assistant turn shown from cache would be worse than a brief
// offline error. The SW exists purely so browsers consider the app
// installable (Chrome/Android requires a `fetch` handler, even a passthrough).
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  // Pure passthrough. Network only, no caching, no offline shell.
  return;
});
