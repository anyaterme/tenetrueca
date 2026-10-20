from django.test.runner import DiscoverRunner

from assistant.safety import enable_test_mode


class NoOpenAITestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        enable_test_mode()
        return super().setup_test_environment(**kwargs)
