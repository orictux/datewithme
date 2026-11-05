"""Public installation and GitHub distribution regression tests."""
from pathlib import Path
from test_app import users
import os
import secrets
from fastapi.testclient import TestClient
import pytest

HEAD={'X-DateWithMe-Request':'1','Origin':'http://testserver'}
PASSWORD='Strong-private-password-2026!'

@pytest.fixture()
def fresh_instance(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'data'/'database.enc'))
    monkeypatch.setenv('DB_KEY_PATH',str(tmp_path/'secrets'/'database.key'))
    monkeypatch.setenv('SETUP_TOKEN_PATH',str(tmp_path/'secrets'/'setup.token'))
    monkeypatch.setenv('COOKIE_SECURE','0')
    monkeypatch.setenv('ALLOW_INSECURE_HTTP','1')
    monkeypatch.setenv('ALLOWED_ORIGINS','http://testserver')
    monkeypatch.setenv('PUBLIC_BASE_URL','http://testserver')
    monkeypatch.setenv('ENABLE_REMINDER_THREAD','0')
    from app import main
    main._rate.clear()
    with TestClient(main.app) as client:
        yield client,tmp_path


def configuration(token,**overrides):
    return {**{'token':token,'owner_name':'John','password':PASSWORD,
               'language':'en','timezone':'UTC','public_base_url':'http://testserver'},**overrides}


def test_fresh_wizard_gate_and_single_use_token(fresh_instance):
    c,root=fresh_instance
    key=root/'secrets/database.key'
    token=root/'secrets/setup.token'
    assert key.is_file() and token.is_file() and len(token.read_text().strip())>30
    if os.name=='posix':assert key.stat().st_mode&0o077==0
    assert c.get('/api/health').json()['setup_complete'] is False
    status=c.get('/api/setup/status').json()
    assert status['configured'] is False and status['default_language']=='en'
    assert len(status['languages'])==16 and 'tr' in status['languages']
    assert c.get('/api/public-config').status_code==503
    assert c.get('/api/admin/state').status_code==503
    assert c.get('/panel',follow_redirects=False).headers['location']=='/setup'
    assert c.get('/setup').status_code==200
    assert token.read_text().strip() not in c.get('/setup').text
    bad=c.post('/api/setup/complete',headers=HEAD,json=configuration('wrong-secret-token-with-enough-length'))
    assert bad.status_code==403
    assert c.get('/api/health').json()['setup_complete'] is False
    good=c.post('/api/setup/complete',headers=HEAD,json=configuration(token.read_text().strip()))
    assert good.status_code==200,good.text
    assert not token.exists()
    assert c.get('/api/health').json()['setup_complete'] is True
    assert c.get('/setup',follow_redirects=False).headers['location']=='/panel'
    assert c.post('/api/setup/complete',headers=HEAD,json=configuration('another-secret-token-with-enough-length')).status_code==409
    info=c.get('/api/public-config').json()
    assert info['owner_name']=='John' and info['default_language']=='en'
    assert c.post('/api/admin/login',headers=HEAD,json={'password':PASSWORD}).status_code==200
    state=c.get('/api/admin/state').json()
    assert state['settings']['runaway_no']=='1' and state['settings']['no_click_cancels']=='0'
    inv=c.post('/api/admin/invites',headers=HEAD,json={'name':'Taylor'})
    assert inv.status_code==200,inv.text
    assert len(inv.json()['code'])==16
    assert inv.json()['url'].startswith('http://testserver/')


def test_rejects_untrusted_origin_and_public_http(fresh_instance):
    c,root=fresh_instance
    token=(root/'secrets/setup.token').read_text().strip()
    fake=c.post('/api/setup/complete',headers={'X-DateWithMe-Request':'1','Origin':'https://attacker.example'},json=configuration(token))
    assert fake.status_code==403
    insecure=c.post('/api/setup/complete',headers=HEAD,json=configuration(token,public_base_url='http://evil.example'))
    assert insecure.status_code==400
    invalid_owner=c.post('/api/setup/complete',headers=HEAD,json=configuration(token,owner_name='<script>'))
    assert invalid_owner.status_code==400
    unsupported=c.post('/api/setup/complete',headers=HEAD,json=configuration(token,language='xx'))
    assert unsupported.status_code==400
    weak=c.post('/api/setup/complete',headers=HEAD,json=configuration(token,password='weak'))
    assert weak.status_code==422
    assert c.get('/api/health').json()['setup_complete'] is False


def test_database_cannot_be_reinitialized_without_key(tmp_path,monkeypatch):
    from app.installation import ensure_installation_files
    data=tmp_path/'data';data.mkdir();(data/'database.enc').write_bytes(b'encrypted backup data')
    monkeypatch.setenv('DB_PATH',str(data/'database.enc'))
    monkeypatch.setenv('DB_KEY_PATH',str(tmp_path/'secrets'/'database.key'))
    with pytest.raises(RuntimeError,match='encryption key is missing'):
        ensure_installation_files()


def test_distribution_has_required_source_files_and_no_private_branding():
    base=Path(__file__).resolve().parents[1]
    required=['LICENSE','README.md','Dockerfile','compose.yaml','SECURITY.md','CONTRIBUTING.md',
              '.env.example','.gitignore','docs/DEPLOYMENT.md','docs/NATIVE_LINUX.md',
              'web/setup.html','web/setup.js','web/client.js','web/layout.css']
    assert all((base/f).exists() for f in required)
    for file in list((base/'app').glob('*.py'))+list((base/'web').glob('*.html'))+list((base/'web').glob('*.js')):
        source=file.read_text().lower()
        assert '192.168.0.91' not in source
    assert 'MIT License' in (base/'LICENSE').read_text()
    assert 'runaway_no' in (base/'web/client.js').read_text()


