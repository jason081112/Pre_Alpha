// Service Worker: cache-first, offline-ready for core assets
const CACHE = 'alpha-v1';
const PRECACHE = [
  '/',
  '/index.html',
  '/static/manifest.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE))
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Skip non-GET
  if (req.method !== 'GET') return;

  // Cache-first strategy
  e.respondWith(
    caches.match(req).then((cached) => {
      if (cached) {
        return cached;
      }
      // Fetch and cache in background
      return fetch(req).then((res) => {
        if (!res || res.status !== 200 || res.type !== 'basic') {
          return res;
        }
        const clone = res.clone();
        caches.open(CACHE).then((cache) => {
          cache.put(req, clone);
        });
        return res;
      }).catch(() => {
        // Offline fallback for HTML
        if (url.origin === location.origin) {
          return caches.match('/');
        }
        throw new Error('offline');
      });
    })
  );
});
