/* Custo Plano: funciona sem internet.
 * - A página e os ícones ficam guardados no aparelho.
 * - Cronograma, plano de ataque e histórico: busca na rede; sem rede, usa a última cópia.
 * - A medição feita offline fica no próprio app (IndexedDB) e é enviada quando a conexão volta.
 */
const VERSAO = 'cp-app-v1';
const DADOS = 'cp-api';
const CASCA = ['/', '/manifest.webmanifest', '/web/icon.svg', '/web/icon-192.png', '/web/icon-512.png'];
const API_GUARDADA = ['/api/me', '/api/cronograma', '/api/plano', '/api/historico'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSAO).then((c) => c.addAll(CASCA)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== VERSAO && k !== DADOS).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function redePrimeiro(req, cache, chave) {
  return fetch(req).then((r) => {
    if (r.ok) { const c = r.clone(); caches.open(cache).then((cc) => cc.put(chave || req, c)); }
    return r;
  }).catch(() => caches.match(chave || req).then((r) => r || Promise.reject(new Error('offline'))));
}

function guardadoPrimeiro(req) {
  return caches.match(req).then((achado) => {
    const rede = fetch(req).then((r) => {
      if (r.ok || r.type === 'opaque') { const c = r.clone(); caches.open(VERSAO).then((cc) => cc.put(req, c)); }
      return r;
    }).catch(() => achado);
    return achado || rede;
  });
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (url.origin === location.origin) {
    if (req.mode === 'navigate' && url.pathname === '/') {
      e.respondWith(redePrimeiro(req, VERSAO, '/'));
      return;
    }
    if (API_GUARDADA.includes(url.pathname)) {
      e.respondWith(redePrimeiro(req, DADOS));
      return;
    }
    if (url.pathname.startsWith('/web/') || url.pathname === '/manifest.webmanifest') {
      e.respondWith(guardadoPrimeiro(req));
    }
    return; // exportações, relatório etc.: só com rede
  }

  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(guardadoPrimeiro(req));
  }
});
