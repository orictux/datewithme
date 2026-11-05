"""Dynamic browser titles and social metadata regression tests."""
from test_app import users
from test_public_setup import fresh_instance, configuration, HEAD, PASSWORD
from html import unescape
import re


def test_public_page_metadata_uses_configured_owner(fresh_instance):
    client, root = fresh_instance
    token = (root/'secrets/setup.token').read_text().strip()
    assert client.post('/api/setup/complete', headers=HEAD,
        json=configuration(token, owner_name='Martin', public_base_url='')).status_code == 200
    for page in ('/', '/date?abc123', '/privacy'):
        res = client.get(page)
        assert res.status_code == 200
        assert '<title>DateWithMartin</title>' in res.text
        assert '<meta property="og:title" content="DateWithMartin"' in res.text
        assert '<meta name="twitter:title" content="DateWithMartin"' in res.text
        assert 'og-datewithme.png' in res.text
    panel=client.get('/panel')
    assert '<title>DateWithMartin | Admin</title>' in panel.text
    manifest=client.get('/manifest.webmanifest')
    assert manifest.json()['name']=='DateWithMartin'
    assert manifest.json()['short_name']=='DateWithMartin'
    assert 'no-store' in manifest.headers['cache-control']


def test_social_image_uses_explicit_public_domain_only(fresh_instance):
    client, root = fresh_instance
    token = (root/'secrets/setup.token').read_text().strip()
    assert client.post('/api/setup/complete', headers=HEAD, json=configuration(token,
        owner_name='Sam',public_base_url='https://date.example.com')).status_code == 200
    html=client.get('/',headers={'Host':'attacker.example'}).text
    assert 'content="https://date.example.com/og-datewithme.png"' in html
    assert 'content="https://date.example.com/"' in html
    assert 'attacker.example' not in html


def test_owner_name_escaped_in_document_metadata(fresh_instance):
    client, root = fresh_instance
    token=(root/'secrets/setup.token').read_text().strip()
    name='Alex & <img src=x onerror=alert(1)>'
    # A malicious-looking name is never embedded as raw executable HTML.
    assert client.post('/api/setup/complete',headers=HEAD,
        json=configuration(token,owner_name=name,public_base_url='')).status_code in (200,400)
    if client.get('/api/health').json()['setup_complete']:
        html=client.get('/').text
        assert '<title>DateWithAlex &amp; &lt;img' in html
        assert '<img src=x onerror=alert(1)>' not in html


def test_preview_image_is_neutral_source_asset():
    from pathlib import Path
    p=Path(__file__).resolve().parents[1]/'web'/'og-datewithme.png'
    assert p.exists() and p.stat().st_size > 1000
    script=(Path(__file__).resolve().parents[1]/'web'/'client.js').read_text()
    assert 'updateDocumentBranding();' in script
