"""v1.4 : opt-in decline, post-date notes and editable UI translations.

All endpoints use the existing encrypted store; preview mode never calls them.
"""
import json, re, secrets
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from .presets import LANGUAGES

router = APIRouter()

def a():
    from . import main
    return main

class LocaleEdit(BaseModel):
    language: str
    scope: str = 'visitor'
    translations: dict[str,str] = Field(default_factory=dict)

# A limited set of stable visitor keys; admin keys are the actual Turkish labels.
VISITOR_KEYS = set('stranger strangerNote guestButton anonymous hello inviteQuestion yes no activityEyebrow activityTitle choose back whenEyebrow whenTitle whenSub firstDay pickDate loadingSlots busy book saving planned past pastSub cancelled cancelDefault addCalendar activity date time privacy revoked revokedSub notFound goHome selectLanguage appleCalendar googleCalendar outlookCalendar surpriseTitle surpriseAnswer postDateNote'.split())

@router.post('/api/admin/translations')
def update_translations(req: Request, data: LocaleEdit):
    m=a();m.admin_guard(req,True)
    if data.language not in LANGUAGES or data.scope not in ('visitor','admin'):
        raise HTTPException(400,'Invalid language or translation scope.')
    if len(data.translations)>240 or sum(len(k)+len(v) for k,v in data.translations.items())>28000:
        raise HTTPException(413,'Translation data is too large.')
    cleaned={}
    for k,v in data.translations.items():
        if (not isinstance(k,str) or not isinstance(v,str) or len(k)>120 or len(v)>400
            or not k.strip() or '<' in v or '>' in v):
            raise HTTPException(400,'Invalid translation key or text.')
        if data.scope=='visitor' and k not in VISITOR_KEYS:
            raise HTTPException(400,'Unknown visitor translation key.')
        if data.scope=='admin' and not re.fullmatch(r'[\w\s\.\?\!\-:/()·💕💜ıİğĞüÜşŞöÖçÇ]+',k):
            raise HTTPException(400,'Invalid dashboard translation key.')
        if v.strip():cleaned[k]=v.strip()
    with m.store.write() as db:
        m.set_setting(db,f'ui_i18n_{data.scope}_{data.language}',json.dumps(cleaned,ensure_ascii=False))
    return {'ok':True,'count':len(cleaned)}

@router.get('/api/admin/translations')
def get_translations(req:Request):
    m=a();m.admin_guard(req)
    with m.store.read() as db:
        return {'visitor':{lang:json.loads(m.get_setting(db,f'ui_i18n_visitor_{lang}','{}')) for lang in LANGUAGES},
                'admin':{lang:json.loads(m.get_setting(db,f'ui_i18n_admin_{lang}','{}')) for lang in LANGUAGES}}

class ViewRequest(BaseModel):
    page_load_id:str=Field(min_length=12,max_length=100)

@router.post('/api/after-date/view')
def after_date_view(req:Request, data:ViewRequest, tasks:BackgroundTasks):
    m=a();m.write_guard(req)
    if not re.fullmatch('[A-Za-z0-9_-]{12,100}',data.page_load_id):
        raise HTTPException(400,'Invalid page identifier.')
    with m.store.write() as db:
        v=m.require_visitor(db,req)
        inv=m.active_invite(db,v)
        if not inv or inv['revoked']:
            raise HTTPException(403,'No active invitation found.')
        plan=m.find_plan(db,invite_id=inv['id'])
        if not plan or plan['status']!='planned' or plan['end_utc']>=m.now_epoch():
            raise HTTPException(403,'The date has not happened yet.')
        rows=db.execute("SELECT s.* FROM surprise_assignments s WHERE s.invite_id=? AND s.stage='after_date' ORDER BY s.position,s.id",(inv['id'],)).fetchall()
        if not rows:return {'notes':[]}
        notes=[{'id':r['id'],'body':r['body'],'button_label':r['button_label'],
                'mode':r['mode'],'media_id':r['media_id']} for r in rows]
        # Exactly once per distinct page load ID, even if the browser retries.
        cur=db.execute('INSERT OR IGNORE INTO after_date_views(plan_id,page_load_id,viewed_at) VALUES(?,?,?)',
                       (plan['id'],data.page_load_id,m.now()))
        if cur.rowcount:
            nid=m.notify(db,'after_date_viewed','Post-date surprise viewed',
                         f"{inv['person_name']} opened the post-date surprise screen.",invite_id=inv['id'],plan_id=plan['id'])
            if m.get_setting(db,'notify_admin_after_date_viewed','1')=='1':
                tasks.add_task(m.send_push_event,note_id=nid)
        return {'notes':notes}

@router.post('/api/invites/decline')
def decline_invite(req:Request,tasks:BackgroundTasks):
    m=a();m.write_guard(req)
    with m.store.write() as db:
        if m.get_setting(db,'no_click_cancels','0')!='1':
            raise HTTPException(403,'Decline action is not enabled.')
        v=m.require_visitor(db,req)
        inv=m.active_invite(db,v)
        if not inv or inv['revoked']:
            raise HTTPException(404,'No active invitation found.')
        if m.find_plan(db,invite_id=inv['id']):
            raise HTTPException(409,'Scheduled dates cannot be cancelled this way.')
        db.execute('UPDATE invites SET revoked=1 WHERE id=?',(inv['id'],))
        nid=m.notify(db,'declined','Davet reddedildi',f"{inv['person_name']} daveti reddetti.",invite_id=inv['id'])
        tasks.add_task(m.send_push_event,note_id=nid)
    return {'ok':True}
