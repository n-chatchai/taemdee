// TaemDee service worker — Web Push delivery + offline navigation fallback.
//
// Registered eagerly from _partials/pwa_head.html on every page so the offline
// page is in cache before the network ever drops. Push subscription flows in
// push-subscribe.js / push-prompt.js reuse the same registration.

const OFFLINE_CACHE = 'td-offline-v3';
const OFFLINE_URL = '/static/offline.html';
const NAV_TIMEOUT_MS = 6000;
// Statuses that indicate the origin is unreachable / proxy errors rather
// than a genuine app error. Covers the Cloudflare 5xx family (520-530)
// plus the standard 502/503/504. We swap these for the offline page so
// users don't see a Cloudflare branded error inside the PWA.
const ORIGIN_DOWN_STATUSES = new Set([
  502, 503, 504,
  520, 521, 522, 523, 524, 525, 526, 527, 530,
]);

async function tryPrecacheOffline() {
  try {
    const cache = await caches.open(OFFLINE_CACHE);
    // {cache: 'reload'} bypasses the HTTP cache so we always pull a fresh copy.
    await cache.add(new Request(OFFLINE_URL, { cache: 'reload' }));
  } catch (_) {
    // Origin might be down at install time — don't block SW install. The
    // fetch handler opportunistically caches on the next successful nav.
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    await tryPrecacheOffline();
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

async function getOfflineResponse() {
  const cache = await caches.open(OFFLINE_CACHE);
  let res = await cache.match(OFFLINE_URL);
  if (res) return res;
  // Cache missed at install time (origin was down). Try once more now.
  await tryPrecacheOffline();
  res = await cache.match(OFFLINE_URL);
  return res || null;
}

function fetchWithTimeout(req, ms) {
  // AbortController so a hung connection eventually surfaces as an error
  // and triggers the offline fallback instead of a perpetual spinner.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(req, { signal: ctrl.signal }).finally(() => clearTimeout(timer));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  // Only intercept top-level page navigations. Static assets, API calls, and
  // cross-origin requests pass through untouched so we don't accidentally
  // serve stale CSS or break POSTs.
  if (req.mode !== 'navigate') return;

  event.respondWith((async () => {
    try {
      const res = await fetchWithTimeout(req, NAV_TIMEOUT_MS);
      if (ORIGIN_DOWN_STATUSES.has(res.status)) {
        const offline = await getOfflineResponse();
        if (offline) return offline;
      }
      // Opportunistic refresh: if the offline page wasn't cached at install
      // time and this nav succeeded, prime it now for the next downtime.
      if (res.ok) {
        event.waitUntil((async () => {
          const cache = await caches.open(OFFLINE_CACHE);
          if (!(await cache.match(OFFLINE_URL))) {
            await tryPrecacheOffline();
          }
        })());
      }
      return res;
    } catch (_) {
      const offline = await getOfflineResponse();
      if (offline) return offline;
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
