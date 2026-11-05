from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from test_app import users, HEAD

def day():return (datetime.now(ZoneInfo('Europe/Istanbul'))+timedelta(days=12)).strftime('%Y-%m-%d')

def test_seeded_decks_and_languages(users):
    admin,client,*_=users
    state=admin.get('/api/admin/state').json()
    assert [d['name'] for d in state['decks']][:3]==['Classic','Adventure','Movies & Chill']
    assert len(state['languages'])==16
    assert len(state['decks'][0]['cards'])==6
    assert state['decks'][0]['cards'][1]['translations']['ru']=='Выпьем кофе'
    assert state['settings']['guest_language']=='en'


def test_invite_deck_russian_and_admin_turkish(users):
    admin,client,*_=users
    r=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Julia','deck_id':3,'language':'ru','code_length':4})
    assert r.status_code==200,r.text
    invite=r.json()
    data=client.get('/api/bootstrap',params={'code':invite['code']}).json()
    assert data['language']=='ru' and data['deck']['name']=='Movies & Chill'
    assert data['deck']['cards'][0]['label']=='Посмотрим фильм'
    assert client.post('/api/plans',headers=HEAD,json={'activity':'drive','day':day(),'time':'20:00'}).status_code==400
    slot=next(x['time'] for x in client.get('/api/availability',params={'day':day()}).json()['slots'] if x['available'])
    plan=client.post('/api/plans',headers=HEAD,json={'activity':'movies','day':day(),'time':slot}).json()['plan']
    assert plan['activity_label']=='Посмотрим фильм 🍿'
    # Admin language tr: existing plan must display its Turkish label, not Russian.
    admin_plan=admin.get('/api/admin/state').json()['plans'][0]
    assert admin_plan['activity_label']=='Watch a movie 🍿'
    # No stale session after return to homepage.
    assert client.get('/api/bootstrap').json()['language']=='ru'


def test_clone_card_translation_updates_and_snapshot(users):
    admin,client,*_=users
    c=admin.post('/api/admin/decks',headers=HEAD,json={'name':'Profil 2','clone_from':1})
    assert c.status_code==200,c.text
    did=c.json()['id']
    cards=next(x for x in admin.get('/api/admin/state').json()['decks'] if x['id']==did)['cards']
    card=next(x for x in cards if x['id']=='coffee')
    updated={'emoji':'🚗','translations':{'tr':'Arabayla gezelim','en':"Let's go driving",'ru':'Покатаемся'},'position':1}
    assert admin.put(f'/api/admin/decks/{did}/cards/coffee',headers=HEAD,json=updated).status_code==200
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Taylor','deck_id':did,'language':'en'}).json()
    data=client.get('/api/bootstrap',params={'code':inv['code']}).json()
    renamed=next(c for c in data['deck']['cards'] if c['id']=='coffee')
    assert renamed['emoji']=='🚗' and renamed['label']=="Let's go driving"
    slot=next(x['time'] for x in client.get('/api/availability',params={'day':day()}).json()['slots'] if x['available'])
    plan=client.post('/api/plans',headers=HEAD,json={'activity':'coffee','day':day(),'time':slot}).json()['plan']
    assert plan['activity_label']=="Let's go driving 🚗"
    # Later admin edit must not change already booked date's description.
    updated['translations']['en']='A different new label'
    assert admin.put(f'/api/admin/decks/{did}/cards/coffee',headers=HEAD,json=updated).status_code==200
    assert client.get('/api/bootstrap').json()['plan']['activity_label']=="Let's go driving 🚗"
    # Still possible to remove card while preserving booked record snapshot.
    assert admin.delete(f'/api/admin/decks/{did}/cards/coffee',headers=HEAD).status_code==200
    assert client.get('/api/bootstrap').json()['plan']['activity_label']=="Let's go driving 🚗"


def test_guest_default_deck_locale_and_old_invites_stable(users):
    admin,client,guest,*_=users
    old=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Julia','deck_id':1,'language':'tr'}).json()
    client.get('/api/bootstrap',params={'code':old['code']})
    settings={'duration':120,'slot_step':30,'open_hour':16,'close_hour':23,'photo_enabled':False,
              'guest_deck_id':2,'guest_language':'ru','admin_language':'en'}
    r=admin.post('/api/admin/settings',headers=HEAD,json=settings)
    assert r.status_code==200,r.text
    anon=guest.get('/api/bootstrap').json()
    assert anon['language']=='ru' and anon['deck']['id']==2
    guest.post('/api/guest',headers=HEAD,json={})
    assert guest.get('/api/bootstrap').json()['deck']['id']==2
    assert client.get('/api/bootstrap').json()['language']=='tr'
    assert admin.get('/api/admin/state').json()['settings']['admin_language']=='en'
    assert admin.get('/api/admin/state').json()['decks'][0]['cards'][0]['label']=="Let's grab a bite"


