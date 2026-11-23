// NetWatch PWA - Service Worker
// Versión de caché: incrementar para forzar actualización
const CACHE_VERSION = 'nw-v1';
const CACHE_STATIC = ['/movil'];

// Instalar: pre-cachear la shell
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_VERSION).then(cache => cache.addAll(CACHE_STATIC).catch(() => {}))
  );
});

// Activar: limpiar cachés viejas
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_VERSION).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first para Firebase, cache-first para shell
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Firebase y googleapis: solo red (no cachear tokens ni datos)
  if (url.hostname.includes('firebase') || url.hostname.includes('googleapis')) {
    return; // comportamiento por defecto (red)
  }

  // La shell /movil: network-first con fallback a caché
  if (url.pathname.startsWith('/movil')) {
    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then(c => c.put(event.request, copy));
          }
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
  }
});

// Push notifications (para cuando se implemente servidor VAPID)
self.addEventListener('push', event => {
  if (!event.data) return;
  let data;
  try { data = event.data.json(); } catch { return; }
  event.waitUntil(
    self.registration.showNotification(data.title || 'NetWatch', {
      body: data.body || '',
      icon: '/movil/icons/icon-192.png',
      badge: '/movil/icons/icon-192.png',
      tag: data.tag || 'netwatch',
      data: { url: '/movil' }
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      const target = (event.notification.data && event.notification.data.url) || '/movil';
      for (const c of clients) {
        if (c.url.includes('/movil') && 'focus' in c) return c.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
