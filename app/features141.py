"""v1.4.1 : independent card snapshots, ordered decks, private avatar gallery,
pre-open invite editing. Every mutation requires the existing admin session + CSRF guard.
"""
from __future__ import annotations
import hashlib, json, secrets
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .image_pipeline import process_profile_photo, process_media, read_limited_body, MediaError
from .features130 import SurpriseConfig, add_assignment, validate_surprise

router=APIRouter()
def m():
    from . import main
    return main

def card_fingerprint(emoji, labels):
    return hashlib.sha256(json.dumps([emoji,labels],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def catalog_card(db, emoji, labels):
    fp=card_fingerprint(emoji,labels)
    found=db.execute('SELECT id FROM card_library WHERE fingerprint=?',(fp,)).fetchone()
    if found:return found['id']
    db.execute('INSERT INTO card_library(fingerprint,emoji,translations,created_at) VALUES(?,?,?,?)',
               (fp,emoji,json.dumps(labels,ensure_ascii=False),m().now()))
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]

def initialize_library(db):
    seeded=db.execute("SELECT 1 FROM config WHERE key='card_library_seeded'").fetchone()
    if not seeded:
        for card in db.execute('SELECT emoji,translations FROM deck_cards ORDER BY id').fetchall():
            try: labels=json.loads(card['translations'])
            except (ValueError, TypeError): continue
            catalog_card(db,card['emoji'],labels)
        db.execute("INSERT OR IGNORE INTO config(key,value) VALUES('card_library_seeded','1')")
    migrated=db.execute("SELECT 1 FROM config WHERE key='profile_gallery_migrated'").fetchone()
    if not migrated:
        old=db.execute('SELECT webp FROM photos WHERE id=1').fetchone()
        if old and not db.execute('SELECT id FROM profile_photos LIMIT 1').fetchone():
            db.execute('INSERT INTO profile_photos(name,webp,created_at) VALUES(?,?,?)',
                       ('Previous profile photo',old['webp'],m().now()))
        db.execute("INSERT OR IGNORE INTO config(key,value) VALUES('profile_gallery_migrated','1')")

def get_library(db):
    return [dict(id=r['id'],emoji=r['emoji'],translations=json.loads(r['translations']))
            for r in db.execute('SELECT * FROM card_library ORDER BY id DESC LIMIT 700')]

def get_gallery(db):
    return [dict(id=r['id'],name=r['name']) for r in db.execute('SELECT id,name FROM profile_photos ORDER BY id DESC')]

class Reorder(BaseModel):
    slugs:list[str]=Field(min_length=1,max_length=40)

@router.put('/api/admin/decks/{did}/reorder')
def reorder_deck(req:Request,did:int,data:Reorder):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:
        actual=[r['slug'] for r in db.execute('SELECT slug FROM deck_cards WHERE deck_id=?',(did,))]
        if len(data.slugs)!=len(actual) or set(data.slugs)!=set(actual) or len(set(data.slugs))!=len(data.slugs):
            raise HTTPException(409,'Card list has changed. Refresh the page and try again.')
        for n,slug in enumerate(data.slugs):
            db.execute('UPDATE deck_cards SET position=? WHERE deck_id=? AND slug=?',(n, did, slug))
    return {'ok':True}

class CopyCards(BaseModel):
    source_deck_id:int|None=None
    library_ids:list[int]=Field(default_factory=list,max_length=40)

@router.post('/api/admin/decks/{did}/import-cards')
def import_cards(req:Request,did:int,data:CopyCards):
    a=m();a.admin_guard(req,True)
    if not data.source_deck_id and not data.library_ids:raise HTTPException(400,'No source selected.')
    if data.source_deck_id==did:raise HTTPException(400,'A deck cannot be copied into itself.')
    with a.store.write() as db:
        if not db.execute('SELECT id FROM decks WHERE id=?',(did,)).fetchone():raise HTTPException(404,'Destination deck not found.')
        rows=[]
        if data.source_deck_id:
            rows+=db.execute('SELECT emoji,translations FROM deck_cards WHERE deck_id=? ORDER BY position,id',(data.source_deck_id,)).fetchall()
            if not db.execute('SELECT id FROM decks WHERE id=?',(data.source_deck_id,)).fetchone():raise HTTPException(404,'Source deck not found.')
        for cid in dict.fromkeys(data.library_ids):
            c=db.execute('SELECT emoji,translations FROM card_library WHERE id=?',(cid,)).fetchone()
            if not c:raise HTTPException(404,'Library card not found.')
            rows.append(c)
        count=db.execute('SELECT COUNT(*) FROM deck_cards WHERE deck_id=?',(did,)).fetchone()[0]
        if count+len(rows)>40:raise HTTPException(409,'Each deck can contain up to 40 cards.')
        pos=count
        for r in rows:
            # Distinct deck-local ID ensures two cards with the same source can coexist.
            slug='custom_'+secrets.token_hex(7)
            db.execute('INSERT INTO deck_cards(deck_id,slug,emoji,translations,position) VALUES(?,?,?,?,?)',
                       (did,slug,r['emoji'],r['translations'],pos))
            pos+=1
            catalog_card(db,r['emoji'],json.loads(r['translations']))
    return {'ok':True,'added':len(rows)}

@router.delete('/api/admin/card-library/{cid}')
def delete_catalog_card(req:Request,cid:int):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:db.execute('DELETE FROM card_library WHERE id=?',(cid,))
    return {'ok':True}  # Deck-local copies remain untouched.

class LibraryCardEdit(BaseModel):
    emoji:str=Field(min_length=1,max_length=16)
    translations:dict[str,str]

@router.put('/api/admin/card-library/{cid}')
def edit_catalog_card(req:Request,cid:int,data:LibraryCardEdit):
    a=m();a.admin_guard(req,True)
    labels=json.loads(a.validate_labels(data.translations))
    fp=card_fingerprint(data.emoji,labels)
    with a.store.write() as db:
        if db.execute('SELECT 1 FROM card_library WHERE fingerprint=? AND id!=?',(fp,cid)).fetchone():
            raise HTTPException(409,'This card already exists in the library.')
        if not db.execute('UPDATE card_library SET emoji=?,translations=?,fingerprint=? WHERE id=?',
                          (data.emoji,json.dumps(labels,ensure_ascii=False),fp,cid)).rowcount:
            raise HTTPException(404,'Card not found.')
    return {'ok':True}

@router.post('/api/admin/profile-photos/normalize')
async def normalize_for_crop(req:Request):
    a=m();a.admin_guard(req,True)
    if req.headers.get('content-type','').split(';')[0].strip().lower()!='application/octet-stream':
        raise HTTPException(415,'Choose the photo using the file selector.')
    try: media=process_media(await read_limited_body(req))
    except MediaError as e:raise HTTPException(e.status,str(e)) from e
    if media.frames>1:raise HTTPException(400,'Select a still frame for the profile photo.')
    return Response(media.content,media_type='image/webp',headers={'Cache-Control':'no-store'})

@router.post('/api/admin/profile-photos')
async def upload_profile_photo(req:Request):
    a=m();a.admin_guard(req,True)
    if req.headers.get('content-type','').split(';')[0].strip().lower()!='application/octet-stream':
        raise HTTPException(415,'Choose the photo using the file selector.')
    try: media=process_profile_photo(await read_limited_body(req))
    except MediaError as e:raise HTTPException(e.status,str(e)) from e
    with a.store.write() as db:
        if db.execute('SELECT COUNT(*) FROM profile_photos').fetchone()[0]>=30:
            raise HTTPException(409,'The gallery can hold up to 30 photos.')
        db.execute('INSERT INTO profile_photos(name,webp,created_at) VALUES(?,?,?)',
                   (f'Profile Photo {a.now()[:10]}',media.content,a.now()))
        return {'id':db.execute('SELECT last_insert_rowid()').fetchone()[0]}

@router.get('/api/admin/profile-photos/{pid}')
def admin_profile_photo(req:Request,pid:int):
    a=m();a.admin_guard(req)
    with a.store.read() as db:r=db.execute('SELECT webp FROM profile_photos WHERE id=?',(pid,)).fetchone()
    if not r:raise HTTPException(404)
    return Response(r['webp'],media_type='image/webp',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

class ProfileName(BaseModel):
    name:str=Field(min_length=1,max_length=80)

@router.patch('/api/admin/profile-photos/{pid}')
def rename_profile(req:Request,pid:int,data:ProfileName):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:
        if not db.execute('UPDATE profile_photos SET name=? WHERE id=?',(data.name.strip(),pid)).rowcount:raise HTTPException(404)
    return {'ok':True}

@router.delete('/api/admin/profile-photos/{pid}')
def delete_profile(req:Request,pid:int):
    a=m();a.admin_guard(req,True)
    with a.store.write() as db:
        if db.execute('SELECT 1 FROM invites WHERE photo_id=? LIMIT 1',(pid,)).fetchone():
            raise HTTPException(409,'This photo is in use by an invitation. Edit the linked invitations first.')
        db.execute('DELETE FROM profile_photos WHERE id=?',(pid,))
    return {'ok':True}

@router.get('/api/invite-photo/{pid}')
def private_invite_photo(req:Request,pid:int):
    a=m()
    with a.store.read() as db:
        if not a.admin_required(req):
            visit=a.visitor(db,req)
            if not visit or not visit['invite_id'] or not db.execute('SELECT 1 FROM invites WHERE id=? AND photo_id=?',(visit['invite_id'],pid)).fetchone():
                raise HTTPException(404)
        r=db.execute('SELECT webp FROM profile_photos WHERE id=?',(pid,)).fetchone()
    if not r:raise HTTPException(404)
    return Response(r['webp'],media_type='image/webp',headers={'Cache-Control':'private,no-store','X-Content-Type-Options':'nosniff'})

class EditInvite(BaseModel):
    person_id:int|None=None
    name:str=Field(default='',max_length=60)
    deck_id:int
    language:str
    photo_id:int|None=None
    surprises:list[SurpriseConfig]=Field(default_factory=list,max_length=12)

@router.patch('/api/admin/invites/{iid}')
def edit_invite(req:Request,iid:int,data:EditInvite):
    a=m();a.admin_guard(req,True)
    if data.language not in a.LANGUAGES:raise HTTPException(400,'Unknown language.')
    with a.store.write() as db:
        inv=db.execute('SELECT * FROM invites WHERE id=?',(iid,)).fetchone()
        if not inv:raise HTTPException(404,'Invitation not found.')
        # The first successful visit irreversibly freezes the invitation details.
        if inv['opened_at'] or db.execute('SELECT 1 FROM visits WHERE invite_id=? LIMIT 1',(iid,)).fetchone() or db.execute('SELECT 1 FROM plans WHERE invite_id=?',(iid,)).fetchone():
            raise HTTPException(409,'This invitation has already been opened and can no longer be edited.')
        if not db.execute('SELECT 1 FROM decks WHERE id=? AND archived=0',(data.deck_id,)).fetchone() or not db.execute('SELECT 1 FROM deck_cards WHERE deck_id=? LIMIT 1',(data.deck_id,)).fetchone():
            raise HTTPException(400,'Invalid or empty deck.')
        if data.photo_id is not None and not db.execute('SELECT id FROM profile_photos WHERE id=?',(data.photo_id,)).fetchone():
            raise HTTPException(400,'Profile photo not found.')
        # Validate all surprises before mutating invitation or deleting old assignments.
        prepared=[validate_surprise(s,db) for s in data.surprises]
        if data.person_id:
            p=db.execute('SELECT * FROM people WHERE id=? AND archived=0',(data.person_id,)).fetchone()
            if not p:raise HTTPException(404,'Person not found.')
            person_id=p['id']
        elif data.name.strip():
            db.execute('INSERT INTO people(name,created_at) VALUES(?,?)',(data.name.strip(),a.now()))
            person_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]
        else:
            person_id=inv['person_id']
        db.execute('UPDATE invites SET person_id=?,deck_id=?,language=?,photo_id=? WHERE id=?',
                   (person_id,data.deck_id,data.language,data.photo_id,iid))
        db.execute('DELETE FROM surprise_assignments WHERE invite_id=?',(iid,))
        for idx,(stage,body,label,mode,media,template) in enumerate(prepared):
            db.execute('INSERT INTO surprise_assignments(invite_id,stage,body,button_label,mode,media_id,template_id,position,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (iid,stage,body,label,mode,media,template,idx,a.now()))
    return {'ok':True,'code':inv['code']}
