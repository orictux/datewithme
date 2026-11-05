"""v1.3.1: bounded, metadata-free media processing for private invitations.

Accepted input is identified by image decoding, not file name or MIME type. All
newly uploaded media is re-encoded to WebP, never stored as the original bytes.
"""
from __future__ import annotations
import io
from dataclasses import dataclass
from PIL import Image, ImageOps, ImageCms, UnidentifiedImageError

MAX_SOURCE_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 75_000_000
MAX_STATIC_EDGE = 2560
MAX_ANIM_EDGE = 900
MAX_ANIM_FRAMES = 120
MAX_ANIM_TOTAL_PIXELS = 110_000_000
MAX_RETAINED_ANIM_PIXELS = 60_000_000
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
ACCEPTED = {'JPEG', 'MPO', 'PNG', 'GIF', 'WEBP', 'HEIF', 'HEIC', 'AVIF'}

# Pillow's decompression-bomb check remains enabled as a second line of defence.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
_heif_registered = False


class MediaError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Processed:
    content: bytes
    mime: str
    width: int
    height: int
    frames: int
    source_format: str


def register_heif() -> bool:
    """Optional in test hosts; mandatory in deployed requirements for HEIC/HEIF."""
    global _heif_registered
    if _heif_registered:
        return True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener(thumbnails=False)
        _heif_registered = True
        return True
    except ImportError:
        return False


def _preflight(source: bytes) -> None:
    if not source:
        raise MediaError('Boş dosya yüklenemez.')
    if len(source) > MAX_SOURCE_BYTES:
        raise MediaError('Dosya en fazla 24 MB olabilir. Daha küçük bir dosya seç.', 413)
    # SVG / HTML / XML are deliberately never parsed or served as images.


def _safe_rgb(im: Image.Image) -> Image.Image:
    """Apply EXIF orientation and ICC color transformation without retaining metadata."""
    im = ImageOps.exif_transpose(im)
    has_alpha = 'A' in im.getbands() or 'transparency' in im.info
    # ICC is not carried over; where practical convert embedded P3 to sRGB.
    icc = im.info.get('icc_profile')
    if icc and not has_alpha and isinstance(icc, bytes) and len(icc) < 1024 * 1024:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            target = ImageCms.createProfile('sRGB')
            im = ImageCms.profileToProfile(im.convert('RGB'), src, target, outputMode='RGB')
        except (OSError, ValueError, TypeError):
            im = im.convert('RGB')
    else:
        im = im.convert('RGBA' if has_alpha else 'RGB')
    # A brand-new pixel raster has no source EXIF, GPS, comments or XMP attached.
    return im


def _resize(im: Image.Image, edge: int) -> Image.Image:
    if max(im.size) > edge:
        im.thumbnail((edge, edge), Image.Resampling.LANCZOS, reducing_gap=2.0)
    return im


def _finish(payload: bytes, raster: Image.Image, frames: int, source_format: str) -> Processed:
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MediaError('Görselin dönüştürülmüş boyutu çok büyük. Daha kısa bir GIF dene.', 413)
    return Processed(payload, 'image/webp', raster.width, raster.height, frames, source_format)


