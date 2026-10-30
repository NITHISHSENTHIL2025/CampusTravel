const CACHE='campusride-ui-v2-20261030';
const STATIC=['/assets/liquid.css','/assets/liquid.js','/assets/favicon.svg','/assets/icon.svg','/admin','/student','/driver'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(c=>c.addAll(STATIC)).then(()=>self.skipWaiting()).catch(()=>self.skipWaiting()))});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(x=>x!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',event=>{
 const req=event.request,url=new URL(req.url);
 // Never cache student/driver personal API data, authentication responses, fees or device GPS.
 if(req.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
 if(url.pathname.startsWith('/assets/'))event.respondWith(caches.match(req).then(r=>r||fetch(req)));
 // Navigation always uses the current network; offline shows only an explicitly offline page.
 else if(req.mode==='navigate')event.respondWith(fetch(req).catch(()=>new Response('<!doctype html><title>CampusRide offline</title><body style="font-family:system-ui;padding:30px"><h1>You are offline</h1><p>CampusRide requires a server connection for actual bus locations, bookings and records.</p></body>',{headers:{'Content-Type':'text/html'}})));
});
