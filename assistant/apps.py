from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assistant'
    verbose_name = 'Asistente ECO'

    def ready(self):
        from assistant.safety import install_sdk_test_guard

        install_sdk_test_guard()