def process_media(source: bytes) -> Processed:
    _preflight(source)
    heif_present = register_heif()
    try:
        with Image.open(io.BytesIO(source)) as im:
            fmt = (im.format or '').upper()
            if fmt not in ACCEPTED:
                raise MediaError('Desteklenmeyen içerik. JPG, PNG, WebP, GIF, HEIC, HEIF, MPO veya AVIF seç.')
            w, h = im.size
            if not w or not h or w*h > MAX_PIXELS:
                raise MediaError('Fotoğrafın çözünürlüğü güvenli işleme sınırını aşıyor.', 413)
            # MPO is intentionally one still image. Do not expose additional images.
            is_anim = fmt in ('GIF', 'WEBP') and getattr(im, 'n_frames', 1) > 1
            frames = int(getattr(im, 'n_frames', 1)) if is_anim else 1
            if is_anim:
                if frames > MAX_ANIM_FRAMES or w*h*frames > MAX_ANIM_TOTAL_PIXELS:
                    raise MediaError('Animasyon çok uzun veya büyük. Daha kısa bir GIF dene.', 413)
                scale = min(1.0, MAX_ANIM_EDGE/max(w,h))
                projected = round(w*scale)*round(h*scale)*frames
                if projected > MAX_RETAINED_ANIM_PIXELS:
                    raise MediaError('Animasyon çok büyük. Daha kısa veya küçük bir GIF dene.', 413)
                rasters: list[Image.Image] = []
                durations: list[int] = []
                for n in range(frames):
                    im.seek(n)
                    durations.append(min(10_000, max(20, int(im.info.get('duration') or 100))))
                    rasters.append(_resize(_safe_rgb(im.copy()), MAX_ANIM_EDGE))
                if not rasters:
                    raise MediaError('Animasyonun kareleri okunamadı.')
                out = io.BytesIO()
                # All frames are new pixel buffers; no source metadata survives.
                rasters[0].save(out, format='WEBP', save_all=True, append_images=rasters[1:],
                                duration=durations, loop=int(min(1000,max(0, im.info.get('loop',0)))),
                                quality=77, method=4)
                return _finish(out.getvalue(), rasters[0], frames, fmt)
            im.seek(0)
            # Reduce before copying/transposing pixels to avoid high memory peaks
            # from 24/48-megapixel phone photos in a 512 MiB container.
            if fmt in ('JPEG','MPO'):
                try: im.draft('RGB',(MAX_STATIC_EDGE*2,MAX_STATIC_EDGE*2))
                except (OSError,ValueError): pass
            im.thumbnail((MAX_STATIC_EDGE,MAX_STATIC_EDGE), Image.Resampling.LANCZOS, reducing_gap=2.0)
            raster = _safe_rgb(im)
            out = io.BytesIO()
            raster.save(out, format='WEBP', quality=85, method=5, exact=True)
            return _finish(out.getvalue(), raster, 1, fmt)
    except MediaError:
        raise
    except (Image.DecompressionBombError, MemoryError):
        raise MediaError('Görsel güvenli çözünürlük sınırını aşıyor.', 413) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError):
        if not heif_present and (b'ftypheic' in source[:40] or b'ftypheix' in source[:40] or b'ftypmif1' in source[:40]):
            raise MediaError('HEIC/HEIF çözücüsü kurulu değil. Uygulamayı yeniden başlat ve bağımlılık kurulumunu kontrol et.', 503) from None
        raise MediaError('Görsel açılamadı. Dosya bozuk veya desteklenmeyen bir codec kullanıyor olabilir.') from None


def process_profile_photo(source: bytes) -> Processed:
    """Avatar remains an intentionally square image; any input aspect is accepted."""
    _preflight(source)
    heif_present = register_heif()
    try:
        with Image.open(io.BytesIO(source)) as im:
            fmt = (im.format or '').upper()
            if fmt not in ACCEPTED:
                raise MediaError('Desteklenmeyen fotoğraf biçimi.')
            if im.width*im.height > MAX_PIXELS:
                raise MediaError('Fotoğrafın çözünürlüğü çok büyük.', 413)
            im.seek(0)
            raster = _safe_rgb(im.copy())
            raster = ImageOps.fit(raster, (700,700), method=Image.Resampling.LANCZOS)
            out = io.BytesIO()
            raster.save(out, format='WEBP', quality=86, method=5)
            return _finish(out.getvalue(), raster, 1, fmt)
    except MediaError:
        raise
    except (Image.DecompressionBombError, MemoryError):
        raise MediaError('Fotoğraf güvenli çözünürlük sınırını aşıyor.',413) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        if not heif_present and b'ftyp' in source[:12]:
            raise MediaError('HEIC/HEIF desteği için pillow-heif kurulumu gerekli.',503) from None
        raise MediaError('Fotoğraf okunamadı veya desteklenmeyen codec kullanıyor.') from None


async def read_limited_body(request, limit: int = MAX_SOURCE_BYTES) -> bytes:
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise MediaError('Dosya 24 MB sınırını aşıyor.',413)
        chunks.append(chunk)
    data = b''.join(chunks)
    _preflight(data)
    return data
