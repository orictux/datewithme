import base64
import ipaddress
from urllib.parse import urlsplit
from html import escape as escape_html
from .installation import ensure_installation_files, setup_token_path, is_configured, validate_public_url, validate_owner_name
from fastapi import BackgroundTasks
from fastapi.responses import RedirectResponse
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import hashlib
import io
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone, date as Date
from pathlib import Path
from contextlib import asynccontextmanager
from threading import Lock, Event, Thread
from urllib.parse import quote
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import FastAPI, Request, Response, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError

from .store import EncryptedStore
from .presets import LANGUAGES
from .features130 import SurpriseConfig

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo('UTC')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', '1') != '0'
ALLOWED_ORIGINS = {s.strip().rstrip('/') for s in os.getenv('ALLOWED_ORIGINS','').split(',') if s.strip()}
ACTIVITIES = {"food":"Let's grab a bite 🍔","coffee":"Let's have coffee ☕",'bar':'Music & drinks 🍺',"party":"Let's party 🎉🍻",'gaming':'Play some games 👾','surprise':'Surprise me 🥰'}
DEFAULT_CANCEL='The invitation has been cancelled by the host.'
INVITE_CHARS='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
ph=PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
store=None
_reminder_stop=Event()
_reminder_thread=None
def vapid_file():
    return Path(os.getenv('VAPID_PRIVATE_PATH', str(Path(os.getenv('DB_KEY_PATH', str(ROOT/'secrets/database.key'))).with_name('vapid-private.pem'))))
PUBLIC_ORIGIN = os.getenv('PUBLIC_BASE_URL','').strip().rstrip('/')
INSTALLATION_ORIGIN = ''
@asynccontextmanager
async def lifespan(_app):
    startup()
    _reminder_stop.clear()
    global _reminder_thread
    if os.getenv('ENABLE_REMINDER_THREAD','1')=='1':
        _reminder_thread=Thread(target=_reminder_loop,daemon=True,name='dwm-reminders')
        _reminder_thread.start()
    try: yield
    finally:
        _reminder_stop.set()
        if _reminder_thread and _reminder_thread.is_alive():_reminder_thread.join(timeout=2)
        _reminder_thread=None
        on_stop()

app=FastAPI(title='DateWithMe', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
_rate_lock=Lock()
_rate={}


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def activity_deck(db, deck_id, language='en'):
    deck=db.execute('SELECT id,name,archived FROM decks WHERE id=?',(deck_id,)).fetchone()
    if not deck:return None
    cards=[]
    for item in db.execute('SELECT id,slug,emoji,translations,position FROM deck_cards WHERE deck_id=? ORDER BY position,id',(deck_id,)):
        labels=json.loads(item['translations'])
        cards.append({'id':item['slug'],'card_id':item['id'],'emoji':item['emoji'],
                      'label':labels.get(language) or labels.get('en') or labels.get('tr') or item['slug'],
                      'translations':labels,'position':item['position']})
    return {'id':deck['id'],'name':deck['name'],'archived':bool(deck['archived']),'cards':cards}

def context_deck(db, invite=None, visit=None):
    if invite:return int(invite['deck_id'] or 1),invite['language'] or 'en'
    if visit and visit['guest']:
        return int(visit['guest_deck_id'] or get_setting(db,'guest_deck_id','1')), (visit['guest_language'] or get_setting(db,'guest_language','en'))
    return int(get_setting(db,'guest_deck_id','1')),get_setting(db,'guest_language','en')

def card_for(db,deck_id,slug):
    row=db.execute('SELECT * FROM deck_cards WHERE deck_id=? AND slug=?',(deck_id,slug)).fetchone()
    return row

def localized_activity(db,plan,language='en'):
    if not plan:return ''
    labels={}
    if 'activity_translations' in plan.keys() and plan['activity_translations']:
        try:labels=json.loads(plan['activity_translations'])
        except (ValueError,TypeError):pass
    deck_id=(plan['deck_id'] if 'deck_id' in plan.keys() else None) or 1
    card=card_for(db,deck_id,plan['activity'])
    if not labels and card: labels=json.loads(card['translations'])
    label=labels.get(language) or labels.get('en') or labels.get('tr')
    if not label:return ACTIVITIES.get(plan['activity'],plan['activity'])
    emoji=(plan['activity_emoji'] if 'activity_emoji' in plan.keys() else None) or (card['emoji'] if card else '')
    return (label+' '+emoji).strip()

def now_epoch(): return int(time.time())
def digest(v): return hashlib.sha256(v.encode('utf-8')).hexdigest()
def get_setting(db,key,default=''):
    row=db.execute('SELECT value FROM config WHERE key=?',(key,)).fetchone()
    return row['value'] if row else default

def set_setting(db,key,value):
    db.execute('INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))

def stamp(epoch): return datetime.fromtimestamp(epoch,TZ).strftime('%Y-%m-%d %H:%M')
def day_from_utc(epoch): return datetime.fromtimestamp(epoch,TZ).strftime('%Y-%m-%d')
def clock_from_utc(epoch): return datetime.fromtimestamp(epoch,TZ).strftime('%H:%M')

def vapid_private():
    """Persist a server-owned, separate P-256 key; never publish this private PEM."""
    path=vapid_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key=ec.generate_private_key(ec.SECP256R1())
        content=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption())
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd,'wb') as f: f.write(content)
    key=serialization.load_pem_private_key(path.read_bytes(),password=None)
    if not isinstance(key,ec.EllipticCurvePrivateKey):raise RuntimeError('Invalid VAPID key')
    return key

def vapid_public():
    b=vapid_private().public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(b).decode().rstrip('=')

def notify(db,kind,title,body,invite_id=None,plan_id=None):
    db.execute('INSERT INTO notifications(kind,title,body,invite_id,plan_id,created_at) VALUES(?,?,?,?,?,?)',
               (kind,title,body,invite_id,plan_id,now()))
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]

def allowed_push_endpoint(endpoint):
    try:
        u=urlsplit(endpoint)
        if u.scheme!='https' or not u.hostname or u.username or u.password or (u.port and u.port!=443):return False
        h=u.hostname.lower()
        return h in ('fcm.googleapis.com','updates.push.services.mozilla.com','push.services.mozilla.com','web.push.apple.com') or h.endswith('.push.apple.com') or h.endswith('.push.services.mozilla.com')
    except (ValueError,TypeError): return False

def push_payload(subscription,title,body,url):
    """Run only in FastAPI's background threadpool; no user request depends on delivery."""
    from pywebpush import webpush
    webpush(subscription_info=subscription,data=json.dumps({'title':title,'body':body,'url':url},ensure_ascii=False),
            vapid_private_key=str(vapid_file()),vapid_claims={'sub':PUBLIC_ORIGIN or os.getenv('VAPID_SUBJECT','mailto:admin@datewithme.invalid')},ttl=600,timeout=8)

def send_push_event(note_id=None,plan_id=None,title=None,body=None):
    if store is None:return
    with store.read() as db:
        if note_id is not None:
            note=db.execute('SELECT * FROM notifications WHERE id=?',(note_id,)).fetchone()
            if not note:return
            title='DateWithMe 💕';body=note['body']
            if get_setting(db,'notify_admin_'+note['kind'],'1')!='1':
                subscriptions=[]
            else:
                subscriptions=[dict(r) for r in db.execute("SELECT * FROM push_subscriptions WHERE audience='admin'")]
            url='/panel'
            # Visitor notification delivery is opt-in at both site and event level.
            if note['plan_id'] and get_setting(db,'visitor_notifications','0')=='1' and get_setting(db,'notify_visitor_'+note['kind'],'0')=='1':
                subscriptions.extend([dict(r) for r in db.execute("SELECT * FROM push_subscriptions WHERE audience='visitor' AND plan_id=?",(note['plan_id'],))])
        else:
            if get_setting(db,'visitor_notifications','0')!='1':return
            subscriptions=[dict(r) for r in db.execute("SELECT * FROM push_subscriptions WHERE audience='visitor' AND plan_id=?",(plan_id,))]
            url='/'
    for sub in subscriptions:
        try:
            info=json.loads(sub['subscription_json'])
            if not allowed_push_endpoint(info.get('endpoint','')):continue
            push_payload(info,title,body,'/' if sub.get('audience')=='visitor' else url)
        except Exception as exc:
            # Push errors never prevent booking or cancellation.
            print('[DateWithMe] Push delivery failed:', type(exc).__name__,flush=True)
            # Expired/invalid subscriptions are cleaned up on the next explicit subscription update.

def startup():
    global store
    if store is not None: return
    global TZ, PUBLIC_ORIGIN, INSTALLATION_ORIGIN
    INSTALLATION_ORIGIN = ''
    from .image_pipeline import register_heif
    register_heif()
    ensure_installation_files()
    store=EncryptedStore()
    vapid_private()
    with store.write() as db:
        for k,v in [('duration','120'),('slot_step','30'),('open_hour','16'),('close_hour','23'),('photo_enabled','0'),('visitor_notifications','0'),('guest_language_picker','1'),('no_click_cancels','0'),('runaway_no','1'),('timeline_enabled','0')]:
            db.execute('INSERT OR IGNORE INTO config(key,value) VALUES(?,?)',(k,v))
        refresh_default_deck_translations(db)
        if is_configured(db):
            TZ=ZoneInfo(get_setting(db,'app_timezone','UTC'))
            PUBLIC_ORIGIN=get_setting(db,'public_base_url','').rstrip('/')
            INSTALLATION_ORIGIN=get_setting(db,'installation_origin','').rstrip('/')
    token=setup_token_path()
    if is_ready():
        token.unlink(missing_ok=True)
    elif not token.exists():
        from .installation import create_setup_token
        create_setup_token(token)



