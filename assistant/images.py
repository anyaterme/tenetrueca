"""Bounded, in-memory derivatives exclusively for AI requests; originals are read-only."""
import warnings
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

from assistant.errors import ResponseFailure
from publications.images import ALLOWED_IMAGE_FORMATS


class ImagePreparationError(ResponseFailure):
    def __init__(self, reason):
        super().__init__('image_preparation_failed', stage='images', reason=reason)


@dataclass(frozen=True)
class ImageOptions:
    max_dimension: int
    quality: int
    detail: str
    max_count: int


def image_options(*, max_side=None, detail=None):
    """Validate lazily so invalid AI configuration cannot prevent manual publication review."""
    try:
        dimension = int(settings.AI_IMAGE_MAX_DIMENSION)
        quality = int(settings.AI_IMAGE_JPEG_QUALITY)
        count = int(settings.AI_IMAGE_MAX_COUNT)
        selected_detail = settings.AI_IMAGE_DETAIL if detail is None else detail
        if (not 128 <= dimension <= 4096 or not 1 <= quality <= 95
                or not 1 <= count <= settings.PUBLICATION_PHOTO_MAX_COUNT
                or settings.AI_IMAGE_FORMAT != 'JPEG'
                or settings.AI_IMAGE_REMOVE_EXIF is not True
                or selected_detail not in ('low', 'high', 'auto', 'original')):
            raise ValueError
        if max_side is not None:
            override = int(max_side)
            if not 128 <= override <= 4096:
                raise ValueError
            dimension = min(dimension, override)
    except (TypeError, ValueError, OverflowError):
        raise ImagePreparationError('configuration') from None
    return ImageOptions(dimension, quality, selected_detail, count)


def evaluation_photos(publication, *, manual=False):
    options = image_options()
    count = 1 if manual else options.max_count
    return list(publication.photos.order_by('-is_primary', 'sort_order', 'pk')[:count])


def optimize_image(photo, *, options=None):
    """Return a JPEG BytesIO; never write to storage or fall back to the original."""
    options = options or image_options()
    try:
        # Both compressed bytes and decoded pixels are bounded before allocation/decoding.
        with photo.image.open('rb') as original:
            payload = original.read(settings.PUBLICATION_PHOTO_MAX_BYTES + 1)
        if not payload or len(payload) > settings.PUBLICATION_PHOTO_MAX_BYTES:
            raise ImagePreparationError('file_size')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as source:
                if source.format not in ALLOWED_IMAGE_FORMATS:
                    raise ImagePreparationError('format')
                if getattr(source, 'n_frames', 1) != 1:
                    raise ImagePreparationError('animated')
                width, height = source.size
                if (width <= 0 or height <= 0
                        or width > settings.PUBLICATION_PHOTO_MAX_WIDTH
                        or height > settings.PUBLICATION_PHOTO_MAX_HEIGHT
                        or width * height > settings.PUBLICATION_PHOTO_MAX_PIXELS):
                    raise ImagePreparationError('dimensions')
                source.load()
                with ImageOps.exif_transpose(source) as oriented:
                    oriented.thumbnail((options.max_dimension, options.max_dimension),
                                       Image.Resampling.LANCZOS)
                    # RGBA also handles palette transparency and LA. Composite on white
                    # before discarding alpha, instead of turning transparent pixels black.
                    with oriented.convert('RGBA') as rgba:
                        with Image.new('RGB', rgba.size, 'white') as clean:
                            clean.paste(rgba, mask=rgba.getchannel('A'))
                            output = BytesIO()
                            # Fresh pixels plus no metadata save arguments drop EXIF/GPS,
                            # ICC profiles, PNG text, comments and orientation tags.
                            clean.save(output, format='JPEG', quality=options.quality, optimize=True)
                            output.seek(0)
                            return output
    except ImagePreparationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImagePreparationError('dimensions') from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, MemoryError):
        raise ImagePreparationError('unreadable') from None
