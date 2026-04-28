// TaemDee service worker — handles Web Push events from DeeReach.
// Registered by /static/js/push-subscribe.js when the customer enables
// notifications. The push event delivers an encrypted payload that pywebpush
// signs server-side; the SW unwraps it via showNotification.

self.addEventListener('install', (event) => {
  // Activate immediately on first install — no need to keep the old SW
  // around since this one only handles push and notificationclick.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
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
