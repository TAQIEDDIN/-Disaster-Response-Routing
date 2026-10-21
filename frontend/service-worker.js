const SHELL_CACHE = 'afet-rota-shell-v1';
const TILE_CACHE = 'afet-rota-map-tiles-v1';
const CDN_ASSETS = [
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
    'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js',
    'https://cdn.jsdelivr.net/npm/sweetalert2@11',
    'https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js',
    'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore-compat.js'
];
const APP_SHELL = [
    '/',
    '/static/index.html',
    '/static/style2.css',
    '/static/application.js',
    '/static/firebase-config.js'
];

self.addEventListener('install', event => {
    event.waitUntil((async () => {
        const cache = await caches.open(SHELL_CACHE);
        await Promise.all(APP_SHELL.map(async url => {
            try {
                const response = await fetch(url, { cache: 'reload' });
                if (response.ok) await cache.put(url, response);
            } catch {
                // Keep installation available when only part of the app server responds.
            }
        }));
        await Promise.all(CDN_ASSETS.map(async url => {
            try {
                const response = await fetch(new Request(url, { mode: 'no-cors' }));
                if (response.ok || response.type === 'opaque') await cache.put(url, response);
            } catch {
                // CDN assets stay optional if the external host is unavailable during install.
            }
        }));
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', event => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names.filter(name => name.startsWith('afet-rota-') && name !== SHELL_CACHE)
            .map(name => caches.delete(name)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== 'GET') return;

    if (url.origin !== self.location.origin) {
        const isCdnAsset = CDN_ASSETS.includes(request.url);
        const isSatelliteTile = url.hostname === 'server.arcgisonline.com' &&
            url.pathname.startsWith('/ArcGIS/rest/services/World_Imagery/MapServer/tile/');
        if (!isCdnAsset && !isSatelliteTile) return;
        event.respondWith((async () => {
            const cache = await caches.open(isSatelliteTile ? TILE_CACHE : SHELL_CACHE);
            try {
                const response = await fetch(request);
                if (response.ok || response.type === 'opaque') {
                    await cache.put(request, response.clone());
                    if (isSatelliteTile) {
                        const keys = await cache.keys();
                        await Promise.all(keys.slice(0, Math.max(0, keys.length - 250)).map(key => cache.delete(key)));
                    }
                }
                return response;
            } catch {
                return (await cache.match(request)) || Response.error();
            }
        })());
        return;
    }
    if (url.pathname.startsWith('/api/')) return;

    const isAppShell = url.pathname === '/' || APP_SHELL.includes(url.pathname);
    if (!isAppShell) return;
    event.respondWith((async () => {
        const cache = await caches.open(SHELL_CACHE);
        try {
            const response = await fetch(request);
            if (response.ok) await cache.put(request, response.clone());
            return response;
        } catch {
            return (await cache.match(request)) || (await cache.match('/')) || Response.error();
        }
    })());
});
