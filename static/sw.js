/* Devil Video Downloader - service worker. Only the page itself is cached (so the app opens instantly).
   Downloads, videos and the API always go to the server. */
const CACHE = "errorhub-v17";
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(["/", "/icons/icon-192.png"])).catch(() => {})); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin) return;
  if (u.pathname.startsWith("/api/") || u.pathname.startsWith("/media/")) return;
  if (r.mode === "navigate") {
    e.respondWith(fetch(r).then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put("/", copy)); return res; })
      .catch(() => caches.match("/")));
  }
});
