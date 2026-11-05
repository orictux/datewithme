// Independent loader watchdog: a stalled API and a missing JS module are not the same error.
// Diagnostic state stays in this browser; no analytics are transmitted.
let bootErrorCode='DWM-MODULE';
window.addEventListener('error', event=>{
  if(event.target instanceof HTMLScriptElement)bootErrorCode='DWM-ASSET';
  else if(event.message)bootErrorCode='DWM-SCRIPT';
},true);
window.addEventListener('unhandledrejection', ()=>{bootErrorCode='DWM-SCRIPT';});
window.setTimeout(() => {
  const root=document.getElementById('root');
  if(!root || !root.querySelector('.initial-load'))return;
  const phase=window.__dwmBootPhase || 'not-started';
  const code=phase==='bootstrap-request'?'DWM-BOOTSTRAP':bootErrorCode;
  const box=document.createElement('div');box.className='fallback-error glass';
  const title=document.createElement('h2');title.textContent='This page could not be loaded 💜';
  const description=document.createElement('p');
  description.textContent=code==='DWM-BOOTSTRAP'
    ?'The server is taking too long to respond. Please try again.'
    :'A loading problem occurred. Try again or open the link in your browser.';
  const retry=document.createElement('button');retry.className='primary-btn';retry.textContent='Try again';
  retry.onclick=()=>{
    // Avoid reusing an old HTML/JS bundle while preserving short invite codes.
    // Keep the nonstandard ?Ab12 invitation format unchanged: URLSearchParams
    // would turn it into ?Ab12= and break the invitation parser.
    const query=window.location.search;
    const next=window.location.pathname+query+(query?'&':'?')+'refresh='+Date.now()+window.location.hash;
    window.location.replace(next);
  };
  const info=document.createElement('small');info.textContent=code;info.style.opacity='.55';info.style.fontSize='11px';
  box.append(title,description,retry,info);root.replaceChildren(box);
},13000);
