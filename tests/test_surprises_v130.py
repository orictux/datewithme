import base64, io, json, os
from pathlib import Path
from PIL import Image
from test_app import HEAD, invite, book, future_day, users

def new_png():
    out=io.BytesIO();Image.new('RGB',(10,10),(100,40,125)).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()

def test_surprise_multiple_stages_and_reply_once(users):
    admin,invitee,guest,other,_=users
    image=admin.post('/api/admin/surprise-media',json={'data':new_png()},headers=HEAD)
    assert image.status_code==200,image.text
    mid=image.json()['id']
    templates=admin.post('/api/admin/surprise-templates',headers=HEAD,json={
        'title':'Çiçek şakası','body':'Umarım çiçeğe alerjin yoktur :))','button_label':'AHAHAHA','mode':'reply','media_id':mid})
    assert templates.status_code==200
    tid=templates.json()['id']
    created=admin.post('/api/admin/invites',headers=HEAD,json={
        'name':'Taylor','deck_id':1,'language':'tr',
        'surprises':[{'stage':'after_yes','template_id':tid},{'stage':'result','body':'Görüşürüz!','mode':'continue','button_label':'Tamam 💕'}]})
    assert created.status_code==200,created.text
    inv=created.json()
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    assert invitee.get('/api/surprises/next?stage=before_intro').json()['surprise'] is None
    note=invitee.get('/api/surprises/next?stage=after_yes').json()['surprise']
    assert note['body'].startswith('Umarım') and note['media_id']==mid
    assert invitee.get(f'/api/surprise-media/{mid}').status_code==200
    assert guest.get(f'/api/surprise-media/{mid}').status_code==404
    response=invitee.post(f'/api/surprises/{note["id"]}/respond',headers=HEAD,json={'answer':'çok tatlı 😂'})
    assert response.status_code==200,response.text
    assert invitee.get('/api/surprises/next?stage=after_yes').json()['surprise'] is None
    assert invitee.get(f'/api/surprise-media/{mid}').status_code==404
    assert invitee.post(f'/api/surprises/{note["id"]}/respond',headers=HEAD,json={'answer':'başka'}).json()['already_done']
    state=admin.get('/api/admin/state').json()
    assert any(s['response_text']=='çok tatlı 😂' for s in state['surprises'])
    assert any(n['kind']=='surprise_replied' for n in state['notifications'])
    assert invitee.get('/api/surprises/next?stage=result').json()['surprise']['body']=='Görüşürüz!'

def test_surprise_not_cross_invitation_or_guest(users):
    admin,invitee,guest,other,_=users
    first=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Taylor','surprises':[{'stage':'before_intro','body':'Sır'}]}).json()
    second=invite(admin,'Cansu')
    invitee.get('/api/bootstrap',params={'code':first['code']})
    sid=invitee.get('/api/surprises/next?stage=before_intro').json()['surprise']['id']
    other.get('/api/bootstrap',params={'code':second['code']})
    assert other.post(f'/api/surprises/{sid}/respond',headers=HEAD,json={}).status_code==404
    assert guest.get('/api/surprises/next?stage=before_intro').json()['surprise'] is None
    assert guest.post(f'/api/surprises/{sid}/respond',headers=HEAD,json={}).status_code==401

def test_surprise_validation_and_media_security(users):
    admin,invitee,guest,other,_=users
    assert other.post('/api/admin/surprise-media',headers=HEAD,json={'data':new_png()}).status_code==401
    assert admin.post('/api/admin/surprise-media',headers=HEAD,json={'data':'data:image/svg+xml;base64,AAA='}).status_code==400
    assert admin.post('/api/admin/surprise-media',headers=HEAD,json={'data':'data:image/png;base64,AAA='}).status_code==400
    inv=invite(admin)
    r=admin.post(f'/api/admin/invites/{inv["id"]}/surprises',headers=HEAD,json={'stage':'not-a-stage','body':'test'})
    assert r.status_code==400
    assert admin.get('/api/surprises/next?stage=impossible').status_code==400

def test_rules_and_export_import_exclude_private_data(users):
    admin,invitee,guest,other,root=users
    assert other.get('/api/admin/config/export').status_code==401
    tpl=admin.post('/api/admin/surprise-templates',headers=HEAD,json={'title':'Özel','body':'Şaka','mode':'continue'}).json()
    inv=invite(admin)
    rules=admin.get('/api/admin/notification-rules').json()['rules'];assert len(rules)>=5
    assert admin.post('/api/admin/notification-rules',headers=HEAD,json={'kind':'surprise_replied','admin':False,'visitor':True}).status_code==200
    cfg=admin.get('/api/admin/config/export').json()
    s=json.dumps(cfg,ensure_ascii=False)
    assert cfg['format']=='DateWithMe-config'
    assert inv['code'] not in s and 'password_hash' not in s and 'token_hash' not in s
    assert cfg['rules']['surprise_replied']['visitor'] is True
    assert admin.post('/api/admin/config/import',headers=HEAD,json={'config':cfg}).status_code==200
    assert admin.post('/api/admin/config/import',headers=HEAD,json={'config':{'format':'bad','version':1}}).status_code==400
    assert admin.get('/api/admin/state').json()['invites']

def test_backup_encrypted_and_schema_no_data_loss(users):
    admin,invitee,guest,other,root=users
    inv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    backup_files=list((root/'backups').glob('*.db.enc'))
    assert backup_files
    assert all(f.read_bytes().startswith(b'DWM-AES256-GCM-v1\n') for f in backup_files)
    assert b'SQLite format 3' not in backup_files[0].read_bytes()

def test_reminder_once_with_fake_time(users,monkeypatch):
    admin,invitee,guest,other,_=users
    from app import main
    from app.features130 import run_reminders
    inv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    plan=book(invitee)
    with main.store.write() as db:
        new_start=main.now_epoch()+1800
        db.execute('UPDATE plans SET start_utc=?,end_utc=? WHERE id=?',(new_start,new_start+7200,plan['id']))
    assert run_reminders()==1
    assert run_reminders()==0
    rules=admin.get('/api/admin/state').json()['notification_rules']
    assert any(r['kind']=='reminder_60' and r['admin'] for r in rules)
    notices=admin.get('/api/admin/notifications').json()['notifications']
    assert sum(n['kind']=='reminder_60' for n in notices)==1

def test_realistic_upgrade_from_v124_schema(users):
    """A DB missing all v1.3 tables is upgraded without erasing an invitation."""
    admin,invitee,guest,other,root=users
    from app import main
    from app.store import EncryptedStore
    inv=invite(admin)
    # Simulate the last pre-v1.3 database schema before creating this release.
    with main.store.write() as db:
        db.executescript('''DROP TABLE IF EXISTS reminder_sent;
            DROP TABLE IF EXISTS surprise_answers;
            DROP TABLE IF EXISTS surprise_assignments;
            DROP TABLE IF EXISTS surprise_templates;
            DROP TABLE IF EXISTS surprise_media;''')
    # Discard old in-memory store; reopening is the migration path.
    main.store.close()
    old_db=main.store
    try:
        fresh=EncryptedStore(path=root/'database.enc',key_path=root/'key')
        with fresh.read() as db:
            assert db.execute('SELECT code FROM invites WHERE id=?',(inv['id'],)).fetchone()['code']==inv['code']
            assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='surprise_templates'").fetchone()
        assert (root/'database.enc.pre-v1.3.0.bak').exists()
        fresh.close()
    finally:
        # TestClient lifespan closes the active store after the fixture finishes.
        main.store=EncryptedStore(path=root/'database.enc',key_path=root/'key')
