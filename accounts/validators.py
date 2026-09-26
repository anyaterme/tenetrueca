import re

from django.core.exceptions import ValidationError


NIF_LETTERS = 'TRWAGMYFPDXBNJZSQVHLCKE'
NIF_NIE_PATTERN = re.compile(r'^(?:\d{8}|[XYZ]\d{7})[A-Z]$')


def normalize_nif_nie(value):
    return re.sub(r'[\s-]+', '', (value or '').strip().upper())


def validate_nif_nie(value):
    normalized = normalize_nif_nie(value)
    if not normalized:
        return
    if not NIF_NIE_PATTERN.fullmatch(normalized):
        raise ValidationError('Introduce un DNI o NIE válido.')

    numeric_part = normalized[:-1].translate(str.maketrans({'X': '0', 'Y': '1', 'Z': '2'}))
    expected_letter = NIF_LETTERS[int(numeric_part) % 23]
    if normalized[-1] != expected_letter:
        raise ValidationError('Introduce un DNI o NIE válido.')
