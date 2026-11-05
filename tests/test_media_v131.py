"""v1.3.1 regression tests: media content, shape, privacy and upload security."""
import base64
import io
from pathlib import Path
from PIL import Image
from app.image_pipeline import process_media, process_profile_photo, MediaError, MAX_SOURCE_BYTES
from test_app import HEAD, invite, users

BINARY_HEADERS={**HEAD,'Content-Type':'application/octet-stream'}

def jpeg_with_gps():
    im=Image.new('RGB',(120,220),'purple')
    exif=Image.Exif()
    exif[0x010F]='Test camera'
    exif[0x0112]=6
    out=io.BytesIO(); im.save(out,'JPEG',exif=exif)
    return out.getvalue()


def test_portrait_jpeg_reencoded_with_no_private_metadata():
    result=process_media(jpeg_with_gps())
    assert result.mime=='image/webp' and result.frames==1
    assert result.width==220 and result.height==120  # EXIF orientation 6
    with Image.open(io.BytesIO(result.content)) as cleaned:
        assert cleaned.format=='WEBP'
        assert not cleaned.getexif()
        assert 'exif' not in cleaned.info and 'icc_profile' not in cleaned.info


def test_landscape_and_alpha_keep_aspect():
    buffer=io.BytesIO();Image.new('RGBA',(1200,420),(10,20,30,140)).save(buffer,format='PNG')
    result=process_media(buffer.getvalue())
    assert result.width==1200 and result.height==420
    assert Image.open(io.BytesIO(result.content)).mode=='RGBA'


def test_animation_survives_and_is_anonymized():
    out=io.BytesIO()
    Image.new('RGB',(72,48),'red').save(out,format='GIF',save_all=True,
        append_images=[Image.new('RGB',(72,48),'green')],duration=[100,120],loop=0,comment=b'metadata')
    result=process_media(out.getvalue())
    assert result.mime=='image/webp' and result.frames==2
    with Image.open(io.BytesIO(result.content)) as safe:
        assert safe.n_frames==2
        assert 'comment' not in safe.info and 'exif' not in safe.info


def test_avif_input_when_codec_is_available():
    src=io.BytesIO()
    try:Image.new('RGB',(78,95),'red').save(src,format='AVIF')
    except (OSError,ValueError):return # Older platforms without native AVIF
    result=process_media(src.getvalue())
    assert result.source_format=='AVIF' and result.mime=='image/webp'


def test_bad_and_oversized_inputs_rejected():
    for bad in (b'<svg><script>alert(1)</script></svg>',b'',b'not-an-image'):
        try:process_media(bad)
        except MediaError:pass
        else:raise AssertionError('Unrecognized file should fail')
    try:process_media(b'X'*(MAX_SOURCE_BYTES+1))
    except MediaError as exc:assert exc.status==413
    else:raise AssertionError('Source cap should be enforced')


def test_binary_upload_security_and_private_delivery(users):
    admin,person,guest,other,_=users
    photo=jpeg_with_gps()
    assert other.post('/api/admin/surprise-media/file',content=photo,headers=BINARY_HEADERS).status_code==401
    response=admin.post('/api/admin/surprise-media/file',content=photo,headers=BINARY_HEADERS)
    assert response.status_code==200,response.text
    doc=response.json()
    assert doc['mime']=='image/webp' and doc['width']==220 and doc['height']==120
    inv=admin.post('/api/admin/invites',headers=HEAD,json={'name':'Taylor','surprises':[{'stage':'after_yes','body':'Merhaba','media_id':doc['id']}]}).json()
    person.get('/api/bootstrap',params={'code':inv['code']})
    source=person.get(f'/api/surprise-media/{doc["id"]}')
    assert source.status_code==200 and source.headers['content-type']=='image/webp'
    assert not Image.open(io.BytesIO(source.content)).getexif()
    assert guest.get(f'/api/surprise-media/{doc["id"]}').status_code==404


def test_legacy_json_mime_mismatch_and_profile_photo(users):
    admin,_,_,_,_=users
    photo=jpeg_with_gps()
    payload={'data':'data:image/png;base64,'+base64.b64encode(photo).decode()}
    response=admin.post('/api/admin/surprise-media',json=payload,headers=HEAD)
    assert response.status_code==200,response.text
    assert response.json()['source_format']=='JPEG'
    profile=admin.post('/api/admin/photo/file',content=photo,headers=BINARY_HEADERS)
    assert profile.status_code==200,profile.text
    stored=admin.get('/api/admin/photo')
    with Image.open(io.BytesIO(stored.content)) as im:
        assert im.size==(700,700) and not im.getexif()


def test_mpo_decoder_result_is_not_rejected_by_extension_rules(monkeypatch):
    # Simulates the MPO type reported by Pillow (including iPhone .jpeg captures).
    from app import image_pipeline
    real_open=Image.open
    data=jpeg_with_gps()
    def recognized_as_mpo(*args,**kwargs):
        result=real_open(*args,**kwargs)
        result.format='MPO'
        return result
    with monkeypatch.context() as patch:
        patch.setattr(image_pipeline.Image,'open',recognized_as_mpo)
        result=process_media(data)
    assert result.source_format=='MPO' and result.mime=='image/webp'
    assert not Image.open(io.BytesIO(result.content)).getexif()