def test_deck_delete_guards_and_new_card_fallback(users):
    admin,client,*_=users
    did=admin.post('/api/admin/decks',headers=HEAD,json={'name':'Yeni Deste'}).json()['id']
    card={'emoji':'🍿','position':0,'translations':{'tr':'Film İzlemek','en':'Watching Movies'}}
    r=admin.post(f'/api/admin/decks/{did}/cards',headers=HEAD,json=card)
    assert r.status_code==200,r.text
    slug=r.json()['slug']
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Jennifer','deck_id':did,'language':'ru'}).json()
    info=client.get('/api/bootstrap',params={'code':inv['code']}).json()
    assert info['deck']['cards'][0]['label']=='Watching Movies' # Fallback to English
    assert admin.delete(f'/api/admin/decks/{did}',headers=HEAD).status_code==409
    assert admin.delete(f'/api/admin/decks/{did}/cards/{slug}',headers=HEAD).status_code==409
    assert admin.put(f'/api/admin/decks/{did}',headers=HEAD,json={'name':'Arşiv','archived':True}).status_code==200
    assert client.get('/api/bootstrap').json()['deck']['cards'][0]['label']=='Watching Movies'

def test_migration_from_v104_retains_old_invitation_and_plan(tmp_path):
    import sqlite3,secrets,json
    from datetime import datetime,timedelta
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from app.store import EncryptedStore,MAGIC,AAD
    old=sqlite3.connect(':memory:')
    old.executescript('''
    CREATE TABLE config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE invites(id INTEGER PRIMARY KEY,person_id INTEGER NOT NULL,code TEXT UNIQUE,created_at TEXT,revoked INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE visits(id INTEGER PRIMARY KEY,token_hash TEXT UNIQUE,invite_id INTEGER,guest INTEGER NOT NULL DEFAULT 0,device TEXT DEFAULT 'Other',os TEXT DEFAULT 'Other',browser TEXT DEFAULT 'Other',no_count INTEGER DEFAULT 0,stage TEXT DEFAULT 'intro',chosen_activity TEXT,chosen_day TEXT,chosen_time TEXT,created_at TEXT,updated_at TEXT);
    CREATE TABLE plans(id INTEGER PRIMARY KEY,invite_id INTEGER UNIQUE,visit_id INTEGER UNIQUE,activity TEXT NOT NULL,start_utc INTEGER,end_utc INTEGER,no_attempts INTEGER DEFAULT 0,status TEXT DEFAULT 'planned',cancel_cycle INTEGER DEFAULT 0,cancellation_message TEXT DEFAULT 'The host has cancelled this invitation.',created_at TEXT,updated_at TEXT);
    CREATE TABLE blocks(id INTEGER PRIMARY KEY,start_utc INTEGER,end_utc INTEGER,title TEXT DEFAULT 'Meşgul',created_at TEXT);
    CREATE TABLE cancel_views(id INTEGER PRIMARY KEY,plan_id INTEGER,cycle INTEGER,page_load_id TEXT UNIQUE,viewed_at TEXT);
    CREATE TABLE admins(id INTEGER PRIMARY KEY,password_hash TEXT);
    CREATE TABLE admin_sessions(token_hash TEXT PRIMARY KEY,expires_at INTEGER);
    CREATE TABLE events(id INTEGER PRIMARY KEY,kind TEXT,subject TEXT,created_at TEXT);
    CREATE TABLE photos(id INTEGER PRIMARY KEY,webp BLOB);
    INSERT INTO people VALUES(1,'Taylor','2026-11-02T00:00:00Z');
    INSERT INTO invites VALUES(1,1,'K7pQ','2026-11-02T00:00:00Z',0);
    INSERT INTO plans(id,invite_id,activity,start_utc,end_utc,created_at,updated_at)
        VALUES(1,1,'coffee',1920230000,1920237200,'2026-11-02T00:00:00Z','2026-11-02T00:00:00Z');
    ''')
    raw=old.serialize();old.close()
    key=secrets.token_bytes(32);nonce=secrets.token_bytes(12)
    path=tmp_path/'db.enc';path.write_bytes(MAGIC+nonce+AESGCM(key).encrypt(nonce,raw,AAD))
    store=EncryptedStore(path=path,key=key)
    with store.read() as db:
        inv=db.execute("SELECT * FROM invites WHERE code='K7pQ'").fetchone()
        assert inv['language']=='tr' and inv['deck_id']==1
        plan=db.execute('SELECT * FROM plans WHERE id=1').fetchone()
        assert json.loads(plan['activity_translations'])['tr']=='Kahvelenelim'
        assert db.execute('SELECT name FROM people WHERE id=1').fetchone()['name']=='Taylor'
    store.close()
    assert path.with_name('db.enc.pre-v1.1.0.bak').exists()
    # Reopening and migrating twice must preserve all records.
    reopened=EncryptedStore(path=path,key=key)
    with reopened.read() as db:
        assert db.execute('SELECT COUNT(*) FROM invites').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM plans').fetchone()[0]==1
    reopened.close()


def test_localizations_ship_inside_static_client(users):
    admin,client,*_=users
    r=client.get('/client.js')
    assert r.status_code==200
    assert 'const LOCALES' in r.text
    assert client.get('/app/presets.py').status_code==404
