// TaemDee service worker — Web Push delivery + offline navigation fallback.
//
// Registered eagerly from _partials/pwa_head.html on every page so the offline
// page is in cache before the network ever drops. Push subscription flows in
// push-subscribe.js / push-prompt.js reuse the same registration.

const OFFLINE_CACHE = 'td-offline-v2';
const OFFLINE_URL = '/static/offline.html';
// Statuses that indicate the origin is unreachable behind a proxy (Cloudflare
// 5xx family) rather than a genuine app error. We swap these for the offline
// page so users don't see a Cloudflare branded error inside the PWA.
const ORIGIN_DOWN_STATUSES = new Set([502, 503, 504, 521, 522, 523, 524]);

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(OFFLINE_CACHE);
    // {cache: 'reload'} bypasses the HTTP cache so we always pull a fresh copy
    // of the offline page when the SW updates.
    await cache.add(new Request(OFFLINE_URL, { cache: 'reload' }));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // Drop any old offline caches from previous SW versions.
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) => k.startsWith('td-offline-') && k !== OFFLINE_CACHE)
          .map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  // Only intercept top-level page navigations. Static assets, API calls, and
  // cross-origin requests pass through untouched so we don't accidentally
  // serve stale CSS or break POSTs.
  if (req.mode !== 'navigate') return;

  event.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (ORIGIN_DOWN_STATUSES.has(res.status)) {
        const cache = await caches.open(OFFLINE_CACHE);
        const offline = await cache.match(OFFLINE_URL);
        if (offline) return offline;
      }
      return res;
    } catch (_) {
      const cache = await caches.open(OFFLINE_CACHE);
      const offline = await cache.match(OFFLINE_URL);
      if (offline) return offline;
      // Cache miss (e.g. offline page evicted) — let the browser show its
      // default error rather than throwing inside the SW.
      return Response.error();
    }
  })());
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { title: 'แต้มดี', body: event.data ? event.data.text() : '' };
  }
  const title = payload.title || 'แต้มดี';
  const opts = {
    body: payload.body || '',
    icon: '/static/taemdee-icons/taemdee-icon-192.png',
    badge: '/static/taemdee-icons/taemdee-icon-32.png',
    data: { url: payload.url || '/my-cards' },
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/my-cards';
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // If a TaemDee tab already exists, focus it and navigate.
    for (const client of all) {
      if (client.url.includes('/my-cards') || client.url.includes(url)) {
        return client.focus().then((c) => c.navigate(url));
      }
    }
    return self.clients.openWindow(url);
  })());
});
