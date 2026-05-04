// Web Push subscribe/unsubscribe helpers used by the customer-side
// "เปิดแจ้งเตือน" button. The button checks current state on mount and
// flips between subscribe and unsubscribe.
//
// Flow:
//   1. Register the service worker (idempotent — getRegistration first).
//   2. GET /push/vapid-public for the server's VAPID public key.
//   3. pushManager.subscribe({applicationServerKey}) — browser shows the
//      permission prompt, returns a subscription with endpoint + p256dh + auth.
//   4. POST those to /push/subscribe so DeeReach can target the customer.

(function () {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return; // Browser doesn't support Web Push — leave the UI button hidden.
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function arrayBufferToBase64Url(buffer) {
    const bytes = new Uint8Array(buffer);
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  async function getRegistration() {
    let reg = await navigator.serviceWorker.getRegistration();
    if (!reg) reg = await navigator.serviceWorker.register('/sw.js');
    return reg;
  }

  async function getPublicKey() {
    const r = await fetch('/push/vapid-public');
    if (!r.ok) throw new Error('vapid-not-configured');
    const j = await r.json();
    return urlBase64ToUint8Array(j.public_key);
  }

  async function subscribePush() {
    const reg = await getRegistration();
    const applicationServerKey = await getPublicKey();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });
    const fd = new FormData();
    fd.set('endpoint', sub.endpoint);
    fd.set('p256dh', arrayBufferToBase64Url(sub.getKey('p256dh')));
    fd.set('auth', arrayBufferToBase64Url(sub.getKey('auth')));
    const r = await fetch('/push/subscribe', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('subscribe-failed');
    return sub;
  }

  async function unsubscribePush() {
    const reg = await navigator.serviceWorker.getRegistration();
    if (reg) {
      const sub = await reg.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
    }
    await fetch('/push/unsubscribe', { method: 'POST' });
  }

  async function getStatus() {
    try {
      const r = await fetch('/push/status');
      return r.ok ? r.json() : null;
    } catch (_) { return null; }
  }

  // True when (1) browser has a live pushManager subscription AND
  // (2) the server has the matching endpoint saved. If only one side
  // is on, we re-sync up so the next campaign actually delivers.
  async function isSubscribedAndSynced() {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg) return false;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return false;

    const status = await getStatus();
    if (!status) return false;

    if (!status.has_endpoint || !sub.endpoint.startsWith(status.endpoint_prefix)) {
      // Browser has a sub but server is empty / stale — re-upload so
      // they line up. Without this the badge looks ON but DeeReach
      // ships through LINE (or fails) because audience routing reads
      // shop.web_push_endpoint, not the browser-side state.
      const fd = new FormData();
      fd.set('endpoint', sub.endpoint);
      fd.set('p256dh', arrayBufferToBase64Url(sub.getKey('p256dh')));
      fd.set('auth', arrayBufferToBase64Url(sub.getKey('auth')));
      try { await fetch('/push/subscribe', { method: 'POST', body: fd }); } catch (_) {}
    }
    return true;
  }

  // Wire the toggle button when the page declares one with [data-push-toggle].
  // The button text/state lives on the page (data-on-label / data-off-label).
  document.addEventListener('DOMContentLoaded', async () => {
    const btn = document.querySelector('[data-push-toggle]');
    if (!btn) return;
    const status = await getStatus();
    if (!status || !status.vapid_configured) { btn.style.display = 'none'; return; }

    async function paint() {
      const on = await isSubscribedAndSynced();
      btn.dataset.state = on ? 'on' : 'off';
      btn.textContent = on
        ? (btn.dataset.onLabel || 'แจ้งเตือนเปิดอยู่')
        : (btn.dataset.offLabel || 'เปิดแจ้งเตือน');
    }

    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        if (btn.dataset.state === 'on') await unsubscribePush();
        else await subscribePush();
      } catch (e) {
        (window._tdShowFlash || alert)('เปิดแจ้งเตือนไม่สำเร็จ — ลองอีกครั้ง', 'error');
      }
      btn.disabled = false;
      await paint();
    });

    await paint();
  });
})();