def refresh_default_deck_translations(db):
    """Adjust shipped default translations without overwriting clearly custom text."""
    replacements={
        'en': ('Surprise me!', "Let's go with the flow"),
        'ru': ('Пусть всё будет спонтанно', 'Давай решим на месте'),
        'de': ('Lass uns spontan sein', 'Mal sehen, worauf wir Lust haben'),
        'fr': ('On improvise', 'On verra sur le moment'),
        'es': ('Improvisemos', 'Ya veremos qué nos apetece'),
        'it': ('Lasciamoci sorprendere', 'Vediamo cosa ci va di fare'),
        'pt': ('Deixa acontecer', 'Vamos decidir na hora'),
        'nl': ('Laat het gebeuren', 'We zien wel waar we zin in hebben'),
        'pl': ('Zdajmy się na spontan', 'Zdecydujemy na miejscu'),
        'uk': ('Як піде, так і буде', 'Вирішимо разом на місці'),
        'ar': ('لنتركها للمفاجأة', 'نقرر معًا وقتها'),
        'ja': ('流れに任せよう', 'そのとき一緒に決めよう'),
        'ko': ('흐름에 맡기자', '그때 같이 정하자'),
        'zh': ('随缘吧', '到时候一起决定吧'),
    }
    rows=db.execute("SELECT id,translations FROM deck_cards WHERE slug='surprise'").fetchall()
    for row in rows:
        try:data=json.loads(row['translations'])
        except Exception:continue
        changed=False
        for lang,(old_value,new_value) in replacements.items():
            if data.get(lang)==old_value:
                data[lang]=new_value
                changed=True
            # Earlier test releases briefly used these alternative labels.
            if lang=='it' and data.get(lang)=='Vediamo dove ci porta la serata':
                data[lang]=new_value
                changed=True
        if changed:
            db.execute('UPDATE deck_cards SET translations=? WHERE id=?',(json.dumps(data,ensure_ascii=False),row['id']))

def _reminder_loop():
    from .features130 import run_reminders
    while not _reminder_stop.wait(60):
        try:run_reminders()
        except Exception as exc:
            print('[DateWithMe] Reminder check failed:',type(exc).__name__,flush=True)

def on_stop():
    global store
    if store: store.close(); store=None

@app.middleware('http')
async def security_headers(request:Request,call_next):
    # The reverse proxy owns HTTPS and canonical-host redirects.
    # Never build external redirects from an untrusted Host header.
    if request.url.path.startswith('/api/') and request.url.path not in ('/api/health','/api/setup/status','/api/setup/complete') and not is_ready():
        return JSONResponse({'detail':'Complete setup before using this service.'},status_code=503,headers={'Cache-Control':'no-store'})
    # Do not use IP addresses for analytics. Uvicorn access logs are disabled.
    resp=await call_next(request)
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['Referrer-Policy']='no-referrer'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=(), payment=()'
    resp.headers['Content-Security-Policy']="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; worker-src 'self'; manifest-src 'self'"
    # Only the admin's optional GIF picker previews provider images; invitees never need a third-party image connection.
    if request.url.path=='/panel' and os.getenv('GIPHY_API_KEY','').strip():
        resp.headers['Content-Security-Policy']=resp.headers['Content-Security-Policy'].replace("img-src 'self' data: blob:;", "img-src 'self' data: blob: https://*.giphy.com;")
    # Shared navigation HTML and module scripts must never outlive a deployment.
    # In-app browsers (WhatsApp, etc.) may otherwise mix old JavaScript with a new backend.
    uncacheable={'/','/date','/panel','/privacy','/app.js','/locales.js',
                 '/fallback.js','/style.css','/manifest.webmanifest',
                 '/client.4c83dda779ac9bbe.js','/client.3a9b33d1fc5550aa.js','/layout.92ba010ff42b0af1.css','/client.39b48e6b6e9edb6d.js','/client.20b40fcedb35de15.js','/client.a81e69dad9498c4f.js','/layout.0cce5f17dd356774.css','/layout.888ac476f517aedb.css','/client.a72909f46d7bd2b3.js','/layout.5cef94d51e79b672.css','/client.49bdb33c86a6ff77.js','/client.44b6b91c0f508393.js','/client.0cb262f049493b62.js','/layout.9f433a3cd8bd575e.css','/layout.409cd42739c9ac61.css','/layout.af583beb3c9ad375.css','/recovery.9399b758f2d3f111.js'}
    if request.url.path.startswith('/api/') or request.url.path in uncacheable:
        resp.headers['Cache-Control']='no-store, max-age=0'
        resp.headers['Pragma']='no-cache'
    return resp

def local_origins():
    """Local browser endpoints are exact origins, including the published host port."""
    port = os.getenv('LOCAL_PORT', '8031')
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        return set()
    return {f'http://localhost:{port}', f'http://127.0.0.1:{port}',
            f'http://[::1]:{port}'}


def trusted_origins():
    # The administrator explicitly authorizes the public site in Settings.
    return (ALLOWED_ORIGINS | local_origins() |
            {value for value in (PUBLIC_ORIGIN, INSTALLATION_ORIGIN) if value})


def setup_origin_matches_host(req: Request, origin: str) -> bool:
    """First installation can begin on HTTPS without prior domain configuration.

    The one-time token is still required. This exception applies only to setup.
    Never automatically trust arbitrary Host headers for authenticated writes.
    """
    try:
        parsed = urlsplit(origin)
        host = req.headers.get('host', '').lower()
        if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            return False
        if parsed.netloc.lower() != host or not parsed.hostname:
            return False
        if parsed.scheme == 'https':
            return True
        return (parsed.scheme == 'http' and parsed.hostname.lower() in ('localhost', '127.0.0.1', '::1', 'testserver')
                and os.getenv('ALLOW_INSECURE_HTTP', '0') == '1')
    except ValueError:
        return False


def write_guard(req:Request):
    if req.headers.get('x-datewithme-request')!='1':
        raise HTTPException(403,'Application request header required.')
    origin=req.headers.get('origin')
    if not origin:
        return  # Non-browser clients still require the custom request header.
    if origin.rstrip('/') in trusted_origins():
        return
    if req.url.path == '/api/setup/complete' and setup_origin_matches_host(req, origin):
        return
    raise HTTPException(403,'Origin verification failed.')


def invitation_base_url(req: Request) -> str:
    """Use explicit public URL, otherwise the exact approved browser origin."""
    if PUBLIC_ORIGIN:
        return PUBLIC_ORIGIN
    origin = req.headers.get('origin','').rstrip('/')
    if origin in trusted_origins():
        return origin
    # Without a public URL, the browser uses its own origin for share links.
    return '' 

def throttle(key,limit,seconds):
    with _rate_lock:
        t=time.monotonic()
        # Periodic opportunistic cleanup
        if len(_rate)>5000:
            for k,v in list(_rate.items()):
                if v[1]<t: _rate.pop(k,None)
        count,expiry=_rate.get(key,(0,t+seconds))
        if expiry<t:count,expiry=0,t+seconds
        if count>=limit:raise HTTPException(429,'Too many attempts. Try again later.')
        _rate[key]=(count+1,expiry)

def new_visitor(db, invite_id=None, guest=False):
    token=secrets.token_urlsafe(36)
    ts=now()
    guest_deck,guest_lang=context_deck(db) if guest else (None,None)
    db.execute('INSERT INTO visits(token_hash,invite_id,guest,guest_deck_id,guest_language,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
               (digest(token),invite_id,int(guest),guest_deck,guest_lang,ts,ts))
    return token

def cookie_visitor(resp,token):
    resp.set_cookie('dwm_visit',token,max_age=60*60*24*365,secure=COOKIE_SECURE,httponly=True,samesite='lax',path='/')

def visitor(db,req):
    token=req.cookies.get('dwm_visit','')
    if len(token)>150: return None
    return db.execute('SELECT * FROM visits WHERE token_hash=?',(digest(token),)).fetchone() if token else None

def require_visitor(db,req):
    v=visitor(db,req)
    if not v:raise HTTPException(401,'Session not found. Open your invitation link again.')
    return v

def active_invite(db,v):
    return db.execute('SELECT i.*,p.name person_name FROM invites i JOIN people p ON i.person_id=p.id WHERE i.id=?',(v['invite_id'],)).fetchone() if v and v['invite_id'] else None

def find_plan(db,invite_id=None,visit_id=None):
    if invite_id is not None:return db.execute('SELECT * FROM plans WHERE invite_id=?',(invite_id,)).fetchone()
    if visit_id is not None:return db.execute('SELECT * FROM plans WHERE visit_id=? AND invite_id IS NULL',(visit_id,)).fetchone()

def plan_json(plan,db=None,language="en"):
    if not plan:return None
    return {'id':plan['id'],'activity':plan['activity'],'activity_label':localized_activity(db,plan,language) if db else ACTIVITIES.get(plan['activity'],'Aktivite'),
            'day':day_from_utc(plan['start_utc']),'time':clock_from_utc(plan['start_utc']),
            'start_utc':datetime.fromtimestamp(plan['start_utc'],timezone.utc).isoformat(),
            'end_utc':datetime.fromtimestamp(plan['end_utc'],timezone.utc).isoformat(),
            'end_local':stamp(plan['end_utc']),'status':plan['status'],
            'past':plan['end_utc']<now_epoch(),'cancel_cycle':plan['cancel_cycle'],
            'cancellation_message':plan['cancellation_message']}

