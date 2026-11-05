"""Regression tests for v1.2.4 guest-language controls and translation/caching."""
import re
from test_app import HEAD, users

DEFAULT_SETTINGS = {
    'duration': 120, 'slot_step': 30, 'open_hour': 16,
    'close_hour': 23, 'photo_enabled': False,
    'guest_deck_id': 1, 'guest_language': 'en',
    'admin_language': 'tr', 'visitor_notifications': False,
}


def test_guest_language_picker_toggle_and_default(users):
    admin, invited, guest, other, _ = users
    settings = dict(DEFAULT_SETTINGS, guest_language_picker=False)
    assert admin.post('/api/admin/settings', headers=HEAD, json=settings).status_code == 200
    initial = guest.get('/api/bootstrap').json()
    assert initial['mode'] == 'stranger'
    assert initial['language'] == 'en'
    assert initial['guest_language_picker'] is False
    assert admin.get('/api/admin/state').json()['settings']['guest_language_picker'] == '0'
    settings['guest_language_picker'] = True
    assert admin.post('/api/admin/settings', headers=HEAD, json=settings).status_code == 200
    assert guest.get('/api/bootstrap').json()['guest_language_picker'] is True


def test_guest_selection_changes_server_session_and_blocks_other_roles(users):
    admin, invited, guest, other, _ = users
    assert other.post('/api/guest-language', headers=HEAD, json={'language': 'ru'}).status_code == 401
    assert guest.post('/api/guest', headers=HEAD, json={}).status_code == 200
    assert guest.post('/api/guest-language', headers=HEAD, json={'language': 'xx'}).status_code == 400
    assert guest.post('/api/guest-language', headers=HEAD, json={'language': 'en'}).status_code == 200
    payload = guest.get('/api/bootstrap').json()
    assert payload['mode'] == 'guest' and payload['language'] == 'en'
    assert payload['deck']['cards'][-1]['label'] == "Let's go with the flow"
    r = admin.post('/api/admin/invites', headers=HEAD, json={
        'name':'Tami','deck_id':1,'language':'uk','code_length':4})
    assert r.status_code == 200
    code = r.json()['code']
    invited.get('/api/bootstrap', params={'code': code})
    assert invited.post('/api/guest-language', headers=HEAD, json={'language': 'tr'}).status_code == 403
    assert invited.get('/api/bootstrap').json()['language'] == 'uk'


def test_default_surprise_translation_is_updated_without_changing_custom_cards(users):
    admin, _, _, _, _ = users
    decks = admin.get('/api/admin/state').json()['decks']
    default_card = next(c for c in decks[0]['cards'] if c['id'] == 'surprise')
    assert default_card['translations']['en'] == "Let's go with the flow"
    assert 'Surprise me' not in default_card['translations']['en']
    assert default_card['translations']['tr'] == 'Farketmez, akışına bırakalım'
    assert len(default_card['translations']) == 16


def test_calendar_labels_are_localized_and_static_assets_match(users):
    _, client, _, _, _ = users
    html = client.get('/').text
    js_url = re.search(r'<script defer src="(/client\.js(?:\?v=[\w.]+)?)"', html).group(1)
    css_url = re.search(r'<link rel="stylesheet" href="(/layout\.css(?:\?v=[\w.]+)?)"', html).group(1)
    js = client.get(js_url)
    css = client.get(css_url)
    assert js.status_code == css.status_code == 200
    assert 'Apple Calendar' in js.text and 'Outlook Calendar' in js.text
    assert '${esc(PU(\'appleCalendar\'))}' in js.text
    assert 'guest-language-select' in css.text
    assert 'guest_language_picker' in js.text
    assert js.headers['cache-control'].startswith('no-store')
