import warnings
from dataclasses import dataclass
from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_IMAGE_FORMATS = {
    'JPEG': ('jpg', 'image/jpeg'),
    'PNG': ('png', 'image/png'),
    'WEBP': ('webp', 'image/webp'),
}


@dataclass(frozen=True)
class NormalizedPublicationPhoto:
    content: ContentFile
    image_format: str
    width: int
    height: int
    file_size: int


def _read_limited(uploaded_file):
    max_bytes = settings.PUBLICATION_PHOTO_MAX_BYTES
    if getattr(uploaded_file, 'size', 0) > max_bytes:
        raise ValidationError(f'La fotografía no puede superar {max_bytes // (1024 * 1024)} MB.')

    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass
    payload = uploaded_file.read(max_bytes + 1)
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass
    if len(payload) > max_bytes:
        raise ValidationError(f'La fotografía no puede superar {max_bytes // (1024 * 1024)} MB.')
    if not payload:
        raise ValidationError('La fotografía está vacía.')
    return payload


def _clean_pixel_data(image):
    has_alpha = image.mode in {'RGBA', 'LA'} or (
        image.mode == 'P' and 'transparency' in image.info
    )
    target_mode = 'RGBA' if has_alpha else 'RGB'
    converted = image.convert(target_mode)
    clean_image = Image.new(target_mode, converted.size)
    clean_image.paste(converted)
    return clean_image


def normalize_publication_photo(uploaded_file):
    payload = _read_limited(uploaded_file)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as source:
                image_format = (source.format or '').upper()
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise ValidationError('Formato no permitido. Usa JPEG, PNG o WebP.')
                if getattr(source, 'is_animated', False) or getattr(source, 'n_frames', 1) != 1:
                    raise ValidationError('No se admiten imágenes animadas.')

                width, height = source.size
                if width <= 0 or height <= 0:
                    raise ValidationError('La fotografía no tiene dimensiones válidas.')
                if width > settings.PUBLICATION_PHOTO_MAX_WIDTH:
                    raise ValidationError('La fotografía supera el ancho máximo permitido.')
                if height > settings.PUBLICATION_PHOTO_MAX_HEIGHT:
                    raise ValidationError('La fotografía supera el alto máximo permitido.')
                if width * height > settings.PUBLICATION_PHOTO_MAX_PIXELS:
                    raise ValidationError('La fotografía supera el número máximo de píxeles permitido.')

                source.load()
                oriented = ImageOps.exif_transpose(source)
                clean_image = _clean_pixel_data(oriented)
    except ValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        MemoryError,
        SyntaxError,
        ValueError,
    ) as error:
        raise ValidationError('El archivo no contiene una imagen válida o está corrupto.') from error

    output = BytesIO()
    extension, _content_type = ALLOWED_IMAGE_FORMATS[image_format]
    save_options = {}
    if image_format == 'JPEG':
        save_options = {'quality': 88, 'optimize': True, 'progressive': True}
    elif image_format == 'PNG':
        save_options = {'optimize': True}
    elif image_format == 'WEBP':
        save_options = {'quality': 88, 'method': 4}
    clean_image.save(output, format=image_format, **save_options)
    normalized_payload = output.getvalue()
    if len(normalized_payload) > settings.PUBLICATION_PHOTO_MAX_BYTES:
        raise ValidationError('La fotografía procesada supera el tamaño máximo permitido.')

    normalized_width, normalized_height = clean_image.size
    random_name = f'{uuid4().hex}.{extension}'
    return NormalizedPublicationPhoto(
        content=ContentFile(normalized_payload, name=random_name),
        image_format=image_format,
        width=normalized_width,
        height=normalized_height,
        file_size=len(normalized_payload),
    )
