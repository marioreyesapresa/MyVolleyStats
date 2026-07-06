/**
 * Service Worker mínimo de Scout Rotation Pro.
 *
 * Objetivo único: cumplir el requisito de instalación de PWA en iOS/Android
 * (Safari/Chrome exigen un Service Worker registrado con un manejador de
 * `fetch`, aunque sea trivial, para permitir "Añadir a pantalla de inicio"
 * en modo standalone). Deliberadamente NO cachea nada ni intercepta la
 * lógica de red: todas las peticiones pasan de largo directas a la red.
 *
 * Esto es intencional. El modo partido depende de datos en tiempo real
 * (marcador, rotaciones, estadísticas) y ya implementa su propio mecanismo
 * de reintentos con backoff exponencial en el cliente (`fetchConReintentos`
 * en modo_partido.html). Una estrategia de caché aquí (cache-first o
 * stale-while-revalidate) podría servir datos obsoletos del partido en
 * curso, lo cual sería mucho peor que no tener Service Worker. Si en el
 * futuro se añade soporte offline real, debe hacerse con mucho cuidado y
 * solo para assets estáticos (CSS/JS/iconos), nunca para las respuestas de
 * `/api/`.
 */

const SW_VERSION = 'scout-rotation-pro-sw-v1';

self.addEventListener('install', (event) => {
    // Activa la nueva versión del Service Worker sin esperar a que se
    // cierren las pestañas/instancias abiertas de la PWA.
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    // Toma el control inmediato de los clientes ya abiertos (evita tener
    // que recargar manualmente tras la primera instalación).
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    // Pass-through explícito: no se cachea nada, no se interfiere con los
    // reintentos ni con las peticiones a las APIs de scouting en vivo.
    event.respondWith(fetch(event.request));
});