def response_payload(db,invite,plan,guest,visit=None):
    deck_id,language=context_deck(db,invite,visit)
    deck=activity_deck(db,deck_id,language)
    if invite and invite['revoked']:return {'mode':'revoked','name':invite['person_name'],'language':language}
    return {'mode':'invite' if invite else 'guest' if guest else 'stranger',
            'name':invite['person_name'] if invite else None,
            'invite_code':invite['code'] if invite else None,
            'language':language,'deck':deck,
            'plan':plan_json(plan,db,language),
            'photo_enabled':(bool(invite['photo_id']) if invite else get_setting(db,'photo_enabled')=='1'),
            'photo_id':invite['photo_id'] if invite else None,
            'guest_language_picker':get_setting(db,'guest_language_picker','1')=='1',
            'visitor_notifications':get_setting(db,'visitor_notifications','0')=='1',
            'no_click_cancels':bool(invite) and get_setting(db,'no_click_cancels','0')=='1',
            'runaway_no':get_setting(db,'runaway_no','1')=='1',
            'owner_name':get_setting(db,'owner_name',''),
            'app_timezone':get_setting(db,'app_timezone','UTC'),
            'public_base_url':PUBLIC_ORIGIN,
            'ui_translations':json.loads(get_setting(db,f'ui_i18n_visitor_{language}','{}'))}

def is_ready():
    if store is None:
        return False
    with store.read() as db:
        return is_configured(db)

@app.get('/api/health')
def health():
    return {'ok': True, 'setup_complete': is_ready(), 'version':'1.4.9'}

@app.get('/api/setup/status')
def setup_status():
    return {'configured':is_ready(),'languages':LANGUAGES,'default_language':'en'}

class SetupInput(BaseModel):
    token:str=Field(min_length=20,max_length=200)
    owner_name:str=Field(min_length=1,max_length=60)
    password:str=Field(min_length=14,max_length=256)
    language:str='en'
    timezone:str='UTC'
    public_base_url:str=Field(default='',max_length=250)

@app.post('/api/setup/complete')
def setup_complete(req:Request,data:SetupInput):
    global TZ, PUBLIC_ORIGIN, INSTALLATION_ORIGIN
    write_guard(req)
    throttle('setup-complete',10,300)
    if is_ready():raise HTTPException(409,'Installation is already configured.')
    import hmac
    token_file=setup_token_path()
    expected=token_file.read_text().strip() if token_file.is_file() else ''
    if not expected or not hmac.compare_digest(data.token,expected):
        raise HTTPException(403,'Invalid installation token.')
    if data.language not in LANGUAGES:raise HTTPException(400,'Unsupported language.')
    try: tz=ZoneInfo(data.timezone)
    except (KeyError,ValueError):raise HTTPException(400,'Unsupported time zone.')
    owner=validate_owner_name(data.owner_name)
    base_url=validate_public_url(data.public_base_url) if data.public_base_url.strip() else ''
    origin=req.headers.get('origin','').rstrip('/')
    installation_origin=origin if origin and setup_origin_matches_host(req,origin) and origin not in local_origins() else ''
    with store.write() as db:
        if is_configured(db):raise HTTPException(409,'Installation is already configured.')
        db.execute('INSERT INTO admins(password_hash) VALUES(?)',(ph.hash(data.password),))
        for k,v in [('owner_name',owner),('guest_language',data.language),('admin_language',data.language),('app_timezone',data.timezone),('public_base_url',base_url),('installation_origin',installation_origin)]:
            set_setting(db,k,v)
        set_setting(db,'setup_completed_at',now())
    TZ=tz
    PUBLIC_ORIGIN=base_url
    INSTALLATION_ORIGIN=installation_origin
    token_file.unlink(missing_ok=True)
    return {'ok':True,'redirect':'/panel'}

@app.get('/api/public-config')
def public_config():
    with store.read() as db:
        return {'owner_name':get_setting(db,'owner_name','Me'),
                'default_language':get_setting(db,'guest_language','en'),
                'timezone':get_setting(db,'app_timezone','UTC'),
                'app_name':'DateWithMe','version':'1.4.9'}


@app.get('/api/bootstrap')
def bootstrap(req:Request, response:Response, background_tasks:BackgroundTasks, code:str=''):
    if code and (len(code)>32 or not re.fullmatch(r'[a-zA-Z0-9]+',code)):
        raise HTTPException(404,'Invitation not found.')
    if code:throttle('invite-global',200,60)
    with store.write() as db:
        v=visitor(db,req)
        if code:
            inv=db.execute('SELECT i.*,p.name person_name FROM invites i JOIN people p ON i.person_id=p.id WHERE i.code=?',(code,)).fetchone()
            if not inv:raise HTTPException(404,'Invitation not found or revoked.')
            if inv['opened_at'] is None:db.execute('UPDATE invites SET opened_at=? WHERE id=?',(now(),inv['id']))
            if inv['revoked']:return response_payload(db,inv,None,False,v)
            # New invite takes over an existing guest/older invite session; older links can
            # be viewed without changing the remembered newest invitation.
            old=active_invite(db,v)
            should_bind=not old or (inv['created_at'],inv['id'])>(old['created_at'],old['id'])
            if should_bind:
                token=new_visitor(db,inv['id'])
                cookie_visitor(response,token)
                if not db.execute("SELECT 1 FROM notifications WHERE kind='invite_opened' AND invite_id=?",(inv['id'],)).fetchone():
                    nid=notify(db,'invite_opened','Invitation opened',f"{inv['person_name']} opened the invitation link.",invite_id=inv['id'])
                    background_tasks.add_task(send_push_event,note_id=nid)
            plan=find_plan(db,invite_id=inv['id'])
            data=response_payload(db,inv,plan,False,v)
            if not should_bind and old and old['id']!=inv['id'] and not plan:
                data['read_only']=True
            elif v and old and old['id']==inv['id']:
                data['stage']=v['stage']
                data['draft']={'activity':v['chosen_activity'],'day':v['chosen_day'],'time':v['chosen_time']}
            return data
        if not v:return response_payload(db,None,None,False)
        inv=active_invite(db,v)
        plan=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])
        data=response_payload(db,inv,plan,bool(v['guest']),v)
        data['stage']=v['stage']
        data['draft']={'activity':v['chosen_activity'],'day':v['chosen_day'],'time':v['chosen_time']}
        return data

@app.post('/api/guest')
def guest(req:Request,resp:Response):
    write_guard(req)
    with store.write() as db:
        v=visitor(db,req)
        if v:
            inv=active_invite(db,v)
            return response_payload(db,inv,find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id']),bool(v['guest']),v)
        token=new_visitor(db,guest=True)
        cookie_visitor(resp,token)
    with store.read() as db:return response_payload(db,None,None,True)


class GuestLanguage(BaseModel):
    language:str='en'

@app.post('/api/guest-language')
def guest_language(req:Request,data:GuestLanguage):
    write_guard(req)
    if data.language not in LANGUAGES: raise HTTPException(400,'Desteklenmeyen dil.')
    with store.write() as db:
        v=require_visitor(db,req)
        if not v['guest'] or v['invite_id']:
            raise HTTPException(403,'Language can only be changed for guest sessions.')
        db.execute('UPDATE visits SET guest_language=?, updated_at=? WHERE id=?',(data.language,now(),v['id']))
    return {'ok':True}

class Device(BaseModel):
    device:str='Other'
    os:str='Other'
    browser:str='Other'

@app.post('/api/device')
def device(req:Request,data:Device):
    write_guard(req)
    allowed={'device':{'Mobile','Desktop','Tablet','Other'},'os':{'iOS','Android','Windows','macOS','Linux','Other'},'browser':{'Safari','Chrome','Firefox','Edge','Other'}}
    with store.write() as db:
        v=require_visitor(db,req)
        db.execute('UPDATE visits SET device=?,os=?,browser=?,updated_at=? WHERE id=?',
                   (*(getattr(data,k) if getattr(data,k) in allowed[k] else 'Other' for k in ('device','os','browser')),now(),v['id']))
    return {'ok':True}

class Progress(BaseModel):
    stage:str
    activity:str|None=None
    day:str|None=None
    time:str|None=None

@app.post('/api/progress')
def progress(req:Request,data:Progress):
    write_guard(req)
    if data.stage not in ('intro','activity','schedule','complete'):raise HTTPException(400,'Invalid stage')
    with store.write() as db:
        v=require_visitor(db,req)
        inv=active_invite(db,v)
        if inv and inv['revoked']:raise HTTPException(410,'Davet iptal edildi.')
        deck_id,_lang=context_deck(db,inv,v)
        if data.activity and not card_for(db,deck_id,data.activity):raise HTTPException(400,'This card does not exist in the selected deck.')
        plan=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])
        if plan: return {'ok':True,'already_planned':True}
        db.execute('UPDATE visits SET stage=?,chosen_activity=COALESCE(?,chosen_activity),chosen_day=COALESCE(?,chosen_day),chosen_time=COALESCE(?,chosen_time),updated_at=? WHERE id=?',
                   (data.stage,data.activity,data.day,data.time,now(),v['id']))
    return {'ok':True}

@app.post('/api/no-attempt')
def no_attempt(req:Request):
    write_guard(req)
    with store.write() as db:
        v=require_visitor(db,req)
        inv=active_invite(db,v)
        if inv and inv['revoked']:raise HTTPException(410,'Davet iptal edildi.')
        plan=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])
        if plan:return {'count':v['no_count']}
        new_count=min(v['no_count']+1,999)
        db.execute('UPDATE visits SET no_count=?,updated_at=? WHERE id=?',(new_count,now(),v['id']))
    return {'count':new_count}

