from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CheckStatus(str, Enum):
    CORRECT = 'CORRECTO'
    ISSUE = 'INCIDENCIA'
    UNVERIFIABLE = 'NO_VERIFICABLE'


Text = Annotated[str, Field(min_length=1, max_length=1500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Check(StrictModel):
    status: CheckStatus
    explanation: Text


class Checks(StrictModel):
    identification: Check
    photo_consistency: Check
    title: Check
    description: Check
    category: Check = Field(description='Pertinencia del objeto para classification.category '
                           '(categoría raíz); citar el nombre evaluado.')
    subcategory: Check = Field(description='Pertinencia del objeto para classification.subcategory '
                              'y su ruta; evaluar independientemente de la categoría raíz, '
                              'citando la subcategoría seleccionada. Explicar si no aplica.')
    admission: Check
    prohibited_items: Check
    photo_quality: Check
    dimensions: Check


class Axis(StrictModel):
    minimum_cm: Annotated[float, Field(gt=0, le=10000)] | None
    maximum_cm: Annotated[float, Field(gt=0, le=10000)] | None

    @model_validator(mode='after')
    def valid_range(self):
        if (self.minimum_cm is None) != (self.maximum_cm is None):
            raise ValueError('Se necesitan ambos extremos del intervalo.')
        if self.minimum_cm is not None and self.minimum_cm > self.maximum_cm:
            raise ValueError('Intervalo invertido.')
        return self


class Dimensions(StrictModel):
    method: Annotated[str, Field(pattern='^(documented|declared|visual|unverifiable)$')]
    height: Axis
    width: Axis
    depth: Axis
    evidence: Text
    limitations: Text
    source: Text | None
    physical_verification: bool

    @model_validator(mode='after')
    def method_matches_evidence(self):
        axes = [self.height, self.width, self.depth]
        if self.method == 'documented' and not self.source:
            raise ValueError('Las medidas documentadas requieren una fuente.')
        if self.method == 'unverifiable' and any(a.minimum_cm is not None for a in axes):
            raise ValueError('No se pueden inventar medidas no verificables.')
        if self.method == 'visual' and any(
            a.minimum_cm is not None and a.minimum_cm == a.maximum_cm for a in axes
        ):
            raise ValueError('Las estimaciones visuales requieren intervalos.')
        return self


class EvaluationResult(StrictModel):
    overall: Annotated[str, Field(pattern='^(apparently_suitable|issues|needs_verification)$')]
    identified_object: Text
    checks: Checks
    issues: list[Text]
    recommendations: list[Text]
    alternative_category_ids: list[int]
    dimensions: Dimensions
    unverifiable: list[Text]


def response_json_schema():
    # The SDK's strict conversion resolves $ref siblings introduced by Field descriptions.
    # Keep create + local validation so refusals/invalid output never discard usage.
    from openai.lib._pydantic import to_strict_json_schema

    return to_strict_json_schema(EvaluationResult)


CHECK_LABELS = {
    'identification': 'Identificación del objeto', 'photo_consistency': 'Coherencia de fotografías',
    'title': 'Nombre', 'description': 'Descripción', 'category': 'Categoría',
    'subcategory': 'Subcategoría', 'admission': 'Criterios de admisión',
    'prohibited_items': 'Artículos prohibidos', 'photo_quality': 'Calidad de fotografías',
    'dimensions': 'Dimensiones',
}
OVERALL_LABELS = {'apparently_suitable': 'Aparentemente cumple los criterios',
                  'issues': 'Se han detectado incidencias',
                  'needs_verification': 'Requiere comprobaciones adicionales'}
