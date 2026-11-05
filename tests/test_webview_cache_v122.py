"""Regression checks for cache safety and personal-data-free release assets."""
from pathlib import Path
from test_app import users


def test_website_html_and_modules_never_use_stale_cache(users):
    client=users[0]
    for path in ['/', '/date?ABC4', '/panel', '/client.js', '/layout.css', '/recovery.js', '/setup.js', '/setup.css']:
        response=client.get(path)
        assert response.status_code==200, path
        assert response.headers['Cache-Control'].startswith('no-store'), path
    html=client.get('/').text
    assert '/client.js' in html and '/recovery.js' in html and '/layout.css' in html
    assert 'og-datewithme.png' in html


def test_recovery_reports_errors_without_leaking_original_query():
    source=(Path(__file__).resolve().parents[1]/'web'/'recovery.js').read_text(encoding='utf-8')
    assert 'DWM-' in source
    assert 'window.location.replace(next)' in source
