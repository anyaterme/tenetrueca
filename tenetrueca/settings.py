import os
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "unsafe-development-placeholder"
DEBUG = False

ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'backoffice',
    'core',
    'accounts',
    'content',
    'locations.apps.LocationsConfig',
    'catalog',
    'publications',
    'moderation',
    'inventory',
    'reservations',
    'points',
    'auctions',
    'notifications',
    'assistant',
    'analytics',
    'reports',
    'incidents',
    'audit',
    'operations',
    'configuration',
    'faq.apps.FaqConfig',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.ForcePasswordChangeMiddleware',
    'accounts.middleware.ManagerOperationalScopeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'tenetrueca.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.authentication_capabilities',
                'accounts.context_processors.staff_capabilities',
            ],
        },
    },
]

WSGI_APPLICATION = 'tenetrueca.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'es-es'

TIME_ZONE = 'Atlantic/Canary'

USE_I18N = True

USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
PRIVATE_MEDIA_ROOT = BASE_DIR / 'private_media'

GOOGLE_MAPS_API_KEY = ''
GOOGLE_MAPS_MAP_ID = ''

PUBLICATION_PHOTO_MAX_BYTES = 8 * 1024 * 1024
PUBLICATION_PHOTO_MAX_COUNT = 8
PUBLICATION_PHOTO_MAX_WIDTH = 6000
PUBLICATION_PHOTO_MAX_HEIGHT = 6000
PUBLICATION_PHOTO_MAX_PIXELS = 24_000_000
CATALOG_PAGE_SIZE = 12
PUBLICATION_PAGE_SIZE = 10
PUBLICATION_DAILY_LIMIT = 5
MODERATION_PAGE_SIZE = 20
RECEPTION_PAGE_SIZE = 20
RESERVATION_EXPIRATION_HOURS = 72

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'
AUTHENTICATION_BACKENDS = ['accounts.backends.TenetruecaAuthenticationBackend']
AUTH_PROVIDER = 'local'
AUTH_API_URL = ''
AUTH_API_TOKEN = ''
AUTH_API_TIMEOUT = 5
MAGIC_LOGIN_ENABLED = True
MAGIC_LOGIN_EXPIRATION_MINUTES = 30
MAGIC_LOGIN_COOLDOWN_SECONDS = 60
REGISTRATION_VERIFICATION_EXPIRATION_MINUTES = 60
REGISTRATION_VERIFICATION_COOLDOWN_SECONDS = 60
TURNSTILE_SITE_KEY = ''
TURNSTILE_SECRET_KEY = ''
TURNSTILE_TIMEOUT = 5
STAFF_INVITATION_EXPIRATION_HOURS = 48
EMAIL_USE_SSL = False
EMAIL_TIMEOUT = 10
EMAIL_SETTINGS_ENCRYPTION_KEY = SECRET_KEY
TERMS_CONSENT_VERSION = '1.0'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'profile'
LOGOUT_REDIRECT_URL = 'home'

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
}

# No network is needed for normal development or automated tests.
TEST_RUNNER = 'assistant.testing.NoOpenAITestRunner'
OPENAI_EVALUATION_BACKEND = os.getenv('OPENAI_EVALUATION_BACKEND', 'disabled')
OPENAI_LIVE_ENABLED = os.getenv('OPENAI_LIVE_ENABLED', '').lower() in ('1', 'true', 'yes')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', '')
OPENAI_REASONING_EFFORT = os.getenv('OPENAI_REASONING_EFFORT', 'none')
OPENAI_TIMEOUT_SECONDS = int(os.getenv('OPENAI_TIMEOUT_SECONDS', '45'))
AI_PREANALYSIS_TIMEOUT_SECONDS = int(os.getenv('AI_PREANALYSIS_TIMEOUT_SECONDS', '60'))
AI_PREPARATION_TTL_HOURS = int(os.getenv('AI_PREPARATION_TTL_HOURS', '24'))
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv('OPENAI_MAX_OUTPUT_TOKENS', '2400'))
# AI derivatives only; validate lazily when building an evaluation, not at web startup.
# Legacy environment name remains a fallback for existing deployments.
AI_IMAGE_MAX_DIMENSION = os.getenv('AI_IMAGE_MAX_DIMENSION', os.getenv('OPENAI_IMAGE_MAX_SIDE', '1024'))
AI_IMAGE_JPEG_QUALITY = os.getenv('AI_IMAGE_JPEG_QUALITY', '80')
AI_IMAGE_FORMAT = os.getenv('AI_IMAGE_FORMAT', 'JPEG').upper()
AI_IMAGE_DETAIL = os.getenv('AI_IMAGE_DETAIL', 'high').lower()
AI_IMAGE_REMOVE_EXIF = os.getenv('AI_IMAGE_REMOVE_EXIF', 'True').lower() in ('1', 'true', 'yes')
AI_IMAGE_MAX_COUNT = os.getenv('AI_IMAGE_MAX_COUNT', '3')
OPENAI_IMAGE_MAX_SIDE = AI_IMAGE_MAX_DIMENSION  # Deprecated alias; use AI_IMAGE_MAX_DIMENSION.
OPENAI_IMAGE_TOKEN_RESERVE = int(os.getenv('OPENAI_IMAGE_TOKEN_RESERVE', '8192'))
OPENAI_MAX_ATTEMPTS = int(os.getenv('OPENAI_MAX_ATTEMPTS', '2'))
OPENAI_RETRY_DELAY_SECONDS = int(os.getenv('OPENAI_RETRY_DELAY_SECONDS', '120'))
OPENAI_DAILY_REQUEST_LIMIT = int(os.getenv('OPENAI_DAILY_REQUEST_LIMIT', '20'))
OPENAI_DAILY_BUDGET_USD = Decimal(os.getenv('OPENAI_DAILY_BUDGET_USD', '1'))
# Optional cumulative cap, including known costs and unresolved reservations on every date.
_openai_total_budget = os.getenv('OPENAI_TOTAL_BUDGET_USD', '').strip()
OPENAI_TOTAL_BUDGET_USD = Decimal(_openai_total_budget) if _openai_total_budget else None
# No assumed tariffs. Real calls fail closed until rates for OPENAI_MODEL are configured.
OPENAI_INPUT_USD_PER_MILLION = Decimal(os.getenv('OPENAI_INPUT_USD_PER_MILLION', '0'))
OPENAI_OUTPUT_USD_PER_MILLION = Decimal(os.getenv('OPENAI_OUTPUT_USD_PER_MILLION', '0'))
OPENAI_REQUEST_RESERVE_USD = Decimal(os.getenv('OPENAI_REQUEST_RESERVE_USD', '0.10'))
OPENAI_MAX_INPUT_BYTES = int(os.getenv('OPENAI_MAX_INPUT_BYTES', '30000'))

try:
    from .local_settings import *
except ImportError:
    pass
