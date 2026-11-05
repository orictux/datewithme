/* No analytics or visitor tracking. Only handles explicitly subscribed Web Push. */
self.addEventListener('push', event => {
  let d={};try{d=event.data?event.data.json():{}}catch{}
  const title=typeof d.title==='string'?d.title:'DateWithMe 💕';
  const body=typeof d.body==='string'?d.body:'You have a new notification.';
  const url=typeof d.url==='string' && d.url.startsWith('/') && !d.url.startsWith('//')?d.url:'/panel';
  event.waitUntil(self.registration.showNotification(title,{body,icon:'/icon-192.png',badge:'/icon-192.png',tag:'dwm-'+Date.now(),data:{url}}));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target=new URL(event.notification.data?.url||'/panel',self.location.origin);
  if(target.origin!==self.location.origin)return;
  event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{
    const existing=list.find(c=>new URL(c.url).pathname===target.pathname);
    if(existing)return existing.focus();
    return clients.openWindow(target.href);
  }));
});