def slot_data(db,day):
    try: d=Date.fromisoformat(day)
    except ValueError:raise HTTPException(400,'Invalid date.')
    today=datetime.now(TZ).date()
    if d<today or d>today+timedelta(days=120):raise HTTPException(400,'The date must be within the next 120 days.')
    dur=int(get_setting(db,'duration','120'))
    step=int(get_setting(db,'slot_step','30'))
    open_hour=int(get_setting(db,'open_hour','16'))
    close_hour=int(get_setting(db,'close_hour','23'))
    occupied=[(r['start_utc'],r['end_utc']) for r in db.execute("SELECT start_utc,end_utc FROM plans WHERE invite_id IS NOT NULL AND status='planned' AND end_utc>=?",(int(datetime.combine(d,datetime.min.time(),TZ).timestamp()),))]
    occupied += [(r['start_utc'],r['end_utc']) for r in db.execute('SELECT start_utc,end_utc FROM blocks WHERE end_utc>=?',(int(datetime.combine(d,datetime.min.time(),TZ).timestamp()),))]
    result=[]
    for minute in range(open_hour*60,close_hour*60-dur+1,step):
        starts=datetime(d.year,d.month,d.day,minute//60,minute%60,tzinfo=TZ)
        a=int(starts.timestamp());b=a+dur*60
        result.append({'time':starts.strftime('%H:%M'), 'available':a>now_epoch()+60 and not any(a<y and b>x for x,y in occupied)})
    return result

@app.get('/api/availability')
def availability(day:str):
    with store.read() as db: return {'day':day,'slots':slot_data(db,day)}

class NewPlan(BaseModel):
    activity:str
    day:str
    time:str

@app.post('/api/plans')
def book(req:Request,data:NewPlan,background_tasks:BackgroundTasks):
    write_guard(req)
    with store.write() as db:
        v=require_visitor(db,req)
        inv=active_invite(db,v)
        if inv and inv['revoked']:raise HTTPException(410,'Invitation revoked.')
        deck_id,lang=context_deck(db,inv,v)
        card=card_for(db,deck_id,data.activity)
        if not card:raise HTTPException(400,'Activity not found in the selected deck.')
        snapshot=card['translations']
        old=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])
        if old:return {'already_planned':True,'plan':plan_json(old,db,lang)}
        allowed={s['time']:s['available'] for s in slot_data(db,data.day)}
        if not allowed.get(data.time):raise HTTPException(409,'This time is unavailable. Choose another time.')
        h,m=map(int,data.time.split(':'))
        when=datetime(*map(int,data.day.split('-')),h,m,tzinfo=TZ)
        a=int(when.timestamp());b=a+int(get_setting(db,'duration','120'))*60
        ts=now()
        db.execute('INSERT INTO plans(invite_id,visit_id,activity,deck_id,activity_translations,activity_emoji,start_utc,end_utc,no_attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (inv['id'] if inv else None,v['id'] if not inv else None,data.activity,deck_id,snapshot,card['emoji'],a,b,v['no_count'],ts,ts))
        db.execute('UPDATE visits SET stage=?,chosen_activity=?,chosen_day=?,chosen_time=?,updated_at=? WHERE id=?',
                   ('complete',data.activity,data.day,data.time,ts,v['id']))
        plan=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])
        desc=f'{inv["person_name"] if inv else "Misafir"} · {localized_activity(db,plan,'en')} · {stamp(a)}'
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('planned',desc,ts))
        nid=notify(db,'planned','Date scheduled',desc,invite_id=inv['id'] if inv else None,plan_id=plan['id'])
        background_tasks.add_task(send_push_event,note_id=nid)
        return {'plan':plan_json(plan,db,lang),'mode':'invite' if inv else 'guest'}

class ViewEvent(BaseModel):
    page_load_id:str=Field(min_length=16,max_length=100)
    code:str|None=None

@app.post('/api/cancel-view')
def cancel_view(req:Request,data:ViewEvent):
    write_guard(req)
    if not re.fullmatch('[a-zA-Z0-9_-]{16,100}',data.page_load_id):raise HTTPException(400,'Invalid event identifier')
    with store.write() as db:
        v=visitor(db,req)
        inv=None
        if data.code:
            inv=db.execute('SELECT * FROM invites WHERE code=?',(data.code,)).fetchone()
        if not inv and v:inv=active_invite(db,v)
        plan=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id']) if v else None
        if not plan or plan['status']!='cancelled':return {'ok':False}
        db.execute('INSERT OR IGNORE INTO cancel_views(plan_id,cycle,page_load_id,viewed_at) VALUES(?,?,?,?)',
                   (plan['id'],plan['cancel_cycle'],data.page_load_id,now()))
    return {'ok':True}

def resolve_plan(db,req,code):
    if code:
        inv=db.execute('SELECT * FROM invites WHERE code=? AND revoked=0',(code,)).fetchone()
        return find_plan(db,invite_id=inv['id']) if inv else None
    v=visitor(db,req)
    if not v:return None
    inv=active_invite(db,v)
    return find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])

