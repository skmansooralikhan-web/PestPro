// PCT CRM — minimal service worker for "Add to Home Screen" installability,
// plus offline access to a technician's own job card forms.
//
// Three tiers of behaviour:
//  1. /static/* assets       → cache-first (instant repeat loads)
//  2. /jobcards/start/<id>   → network-first, falling back to a cached copy
//     if the network is unreachable. tech_home.html explicitly warms this
//     cache (pct-crm-forms-v1) for today's visits while online, via the
//     Cache API directly — this handler is what serves them back offline.
//  3. everything else        → always network (live operational data)

const SHELL_CACHE = 'pct-crm-shell-v1';
const FORMS_CACHE = 'pct-crm-forms-v1';
const SHELL_ASSETS = [
  '/static/css/main.css',
  '/static/js/app.js',
  '/static/js/calendar.js',
  '/static/js/charts.js',
  '/static/js/jobcard.js',
  '/static/js/offline-queue.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== FORMS_CACHE)
        .map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return; // never intercept POSTs

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
    return;
  }

  if (url.pathname.startsWith('/jobcards/start/')) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          // Refresh the cached copy whenever we do have a connection, so it
          // doesn't go stale (e.g. inventory levels shown in the form).
          const copy = resp.clone();
          caches.open(FORMS_CACHE).then((cache) => cache.put(event.request, copy));
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
  }
});
