import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

ADMIN_PWD='a-strong-admin-test-password-2026'
HEAD={'x-datewithme-request':'1','origin':'http://testserver'}

@pytest.fixture()
def users(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'database.enc'))
    key_path=tmp_path/'key'
    key_path.write_text(secrets.token_hex(32))
    key_path.chmod(0o600)
    monkeypatch.setenv('DB_KEY_PATH',str(key_path))
    monkeypatch.setenv('SETUP_TOKEN_PATH',str(tmp_path/'setup.token'))
    monkeypatch.setenv('ALLOW_INSECURE_HTTP','1')
    monkeypatch.setenv('PUBLIC_BASE_URL','http://testserver')
    monkeypatch.setenv('COOKIE_SECURE','0')
    monkeypatch.setenv('ALLOWED_ORIGINS','http://testserver')
    from app.main import app, _rate
    _rate.clear()  # isolate global login throttle between independent test fixtures
    with TestClient(app) as administrator:
        token=(tmp_path/'setup.token').read_text().strip()
        setup=administrator.post('/api/setup/complete',headers=HEAD,json={
            'token':token,'owner_name':'John','password':ADMIN_PWD,
            'language':'en','timezone':'Europe/Istanbul','public_base_url':'http://testserver'})
        assert setup.status_code==200,setup.text
        assert administrator.post('/api/admin/login',headers=HEAD,json={'password':ADMIN_PWD}).status_code==200
        with TestClient(app) as invitee, TestClient(app) as guest, TestClient(app) as other:
            yield (administrator,invitee,guest,other,tmp_path)

def invite(admin,name='Taylor',person_id=None):
    payload={'name':name,'code_length':4}
    if person_id:payload={'person_id':person_id,'code_length':4}
    r=admin.post('/api/admin/invites',headers=HEAD,json=payload)
    assert r.status_code==200,r.text
    return r.json()

def future_day():
    return (datetime.now(ZoneInfo('Europe/Istanbul'))+timedelta(days=9)).strftime('%Y-%m-%d')

def book(v,day=None):
    day=day or future_day()
    s=v.get('/api/availability',params={'day':day}).json()['slots']
    slot=next(x['time'] for x in s if x['available'])
    r=v.post('/api/plans',headers=HEAD,json={'activity':'coffee','day':day,'time':slot})
    assert r.status_code==200,r.text
    return r.json()['plan']

def test_invite_is_named_and_persists_across_visits(users):
    admin,invitee,guest,other,root=users
    inv=invite(admin)
    res=invitee.get('/api/bootstrap',params={'code':inv['code']}).json()
    assert res['name']=='Taylor' and res['plan'] is None
    for i in range(3):assert invitee.post('/api/no-attempt',headers=HEAD,json={}).json()['count']==i+1
    plan=book(invitee)
    assert plan['status']=='planned'
    remembered=invitee.get('/api/bootstrap').json()
    assert remembered['name']=='Taylor' and remembered['plan']['id']==plan['id']
    foreign=other.get('/api/bootstrap',params={'code':inv['code']}).json()
    assert foreign['name']=='Taylor' and foreign['plan']['id']==plan['id']
    assert other.post('/api/plans',headers=HEAD,json={'activity':'food','day':future_day(),'time':'19:00'}).json()['already_planned']
    # Whole DB is encrypted on disk, not a raw sqlite3 file.
    encrypted=(root/'database.enc').read_bytes()
    assert encrypted.startswith(b'DWM-AES256-GCM-v1\n') and b'SQLite format 3' not in encrypted


def test_guest_does_not_block_availability_and_remembers(users):
    admin,invitee,guest,other,root=users
    assert guest.get('/api/bootstrap').json()['mode']=='stranger'
    assert guest.post('/api/guest',headers=HEAD,json={}).json()['mode']=='guest'
    first=book(guest)
    assert guest.get('/api/bootstrap').json()['plan']['id']==first['id']
    inv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    same_day=invitee.get('/api/availability',params={'day':first['day']}).json()['slots']
    assert any(x['time']==first['time'] and x['available'] for x in same_day)
    second=book(invitee,first['day'])
    assert second['time']==first['time']
    other.get('/api/bootstrap')
    available=other.get('/api/availability',params={'day':first['day']}).json()['slots']
    assert not next(x for x in available if x['time']==second['time'])['available']