@app.get('/api/calendar.ics')
def calendar_ics(req:Request,code:str=''):
    with store.read() as db:
        plan=resolve_plan(db,req,code)
        owner=get_setting(db,'owner_name','Me')
        description=localized_activity(db,plan,'en') if plan else ''
    if not plan or plan['status']!='planned':raise HTTPException(404,'Active date not found.')
    start=datetime.fromtimestamp(plan['start_utc'],timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    end=datetime.fromtimestamp(plan['end_utc'],timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    def esc_ics(value):
        return str(value).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')
    uid_host=urlsplit(PUBLIC_ORIGIN).hostname or 'datewithme.local'
    ics='\r\n'.join(['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//DateWithMe//EN','CALSCALE:GREGORIAN','METHOD:PUBLISH',
        'BEGIN:VEVENT',f'UID:date-{plan["id"]}@{uid_host}',f'DTSTAMP:{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}',
        f'DTSTART:{start}',f'DTEND:{end}',f'SUMMARY:{esc_ics("Date with " + owner)}',
        f'DESCRIPTION:{esc_ics(description)}','STATUS:CONFIRMED','END:VEVENT','END:VCALENDAR',''])
    return Response(ics,media_type='text/calendar; charset=utf-8',headers={'Content-Disposition':'inline; filename="datewithme.ics"','Cache-Control':'no-store'})

@app.get('/api/photo')
def photo():
    with store.read() as db:
        if get_setting(db,'photo_enabled')!='1':raise HTTPException(404,'Photo display is disabled.')
        item=db.execute('SELECT webp FROM photos WHERE id=1').fetchone()
    if not item:raise HTTPException(404,'Photo not found.')
    return Response(item['webp'],media_type='image/webp',headers={'Cache-Control':'no-store'})

# ---- Administration: separate HttpOnly sessions, never expose password/key in JS ----
def admin_required(req):
    t=req.cookies.get('dwm_admin','')
    if not t:return False
    with store.read() as db:
        r=db.execute('SELECT expires_at FROM admin_sessions WHERE token_hash=?',(digest(t),)).fetchone()
    return bool(r and r['expires_at']>now_epoch())

def admin_guard(req,write=False):
    if write:write_guard(req)
    if not admin_required(req):raise HTTPException(401,'Administrator login required.')

class Login(BaseModel): password:str

@app.post('/api/admin/login')
def admin_login(req:Request,resp:Response,data:Login):
    write_guard(req)
    throttle('admin-login-global',12,60*10)
    with store.write() as db:
        r=db.execute('SELECT password_hash FROM admins LIMIT 1').fetchone()
        try:ok=ph.verify(r['password_hash'],data.password)
        except (VerifyMismatchError,VerificationError):ok=False
        if not ok: raise HTTPException(401,'Incorrect password.')
        token=secrets.token_urlsafe(36)
        db.execute('DELETE FROM admin_sessions WHERE expires_at<?',(now_epoch(),))
        db.execute('INSERT INTO admin_sessions(token_hash,expires_at) VALUES(?,?)',(digest(token),now_epoch()+12*3600))
    resp.set_cookie('dwm_admin',token,max_age=12*3600,secure=COOKIE_SECURE,httponly=True,samesite='strict',path='/')
    return {'ok':True}

@app.get('/api/admin/me')
def admin_me(req:Request):return {'authenticated':admin_required(req)}

@app.post('/api/admin/logout')
def admin_logout(req:Request,resp:Response):
    write_guard(req)
    token=req.cookies.get('dwm_admin','')
    if token:
        with store.write() as db:
            db.execute('DELETE FROM admin_sessions WHERE token_hash=?',(digest(token),))
            db.execute("DELETE FROM push_subscriptions WHERE audience='admin' AND token_hash=?",(digest(token),))
    resp.delete_cookie('dwm_admin',path='/')
    return {'ok':True}

@app.get('/api/admin/state')
def admin_state(req:Request):
    admin_guard(req)
    with store.read() as db:
        invites=[dict(r) for r in db.execute('''SELECT i.id,i.code,i.created_at,i.revoked,i.deck_id,i.language,i.opened_at,i.photo_id,p.id person_id,p.name person_name,
          (SELECT COUNT(*) FROM invites z WHERE z.person_id=i.person_id AND (z.created_at<i.created_at OR (z.created_at=i.created_at AND z.id<=i.id))) sequence
          FROM invites i JOIN people p ON p.id=i.person_id ORDER BY i.id DESC''')]
        plan_rows=[dict(r) for r in db.execute('''SELECT pl.*,pe.name person_name,i.code invite_code,
          (SELECT COUNT(*) FROM cancel_views c WHERE c.plan_id=pl.id AND c.cycle=pl.cancel_cycle) cancellation_views
          FROM plans pl LEFT JOIN invites i ON i.id=pl.invite_id LEFT JOIN people pe ON pe.id=i.person_id ORDER BY pl.id DESC''')]
        invite_sequences={i['id']:i['sequence'] for i in invites}
        panel_lang=get_setting(db,'admin_language','en')
        for inv in invites:
            inv['editable']=not bool(inv.get('opened_at') or db.execute('SELECT 1 FROM visits WHERE invite_id=? LIMIT 1',(inv['id'],)).fetchone() or db.execute('SELECT 1 FROM plans WHERE invite_id=?',(inv['id'],)).fetchone())
        for pl in plan_rows:
            pl.update(plan_json(pl,db,panel_lang))
            pl['sequence']=invite_sequences.get(pl['invite_id'],pl['id'])
        guests=[dict(r) for r in db.execute("SELECT v.id,v.device,v.os,v.browser,v.no_count,v.created_at,v.updated_at,v.stage FROM visits v WHERE v.guest=1 ORDER BY v.id DESC")]
        visits=[dict(r) for r in db.execute('''SELECT v.id,v.device,v.os,v.browser,v.no_count,v.stage,v.chosen_activity,v.chosen_day,v.chosen_time,v.created_at,v.updated_at,pe.name person_name,i.code invite_code
          FROM visits v LEFT JOIN invites i ON i.id=v.invite_id LEFT JOIN people pe ON pe.id=i.person_id ORDER BY v.id DESC''')]
        views=[dict(r) for r in db.execute('''SELECT c.id,c.plan_id,c.cycle,c.viewed_at,pe.name person_name FROM cancel_views c JOIN plans pl ON pl.id=c.plan_id LEFT JOIN invites i ON pl.invite_id=i.id LEFT JOIN people pe ON pe.id=i.person_id ORDER BY c.id DESC''')]
        by_plan={p['id']:p['sequence'] for p in plan_rows}
        for v in views: v['sequence']=by_plan.get(v['plan_id'],v['plan_id'])
        blocks=[dict(r) for r in db.execute('SELECT * FROM blocks ORDER BY start_utc DESC LIMIT 120')]
        for b in blocks:b['start_local']=stamp(b['start_utc']);b['end_local']=stamp(b['end_utc'])
        people=[dict(r) for r in db.execute('''SELECT p.id,p.name,p.created_at,p.archived,
            (SELECT COUNT(*) FROM invites i WHERE i.person_id=p.id) invite_count,
            (SELECT COUNT(*) FROM plans pl JOIN invites i ON pl.invite_id=i.id WHERE i.person_id=p.id) plan_count
            FROM people p ORDER BY p.archived, p.name COLLATE NOCASE''')]
        events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 80')]
        notifications=[dict(r) for r in db.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT 150')]
        unread=db.execute('SELECT COUNT(*) FROM notifications WHERE read_at IS NULL').fetchone()[0]
        settings={k:get_setting(db,k) for k in ('duration','slot_step','open_hour','close_hour','photo_enabled','guest_deck_id','guest_language','guest_language_picker','admin_language','visitor_notifications','no_click_cancels','runaway_no','owner_name','app_timezone','public_base_url','timeline_enabled')}
        has_photo=bool(db.execute('SELECT 1 FROM photos WHERE id=1').fetchone())
        from .features141 import get_gallery, get_library
        profile_photos=get_gallery(db)
        card_library=get_library(db)
        decks=[activity_deck(db,r['id'],get_setting(db,'admin_language','en')) for r in db.execute('SELECT id FROM decks ORDER BY id')]
        templates=[{k:r[k] for k in ('id','title','body','button_label','mode','media_id')} for r in db.execute('SELECT * FROM surprise_templates ORDER BY id DESC')]
        surprises=[dict(r) for r in db.execute('''SELECT s.*,p.name person_name,i.code invite_code,
            r.answer response_text,r.completed_at FROM surprise_assignments s
            JOIN invites i ON i.id=s.invite_id JOIN people p ON p.id=i.person_id
            LEFT JOIN surprise_answers r ON r.assignment_id=s.id ORDER BY s.invite_id,s.position,s.id''')]
        rules=[{'kind':kind,'admin':get_setting(db,'notify_admin_'+kind,'1')=='1','visitor':get_setting(db,'notify_visitor_'+kind,'0')=='1'} for kind in ('invite_opened','planned','cancelled','reactivated','surprise_replied','reminder_60','after_date_viewed')]
        ui_overrides={scope:{lang:json.loads(get_setting(db,f'ui_i18n_{scope}_{lang}','{}')) for lang in LANGUAGES} for scope in ('visitor','admin')}
        after_date_views=[dict(r) for r in db.execute('''SELECT v.*,pe.name person_name FROM after_date_views v JOIN plans pl ON pl.id=v.plan_id JOIN invites i ON i.id=pl.invite_id JOIN people pe ON pe.id=i.person_id ORDER BY v.id DESC LIMIT 80''')]
        timeline={}
        if get_setting(db,'timeline_enabled','0')=='1':
            for inv in invites:
                iid=inv['id']
                events_for=[{'kind':'created','when':inv['created_at'],'label':'Invitation created'}]
                events_for += [{'kind':'opened','when':r['created_at'],'label':'Invitation opened'} for r in db.execute('SELECT created_at FROM visits WHERE invite_id=? ORDER BY id',(iid,))]
                events_for += [{'kind':'surprise','when':r['completed_at'],'label':'Surprise answered or skipped'} for r in db.execute('SELECT ans.completed_at FROM surprise_answers ans JOIN surprise_assignments s ON s.id=ans.assignment_id WHERE s.invite_id=?',(iid,))]
                events_for += [{'kind':'planned','when':r['created_at'],'label':'Date scheduled'} for r in db.execute('SELECT created_at FROM plans WHERE invite_id=?',(iid,))]
                events_for += [{'kind':'after_date_viewed','when':r['viewed_at'],'label':'Post-date surprise viewed'} for r in db.execute('SELECT v.viewed_at FROM after_date_views v JOIN plans pl ON pl.id=v.plan_id WHERE pl.invite_id=?',(iid,))]
                timeline[str(iid)]=sorted(events_for,key=lambda e:e['when'])

    return {'invites':invites,'plans':plan_rows,'guests':guests,'visits':visits,'views':views,
            'blocks':blocks,'people':people,'events':events,'settings':settings,'has_photo':has_photo,'profile_photos':profile_photos,'card_library':card_library,
            'decks':decks, 'languages':LANGUAGES,'notifications':notifications,'unread_notifications':unread,
            'surprise_templates':templates,'surprises':surprises,'notification_rules':rules,
            'giphy_enabled':bool(os.getenv('GIPHY_API_KEY','').strip()),
            'ui_overrides':ui_overrides,'after_date_views':after_date_views,'timeline':timeline}

class CreateInvite(BaseModel):
    name:str=Field(default='',max_length=60)
    person_id:int|None=None
    code_length:int=Field(default=16,ge=4,le=24)
    deck_id:int=1
    language:str='en'
    photo_id:int|None=None
    surprises:list[SurpriseConfig]=Field(default_factory=list,max_length=12) 

@app.post('/api/admin/invites')
def create_invite(req:Request,data:CreateInvite):
    admin_guard(req,True)
    if data.language not in LANGUAGES:raise HTTPException(400,'Desteklenmeyen dil.')
    with store.write() as db:
        d=db.execute('SELECT id FROM decks WHERE id=? AND archived=0',(data.deck_id,)).fetchone()
        if not d:raise HTTPException(400,'Invalid or archived deck.')
        if not db.execute('SELECT 1 FROM deck_cards WHERE deck_id=?',(data.deck_id,)).fetchone():raise HTTPException(400,'Cannot select an empty deck.')
        if data.person_id:
            person=db.execute('SELECT * FROM people WHERE id=?',(data.person_id,)).fetchone()
            if not person:raise HTTPException(404,'Person not found.')
            if person['archived']:raise HTTPException(409,'This person is archived. Restore them before using.')
            pid=person['id'];name=person['name']
        else:
            name=data.name.strip()
            if not (1<=len(name)<=60):raise HTTPException(400,'Invitee name is required.')
            # Same name is not automatically the same person. Select an existing person explicitly.
            db.execute('INSERT INTO people(name,created_at) VALUES(?,?)',(name,now()))
            pid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for _ in range(100):
            code=''.join(secrets.choice(INVITE_CHARS) for _ in range(data.code_length))
            if not db.execute('SELECT 1 FROM invites WHERE code=?',(code,)).fetchone():break
        else:raise HTTPException(503,'Unable to generate a unique invitation code.')
        if data.photo_id is not None and not db.execute('SELECT id FROM profile_photos WHERE id=?',(data.photo_id,)).fetchone():raise HTTPException(400,'Profile photo not found.')
        db.execute('INSERT INTO invites(person_id,code,created_at,deck_id,language,photo_id) VALUES(?,?,?,?,?,?)',(pid,code,now(),data.deck_id,data.language,data.photo_id))
        iid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
        from .features130 import add_assignment
        for spec in data.surprises: add_assignment(db,iid,spec)
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('invitation',name+' · '+code,now()))
        seq=db.execute('SELECT COUNT(*) FROM invites WHERE person_id=?',(pid,)).fetchone()[0]
        return {'id':iid,'code':code,'name':name,'sequence':seq,'deck_id':data.deck_id,'language':data.language,'url':invitation_base_url(req)+'/date?'+code}

class UpdatePerson(BaseModel):
    name:str=Field(min_length=1,max_length=60)

@app.patch('/api/admin/people/{pid}')
def update_person(req:Request,pid:int,data:UpdatePerson):
    admin_guard(req,True)
    name=data.name.strip()
    if not name:raise HTTPException(400,'The person name cannot be blank.')
    with store.write() as db:
        old=db.execute('SELECT id FROM people WHERE id=?',(pid,)).fetchone()
        if not old:raise HTTPException(404,'Person not found.')
        db.execute('UPDATE people SET name=? WHERE id=?',(name,pid))
    return {'ok':True,'name':name}

@app.post('/api/admin/people/{pid}/archive')
def archive_person(req:Request,pid:int):
    admin_guard(req,True)
    with store.write() as db:
        if not db.execute('SELECT 1 FROM people WHERE id=?',(pid,)).fetchone():raise HTTPException(404,'Person not found.')
        db.execute('UPDATE people SET archived=1 WHERE id=?',(pid,))
    return {'ok':True}

@app.post('/api/admin/people/{pid}/restore')
def restore_person(req:Request,pid:int):
    admin_guard(req,True)
    with store.write() as db:
        if not db.execute('SELECT 1 FROM people WHERE id=?',(pid,)).fetchone():raise HTTPException(404,'Person not found.')
        db.execute('UPDATE people SET archived=0 WHERE id=?',(pid,))
    return {'ok':True}

@app.delete('/api/admin/people/{pid}')
def delete_person(req:Request,pid:int,cascade:bool=False):
    # Permanent deletion requires explicit cascade approval for linked invitations.
    admin_guard(req,True)
    with store.write() as db:
        person=db.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not person:raise HTTPException(404,'Person not found.')
        invites=db.execute('SELECT id,code FROM invites WHERE person_id=?',(pid,)).fetchall()
        if invites and not cascade:
            raise HTTPException(409,f'Bu kişinin {len(invites)} daveti var. Kalıcı silme için bağlı davet ve kayıtların da silinmesini ayrıca onayla.')
        ids=[r['id'] for r in invites]
        media_to_review=set()
        for iid in ids:
            media_to_review.update(r['media_id'] for r in db.execute('SELECT media_id FROM surprise_assignments WHERE invite_id=? AND media_id IS NOT NULL',(iid,)))
            # Delete event data related to the person's invitations and date records.
            plans=db.execute('SELECT id FROM plans WHERE invite_id=?',(iid,)).fetchall()
            for plan in plans:
                db.execute('DELETE FROM notifications WHERE plan_id=?',(plan['id'],))
                db.execute("DELETE FROM events WHERE kind IN ('cancelled','reactivated') AND subject=?",(str(plan['id']),))
            db.execute('DELETE FROM notifications WHERE invite_id=?',(iid,))
            db.execute('DELETE FROM visits WHERE invite_id=?',(iid,))
            db.execute('DELETE FROM invites WHERE id=?',(iid,))  # cascades plans/surprise assignments
        # Remove known invitation audit entries, including code-less name entries on older versions.
        for row in invites:
            db.execute("DELETE FROM events WHERE kind IN ('invitation','reset','invitation_deleted') AND (subject=? OR subject=?)",
                       (person['name']+' · '+row['code'],row['code']))
        db.execute('DELETE FROM people WHERE id=?',(pid,))
        # Only clean orphaned media owned by deleted invitations; leave unrelated uploads alone.
        for mid in media_to_review:
            db.execute('''DELETE FROM surprise_media WHERE id=?
                AND NOT EXISTS(SELECT 1 FROM surprise_templates WHERE media_id=?)
                AND NOT EXISTS(SELECT 1 FROM surprise_assignments WHERE media_id=?)''',(mid,mid,mid))
    return {'ok':True,'deleted_invites':len(invites)}

class CancelPlan(BaseModel):message:str=Field(default=DEFAULT_CANCEL,max_length=240)

@app.post('/api/admin/plans/{plan_id}/cancel')
def cancel_plan(req:Request,plan_id:int,data:CancelPlan,background_tasks:BackgroundTasks):
    admin_guard(req,True)
    with store.write() as db:
        p=db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        if not p:raise HTTPException(404,'Plan not found.')
        if p['status']=='cancelled':return {'ok':True,'unchanged':True}
        db.execute('UPDATE plans SET status=?,cancel_cycle=cancel_cycle+1,cancellation_message=?,updated_at=? WHERE id=?',
                   ('cancelled',data.message.strip() or DEFAULT_CANCEL,now(),plan_id))
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('cancelled',str(plan_id),now()))
        background_tasks.add_task(send_push_event,plan_id=plan_id,title='Date cancelled 💔',body='Your date schedule has changed.')
    return {'ok':True}

@app.post('/api/admin/plans/{plan_id}/reactivate')
def reactivate_plan(req:Request,plan_id:int,background_tasks:BackgroundTasks):
    admin_guard(req,True)
    with store.write() as db:
        p=db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        if not p:raise HTTPException(404,'Plan not found.')
        if p['status']=='planned':return {'ok':True}
        if p['end_utc']<now_epoch():raise HTTPException(409,'Past dates cannot be reactivated.')
        if p['invite_id']:
            other=db.execute("SELECT id FROM plans WHERE status='planned' AND invite_id IS NOT NULL AND id<>? AND start_utc<? AND end_utc>? LIMIT 1",(plan_id,p['end_utc'],p['start_utc'])).fetchone()
            block=db.execute('SELECT id FROM blocks WHERE start_utc<? AND end_utc>? LIMIT 1',(p['end_utc'],p['start_utc'])).fetchone()
            if other or block:raise HTTPException(409,'This time is no longer available. Update availability first.')
        db.execute("UPDATE plans SET status='planned',updated_at=? WHERE id=?",(now(),plan_id))
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('reactivated',str(plan_id),now()))
        background_tasks.add_task(send_push_event,plan_id=plan_id,title='Date restored 💕',body='Your date schedule has changed.')
    return {'ok':True}

