const CACHE='campusride-admin-cloud-v3';
const STATIC=['/assets/liquid.css','/assets/liquid.js','/assets/favicon.svg','/assets/icon.svg','/admin'];
self.addEventListener('install', event=>event.waitUntil(caches.open(CACHE).then(c=>c.addAll(STATIC)).then(()=>self.skipWaiting()).catch(()=>self.skipWaiting())));
self.addEventListener('activate', event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(x=>x!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch', event=>{
  const req=event.request,url=new URL(req.url);
  if(req.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
  if(url.pathname.startsWith('/assets/'))event.respondWith(caches.match(req).then(r=>r||fetch(req)));
  else if(req.mode==='navigate')event.respondWith(fetch(req).catch(()=>new Response('<!doctype html><title>Offline</title><main style="padding:32px;font-family:system-ui"><h2>CampusRide offline</h2><p>Live transport data requires internet access.</p></main>',{headers:{'Content-Type':'text/html'}})));
});
