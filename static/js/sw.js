// TaemDee service worker — Web Push delivery + offline navigation fallback.
//
// Registered eagerly from _partials/pwa_head.html on every page so the offline
// page is in cache before the network ever drops. Push subscription flows in
// push-subscribe.js / push-prompt.js reuse the same registration.

const OFFLINE_CACHE = 'td-offline-v3';
const ASSET_CACHE = 'td-assets-v1';
const OFFLINE_URL = '/static/offline.html';
const NAV_TIMEOUT_MS = 6000;
// URL prefixes / paths to cache stale-while-revalidate. Pages already
// pin asset versions via ?v=ASSET_VERSION in templates, so each
// version-bumped URL is a fresh cache key — old entries fall out
// naturally when the cache hits its quota and are also pruned by the
// activate handler when a new ASSET_CACHE version ships.
const STATIC_PREFIXES = [
  '/static/css/',
  '/static/js/',
  '/static/taemdee-icons/',
  '/static/apps/',
];
const STATIC_EXACT = new Set(['/manifest.json', '/manifest_shop.json']);
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
    // Drop any old offline / asset caches from previous SW versions.
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) =>
        (k.startsWith('td-offline-') && k !== OFFLINE_CACHE) ||
        (k.startsWith('td-assets-') && k !== ASSET_CACHE)
      ).map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

function isStaticAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (STATIC_EXACT.has(url.pathname)) return true;
  for (const prefix of STATIC_PREFIXES) {
    if (url.pathname.startsWith(prefix)) return true;
  }
  return false;
}

async function staleWhileRevalidate(req) {
  // Serve cached immediately, revalidate in background. The next
  // request gets the fresh copy. Only successful (200-ish) responses
  // are written back so we don't poison the cache with a 404 / 500.
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(req);
  const fetchPromise = fetch(req).then((res) => {
    if (res && res.ok && res.type !== 'opaque') {
      cache.put(req, res.clone()).catch(() => {});
    }
    return res;
  }).catch(() => null);
  // Cached hit → return now and let the background fetch update for
  // next time. Cache miss → wait for the network and store on the way
  // through. If both fail, fall back to whatever fetch returned (incl.
  // a network error) so callers see a proper failure.
  return cached || fetchPromise;
}

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

  // Static asset stale-while-revalidate — the templates pin
  // ?v=ASSET_VERSION on every static URL, so a version bump on the
  // server-side hands us a brand-new cache key automatically and we
  // never serve genuinely stale CSS/JS. The win is the FOUC-free
  // first paint when the SW is warm: app.css comes from cache before
  // the network round-trip finishes, and the dock + page chrome
  // hydrate without the white-flash a cold-launch would otherwise
  // show.
  if (req.method === 'GET') {
    let url;
    try { url = new URL(req.url); } catch (_) { url = null; }
    if (url && isStaticAsset(url)) {
      event.respondWith(staleWhileRevalidate(req));
      return;
    }
  }

  // Only intercept top-level page navigations beyond this point.
  // API calls, POSTs, cross-origin requests pass through untouched.
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
