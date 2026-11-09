"""The same Responses provider with a durable preflight; no unbudgeted SDK path here."""
from django.conf import settings
from openai import APIStatusError
from openai.types.responses.response_usage import ResponseUsage, InputTokensDetails, OutputTokensDetails

from chatbot.providers import OpenAIProvider, request_arguments
from chatbot.safety import ProviderError
from .access import require_evaluation_access
from .errors import EvaluationStopped
from .ledger import reserve, mark_sent, settle
from .pricing import digest, load_tariff
from .token_budget import input_bound


def supported_usage_schema():
    return (set(ResponseUsage.model_fields) == {'input_tokens', 'output_tokens', 'total_tokens', 'input_tokens_details', 'output_tokens_details'}
            and set(InputTokensDetails.model_fields) == {'cached_tokens', 'cache_write_tokens'}
            and set(OutputTokensDetails.model_fields) == {'reasoning_tokens'})


class BudgetedProvider(OpenAIProvider):
    def __init__(self, *, run_id, case_id, mode, tariff, max_input_tokens, max_output_tokens):
        self.run_id, self.case_id, self.mode = run_id, case_id, mode
        self.tariff = tariff
        self.max_input_tokens, self.max_output_tokens = max_input_tokens, max_output_tokens
        self.response = None
        self.failure_usage = None
        self.failure_model = None
        self.attempt = None
        self.payload_hash = None
        self.effective_mode = None

    def client_options(self):
        return {**super().client_options(), 'project': settings.CHATBOT_EVAL_PROJECT_REF}

    def respond(self, history, *, language, knowledge=None):
        require_evaluation_access(self.run_id)
        if not supported_usage_schema():
            raise EvaluationStopped('unsupported_usage_schema')
        current_tariff = load_tariff(settings.CHATBOT_EVAL_PRICING_FILE, self.tariff.model,
                                    max_age_days=settings.CHATBOT_EVAL_PRICE_MAX_AGE_DAYS)
        if current_tariff.signature != self.tariff.signature:
            raise EvaluationStopped('pricing_changed')
        if (knowledge is None or len(knowledge.fragments) > 5 or not knowledge.context
                or knowledge.generation_mode not in ('extractive', 'grounded')
                or any(f.approval != 'approved' for f in knowledge.fragments)
                or len(knowledge.context) > 6000 or any(len(f.content) > 1200 for f in knowledge.fragments)
                or self.tariff.input_ceiling > self.max_input_tokens
                or not 1 <= self.max_output_tokens <= min(settings.CHATBOT_EVAL_MAX_OUTPUT_TOKENS, 400)
                or not 0 < settings.CHATBOT_REQUEST_TIMEOUT <= 30
                or settings.CHATBOT_OPENAI_MODEL != self.tariff.model
                or settings.CHATBOT_MAX_OUTPUT_TOKENS != self.max_output_tokens
                or any(not isinstance(t.content, str) or len(t.content) > settings.CHATBOT_MAX_INPUT_CHARS for t in history)):
            raise EvaluationStopped('request_outside_limits')
        arguments = request_arguments(history, language=language, knowledge=knowledge)
        self.effective_mode = knowledge.generation_mode
        arguments.update(service_tier='default', truncation='disabled')
        self.bound = input_bound(arguments, max_input_tokens=self.max_input_tokens)
        self.payload_hash = self.bound.payload_hash
        self.attempt = reserve(self.run_id, self.case_id, self.mode, tariff=self.tariff,
            input_ceiling=self.bound.input_tokens, output_ceiling=self.max_output_tokens,
            payload_hash=self.payload_hash,
            source_hash=digest(sorted((f.source.source_id, f.content_hash, f.source.public_url) for f in knowledge.fragments)))
        mark_sent(self.attempt.pk)
        diagnostic = ''
        try:
            return super().respond(history, language=language, knowledge=knowledge)
        except ProviderError as error:
            diagnostic = error.code
            raise
        finally:
            # All ORM accounting is synchronous, after provider I/O and before return.
            response = self.response
            try:
                raw = response.usage.model_dump() if response is not None and response.usage is not None else self.failure_usage
            except (AttributeError, TypeError, ValueError):
                raw = None
            settle(self.attempt.pk, tariff=self.tariff, usage=raw,
                   model=response.model if response is not None else self.failure_model,
                   completed=response is not None and response.status == 'completed' and response.error is None,
                   diagnostic=diagnostic,
                   receipt={'status': response.status, 'response_id': response.id} if response is not None else {})

    async def _request(self, arguments):
        arguments = {**arguments, 'service_tier': 'default', 'truncation': 'disabled'}
        if digest(arguments) != self.payload_hash:
            raise EvaluationStopped('payload_changed')
        try:
            self.response = await super()._request(arguments)
            return self.response
        except APIStatusError as error:
            # Only usage fields can influence accounting; error text never enters logs/results.
            body = error.body if isinstance(error.body, dict) else {}
            self.failure_usage, self.failure_model = body.get('usage'), body.get('model')
            raise
