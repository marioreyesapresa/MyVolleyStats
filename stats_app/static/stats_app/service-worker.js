/**
 * Service Worker de MyVolleyStats.
 *
 * - Cumple el requisito de instalación PWA (iOS/Android).
 * - Cachea SOLO assets estáticos (/static/…) con network-first y fallback
 *   a caché si no hay red. Así la UI ya abierta aguanta mejor cortes de
 *   cobertura en el pabellón.
 * - NUNCA cachea HTML de páginas ni respuestas /api/: el scout en vivo
 *   usa cola offline propia en modo_partido.html.
 */

const SW_VERSION = 'myvolleystats-sw-v2';
const STATIC_CACHE = `${SW_VERSION}-static`;

function esAssetEstatico(url) {
    return url.pathname.startsWith('/static/')
        || url.pathname === '/service-worker.js';
}

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(
            keys
                .filter((k) => k.startsWith('myvolleystats-sw-') && k !== STATIC_CACHE)
                .map((k) => caches.delete(k))
        );
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') {
        event.respondWith(fetch(req));
        return;
    }

    let url;
    try {
        url = new URL(req.url);
    } catch (e) {
        event.respondWith(fetch(req));
        return;
    }

    // Solo assets estáticos; HTML y /api/ pasan siempre a red.
    if (!esAssetEstatico(url)) {
        event.respondWith(fetch(req));
        return;
    }

    event.respondWith((async () => {
        const cache = await caches.open(STATIC_CACHE);
        try {
            const net = await fetch(req);
            if (net && net.ok) {
                cache.put(req, net.clone());
            }
            return net;
        } catch (err) {
            const cached = await cache.match(req);
            if (cached) return cached;
            throw err;
        }
    })());
});