def test_cancellation_multiple_views_dedup_and_reactivation(users):
    admin,invitee,guest,other,root=users
    inv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    plan=book(invitee)
    id=plan['id']
    assert admin.post(f'/api/admin/plans/{id}/cancel',headers=HEAD,json={'message':'The host has cancelled this invitation.'}).status_code==200
    view=invitee.get('/api/bootstrap').json()['plan']
    assert view['status']=='cancelled'
    for a in ['testpageviewident0001','testpageviewident0002','testpageviewident0003','testpageviewident0003']:
        res=invitee.post('/api/cancel-view',headers=HEAD,json={'page_load_id':a,'code':inv['code']})
        assert res.status_code==200
    state=admin.get('/api/admin/state').json()
    assert len(state['views'])==3 and state['plans'][0]['cancellation_views']==3
    assert admin.post(f'/api/admin/plans/{id}/reactivate',headers=HEAD,json={}).status_code==200
    assert invitee.get('/api/bootstrap').json()['plan']['status']=='planned'
    assert admin.post(f'/api/admin/plans/{id}/cancel',headers=HEAD,json={}).status_code==200
    new_cycle=invitee.post('/api/cancel-view',headers=HEAD,json={'page_load_id':'testpageviewident0004','code':inv['code']})
    assert new_cycle.status_code==200
    state=admin.get('/api/admin/state').json()
    assert state['plans'][0]['cancellation_views']==1
    assert len(state['views'])==4


def test_new_invite_replaces_remembered_session_but_keeps_old_detail(users):
    admin,invitee,guest,other,root=users
    one=invite(admin)
    invitee.get('/api/bootstrap',params={'code':one['code']})
    old=book(invitee)
    person_id=admin.get('/api/admin/state').json()['people'][0]['id']
    two=invite(admin,person_id=person_id)
    new=invitee.get('/api/bootstrap',params={'code':two['code']}).json()
    assert new['name']=='Taylor' and new['plan'] is None
    assert invitee.get('/api/bootstrap').json()['invite_code']==two['code']
    old_view=invitee.get('/api/bootstrap',params={'code':one['code']}).json()
    assert old_view['plan']['id']==old['id']
    assert invitee.get('/api/bootstrap').json()['invite_code']==two['code']


def test_reset_and_delete(users):
    admin,invitee,guest,other,root=users
    inv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    book(invitee)
    assert admin.post('/api/admin/invites/'+str(inv['id'])+'/reset',headers=HEAD,json={}).status_code==200
    assert invitee.get('/api/bootstrap').json()['plan'] is None
    assert admin.delete('/api/admin/invites/'+str(inv['id']),headers=HEAD).status_code==200
    assert invitee.get('/api/bootstrap',params={'code':inv['code']}).status_code==404


def test_calendar_ics_and_admin_protection(users):
    admin,invitee,guest,other,root=users
    inv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    book(invitee)
    ics=invitee.get('/api/calendar.ics',params={'code':inv['code']})
    assert ics.status_code==200 and 'BEGIN:VCALENDAR' in ics.text and 'DTSTART:' in ics.text
    assert other.get('/api/admin/state').status_code==401
    assert other.post('/api/admin/invites',json={'name':'Hacker'},headers={'origin':'http://testserver'}).status_code==403
    assert admin.post('/api/admin/invites',json={'name':'Hacker'},headers={'x-datewithme-request':'1','origin':'https://wrong-domain.tld'}).status_code==403


def test_admin_password_rotation(users):
    admin,invitee,guest,other,root=users
    rotated='new-password-for-tests-2026!'
    assert admin.post('/api/admin/password',headers=HEAD,json={'current':ADMIN_PWD,'new':rotated}).status_code==200
    assert admin.get('/api/admin/state').status_code==401
    assert admin.post('/api/admin/login',headers=HEAD,json={'password':rotated}).status_code==200


def test_photo_private_until_enabled_and_metadata_removed(users):
    import base64
    from io import BytesIO
    from PIL import Image
    admin,invitee,guest,other,root=users
    image=Image.new('RGB',(900,1200),(120,40,90))
    raw=BytesIO();image.save(raw,format='JPEG')
    b64=base64.b64encode(raw.getvalue()).decode()
    assert admin.post('/api/admin/photo',headers=HEAD,json={'data':b64}).status_code==200
    assert guest.get('/api/photo').status_code==404
    assert admin.get('/api/admin/photo').status_code==200
    r=admin.post('/api/admin/settings',headers=HEAD,json={'duration':120,'slot_step':30,'open_hour':16,'close_hour':23,'photo_enabled':True})
    assert r.status_code==200
    photo=guest.get('/api/photo'); assert photo.status_code==200
    processed=Image.open(BytesIO(photo.content))
    assert processed.size==(700,700) and processed.format=='WEBP'
    assert not processed.getexif()


def test_database_wrong_key_refused(users):
    from app.store import EncryptedStore
    admin,invitee,guest,other,root=users
    encrypted=root/'database.enc'
    assert encrypted.exists()
    with pytest.raises(RuntimeError,match='Unable to decrypt'):
        EncryptedStore(path=encrypted,key=secrets.token_bytes(32))
