/* 极简 Service Worker：只缓存静态资源，接口一律走网络，避免拿到过期内容。 */
const CACHE = "prism-v16";
const ASSETS = ["/", "/static/style.css?v=20261009-mobile1", "/static/app.js?v=20261009-mobile1", "/static/pen.js", "/manifest.webmanifest",
                "/favicon.ico", "/static/icons/mark.png", "/static/icons/icon-192.png",
                "/static/katex/katex.min.css", "/static/katex/katex.min.js"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;

  e.respondWith(
    fetch(e.request)
      .then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then(r => r || caches.match("/")))
  );
});