@app.post('/api/admin/invites/{iid}/reset')
def reset_invite(req:Request,iid:int):
    admin_guard(req,True)
    with store.write() as db:
        inv=db.execute('SELECT * FROM invites WHERE id=?',(iid,)).fetchone()
        if not inv:raise HTTPException(404,'Invitation not found.')
        db.execute('DELETE FROM plans WHERE invite_id=?',(iid,))
        db.execute("UPDATE visits SET no_count=0,stage='intro',chosen_activity=NULL,chosen_day=NULL,chosen_time=NULL WHERE invite_id=?",(iid,))
        db.execute('UPDATE invites SET revoked=0 WHERE id=?',(iid,))
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('reset',inv['code'],now()))
    return {'ok':True}

@app.delete('/api/admin/invites/{iid}')
def delete_invite(req:Request,iid:int):
    admin_guard(req,True)
    with store.write() as db:
        i=db.execute('SELECT code FROM invites WHERE id=?',(iid,)).fetchone()
        if not i:raise HTTPException(404,'Invitation not found.')
        db.execute('DELETE FROM notifications WHERE invite_id=?',(iid,))
        db.execute('DELETE FROM invites WHERE id=?',(iid,))
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('invitation_deleted',i['code'],now()))
    return {'ok':True}

class BulkDelete(BaseModel):
    plan_ids:list[int]=Field(default_factory=list,max_length=200)
    visit_ids:list[int]=Field(default_factory=list,max_length=200)

@app.post('/api/admin/bulk-delete')
def bulk_delete(req:Request,data:BulkDelete):
    admin_guard(req,True)
    with store.write() as db:
        for pid in set(data.plan_ids):
            db.execute('DELETE FROM notifications WHERE plan_id=?',(pid,))
            db.execute('DELETE FROM plans WHERE id=?',(pid,))
        for vid in set(data.visit_ids):db.execute('DELETE FROM visits WHERE id=?',(vid,))
        db.execute('INSERT INTO events(kind,subject,created_at) VALUES(?,?,?)',('bulk_delete',f'plans:{len(set(data.plan_ids))} visits:{len(set(data.visit_ids))}',now()))
    return {'ok':True}

class NewBlock(BaseModel):
    day:str
    from_time:str
    to_time:str
    title:str=Field(default='Busy',max_length=60)

@app.post('/api/admin/blocks')
def new_block(req:Request,data:NewBlock):
    admin_guard(req,True)
    try:
        a=datetime.fromisoformat(data.day+'T'+data.from_time).replace(tzinfo=TZ)
        b=datetime.fromisoformat(data.day+'T'+data.to_time).replace(tzinfo=TZ)
        if b<=a:b+=timedelta(days=1)
        if b-a>timedelta(days=2):raise ValueError()
    except (ValueError,TypeError):raise HTTPException(400,'Invalid date or time.')
    with store.write() as db:
        db.execute('INSERT INTO blocks(start_utc,end_utc,title,created_at) VALUES(?,?,?,?)',
                   (int(a.timestamp()),int(b.timestamp()),data.title.strip() or 'Busy',now()))
    return {'ok':True}

@app.delete('/api/admin/blocks/{bid}')
def remove_block(req:Request,bid:int):
    admin_guard(req,True)
    with store.write() as db:db.execute('DELETE FROM blocks WHERE id=?',(bid,))
    return {'ok':True}

