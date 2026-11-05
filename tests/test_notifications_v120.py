"""v1.2 end-to-end behavior with fake push transport; no actual device push is claimed."""
from test_app import users, invite, book, HEAD
import json
import pytest

SUB={
  'endpoint':'https://fcm.googleapis.com/fcm/send/abcdef',
  'keys':{'p256dh':'A'*87,'auth':'B'*24},
}

def test_host_independent_date_route_preserves_query(users,monkeypatch):
    admin,invitee,*_=users
    monkeypatch.delenv('AUTO_HTTPS_REDIRECT',raising=False)
    r=invitee.get('http://192.0.2.42:8031/date?Ab9Z',follow_redirects=False)
    assert r.status_code==200
    assert 'DateWithMe' in r.text

def test_cloudflare_origin_http_not_redirect(users,monkeypatch):
    admin,invitee,*_=users
    monkeypatch.delenv('AUTO_HTTPS_REDIRECT',raising=False)
    r=invitee.get('http://example.net/api/health',follow_redirects=False)
    assert r.status_code==200 and r.json()['ok']

def test_invite_opened_only_once(users):
    admin,invitee,guest,other,*_=users
    iv=invite(admin)
    assert invitee.get('/api/bootstrap',params={'code':iv['code']}).status_code==200
    for _ in range(3):invitee.get('/api/bootstrap',params={'code':iv['code']})
    other.get('/api/bootstrap',params={'code':iv['code']})
    items=admin.get('/api/admin/notifications').json()['notifications']
    entries=[n for n in items if n['kind']=='invite_opened' and n['invite_id']==iv['id']]
    assert len(entries)==1
    assert entries[0]['read_at'] is None

def test_book_generates_notification(users):
    admin,invitee,*_=users
    iv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':iv['code']})
    p=book(invitee)
    notices=admin.get('/api/admin/notifications').json()
    assert notices['unread']==2
    assert notices['notifications'][0]['kind']=='planned'
    assert notices['notifications'][0]['plan_id']==p['id']

def test_mark_one_all_clear(users):
    admin,invitee,*_=users
    iv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':iv['code']})
    book(invitee)
    n=admin.get('/api/admin/notifications').json()['notifications']
    r=admin.post('/api/admin/notifications/read',json={'ids':[n[0]['id']]},headers=HEAD)
    assert r.status_code==200
    assert admin.get('/api/admin/notifications').json()['unread']==1
    r=admin.post('/api/admin/notifications/read',json={},headers=HEAD)
    assert r.status_code==200
    assert admin.get('/api/admin/notifications').json()['unread']==0
    r=admin.post('/api/admin/notifications/clear',json={},headers=HEAD)
    assert r.status_code==200
    assert admin.get('/api/admin/notifications').json()['notifications']==[]

def test_anonymous_cannot_read_notifications(users):
    admin,invitee,*_=users
    assert invitee.get('/api/admin/notifications').status_code==401
    assert invitee.post('/api/admin/notifications/read',headers=HEAD,json={}).status_code==401
    assert invitee.post('/api/admin/push/subscribe',headers=HEAD,json={'subscription':SUB}).status_code==401

def test_visitor_push_defaults_off(users):
    admin,invitee,guest,*_=users
    assert guest.get('/api/bootstrap').json()['visitor_notifications'] is False
    guest.post('/api/guest',headers=HEAD,json={})
    book(guest)
    assert guest.post('/api/visitor/push/subscribe',headers=HEAD,json={'subscription':SUB}).status_code==403

def test_visitor_push_requires_plan_even_if_enabled(users):
    admin,invitee,guest,*_=users
    st=admin.get('/api/admin/state').json()['settings']
    settings={
      'duration':int(st['duration']),'slot_step':int(st['slot_step']),'open_hour':int(st['open_hour']),
      'close_hour':int(st['close_hour']),'guest_deck_id':int(st['guest_deck_id']),
      'guest_language':st['guest_language'],'admin_language':st['admin_language'],
      'photo_enabled':False,'visitor_notifications':True
    }
    assert admin.post('/api/admin/settings',headers=HEAD,json=settings).status_code==200
    guest.post('/api/guest',headers=HEAD,json={})
    assert guest.get('/api/bootstrap').json()['visitor_notifications'] is True
    assert guest.post('/api/visitor/push/subscribe',headers=HEAD,json={'subscription':SUB}).status_code==403
    book(guest)
    assert guest.post('/api/visitor/push/subscribe',headers=HEAD,json={'subscription':SUB}).status_code==200

def test_endpoint_allowlist_avoids_ssrf(users):
    from app import main
    admin,invitee,*_=users
    assert not main.allowed_push_endpoint('https://localhost:8080/secret')
    assert not main.allowed_push_endpoint('http://fcm.googleapis.com/')
    assert not main.allowed_push_endpoint('https://fcm.googleapis.com.evil.com/')
    assert not main.allowed_push_endpoint('https://127.0.0.1/')
    assert main.allowed_push_endpoint(SUB['endpoint'])
    r=admin.post('/api/admin/push/subscribe',headers=HEAD,json={'subscription':{'endpoint':'https://localhost:8080/','keys':SUB['keys']}})
    assert r.status_code==400

def test_admin_opt_in_and_push_dispatch(users,monkeypatch):
    from app import main
    admin,invitee,*_=users
    sent=[]
    monkeypatch.setattr(main,'push_payload',lambda info,title,body,url:sent.append((info,title,body,url)))
    assert admin.post('/api/admin/push/subscribe',headers=HEAD,json={'subscription':SUB}).status_code==200
    iv=invite(admin)
    invitee.get('/api/bootstrap',params={'code':iv['code']})
    assert len(sent)==1 and sent[0][3]=='/panel'
    book(invitee)
    assert len(sent)==2
    assert admin.post('/api/admin/push/unsubscribe',headers=HEAD,json={}).status_code==200
    invite(admin,'Aleyna')
    assert len(sent)==2

def test_admin_password_rotation_removes_push_subscriptions(users):
    from app import main
    admin,invitee,*_=users
    assert admin.post('/api/admin/push/subscribe',headers=HEAD,json={'subscription':SUB}).status_code==200
    with main.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE audience='admin'").fetchone()[0]==1
    r=admin.post('/api/admin/password',headers=HEAD,json={'current':'a-strong-admin-test-password-2026','new':'a-different-very-strong-password-2026'})
    assert r.status_code==200
    with main.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE audience='admin'").fetchone()[0]==0

def test_push_public_key_uncompressed_p256(users):
    admin,*_=users
    key=admin.get('/api/push/public-key').json()['public_key']
    import base64
    raw=base64.urlsafe_b64decode(key+'==')
    assert len(raw)==65 and raw[0]==4

def test_service_worker_and_manifest_available(users):
    admin,*_=users
    sw=admin.get('/service-worker.js')
    assert sw.status_code==200 and 'showNotification' in sw.text
    mf=admin.get('/manifest.webmanifest')
    assert mf.status_code==200 and mf.json()['start_url']=='/panel'
    assert admin.get('/recovery.js').status_code==200
    assert admin.get('/icon-192.png').status_code==200
