import io
from PIL import Image
from test_app import HEAD, invite, users


def photo_bytes(color='pink', size=(800,1200)):
    b=io.BytesIO();Image.new('RGB',size,color).save(b,format='JPEG');return b.getvalue()


def test_card_library_and_merge_preserves_independent_deck_copies(users):
    admin,client,*_=users
    initial=admin.get('/api/admin/state').json()
    assert initial['card_library'] and len(initial['card_library'])>=6
    did=admin.post('/api/admin/decks',headers=HEAD,json={'name':'Yaz Aktiviteleri'}).json()['id']
    sources=next(d for d in initial['decks'] if d['name']=='Adventure')
    r=admin.post(f'/api/admin/decks/{did}/import-cards',headers=HEAD,json={'source_deck_id':sources['id']})
    assert r.status_code==200,r.text
    assert r.json()['added']==len(sources['cards'])
    cards=next(d for d in admin.get('/api/admin/state').json()['decks'] if d['id']==did)['cards']
    assert len(cards)==6 and cards[0]['label']==sources['cards'][0]['label']
    library_id=initial['card_library'][0]['id']
    assert admin.post(f'/api/admin/decks/{did}/import-cards',headers=HEAD,json={'library_ids':[library_id]}).status_code==200
    updated=next(d for d in admin.get('/api/admin/state').json()['decks'] if d['id']==did)['cards']
    assert len(updated)==7
    old=[c['id'] for c in updated]
    reversed_order=list(reversed(old))
    assert admin.put(f'/api/admin/decks/{did}/reorder',headers=HEAD,json={'slugs':reversed_order}).status_code==200
    cards=next(d for d in admin.get('/api/admin/state').json()['decks'] if d['id']==did)['cards']
    assert [c['id'] for c in cards]==reversed_order
    assert admin.put(f'/api/admin/decks/{did}/reorder',headers=HEAD,json={'slugs':['bad']}).status_code==409
    assert client.post(f'/api/admin/decks/{did}/import-cards',headers=HEAD,json={'library_ids':[library_id]}).status_code==401
    assert admin.delete(f'/api/admin/card-library/{library_id}',headers=HEAD).status_code==200
    assert len(next(d for d in admin.get('/api/admin/state').json()['decks'] if d['id']==did)['cards'])==7


def test_gallery_private_photos_and_square_crop(users):
    admin,client,other,*_=users
    raw=photo_bytes()
    normalized=admin.post('/api/admin/profile-photos/normalize',headers={**HEAD,'Content-Type':'application/octet-stream'},content=raw)
    assert normalized.status_code==200,normalized.text
    im=Image.open(io.BytesIO(normalized.content));assert im.format=='WEBP'
    cropped=photo_bytes('blue',(700,700))
    uploaded=admin.post('/api/admin/profile-photos',headers={**HEAD,'Content-Type':'application/octet-stream'},content=cropped)
    assert uploaded.status_code==200,uploaded.text
    pid=uploaded.json()['id']
    assert client.get(f'/api/admin/profile-photos/{pid}').status_code==401
    assert client.get(f'/api/invite-photo/{pid}').status_code==404
    assert admin.get(f'/api/admin/profile-photos/{pid}').headers['content-type']=='image/webp'
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Aleyna','photo_id':pid}).json()
    before=client.get(f'/api/invite-photo/{pid}')
    assert before.status_code==404
    r=client.get('/api/bootstrap',params={'code':inv['code']}).json()
    assert r['photo_enabled'] is True and r['photo_id']==pid
    assert client.get(f'/api/invite-photo/{pid}').status_code==200
    assert other.get(f'/api/invite-photo/{pid}').status_code==404
    assert admin.delete(f'/api/admin/profile-photos/{pid}',headers=HEAD).status_code==409


def test_invite_update_before_open_only_and_allows_surprise(users):
    admin,client,other,*_=users
    p=admin.post('/api/admin/profile-photos',headers={**HEAD,'Content-Type':'application/octet-stream'},content=photo_bytes()).json()['id']
    i=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Test'}).json()
    s=admin.get('/api/admin/state').json()['invites'][0]
    assert s['editable'] is True
    body={'deck_id':2,'language':'en','photo_id':p,'surprises':[{'stage':'after_yes','body':'Funny joke','mode':'continue','button_label':'AHAHA!'}]}
    r=admin.patch(f'/api/admin/invites/{i["id"]}',headers=HEAD,json=body)
    assert r.status_code==200,r.text
    updated=admin.get('/api/admin/state').json()['invites'][0]
    assert updated['code']==i['code'] and updated['language']=='en' and updated['deck_id']==2
    assert len(admin.get('/api/admin/state').json()['surprises'])==1
    assert client.get('/api/bootstrap',params={'code':i['code']}).json()['photo_id']==p
    assert admin.get('/api/admin/state').json()['invites'][0]['editable'] is False
    r=admin.patch(f'/api/admin/invites/{i["id"]}',headers=HEAD,json={'deck_id':1,'language':'tr','photo_id':None,'surprises':[]})
    assert r.status_code==409
    # Different unauthenticated devices cannot bypass admin authorization.
    assert other.patch(f'/api/admin/invites/{i["id"]}',headers=HEAD,json=body).status_code==401


