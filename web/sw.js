/* Volchino service worker — offline cache-first for app shell, network-first for /ws & /health */
const CACHE_NAME = 'volchino-v1';
const SHELL = ['/', '/styles.css', '/avatar.js', '/app.js', '/manifest.json',
               '/icons/icon-192.png', '/icons/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Never cache WebSocket upgrades or the health endpoint.
  if (url.pathname === '/ws' || url.pathname === '/health') return;
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
