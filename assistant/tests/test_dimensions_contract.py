import json
from copy import deepcopy
from unittest import TestCase

from pydantic import ValidationError

from assistant.fixtures import simulated_response
from assistant.schema import EvaluationResult, ProviderEvaluationResult, response_json_schema


class DimensionsContractTests(TestCase):
    def validate_wire(self, payload):
        return ProviderEvaluationResult.model_validate_json(json.dumps(payload))

    def test_unverifiable_wire_contract_requires_all_axes_to_be_null(self):
        payload = simulated_response()
        self.validate_wire(payload)
        for axis in ('height', 'width', 'depth'):
            invalid = deepcopy(payload)
            invalid['dimensions'][axis] = {'minimum_cm': 20.0, 'maximum_cm': 30.0}
            with self.subTest(axis=axis), self.assertRaises(ValidationError):
                self.validate_wire(invalid)

    def test_axis_pair_contract_allows_a_range_or_two_nulls(self):
        payload = simulated_response('visual')
        self.validate_wire(payload)
        payload['dimensions']['width'] = {'minimum_cm': None, 'maximum_cm': None}
        self.validate_wire(payload)
        for minimum, maximum in ((None, 20.0), (10.0, None), (20.0, 10.0), (20.0, 20.0)):
            payload['dimensions']['width'] = {'minimum_cm': minimum, 'maximum_cm': maximum}
            with self.subTest(minimum=minimum, maximum=maximum), self.assertRaises(ValidationError):
                self.validate_wire(payload)

    def test_explicit_declared_measurements_remain_compatible_with_local_validation(self):
        payload = simulated_response('suitable')
        wire = self.validate_wire(payload)
        result = EvaluationResult.model_validate_json(wire.model_dump_json())
        self.assertEqual(result.dimensions.method, 'declared')
        self.assertEqual(result.dimensions.height.minimum_cm, result.dimensions.height.maximum_cm)

    def test_provider_cannot_offer_unverified_documentation_or_a_source(self):
        payload = simulated_response('suitable')
        for method in ('documented', 'visual', 'declared', 'unverifiable'):
            invalid = deepcopy(payload)
            invalid['dimensions']['method'] = method
            invalid['dimensions']['source'] = 'https://unverified.example/spec'
            with self.subTest(method=method), self.assertRaises(ValidationError):
                self.validate_wire(invalid)

    def test_outgoing_schema_enforces_method_and_axes_with_nested_anyof(self):
        schema = response_json_schema()
        branches = schema['properties']['dimensions']['anyOf']
        definitions = schema['$defs']
        self.assertEqual(len(branches), 3)
        self.assertNotIn('anyOf', schema)
        unknown = definitions['UnverifiableDimensions']['properties']
        self.assertEqual(unknown['method']['pattern'], '^unverifiable$')
        for axis in ('height', 'width', 'depth'):
            self.assertEqual(unknown[axis], {'$ref': '#/$defs/UnknownAxis'})
        null_axis = definitions['UnknownAxis']['properties']
        self.assertEqual(null_axis['minimum_cm']['type'], 'null')
        self.assertEqual(null_axis['maximum_cm']['type'], 'null')
        for name in ('VisualDimensions', 'DeclaredDimensions'):
            properties = definitions[name]['properties']
            self.assertEqual(properties['source']['type'], 'null')
            for axis in ('height', 'width', 'depth'):
                self.assertEqual(properties[axis]['anyOf'], [
                    {'$ref': '#/$defs/MeasuredAxis'}, {'$ref': '#/$defs/UnknownAxis'},
                ])
