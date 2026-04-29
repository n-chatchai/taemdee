// Push.prompt — auto-trigger bottom sheet on first PWA open.
//
// Trigger conditions (per design Push.prompt spec):
//   1. Running as installed PWA (display-mode: standalone OR navigator.standalone).
//   2. Notification.permission === 'default' (not granted, not denied).
//   3. Server has VAPID configured (otherwise the subscribe call would 503).
//   4. No active dismissal cooldown (14 days after a "ไว้ก่อน" tap).
//
// Re-ask after C5 redemption is deferred — would need a separate trigger
// from the redeem flow to clear the cooldown.

(function () {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    return; // Browser doesn't support Web Push — nothing to ask for.
  }

  const COOLDOWN_KEY = 'td_push_prompt_until';
  const DONE_KEY = 'td_push_prompt_done';
  const COOLDOWN_DAYS = 14;

  function isPWA() {
    if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true;
    if (window.navigator.standalone === true) return true;  // iOS Safari
    return false;
  }

  function inCooldown() {
    if (localStorage.getItem(DONE_KEY)) return true;
    const until = parseInt(localStorage.getItem(COOLDOWN_KEY) || '0', 10);
    return until && Date.now() < until;
  }

  async function vapidConfigured() {
    try {
      const r = await fetch('/push/vapid-public');
      return r.ok;
    } catch (_) { return false; }
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

  async function subscribePush() {
    let reg = await navigator.serviceWorker.getRegistration();
    if (!reg) reg = await navigator.serviceWorker.register('/static/js/sw.js');
    const r = await fetch('/push/vapid-public');
    if (!r.ok) throw new Error('vapid-not-configured');
    const j = await r.json();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(j.public_key),
    });
    const fd = new FormData();
    fd.set('endpoint', sub.endpoint);
    fd.set('p256dh', arrayBufferToBase64Url(sub.getKey('p256dh')));
    fd.set('auth', arrayBufferToBase64Url(sub.getKey('auth')));
    const r2 = await fetch('/push/subscribe', { method: 'POST', body: fd });
    if (!r2.ok) throw new Error('subscribe-failed');
  }

  function hide(prompt) {
    prompt.style.display = 'none';
    prompt.setAttribute('aria-hidden', 'true');
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const prompt = document.getElementById('push-prompt');
    if (!prompt) return;
    if (!isPWA()) return;
    if (Notification.permission !== 'default') return;
    if (inCooldown()) return;
    if (!(await vapidConfigured())) return;

    // All clear — show the sheet.
    prompt.style.display = 'flex';
    prompt.setAttribute('aria-hidden', 'false');

    const accept = prompt.querySelector('[data-pp-accept]');
    accept.addEventListener('click', async () => {
      accept.disabled = true;
      try {
        await subscribePush();
        // Browser remembers the answer; flag so we don't re-prompt anywhere.
        localStorage.setItem(DONE_KEY, '1');
      } catch (_) {
        // Subscribe failure (denied prompt, network, etc.) — same dismissal
        // path so we don't pester. Browser denial sticks; cooldown handles
        // transient errors.
        localStorage.setItem(COOLDOWN_KEY, String(Date.now() + COOLDOWN_DAYS * 86400000));
      }
      hide(prompt);
    });

    prompt.querySelectorAll('[data-pp-dismiss]').forEach((el) => {
      el.addEventListener('click', () => {
        localStorage.setItem(COOLDOWN_KEY, String(Date.now() + COOLDOWN_DAYS * 86400000));
        hide(prompt);
      });
    });
  });
})();
