"""Security, privacy and regression checks for v1.4.0."""
import re
from test_app import HEAD, invite, book, users


def test_preview_is_an_admin_only_client_simulator(users):
    admin,invitee,guest,other,_=users
    from app.main import ROOT
    html=invitee.get('/').text
    bundle=re.search(r'/client\.js',html)
    if not bundle: bundle=re.search(r'/client\.js',html)
    assert bundle
    js=invitee.get(bundle.group()).text
    assert 'openInvitePreview' in js
    assert 'TEST MODE' in js
    assert 'No records, notifications or real invitations are created.' in js
    assert 'previewState' in js
    before=admin.get('/api/admin/state').json()
    # Rendering all static assets does not create an invitation or visit record.
    invitee.get(bundle.group())
    after=admin.get('/api/admin/state').json()
    assert before['invites']==after['invites'] and before['visits']==after['visits']


def test_admin_translations_for_all_16_languages_and_override(users):
    admin,invitee,guest,other,_=users
    from app.presets import LANGUAGES
    assert len(LANGUAGES)==16
    all_data=admin.get('/api/admin/translations').json()
    assert set(all_data)=={'visitor','admin'}
    for lang in LANGUAGES:
        assert lang in all_data['visitor'] and lang in all_data['admin']
    bad=invitee.post('/api/admin/translations',headers=HEAD,json={'language':'en','scope':'visitor','translations':{'hello':'hack'}})
    assert bad.status_code==401
    valid=admin.post('/api/admin/translations',headers=HEAD,json={'language':'uk','scope':'visitor','translations':{'hello':'Привіт, сонечко'}})
    assert valid.status_code==200
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Julia','language':'uk'}).json()
    payload=invitee.get('/api/bootstrap',params={'code':inv['code']}).json()
    assert payload['ui_translations']['hello']=='Привіт, сонечко'
    assert admin.post('/api/admin/translations',headers=HEAD,json={'language':'uk','scope':'visitor','translations':{'unknownKey':'hi'}}).status_code==400
    assert admin.post('/api/admin/translations',headers=HEAD,json={'language':'en','scope':'admin','translations':{'Davetler':'Invites'}}).status_code==200
    assert admin.get('/api/admin/state').json()['ui_overrides']['admin']['en']['Davetler']=='Invites'


def test_decline_disabled_by_default_then_rejects_only_current_invite(users):
    admin,invitee,guest,other,_=users
    first=invite(admin)
    invitee.get('/api/bootstrap',params={'code':first['code']})
    assert invitee.post('/api/invites/decline',headers=HEAD,json={}).status_code==403
    settings=admin.get('/api/admin/state').json()['settings']
    assert settings['no_click_cancels']=='0'
    body={k:int(settings[k]) for k in ('duration','slot_step','open_hour','close_hour','guest_deck_id')}
    body.update({'guest_language':'tr','admin_language':'tr','no_click_cancels':True})
    response=admin.post('/api/admin/settings',headers=HEAD,json=body)
    assert response.status_code==200,response.text
    assert invitee.get('/api/bootstrap').json()['no_click_cancels'] is True
    assert invitee.post('/api/invites/decline',headers=HEAD,json={}).status_code==200
    assert invitee.get('/api/bootstrap',params={'code':first['code']}).json()['mode']=='revoked'
    assert guest.post('/api/invites/decline',headers=HEAD,json={}).status_code in (401,404)
    assert admin.get('/api/admin/state').json()['notifications'][0]['kind']=='declined'


def test_after_date_notification_for_each_actual_page_load_and_private_media(users):
    admin,invitee,guest,other,_=users
    import base64,io
    from PIL import Image
    from app import main
    image=Image.new('RGB',(30,30),'violet')
    buf=io.BytesIO();image.save(buf,format='PNG')
    data='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
    mid=admin.post('/api/admin/surprise-media',headers=HEAD,json={'data':data}).json()['id']
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Taylor','surprises':[
        {'stage':'after_date','body':'Harika bir anıydı! 💕','mode':'continue','media_id':mid}]}).json()
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    assert invitee.get(f'/api/surprise-media/{mid}').status_code==404
    assert invitee.get('/api/surprises/next',params={'stage':'after_date'}).json()['surprise'] is None
    assert invitee.post('/api/after-date/view',headers=HEAD,json={'page_load_id':'enough-long-unique-01'}).status_code==403
    book(invitee)
    with main.store.write() as db:
        db.execute('UPDATE plans SET start_utc=?,end_utc=? WHERE invite_id=?',(main.now_epoch()-10000,main.now_epoch()-4000,inv['id']))
    assert invitee.get('/api/bootstrap').json()['plan']['past'] is True
    for page_id in ('distinct-view-000001','distinct-view-000001','distinct-view-000002'):
        response=invitee.post('/api/after-date/view',headers=HEAD,json={'page_load_id':page_id})
        assert response.status_code==200,response.text
        assert response.json()['notes'][0]['body'].startswith('Harika')
    s=admin.get('/api/admin/state').json()
    assert len(s['after_date_views'])==2
    assert len([n for n in s['notifications'] if n['kind']=='after_date_viewed'])==2
    assert invitee.get(f'/api/surprise-media/{mid}').status_code==200
    assert other.get(f'/api/surprise-media/{mid}').status_code==404
    assert guest.post('/api/after-date/view',headers=HEAD,json={'page_load_id':'unrelated-view-0001'}).status_code==401
    # Subsequent visits still display the note even if a continue button was used.
    assert invitee.post('/api/after-date/view',headers=HEAD,json={'page_load_id':'distinct-view-000003'}).json()['notes']
    assert len(admin.get('/api/admin/state').json()['after_date_views'])==3

def test_timeline_opt_in_reconstructs_only_real_events(users):
    admin,invitee,guest,other,_=users
    inv=invite(admin)
    before=admin.get('/api/admin/state').json()
    assert before['settings']['timeline_enabled']=='0'
    assert before['timeline']=={}
    settings=before['settings']
    body={k:int(settings[k]) for k in ('duration','slot_step','open_hour','close_hour','guest_deck_id')}
    body.update({'guest_language':'tr','admin_language':'tr','timeline_enabled':True})
    assert admin.post('/api/admin/settings',headers=HEAD,json=body).status_code==200
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    after=admin.get('/api/admin/state').json()
    events=after['timeline'][str(inv['id'])]
    assert [x['kind'] for x in events]==['created','opened']
    assert all(e['when'] for e in events)
    # Reading preview assets never creates a timeline event.
    invitee.get('/')
    assert admin.get('/api/admin/state').json()['timeline'][str(inv['id'])]==events


def test_postdate_media_response_requires_past_plan_even_with_known_id(users):
    admin,invitee,guest,other,_=users
    import io,base64
    from PIL import Image
    pic=Image.new('RGB',(20,20),'red');buf=io.BytesIO();pic.save(buf,format='PNG')
    img=admin.post('/api/admin/surprise-media',headers=HEAD,json={'data':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}).json()
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Tami','surprises':[{'stage':'after_date','body':'Sevgiler','media_id':img['id']}]}).json()
    invitee.get('/api/bootstrap',params={'code':inv['code']})
    assert invitee.get(f'/api/surprise-media/{img["id"]}').status_code==404
    assert invitee.post('/api/surprises/999/respond',headers=HEAD,json={}).status_code==404
    state=admin.get('/api/admin/state').json()
    sid=next(r['id'] for r in state['surprises'] if r['invite_id']==inv['id'])
    assert invitee.post(f'/api/surprises/{sid}/respond',headers=HEAD,json={}).status_code==403