def test_config_exports_library_and_import_restores(users):
    admin,*_=users
    data=admin.get('/api/admin/config/export').json()
    assert data['format']=='DateWithMe-config' and data['card_library']
    assert all('media' not in str(c) for c in data['card_library'])
    assert admin.post('/api/admin/config/import',headers=HEAD,json={'config':data}).status_code==200


def test_frontend_integrates_edit_gallery_drag(users):
    admin,client,*_=users
    import re
    html=client.get('/').text
    match=re.search(r'/client\.js',html)
    assert match,html
    js=client.get(match.group()).text
    for keyword in ['data-sort-card','profile-gallery-upload','data-edit-invite','card-library-picker','crop-canvas']:
        assert keyword in js


def test_migrate_previous_schema_without_gallery_preserves_invites(tmp_path):
    """A version missing the gallery and library tables must migrate safely."""
    import io, secrets, sqlite3
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from app.store import EncryptedStore, MAGIC, AAD, SCHEMA
    from app.presets import seed
    conn=sqlite3.connect(':memory:')
    conn.executescript(SCHEMA)
    conn.execute('ALTER TABLE invites ADD COLUMN deck_id INTEGER NOT NULL DEFAULT 1')
    conn.execute("ALTER TABLE invites ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
    conn.execute('ALTER TABLE people ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
    conn.execute('ALTER TABLE plans ADD COLUMN deck_id INTEGER')
    conn.execute('ALTER TABLE plans ADD COLUMN activity_translations TEXT')
    conn.execute('ALTER TABLE plans ADD COLUMN activity_emoji TEXT')
    seed(conn)
    conn.execute("INSERT INTO people(id,name,created_at) VALUES(1,'Taylor','2026-11-04T14:00:00Z')")
    conn.execute("INSERT INTO invites(id,person_id,code,created_at) VALUES(1,1,'K7pQ','2026-11-04T14:00:00Z')")
    picture=io.BytesIO();Image.new('RGB',(80,80),'salmon').save(picture,format='WEBP')
    conn.execute('INSERT INTO photos(id,webp) VALUES(1,?)',(picture.getvalue(),))
    conn.execute('DROP TABLE profile_photos')
    conn.execute('DROP TABLE card_library')
    conn.commit();raw=conn.serialize();conn.close()
    key=secrets.token_bytes(32);nonce=secrets.token_bytes(12)
    path=tmp_path/'database.enc';original=MAGIC+nonce+AESGCM(key).encrypt(nonce,raw,AAD)
    path.write_bytes(original)
    migrated=EncryptedStore(path=path,key=key)
    with migrated.read() as db:
        assert db.execute('SELECT name FROM people WHERE id=1').fetchone()['name']=='Taylor'
        assert db.execute('SELECT code FROM invites WHERE id=1').fetchone()['code']=='K7pQ'
        assert db.execute('SELECT COUNT(*) FROM deck_cards').fetchone()[0]>=6
        assert db.execute('SELECT COUNT(*) FROM card_library').fetchone()[0]>=6
        assert db.execute('SELECT COUNT(*) FROM profile_photos').fetchone()[0]==1
    migrated.close()
    assert (tmp_path/'database.enc.pre-v1.4.1.bak').read_bytes()==original
    assert not path.read_bytes().startswith(b'SQLite format 3')

def test_opened_lock_remains_after_visit_logs_are_deleted(users):
    admin,visitor,*_=users
    from app import main
    inv=invite(admin,name='Never Reopen')
    assert admin.get('/api/admin/state').json()['invites'][0]['editable'] is True
    visitor.get('/api/bootstrap',params={'code':inv['code']})
    with main.store.write() as db:
        db.execute('DELETE FROM visits WHERE invite_id=?',(inv['id'],))
        db.execute('DELETE FROM notifications WHERE invite_id=?',(inv['id'],))
    state=admin.get('/api/admin/state').json()
    assert state['invites'][0]['editable'] is False
    attempt=admin.patch(f'/api/admin/invites/{inv["id"]}',headers=HEAD,json={'deck_id':2,'language':'en'})
    assert attempt.status_code==409
