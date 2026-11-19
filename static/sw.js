/* This app no longer installs itself as a web app. This file only removes the old service worker + its cache. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil((async () => {
  try { for (const k of await caches.keys()) await caches.delete(k); } catch (_) {}
  try { await self.registration.unregister(); } catch (_) {}
  try { for (const c of await self.clients.matchAll()) c.navigate(c.url); } catch (_) {}
})()));