def test_no_button_is_opt_out_without_affecting_decline(users):
    admin,guest,*_=users
    current=admin.get('/api/admin/state').json()['settings']
    assert current['runaway_no']=='1' and current['no_click_cancels']=='0'
    payload={'duration':int(current['duration']), 'slot_step':int(current['slot_step']),
             'open_hour':int(current['open_hour']), 'close_hour':int(current['close_hour']),
             'photo_enabled':False,'guest_deck_id':1,'guest_language':'en',
             'admin_language':'en','runaway_no':False,'no_click_cancels':False}
    assert admin.post('/api/admin/settings',headers=HEAD,json=payload).status_code==200
    assert guest.get('/api/bootstrap').json()['runaway_no'] is False
    payload['runaway_no']=True
    assert admin.post('/api/admin/settings',headers=HEAD,json=payload).status_code==200
    assert guest.get('/api/bootstrap').json()['runaway_no'] is True


def test_local_8031_setup_without_public_domain_and_later_configuration(fresh_instance,monkeypatch):
    """Installing on either loopback hostname works without a canonical domain."""
    c,root=fresh_instance
    monkeypatch.setenv('LOCAL_PORT','8031')
    key=(root/'secrets/setup.token').read_text().strip()
    local_header={'X-DateWithMe-Request':'1','Origin':'http://127.0.0.1:8031'}
    installed=c.post('/api/setup/complete',headers=local_header,json=configuration(key,public_base_url=''))
    assert installed.status_code==200,installed.text
    assert c.post('/api/admin/login',headers=local_header,json={'password':PASSWORD}).status_code==200
    assert c.get('/api/admin/state').json()['settings']['public_base_url']==''
    new=c.post('/api/admin/invites',headers=local_header,json={'name':'Taylor'})
    assert new.status_code==200,new.text
    assert new.json()['url'].startswith('http://127.0.0.1:8031/date?')
    other_local={'X-DateWithMe-Request':'1','Origin':'http://localhost:8031'}
    assert c.post('/api/admin/invites',headers=other_local,json={'name':'Avery'}).status_code==200
    rejected=c.post('/api/admin/invites',headers={'X-DateWithMe-Request':'1','Origin':'http://localhost:8080'},json={'name':'Wrong Port'})
    assert rejected.status_code==403
    attack=c.post('/api/admin/invites',headers={'X-DateWithMe-Request':'1','Origin':'https://attacker.example'},json={'name':'Attacker'})
    assert attack.status_code==403

    s=c.get('/api/admin/state').json()['settings']
    payload={'duration':int(s['duration']),'slot_step':int(s['slot_step']),
             'open_hour':int(s['open_hour']),'close_hour':int(s['close_hour']),
             'photo_enabled':False,'guest_deck_id':int(s['guest_deck_id']),
             'guest_language':s['guest_language'],'guest_language_picker':True,
             'admin_language':s['admin_language'],'visitor_notifications':False,
             'no_click_cancels':False,'runaway_no':True,
             'owner_name':s['owner_name'],'app_timezone':s['app_timezone'],
             'public_base_url':'https://dates.example.com','timeline_enabled':False}
    saved=c.post('/api/admin/settings',headers=other_local,json=payload)
    assert saved.status_code==200,saved.text
    assert c.get('/api/admin/state').json()['settings']['public_base_url']=='https://dates.example.com'
    invited=c.post('/api/admin/invites',headers={'X-DateWithMe-Request':'1','Origin':'https://dates.example.com'},json={'name':'Jordan'})
    assert invited.status_code==200,invited.text
    assert invited.json()['url'].startswith('https://dates.example.com/date?')
    payload['public_base_url']=''
    assert c.post('/api/admin/settings',headers=other_local,json=payload).status_code==200
    assert c.get('/api/admin/state').json()['settings']['public_base_url']==''
    invited=c.post('/api/admin/invites',headers=other_local,json={'name':'Robin'})
    assert invited.status_code==200 and invited.json()['url'].startswith('http://localhost:8031/date?')
    assert c.post('/api/admin/invites',headers={'X-DateWithMe-Request':'1','Origin':'https://dates.example.com'},json={'name':'Blocked'}).status_code==403


def test_secure_host_first_run_can_reach_settings_without_public_url(fresh_instance):
    """The install token permits secure same-host setup before domain authorization."""
    c,root=fresh_instance
    token=(root/'secrets/setup.token').read_text().strip()
    host_header={'X-DateWithMe-Request':'1','Origin':'https://date.example.com','Host':'date.example.com'}
    installed=c.post('/api/setup/complete',headers=host_header,json=configuration(token,public_base_url=''))
    assert installed.status_code==200,installed.text
    assert c.post('/api/admin/login',headers=host_header,json={'password':PASSWORD}).status_code==200
    assert c.get('/api/admin/state',headers={'Host':'date.example.com'}).status_code==200
    assert c.post('/api/admin/invites',headers=host_header,json={'name':'Riley'}).status_code==200
    assert c.post('/api/admin/invites',headers={'X-DateWithMe-Request':'1','Origin':'https://different.example','Host':'date.example.com'},json={'name':'Intruder'}).status_code==403


def test_distribution_setup_url_optional_and_port_8031():
    base=Path(__file__).resolve().parents[1]
    compose=(base/'compose.yaml').read_text()
    example=(base/'.env.example').read_text()
    setup=(base/'web/setup.html').read_text()
    assert 'PORT=8031' in example
    assert 'PORT:-8031}:8080' in compose
    assert 'LOCAL_PORT: ${PORT:-8031}' in compose
    assert 'id="public_base_url"' in setup
    assert 'type="url" placeholder="https://dates.example.com"' in setup
    assert 'e.g., Burak' in setup
