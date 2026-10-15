// Keeps the app shell available when the connection drops. API calls are never cached:
// an SOS must always go to the real server, and old alerts must never be shown as new.
const CACHE = "raksha-shell-v1";
const SHELL = [
  "/", "/index.html", "/styles.css", "/manifest.webmanifest",
  "/js/app.js", "/js/api.js", "/js/ui.js",
  "/js/views/auth.js", "/js/views/home.js", "/js/views/contacts.js", "/js/views/alerts.js", "/js/views/account.js",
  "/fonts/atkinson-hyperlegible-latin-400-normal.woff2", "/fonts/atkinson-hyperlegible-latin-700-normal.woff2",
  "/icons/icon-192.png", "/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // Network first, so a new release shows up straight away; the cache is only the offline fallback.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/index.html"))),
  );
});
