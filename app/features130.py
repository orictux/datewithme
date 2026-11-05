"""DateWithMe v1.3: invitation surprises, private media, push rules and config portability."""
from __future__ import annotations
import base64, io, json, os, re, secrets, time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from fastapi import APIRouter, HTTPException, Request, Response, BackgroundTasks
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError
from .image_pipeline import (process_media, read_limited_body, MAX_SOURCE_BYTES, MediaError)

router = APIRouter()
STAGES = ('before_intro','after_yes','after_activity','after_schedule','result','after_date')
MODES = ('continue','reply')
NOTIFY_KINDS = ('invite_opened','planned','cancelled','reactivated','surprise_replied','reminder_60','after_date_viewed')
MAX_MEDIA = 3_500_000

def m():
    from . import main
    return main

def validate_surprise(spec, db):
    stage = spec.stage
    if stage not in STAGES or spec.mode not in MODES:raise HTTPException(400, 'Invalid surprise stage or mode.')
    if spec.media_id is not None and not db.execute('SELECT id FROM surprise_media WHERE id=?',(spec.media_id,)).fetchone():
        raise HTTPException(400,'Media not found.')
    if spec.template_id is not None:
        t=db.execute('SELECT * FROM surprise_templates WHERE id=?',(spec.template_id,)).fetchone()
        if not t:raise HTTPException(400,'Template not found.')
        return (stage,t['body'],t['button_label'],t['mode'],t['media_id'],t['id'])
    if not spec.body.strip():raise HTTPException(400,'Surprise message cannot be empty.')
    return (stage,spec.body.strip(),spec.button_label.strip() or 'Continue 💕',spec.mode,spec.media_id,None)

class SurpriseConfig(BaseModel):
    stage:str
    body:str=Field(default='',max_length=1000)
    button_label:str=Field(default='Continue 💕',max_length=50)
    mode:str='continue'
    media_id:int|None=None
    template_id:int|None=None

class TemplateInput(BaseModel):
    title:str=Field(min_length=1,max_length=80)
    body:str=Field(min_length=1,max_length=1000)
    button_label:str=Field(default='Continue 💕',max_length=50)
    mode:str='continue'
    media_id:int|None=None

@router.post('/api/admin/surprise-templates')
def create_template(req:Request, data:TemplateInput):
    a=m();a.admin_guard(req,True)
    if data.mode not in MODES:raise HTTPException(400,'Invalid message mode.')
    with a.store.write() as db:
        if data.media_id and not db.execute('SELECT id FROM surprise_media WHERE id=?',(data.media_id,)).fetchone():raise HTTPException(400,'Media not found.')
        db.execute('INSERT INTO surprise_templates(title,body,button_label,mode,media_id,created_at) VALUES(?,?,?,?,?,?)',(data.title.strip(),data.body.strip(),data.button_label.strip() or 'Continue 💕',data.mode,data.media_id,a.now()))
        return {'id':db.execute('SELECT last_insert_rowid()').fetchone()[0]}

@router.put('/api/admin/surprise-templates/{tid}')
def edit_template(req:Request,tid:int,data:TemplateInput):
    a=m();a.admin_guard(req,True)
    if data.mode not in MODES:raise HTTPException(400,'Invalid type.')
    with a.store.write() as db:
        if data.media_id and not db.execute('SELECT id FROM surprise_media WHERE id=?',(data.media_id,)).fetchone():raise HTTPException(400,'Media not found.')
        r=db.execute('UPDATE surprise_templates SET title=?,body=?,button_label=?,mode=?,media_id=? WHERE id=?',(data.title.strip(),data.body.strip(),data.button_label.strip() or 'Continue 💕',data.mode,data.media_id,tid))
        if r.rowcount==0:raise HTTPException(404,'Template not found.')
    return {'ok':True}

@router.delete('/api/admin/surprise-templates/{tid}')
def delete_template(req:Request,tid:int):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:db.execute('DELETE FROM surprise_templates WHERE id=?',(tid,))
    return {'ok':True}

def add_assignment(db,invite_id:int,spec:SurpriseConfig):
    stage,body,label,mode,media,template=validate_surprise(spec,db)
    position=db.execute('SELECT COALESCE(MAX(position),0)+1 FROM surprise_assignments WHERE invite_id=?',(invite_id,)).fetchone()[0]
    db.execute('INSERT INTO surprise_assignments(invite_id,stage,body,button_label,mode,media_id,template_id,position,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(invite_id,stage,body,label,mode,media,template,position,m().now()))
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]