class Settings(BaseModel):
    duration:int=Field(ge=30,le=360)
    slot_step:int=Field(ge=15,le=60)
    open_hour:int=Field(ge=0,le=22)
    close_hour:int=Field(ge=1,le=24)
    photo_enabled:bool=False
    guest_deck_id:int=1
    guest_language:str='en'
    guest_language_picker:bool=True
    admin_language:str='en'
    visitor_notifications:bool=False
    no_click_cancels:bool=False
    runaway_no:bool=True
    owner_name:str=Field(default='',max_length=60)
    app_timezone:str=Field(default='',max_length=80)
    public_base_url:str=Field(default='',max_length=250)
    timeline_enabled:bool=False

@app.post('/api/admin/settings')
def save_settings(req:Request,data:Settings):
    global TZ, PUBLIC_ORIGIN, INSTALLATION_ORIGIN
    admin_guard(req,True)
    if data.close_hour*60-data.open_hour*60<data.duration:raise HTTPException(400,'Opening hours must be longer than the date duration.')
    if data.guest_language not in LANGUAGES or data.admin_language not in LANGUAGES:raise HTTPException(400,'Unsupported language.')
    try:tz=ZoneInfo(data.app_timezone) if data.app_timezone else TZ
    except (KeyError,ValueError):raise HTTPException(400,'Unsupported time zone.')
    public=validate_public_url(data.public_base_url) if data.public_base_url.strip() else ''
    with store.write() as db:
        if not db.execute('SELECT 1 FROM decks WHERE id=? AND archived=0',(data.guest_deck_id,)).fetchone():raise HTTPException(400,'Guest deck not found.')
        for k in ('guest_deck_id','guest_language','admin_language'):set_setting(db,k,getattr(data,k))
        set_setting(db,'guest_language_picker','1' if data.guest_language_picker else '0')
        set_setting(db,'visitor_notifications','1' if data.visitor_notifications else '0')
        set_setting(db,'no_click_cancels','1' if data.no_click_cancels else '0')
        set_setting(db,'runaway_no','1' if data.runaway_no else '0')
        if data.owner_name.strip():set_setting(db,'owner_name',validate_owner_name(data.owner_name))
        if data.app_timezone.strip():set_setting(db,'app_timezone',data.app_timezone.strip())
        set_setting(db,'public_base_url',public)
        if public and INSTALLATION_ORIGIN and INSTALLATION_ORIGIN != public:
            set_setting(db,'installation_origin','')
        set_setting(db,'timeline_enabled','1' if data.timeline_enabled else '0')
        for k in ('duration','slot_step','open_hour','close_hour'):set_setting(db,k,getattr(data,k))
        has_photo=bool(db.execute('SELECT 1 FROM photos WHERE id=1').fetchone())
        set_setting(db,'photo_enabled','1' if data.photo_enabled and has_photo else '0')
    TZ=tz
    PUBLIC_ORIGIN=public
    if public and INSTALLATION_ORIGIN != public:
        INSTALLATION_ORIGIN=''
    return {'ok':True}

from .image_pipeline import process_profile_photo, read_limited_body, MediaError

class PhotoUpload(BaseModel):
    data:str=Field(max_length=33_600_000)


def store_profile_media(media):
    with store.write() as db:
        db.execute('INSERT INTO photos(id,webp) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET webp=excluded.webp',(media.content,))
    return {'ok':True,'size':len(media.content)}


@app.post('/api/admin/photo/file')
async def upload_photo_binary(req:Request):
    admin_guard(req,True)
    if req.headers.get('content-type','').split(';')[0].strip().lower()!='application/octet-stream':
        raise HTTPException(415,'Select a file and upload it again.')
    try:
        return store_profile_media(process_profile_photo(await read_limited_body(req)))
    except MediaError as e:
        raise HTTPException(e.status,str(e)) from e


@app.post('/api/admin/photo')
def upload_photo(req:Request,data:PhotoUpload):
    admin_guard(req,True)
    try:
        binary=base64.b64decode(data.data.split(',')[-1],validate=True)
        return store_profile_media(process_profile_photo(binary))
    except (ValueError,base64.binascii.Error) as exc:
        raise HTTPException(400,'Unable to read image data.') from exc
    except MediaError as e:
        raise HTTPException(e.status,str(e)) from e


@app.delete('/api/admin/photo')
def delete_photo(req:Request):
    admin_guard(req,True)
    with store.write() as db:
        db.execute('DELETE FROM photos WHERE id=1')
        set_setting(db,'photo_enabled','0')
    return {'ok':True}

class PasswordChange(BaseModel):
    current:str
    new:str=Field(min_length=14,max_length=256)

@app.post('/api/admin/password')
def change_password(req:Request,resp:Response,data:PasswordChange):
    admin_guard(req,True)
    with store.write() as db:
        current=db.execute('SELECT password_hash FROM admins LIMIT 1').fetchone()
        try:correct=ph.verify(current['password_hash'],data.current)
        except (VerifyMismatchError,VerificationError):correct=False
        if not correct:raise HTTPException(403,'Current password is incorrect.')
        db.execute('UPDATE admins SET password_hash=?',(ph.hash(data.new),))
        db.execute('DELETE FROM admin_sessions')
        db.execute("DELETE FROM push_subscriptions WHERE audience='admin'")
    resp.delete_cookie('dwm_admin',path='/')
    return {'ok':True,'login_required':True}

@app.get('/api/admin/photo')
def admin_photo(req:Request):
    admin_guard(req)
    with store.read() as db:
        row=db.execute('SELECT webp FROM photos WHERE id=1').fetchone()
    if not row:raise HTTPException(404)
    return Response(row['webp'],media_type='image/webp',headers={'Cache-Control':'no-store'})



# ---- Activity deck editor (existing invitations keep their chosen deck and language) ----
class DeckCreate(BaseModel):
    name:str=Field(min_length=1,max_length=70)
    clone_from:int|None=None

@app.post('/api/admin/decks')
def create_deck(req:Request,data:DeckCreate):
    admin_guard(req,True)
    with store.write() as db:
        if db.execute('SELECT COUNT(*) FROM decks').fetchone()[0]>=80:raise HTTPException(400,'Deck limit reached.')
        db.execute('INSERT INTO decks(name,created_at) VALUES(?,?)',(data.name.strip(),now()))
        did=db.execute('SELECT last_insert_rowid()').fetchone()[0]
        if data.clone_from:
            if not db.execute('SELECT 1 FROM decks WHERE id=?',(data.clone_from,)).fetchone():raise HTTPException(404,'Source deck not found.')
            for card in db.execute('SELECT slug,emoji,translations,position FROM deck_cards WHERE deck_id=? ORDER BY position',(data.clone_from,)).fetchall():
                db.execute('INSERT INTO deck_cards(deck_id,slug,emoji,translations,position) VALUES(?,?,?,?,?)',(did,card['slug'],card['emoji'],card['translations'],card['position']))
        return {'id':did,'name':data.name.strip()}

class DeckEdit(BaseModel):
    name:str=Field(min_length=1,max_length=70)
    archived:bool=False

@app.put('/api/admin/decks/{did}')
def update_deck(req:Request,did:int,data:DeckEdit):
    admin_guard(req,True)
    with store.write() as db:
        if not db.execute('SELECT 1 FROM decks WHERE id=?',(did,)).fetchone():raise HTTPException(404,'Deck not found.')
        if data.archived and int(get_setting(db,'guest_deck_id','1'))==did:raise HTTPException(409,'The default guest deck cannot be archived.')
        db.execute('UPDATE decks SET name=?,archived=? WHERE id=?',(data.name.strip(),int(data.archived),did))
    return {'ok':True}

@app.delete('/api/admin/decks/{did}')
def delete_deck(req:Request,did:int):
    admin_guard(req,True)
    with store.write() as db:
        if did==1:raise HTTPException(409,'The Classic deck is protected. You may edit its cards.')
        if did==int(get_setting(db,'guest_deck_id','1')):raise HTTPException(409,'Choose a different default guest deck first.')
        if db.execute('SELECT 1 FROM invites WHERE deck_id=? LIMIT 1',(did,)).fetchone():raise HTTPException(409,'This deck is used by invitations. Archive it instead of deleting it.')
        if db.execute('SELECT 1 FROM plans WHERE deck_id=? LIMIT 1',(did,)).fetchone():raise HTTPException(409,'Bu desteye bağlı date kaydı var; arşivle.')
        db.execute('DELETE FROM decks WHERE id=?',(did,))
    return {'ok':True}

class CardInput(BaseModel):
    emoji:str=Field(min_length=1,max_length=16)
    translations:dict[str,str]
    position:int=Field(default=0,ge=0,le=500)

def validate_labels(labels):
    if any(lang not in LANGUAGES for lang in labels):raise HTTPException(400,'Unknown language code.')
    clean={k:v.strip() for k,v in labels.items() if isinstance(v,str) and v.strip()}
    if not clean.get('en'):raise HTTPException(400,'An English card label is required.')
    if any(len(v)>85 for v in clean.values()):raise HTTPException(400,'Card labels cannot exceed 85 characters.')
    return json.dumps(clean,ensure_ascii=False)

@app.post('/api/admin/decks/{did}/cards')
def create_card(req:Request,did:int,data:CardInput):
    admin_guard(req,True)
    with store.write() as db:
        if not db.execute('SELECT 1 FROM decks WHERE id=?',(did,)).fetchone():raise HTTPException(404,'Deck not found.')
        if db.execute('SELECT COUNT(*) FROM deck_cards WHERE deck_id=?',(did,)).fetchone()[0]>=40:raise HTTPException(400,'A deck can contain up to 40 cards.')
        slug='custom_'+secrets.token_hex(5)
        db.execute('INSERT INTO deck_cards(deck_id,slug,emoji,translations,position) VALUES(?,?,?,?,?)',(did,slug,data.emoji,validate_labels(data.translations),data.position))
        from .features141 import catalog_card
        catalog_card(db,data.emoji,json.loads(validate_labels(data.translations)))
        return {'slug':slug}

