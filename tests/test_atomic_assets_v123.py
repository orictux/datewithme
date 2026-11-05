from pathlib import Path
import re
from test_app import users

ROOT=Path(__file__).resolve().parents[1]


def test_single_script_bundle_without_external_modules(users):
    client=users[0]
    response=client.get('/')
    assert response.status_code==200
    html=response.text
    assert '<script type="module"' not in html
    scripts=re.findall(r'<script[^>]+src="([^"]+)"',html)
    assert len(scripts)==2, scripts
    assert scripts[-1].startswith('/client.js')
    bundle=client.get(scripts[-1])
    assert bundle.status_code==200
    assert bundle.headers['Cache-Control'].startswith('no-store')
    assert 'import ' not in bundle.text[:150]
    assert 'const LOCALES' in bundle.text
    assert 'const OPTIONAL_PUSH_COPY' in bundle.text
    assert 'window.__dwmBootPhase' in bundle.text
    assert client.get('/setup').status_code in (200, 302) # setup stays locked after initialization


def test_fingerprinted_styles_and_recovery_are_served(users):
    client=users[0]
    html=client.get('/').text
    css=re.search(r'<link rel="stylesheet" href="([^"]+)"',html).group(1)
    scripts=re.findall(r'<script[^>]+src="([^"]+)"',html)
    assert scripts[0].startswith('/recovery.js')
    for url in [css, scripts[0]]:
        r=client.get(url)
        assert r.status_code==200 and r.headers['Cache-Control'].startswith('no-store')


def test_nonstandard_invite_url_remains_supported(users):
    client=users[0]
    html=client.get('/date?Ab4Z').text
    assert 'client.js' in html and 'og-datewithme.png' in html
