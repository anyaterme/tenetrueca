from enum import Enum
from typing import Literal
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


class Suggestion(StrictModel):
    id: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')]
    field: Literal['title', 'description', 'category', 'photos']
    kind: Literal['replace', 'information', 'photo']
    proposed_text: Annotated[str, Field(min_length=1, max_length=4000)] | None
    category_id: int | None
    reason: Text

    @model_validator(mode='after')
    def applicable_value(self):
        if self.kind == 'replace':
            if self.field == 'category':
                if self.category_id is None or self.proposed_text is not None:
                    raise ValueError('La clasificación necesita un ID válido.')
            elif self.field not in ('title', 'description') or not self.proposed_text or self.category_id is not None:
                raise ValueError('La sustitución necesita texto y un campo editable.')
            if self.field == 'title' and len(self.proposed_text) > 180:
                raise ValueError('El título supera el límite.')
        elif (self.kind == 'information' and self.field != 'description'
              or self.kind == 'photo' and self.field != 'photos'
              or self.category_id is not None or self.proposed_text is not None):
            raise ValueError('Los datos pendientes debe aportarlos el usuario.')
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
    # Defaults preserve validation of historical results and offline fixtures.
    suggestions: Annotated[list[Suggestion], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_suggestions(self):
        ids = [item.id for item in self.suggestions]
        fields = [item.field for item in self.suggestions if item.kind == 'replace']
        if len(ids) != len(set(ids)) or len(fields) != len(set(fields)):
            raise ValueError('Las propuestas deben ser independientes y únicas.')
        return self


class UnknownAxis(Axis):
    minimum_cm: None
    maximum_cm: None


class MeasuredAxis(Axis):
    minimum_cm: Annotated[float, Field(gt=0, le=10000)]
    maximum_cm: Annotated[float, Field(gt=0, le=10000)]


class UnverifiableDimensions(Dimensions):
    method: Annotated[str, Field(pattern='^unverifiable$')]
    height: UnknownAxis
    width: UnknownAxis
    depth: UnknownAxis
    source: None


class VisualDimensions(Dimensions):
    method: Annotated[str, Field(pattern='^visual$')]
    height: MeasuredAxis | UnknownAxis
    width: MeasuredAxis | UnknownAxis
    depth: MeasuredAxis | UnknownAxis
    source: None


class DeclaredDimensions(Dimensions):
    method: Annotated[str, Field(pattern='^declared$')]
    height: MeasuredAxis | UnknownAxis
    width: MeasuredAxis | UnknownAxis
    depth: MeasuredAxis | UnknownAxis
    source: None


class ProviderEvaluationResult(EvaluationResult):
    # Nested anyOf makes the method/axes relationship part of the API contract.
    # Keep the historical model and its semantic validators as the local boundary.
    # Documented measurements require verified lookup, which is not integrated.
    dimensions: UnverifiableDimensions | VisualDimensions | DeclaredDimensions


class PhotoPrefill(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=180)] | None
    description: Annotated[str, Field(min_length=1, max_length=4000)] | None = Field(
        description='Texto breve y listo para publicar que leerán otros usuarios: describe solo '
                    'el objeto y los rasgos o daños visibles. Sin comentarios de IA/análisis, '
                    'incertidumbres ni instrucciones al ciudadano. Las dudas van en unverifiable '
                    'y checks; las acciones, en recommendations o suggestions. No inventes estado, '
                    'funcionamiento ni pertenencia a un conjunto. Usa null si no puedes redactar '
                    'una descripción defendible y explica el motivo en unverifiable.',
    )
    category_id: int | None


class PhotoPrefillResult(EvaluationResult):
    prefill: PhotoPrefill


class ProviderPhotoPrefillResult(ProviderEvaluationResult):
    prefill: PhotoPrefill


def response_json_schema(*, photo_first=False):
    # The SDK's strict conversion resolves $ref siblings introduced by Field descriptions.
    # Keep create + local validation so refusals/invalid output never discard usage.
    from openai.lib._pydantic import to_strict_json_schema

    return to_strict_json_schema(ProviderPhotoPrefillResult if photo_first else ProviderEvaluationResult)


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