@app.put('/api/admin/decks/{did}/cards/{slug}')
def update_card(req:Request,did:int,slug:str,data:CardInput):
    admin_guard(req,True)
    with store.write() as db:
        cur=db.execute('UPDATE deck_cards SET emoji=?,translations=?,position=? WHERE deck_id=? AND slug=?',
                       (data.emoji,validate_labels(data.translations),data.position,did,slug))
        if not cur.rowcount:raise HTTPException(404,'Card not found.')
        from .features141 import catalog_card
        catalog_card(db,data.emoji,json.loads(validate_labels(data.translations)))
    return {'ok':True}

@app.delete('/api/admin/decks/{did}/cards/{slug}')
def delete_card(req:Request,did:int,slug:str):
    admin_guard(req,True)
    with store.write() as db:
        if db.execute('SELECT COUNT(*) FROM deck_cards WHERE deck_id=?',(did,)).fetchone()[0]<=1:raise HTTPException(409,'Destede en az bir kart olmalı.')
        db.execute('DELETE FROM deck_cards WHERE deck_id=? AND slug=?',(did,slug))
    return {'ok':True}

# ----- Notification center and opt-in system notifications -----
@app.get('/api/admin/notifications')
def admin_notifications(req:Request):
    admin_guard(req)
    with store.read() as db:
        entries=[dict(r) for r in db.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT 150')]
        count=db.execute('SELECT COUNT(*) FROM notifications WHERE read_at IS NULL').fetchone()[0]
    return {'notifications':entries,'unread':count}

class NotificationIds(BaseModel):
    ids:list[int]=Field(default_factory=list,max_length=150)

@app.post('/api/admin/notifications/read')
def read_notifications(req:Request,data:NotificationIds):
    admin_guard(req,True)
    with store.write() as db:
        if data.ids:
            db.executemany('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?',[(now(),nid) for nid in set(data.ids)])
        else: db.execute('UPDATE notifications SET read_at=? WHERE read_at IS NULL',(now(),))
    return {'ok':True}

@app.post('/api/admin/notifications/clear')
def clear_notifications(req:Request):
    admin_guard(req,True)
    with store.write() as db:db.execute('DELETE FROM notifications WHERE read_at IS NOT NULL')
    return {'ok':True}

@app.get('/api/push/public-key')
def push_key(req:Request):
    # Does not trigger permission prompt; public key is intended to be public.
    return {'public_key':vapid_public()}

class PushSubscription(BaseModel):
    subscription:dict

def valid_subscription(data):
    if not isinstance(data,dict):raise HTTPException(400,'Geçersiz abonelik.')
    endpoint=data.get('endpoint','')
    keys=data.get('keys',{})
    if not allowed_push_endpoint(endpoint):raise HTTPException(400,'Desteklenmeyen push servisi.')
    if not isinstance(keys,dict) or not all(isinstance(keys.get(k),str) and 20<=len(keys[k])<=256 for k in ('p256dh','auth')):
        raise HTTPException(400,'Abonelik anahtarları geçersiz.')
    data={'endpoint':endpoint,'keys':{'p256dh':keys['p256dh'],'auth':keys['auth']}}
    if len(json.dumps(data))>2048:raise HTTPException(400,'Abonelik çok büyük.')
    return data

def subscribe(db,subscription,audience,token_hash,plan_id=None):
    data=valid_subscription(subscription)
    hashed=digest(data['endpoint'])
    db.execute("""INSERT INTO push_subscriptions(audience,token_hash,plan_id,endpoint_hash,subscription_json,created_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(endpoint_hash) DO UPDATE SET audience=excluded.audience,
        token_hash=excluded.token_hash,plan_id=excluded.plan_id,subscription_json=excluded.subscription_json""",
        (audience,token_hash,plan_id,hashed,json.dumps(data),now()))
    return {'ok':True}

@app.post('/api/admin/push/subscribe')
def admin_push_subscribe(req:Request,data:PushSubscription):
    admin_guard(req,True)
    with store.write() as db:return subscribe(db,data.subscription,'admin',digest(req.cookies['dwm_admin']))

@app.post('/api/admin/push/unsubscribe')
def admin_push_unsubscribe(req:Request):
    admin_guard(req,True)
    with store.write() as db:db.execute("DELETE FROM push_subscriptions WHERE audience='admin' AND token_hash=?",(digest(req.cookies['dwm_admin']),))
    return {'ok':True}

@app.post('/api/admin/push/unsubscribe-all')
def admin_push_unsubscribe_all(req:Request):
    admin_guard(req,True)
    with store.write() as db:db.execute("DELETE FROM push_subscriptions WHERE audience='admin'")
    return {'ok':True}

@app.post('/api/visitor/push/subscribe')
def visitor_push_subscribe(req:Request,data:PushSubscription):
    write_guard(req)
    with store.write() as db:
        if get_setting(db,'visitor_notifications','0')!='1':raise HTTPException(403,'Ziyaretçi bildirimleri kapalı.')
        v=require_visitor(db,req)
        inv=active_invite(db,v)
        plan=find_plan(db,invite_id=inv['id']) if inv else find_plan(db,visit_id=v['id'])
        if not plan:raise HTTPException(403,'Önce date planlanmalı.')
        return subscribe(db,data.subscription,'visitor',v['token_hash'],plan['id'])

@app.post('/api/visitor/push/unsubscribe')
def visitor_push_unsubscribe(req:Request):
    write_guard(req)
    with store.write() as db:
        v=require_visitor(db,req)
        db.execute("DELETE FROM push_subscriptions WHERE audience='visitor' AND token_hash=?",(v['token_hash'],))
    return {'ok':True}

from .features130 import router as features130_router
from .features140 import router as features140_router
from .features141 import router as features141_router
app.include_router(features130_router)
app.include_router(features140_router)
app.include_router(features141_router)

@app.get('/service-worker.js')
def serve_worker():
    return FileResponse(ROOT/'web'/'service-worker.js',media_type='text/javascript',headers={'Cache-Control':'no-store','Service-Worker-Allowed':'/'})

def _site_page_markup(path: str) -> str:
    # The HTML response is personalized on the server so browser tabs and
    # link preview crawlers see the correct name without executing JavaScript.
    with store.read() as db:
        owner = get_setting(db, 'owner_name', 'Me') or 'Me'
    display_name = escape_html('DateWith' + owner, quote=True)
    browser_title = display_name + (' | Admin' if path == 'panel' else '')
    page = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
    replacements = {
        '<title>DateWithMe</title>': f'<title>{browser_title}</title>',
        '<meta property="og:title" content="DateWithMe" />': f'<meta property="og:title" content="{display_name}" />',
        '<meta name="twitter:title" content="DateWithMe" />': f'<meta name="twitter:title" content="{display_name}" />',
    }
    for original, replacement in replacements.items():
        if original not in page:
            raise RuntimeError('Missing expected public page metadata')
        page = page.replace(original, replacement, 1)
    # A configured public URL is authoritative. Do not derive OG URLs from
    # arbitrary Host or forwarded headers supplied by visitors.
    if PUBLIC_ORIGIN:
        origin = escape_html(PUBLIC_ORIGIN, quote=True)
        page = page.replace('property="og:url" content="/"',
                            f'property="og:url" content="{origin}/"', 1)
        page = page.replace('property="og:image" content="/og-datewithme.png"',
                            f'property="og:image" content="{origin}/og-datewithme.png"', 1)
        page = page.replace('name="twitter:image" content="/og-datewithme.png"',
                            f'name="twitter:image" content="{origin}/og-datewithme.png"', 1)
    return page


@app.get('/{path:path}')
def serve_frontend(path:str):
    if path.startswith('api/') or path.startswith('.') or '\\' in path or '..' in path:
        raise HTTPException(404)
    if path in ('','date','panel','privacy','setup'):
        if not is_ready():
            if path != 'setup':return RedirectResponse('/setup',status_code=302)
            return FileResponse(ROOT/'web'/'setup.html',headers={'Cache-Control':'no-store'})
        if path == 'setup':return RedirectResponse('/panel',status_code=302)
        return HTMLResponse(_site_page_markup(path), headers={'Cache-Control':'no-store'})
    safe_assets={'client.js','layout.css','recovery.js','setup.js','setup.css',
        'favicon.svg','manifest.webmanifest','icon-192.png','icon-512.png',
        'apple-touch-icon.png','og-datewithme.png','support-btc-qr.png','orictux-logo.png','robots.txt'}
    if path == 'manifest.webmanifest':
        manifest = json.loads((ROOT / 'web' / 'manifest.webmanifest').read_text(encoding='utf-8'))
        if is_ready():
            with store.read() as db:
                owner = get_setting(db, 'owner_name', 'Me') or 'Me'
            manifest['name'] = 'DateWith' + owner
            manifest['short_name'] = 'DateWith' + owner
        return JSONResponse(manifest, headers={'Cache-Control':'no-store'})
    if path in safe_assets:
        return FileResponse(ROOT/'web'/path,headers={'Cache-Control':'no-store' if path.endswith(('.js','.css','.txt')) else 'public, max-age=86400'})
    raise HTTPException(404,'File not found.')
