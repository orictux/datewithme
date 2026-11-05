"""Regression: secure session cookies must not be weakened for LAN HTTP.

On public HTTPS a guest session persists; on LAN HTTP it cannot be sent.
The client checks persistence before showing the multi-step date flow.
"""
from fastapi.testclient import TestClient
from test_app import users


def test_guest_secure_cookie_only_on_https(users, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'COOKIE_SECURE', True)
    # This legacy test specifically exercises Secure-cookie refusal on LAN HTTP.
    # v1.2 redirects LAN HTTP by default, so temporarily opt out of that redirect.
    monkeypatch.setenv('AUTO_HTTPS_REDIRECT', '0')
    with TestClient(main.app, base_url='http://192.0.2.42:8031') as local:
        created = local.post('/api/guest', headers={'x-datewithme-request':'1'}, json={})
        assert created.status_code == 200
        assert 'secure' in created.headers['set-cookie'].lower()
        assert local.get('/api/bootstrap').json()['mode'] == 'stranger'
        progress = local.post('/api/progress', headers={'x-datewithme-request':'1'}, json={'stage':'activity'})
        assert progress.status_code == 401
    with TestClient(main.app, base_url='https://dates.example.com') as secure:
        created = secure.post('/api/guest', headers={'x-datewithme-request':'1'}, json={})
        assert created.status_code == 200
        assert secure.get('/api/bootstrap').json()['mode'] == 'guest'
        progress = secure.post('/api/progress', headers={'x-datewithme-request':'1'}, json={'stage':'activity'})
        assert progress.status_code == 200
