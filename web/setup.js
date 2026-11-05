'use strict';
const form=document.getElementById('setup-form');
const display=document.getElementById('preview-name');
const owner=document.getElementById('owner_name');
const issue=document.getElementById('error');
const language=document.getElementById('language');
const zone=document.getElementById('timezone');
let step=1;
function report(message){issue.textContent=message||''}
function screen(next){
  step=next;
  for(const element of document.querySelectorAll('[data-step]'))element.classList.toggle('hidden',element.dataset.step!==String(next));
  const progress=document.getElementById('progress');
  progress.setAttribute('aria-valuenow',next==='done'?3:next);
  for(const [index,item] of [...progress.children].entries())item.classList.toggle('active',next==='done'||index<next);
  report('');
}
function validateStep(current){
  const section=document.querySelector(`[data-step="${current}"]`);
  for(const input of section.querySelectorAll('input[required],select[required]')){
    if(!input.reportValidity())return false;
  }
  if(current===2 && document.getElementById('password').value!==document.getElementById('confirm').value){
    report('The administrator passwords do not match.');return false;
  }
  return true;
}
owner.addEventListener('input',()=>{display.textContent=owner.value.trim()||'Me'});
document.querySelectorAll('[data-next]').forEach(button=>button.addEventListener('click',()=>{
  if(validateStep(step))screen(step+1);
}));
document.querySelectorAll('[data-back]').forEach(button=>button.addEventListener('click',()=>screen(step-1)));
try{zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC'}catch{zone.value='UTC'}
fetch('/api/setup/status',{cache:'no-store'}).then(r=>r.json()).then(status=>{
  if(status.configured){location.replace('/panel');return}
  for(const [code,label] of Object.entries(status.languages||{en:'English'})){
    const option=document.createElement('option');option.value=code;option.textContent=label;
    if(code==='en')option.selected=true;
    language.append(option);
  }
}).catch(()=>report('The server is not responding. Check the application logs.'));
form.addEventListener('submit',async event=>{
  event.preventDefault();if(!validateStep(3))return;
  const button=document.getElementById('finish');button.disabled=true;report('');
  const payload=Object.fromEntries(new FormData(form));delete payload.confirm;
  try{
    const response=await fetch('/api/setup/complete',{
      method:'POST',cache:'no-store',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-DateWithMe-Request':'1'},body:JSON.stringify(payload)
    });
    const result=await response.json();
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Setup could not be completed.');
    screen('done');
  }catch(error){report(error.message||'Setup failed.');button.disabled=false}
});