@router.post('/api/admin/invites/{iid}/surprises')
def add_surprise(req:Request,iid:int,data:SurpriseConfig):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:
        if not db.execute('SELECT id FROM invites WHERE id=?',(iid,)).fetchone():raise HTTPException(404,'Invitation not found.')
        return {'id':add_assignment(db,iid,data)}

@router.delete('/api/admin/surprises/{sid}')
def delete_surprise(req:Request,sid:int):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:db.execute('DELETE FROM surprise_assignments WHERE id=?',(sid,))
    return {'ok':True}

class MediaInput(BaseModel):
    # Legacy clients remain supported. New clients use binary streaming.
    data:str=Field(max_length=33_600_000)


def store_processed(media):
    a=m()
    with a.store.write() as db:
        db.execute('INSERT INTO surprise_media(mime,content,created_at) VALUES(?,?,?)',
                   (media.mime,media.content,a.now()))
        return {'id':db.execute('SELECT last_insert_rowid()').fetchone()[0],
                'mime':media.mime,'size':len(media.content),'width':media.width,
                'height':media.height,'frames':media.frames,'source_format':media.source_format}


@router.post('/api/admin/surprise-media/file')
async def upload_surprise_binary(req:Request):
    """Bounded binary upload, independent of filename, declared MIME or extension."""
    a=m();a.admin_guard(req,True)
    if req.headers.get('content-type','').split(';')[0].strip().lower()!='application/octet-stream':
        raise HTTPException(415,'Select an image using the upload field and retry.')
    try:
        blob=await read_limited_body(req)
        return store_processed(process_media(blob))
    except MediaError as e:
        raise HTTPException(e.status,str(e)) from e


@router.post('/api/admin/surprise-media')
def upload_surprise_media(req:Request,data:MediaInput):
    """Preserved for v1.3.0 callers; normalized exactly like the binary path."""
    a=m();a.admin_guard(req,True)
    found=re.fullmatch(r'data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)',data.data)
    if not found:raise HTTPException(400,'Invalid image data. Choose a supported file type.')
    try:
        blob=base64.b64decode(found.group(1),validate=True)
        return store_processed(process_media(blob))
    except (ValueError,base64.binascii.Error) as exc:
        raise HTTPException(400,'Corrupt image data.') from exc
    except MediaError as e:
        raise HTTPException(e.status,str(e)) from e


