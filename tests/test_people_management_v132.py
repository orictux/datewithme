from test_app import HEAD, invite, book, users


def _person(admin):
    return admin.get('/api/admin/state').json()['people'][0]


def test_rename_person_updates_link_and_existing_plan(users):
    admin, visitor, _, _, _root = users
    inv=invite(admin,name='Test')
    pid=_person(admin)['id']
    visitor.get('/api/bootstrap',params={'code':inv['code']})
    book(visitor)
    resp=admin.patch(f'/api/admin/people/{pid}',headers=HEAD,json={'name':'Test Güncel'})
    assert resp.status_code==200,resp.text
    state=admin.get('/api/admin/state').json()
    assert state['people'][0]['name']=='Test Güncel'
    assert state['invites'][0]['person_name']=='Test Güncel'
    assert state['plans'][0]['person_name']=='Test Güncel'
    assert visitor.get('/api/bootstrap').json()['name']=='Test Güncel'
    assert admin.patch(f'/api/admin/people/{pid}',headers=HEAD,json={'name':'   '}).status_code==400


def test_archive_and_restore_person_keeps_history(users):
    admin, visitor, _, _, _root=users
    inv=invite(admin,name='Arşiv Test')
    pid=_person(admin)['id']
    assert admin.post(f'/api/admin/people/{pid}/archive',headers=HEAD).status_code==200
    state=admin.get('/api/admin/state').json()
    assert state['people'][0]['archived']==1
    assert state['invites'][0]['code']==inv['code']
    assert visitor.get('/api/bootstrap',params={'code':inv['code']}).json()['name']=='Arşiv Test'
    denied=admin.post('/api/admin/invites',headers=HEAD,json={'person_id':pid,'code_length':4})
    assert denied.status_code==409
    assert admin.post(f'/api/admin/people/{pid}/restore',headers=HEAD).status_code==200
    assert _person(admin)['archived']==0
    assert invite(admin,person_id=pid)['name']=='Arşiv Test'


def test_delete_person_needs_explicit_cascade_and_cleans_related_records(users):
    admin, visitor, _, _, _root=users
    inv=invite(admin,name='Test')
    pid=_person(admin)['id']
    visitor.get('/api/bootstrap',params={'code':inv['code']})
    plan=book(visitor)
    refused=admin.delete(f'/api/admin/people/{pid}',headers=HEAD)
    assert refused.status_code==409
    assert admin.get('/api/admin/state').json()['people']
    gone=admin.delete(f'/api/admin/people/{pid}?cascade=true',headers=HEAD)
    assert gone.status_code==200,gone.text
    assert gone.json()['deleted_invites']==1
    state=admin.get('/api/admin/state').json()
    assert not state['people'] and not state['invites'] and not state['plans']
    assert not [n for n in state['notifications'] if n['invite_id']==inv['id']]
    assert visitor.get('/api/bootstrap',params={'code':inv['code']}).status_code==404
    assert visitor.get('/api/bootstrap').json()['mode']=='stranger'


def test_delete_person_without_invite_and_forbidden_for_visitors(users):
    admin, visitor, _, _, _root=users
    inv=invite(admin,name='Geçici')
    pid=_person(admin)['id']
    assert visitor.patch(f'/api/admin/people/{pid}',headers=HEAD,json={'name':'Hacker'}).status_code==401
    assert visitor.delete(f'/api/admin/people/{pid}?cascade=true',headers=HEAD).status_code==401
    assert admin.delete(f'/api/admin/invites/{inv["id"]}',headers=HEAD).status_code==200
    assert admin.delete(f'/api/admin/people/{pid}',headers=HEAD).status_code==200
    assert admin.get('/api/admin/state').json()['people']==[]