@router.get('/api/surprise-media/{mid}')
def get_surprise_media(req:Request,mid:int):
    a=m()
    with a.store.read() as db:
        if not a.admin_required(req):
            v=a.visitor(db,req)
            if not v or not v['invite_id']:raise HTTPException(404)
            allowed=db.execute('''SELECT 1 FROM surprise_assignments s
                WHERE s.invite_id=? AND s.media_id=? AND s.stage!='after_date' AND NOT EXISTS
                (SELECT 1 FROM surprise_answers ans WHERE ans.assignment_id=s.id)''',(v['invite_id'],mid)).fetchone()
            if not allowed:
                plan=a.find_plan(db,invite_id=v['invite_id'])
                if plan and plan['status']=='planned' and plan['start_utc']<a.now_epoch():
                    allowed=db.execute('''SELECT 1 FROM surprise_assignments
                        WHERE invite_id=? AND media_id=? AND stage='after_date' ''',(v['invite_id'],mid)).fetchone()
            if not allowed:raise HTTPException(404)
        row=db.execute('SELECT mime,content FROM surprise_media WHERE id=?',(mid,)).fetchone()
        if not row:raise HTTPException(404)
        return Response(bytes(row['content']),media_type=row['mime'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.get('/api/surprises/next')
def next_surprise(req:Request,stage:str):
    a=m()
    if stage not in STAGES:raise HTTPException(400,'Invalid stage.')
    if stage=='after_date':return {'surprise':None}  # separate authenticated, time-gated view endpoint
    with a.store.read() as db:
        v=a.visitor(db,req)
        if not v or not v['invite_id']:return {'surprise':None}
        inv=a.active_invite(db,v)
        if not inv or inv['revoked']:return {'surprise':None}
        row=db.execute('''SELECT s.* FROM surprise_assignments s
            WHERE s.invite_id=? AND s.stage=? AND NOT EXISTS
              (SELECT 1 FROM surprise_answers r WHERE r.assignment_id=s.id)
            ORDER BY s.position,s.id LIMIT 1''',(inv['id'],stage)).fetchone()
        if not row:return {'surprise':None}
        return {'surprise':{'id':row['id'],'stage':row['stage'],'body':row['body'], 'mode':row['mode'],'button_label':row['button_label'], 'media_id':row['media_id']}}

class SurpriseAnswer(BaseModel):
    answer:str=Field(default='',max_length=150)

@router.post('/api/surprises/{sid}/respond')
def answer_surprise(req:Request,sid:int,data:SurpriseAnswer,background_tasks:BackgroundTasks):
    a=m();a.write_guard(req)
    with a.store.write() as db:
        v=a.require_visitor(db,req)
        inv=a.active_invite(db,v)
        if not inv or inv['revoked']:raise HTTPException(403,'Invitation is no longer valid.')
        row=db.execute('SELECT * FROM surprise_assignments WHERE id=? AND invite_id=?',(sid,inv['id'])).fetchone()
        if not row:raise HTTPException(404,'Message not found.')
        if row['stage']=='after_date':
            plan=a.find_plan(db,invite_id=inv['id'])
            if not plan or plan['status']!='planned' or plan['end_utc']>=a.now_epoch():
                raise HTTPException(403,'The date has not happened yet.')
        old=db.execute('SELECT id FROM surprise_answers WHERE assignment_id=?',(sid,)).fetchone()
        if old:return {'ok':True,'already_done':True}
        text=data.answer.strip() if row['mode']=='reply' else ''
        db.execute('INSERT INTO surprise_answers(assignment_id,answer,completed_at) VALUES(?,?,?)',(sid,text,a.now()))
        if row['mode']=='reply':
            name=inv['person_name'];body=f'{name} replied to the surprise message: {text or "(no answer)"}'
            nid=a.notify(db,'surprise_replied','Surprise message reply',body,invite_id=inv['id'])
            background_tasks.add_task(a.send_push_event,note_id=nid)
        return {'ok':True}

@router.get('/api/admin/notification-rules')
def get_rules(req:Request):
    a=m();a.admin_guard(req)
    with a.store.read() as db:
        return {'rules':[{'kind':kind,'admin':a.get_setting(db,'notify_admin_'+kind,'1')=='1','visitor':a.get_setting(db,'notify_visitor_'+kind,'0')=='1'} for kind in NOTIFY_KINDS]}

class RuleInput(BaseModel):
    kind:str
    admin:bool=True
    visitor:bool=False

@router.post('/api/admin/notification-rules')
def set_rule(req:Request,data:RuleInput):
    a=m();a.admin_guard(req,True)
    if data.kind not in NOTIFY_KINDS:raise HTTPException(400,'Invalid notification event.')
    with a.store.write() as db:
        a.set_setting(db,'notify_admin_'+data.kind,'1' if data.admin else '0')
        a.set_setting(db,'notify_visitor_'+data.kind,'1' if data.visitor else '0')
    return {'ok':True}

def run_reminders():
    """Minute-level check, one reminder per plan. Called by optional server thread."""
    a=m()
    if a.store is None:return 0
    created=[]
    with a.store.write() as db:
        for plan in db.execute("SELECT * FROM plans WHERE status='planned' AND start_utc>? AND start_utc<=?",(a.now_epoch(),a.now_epoch()+3600)).fetchall():
            if db.execute('SELECT 1 FROM reminder_sent WHERE plan_id=?',(plan['id'],)).fetchone():continue
            db.execute('INSERT INTO reminder_sent(plan_id,sent_at) VALUES(?,?)',(plan['id'],a.now()))
            inv=db.execute('SELECT p.name FROM invites i JOIN people p ON p.id=i.person_id WHERE i.id=?',(plan['invite_id'],)).fetchone() if plan['invite_id'] else None
            person=inv['name'] if inv else 'Misafir'
            body=f'{person} date is coming up! {a.stamp(plan["start_utc"])} 💕'
            nid=a.notify(db,'reminder_60','Less than one hour until your date',body,invite_id=plan['invite_id'],plan_id=plan['id'])
            created.append((nid,plan['id']))
    for nid,pid in created:
        a.send_push_event(note_id=nid)
        with a.store.read() as db:
            enabled=a.get_setting(db,'visitor_notifications','0')=='1' and a.get_setting(db,'notify_visitor_reminder_60','0')=='1'
        if enabled:a.send_push_event(plan_id=pid,title='DateWithMe 💕',body='Your date is in one hour! 💕')
    return len(created)

@router.post('/api/admin/reminders/check')
def manual_reminder_check(req:Request):
    a=m();a.admin_guard(req,True)
    return {'created':run_reminders()}

ALLOWED_EXPORT_SETTINGS=('duration','slot_step','open_hour','close_hour','guest_language','admin_language','guest_language_picker','visitor_notifications','photo_enabled')

@router.get('/api/admin/config/export')
def export_config(req:Request):
    a=m();a.admin_guard(req)
    with a.store.read() as db:
        settings={k:a.get_setting(db,k) for k in ALLOWED_EXPORT_SETTINGS}
        guest_deck=db.execute('SELECT name FROM decks WHERE id=?',(int(a.get_setting(db,'guest_deck_id','1')),)).fetchone()
        settings['guest_deck_name']=guest_deck['name'] if guest_deck else 'Klasik'
        rules={kind:{'admin':a.get_setting(db,'notify_admin_'+kind,'1')=='1','visitor':a.get_setting(db,'notify_visitor_'+kind,'0')=='1'} for kind in NOTIFY_KINDS}
        decks=[]
        for d in db.execute('SELECT id,name FROM decks WHERE archived=0'):
            cards=[{'slug':r['slug'],'emoji':r['emoji'],'position':r['position'],'translations':json.loads(r['translations'])} for r in db.execute('SELECT * FROM deck_cards WHERE deck_id=? ORDER BY position,id',(d['id'],))]
            decks.append({'name':d['name'],'cards':cards})
        templates=[{k:t[k] for k in ('title','body','button_label','mode')} for t in db.execute('SELECT * FROM surprise_templates')]
        library=[{'emoji':c['emoji'],'translations':json.loads(c['translations'])} for c in db.execute('SELECT emoji,translations FROM card_library ORDER BY id')]
        return {'format':'DateWithMe-config','version':1,'settings':settings,'rules':rules,'decks':decks,'templates':templates,'card_library':library,'notice':'Private media, secrets, sessions, invitation codes and personal records are not exported.'}

class ImportInput(BaseModel):
    config:dict

@router.post('/api/admin/config/import')
def import_config(req:Request,data:ImportInput):
    a=m();a.admin_guard(req,True)
    payload=data.config
    if payload.get('format')!='DateWithMe-config' or payload.get('version')!=1:raise HTTPException(400,'Unsupported settings file.')
    decks=payload.get('decks',[]);templates=payload.get('templates',[]);library=payload.get('card_library',[])
    if not isinstance(decks,list) or len(decks)>60 or not isinstance(templates,list) or len(templates)>150:raise HTTPException(400,'File limits exceeded.')
    if not isinstance(library,list) or len(library)>700:raise HTTPException(400,'Card library limit exceeded.')
    for c in library:
        if not isinstance(c,dict) or not isinstance(c.get('emoji'),str) or not isinstance(c.get('translations'),dict):raise HTTPException(400,'Invalid library card.')
        a=m();a.validate_labels(c['translations'])
    # validate every item before performing any writes
    for deck in decks:
        if not isinstance(deck,dict) or not isinstance(deck.get('name'),str) or len(deck['name'])>70 or len(deck.get('cards',[]))>35:raise HTTPException(400,'Invalid deck.')
        for card in deck['cards']:
            if not re.fullmatch(r'[a-z0-9_-]{1,50}',str(card.get('slug',''))) or not isinstance(card.get('translations'),dict):raise HTTPException(400,'Invalid card.')
    for t in templates:
        if not isinstance(t,dict) or not t.get('title') or not t.get('body') or t.get('mode') not in MODES:raise HTTPException(400,'Invalid surprise template.')
    with a.store.write() as db:
        for key,value in payload.get('settings',{}).items():
            if key not in ALLOWED_EXPORT_SETTINGS:continue
            if key in ('guest_language','admin_language') and value not in a.LANGUAGES:continue
            if key=='photo_enabled':continue  # private image was intentionally not exported
            a.set_setting(db,key,str(value))
        for kind,flags in payload.get('rules',{}).items():
            if kind in NOTIFY_KINDS and isinstance(flags,dict):
                a.set_setting(db,'notify_admin_'+kind,'1' if flags.get('admin') else '0')
                a.set_setting(db,'notify_visitor_'+kind,'1' if flags.get('visitor') else '0')
        for deck in decks:
            if db.execute('SELECT 1 FROM decks WHERE name=? AND archived=0',(deck['name'],)).fetchone():continue
            db.execute('INSERT INTO decks(name,created_at) VALUES(?,?)',(deck['name'],a.now()))
            did=db.execute('SELECT last_insert_rowid()').fetchone()[0]
            for idx,c in enumerate(deck['cards']):
                db.execute('INSERT INTO deck_cards(deck_id,slug,emoji,translations,position) VALUES(?,?,?,?,?)',(did,c['slug'],str(c.get('emoji',''))[:30],json.dumps(c['translations'],ensure_ascii=False),idx))
        from .features141 import catalog_card
        for c in library:catalog_card(db,c['emoji'][:16],json.loads(a.validate_labels(c['translations'])))
        if isinstance(payload.get('settings'),dict):
            deck_name=payload['settings'].get('guest_deck_name')
            if isinstance(deck_name,str):
                match=db.execute('SELECT id FROM decks WHERE name=? AND archived=0',(deck_name,)).fetchone()
                if match:a.set_setting(db,'guest_deck_id',match['id'])
        for t in templates:
            if db.execute('SELECT 1 FROM surprise_templates WHERE title=? AND body=?',(t['title'],t['body'])).fetchone():continue
            db.execute('INSERT INTO surprise_templates(title,body,button_label,mode,media_id,created_at) VALUES(?,?,?,?,NULL,?)',(str(t['title'])[:80],str(t['body'])[:1000],str(t.get('button_label','Continue 💕'))[:50],t['mode'],a.now()))
    return {'ok':True,'note':'Existing invitations were preserved; duplicate decks and templates were skipped.'}

# GIPHY is optional and admin-only. No third-party requests are made by invitees.
class GiphyPick(BaseModel):
    gif_id:str=Field(min_length=1,max_length=70)

def giphy_api(endpoint):
    key=os.getenv('GIPHY_API_KEY','').strip()
    if not key:raise HTTPException(503,'GIPHY is not configured. Upload your own photo or GIF instead.')
    url='https://api.giphy.com/v1/gifs/'+endpoint
    try:
        with urlopen(URLRequest(url,headers={'User-Agent':'DateWithMe/1.3.0','Accept':'application/json'}),timeout=8) as response:
            raw=response.read(1_000_000)
        return json.loads(raw)
    except Exception:raise HTTPException(502,'Unable to reach GIPHY.')

@router.get('/api/admin/giphy/search')
def giphy_search(req:Request,q:str=''):
    a=m();a.admin_guard(req)
    if not 1<=len(q.strip())<=80:raise HTTPException(400,'Search queries must be between 1 and 80 characters.')
    payload=giphy_api('search?'+urlencode({'api_key':os.getenv('GIPHY_API_KEY',''),'q':q.strip(),'limit':12,'rating':'g'}))
    results=[]
    for item in payload.get('data',[]):
        images=item.get('images') or {}
        thumb=(images.get('fixed_width_small') or {}).get('url') or (images.get('downsized') or {}).get('url')
        if re.fullmatch(r'[a-zA-Z0-9_-]{1,70}',str(item.get('id',''))) and isinstance(thumb,str) and thumb.startswith('https://'):
            results.append({'id':item['id'],'title':str(item.get('title',''))[:100],'thumb':thumb})
    return {'results':results}

@router.post('/api/admin/giphy/import')
def giphy_import(req:Request,data:GiphyPick):
    a=m();a.admin_guard(req,True)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,70}',data.gif_id):raise HTTPException(400,'Invalid GIF identifier.')
    response=giphy_api(data.gif_id+'?'+urlencode({'api_key':os.getenv('GIPHY_API_KEY','')}))
    info=response.get('data') or {}
    images=info.get('images') or {}
    src=(images.get('downsized') or {}).get('url') or (images.get('fixed_width') or {}).get('url')
    from urllib.parse import urlsplit
    parts=urlsplit(src or '')
    if parts.scheme!='https' or not re.fullmatch(r'(media[0-9]?|i)\.giphy\.com',parts.hostname or '') or parts.port not in (None,443):
        raise HTTPException(400,'Unable to verify GIPHY media URL.')
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    try:
        opener=build_opener(NoRedirect)
        with opener.open(URLRequest(src,headers={'User-Agent':'DateWithMe/1.3.0'}),timeout=12) as stream:
            content=stream.read(MAX_SOURCE_BYTES+1)
        if not content or len(content)>MAX_SOURCE_BYTES:raise HTTPException(413,'GIF exceeds the 24 MB limit.')
        processed=process_media(content)
        if processed.source_format!='GIF':raise HTTPException(400,'GIPHY import supports GIF animations only.')
    except HTTPException:raise
    except MediaError as e:raise HTTPException(e.status,str(e)) from e
    except Exception:raise HTTPException(502,'Unable to download GIF. Try direct upload.')
    with a.store.write() as db:
        db.execute('INSERT INTO surprise_media(mime,content,created_at) VALUES(?,?,?)',(processed.mime,processed.content,a.now()))
        mid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return {'id':mid,'mime':processed.mime,'frames':processed.frames}
