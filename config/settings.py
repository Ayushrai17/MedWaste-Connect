"""
Django settings for Medical Waste Collection and Segregation System.
"""

from pathlib import Path
from decimal import Decimal
from decouple import config, Csv
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Application Brand Settings
SITE_NAME = "MedWaste Connect"


def resolve_secret_key(debug_mode: bool, raw_secret_key: str) -> str:
    """Resolve SECRET_KEY from environment; fail safely in production if missing or placeholder."""
    cleaned_key = (raw_secret_key or "").strip()
    is_placeholder = (
        not cleaned_key
        or cleaned_key.startswith("django-insecure-")
        or cleaned_key.startswith("change-this-")
    )
    if not debug_mode:
        if is_placeholder or len(cleaned_key) < 32:
            raise ImproperlyConfigured(
                "SECRET_KEY environment variable must be set to a strong, unique secret (>= 32 chars) when DEBUG is False."
            )
        return cleaned_key

    # Local development fallback: use configured .env key when provided, else an ephemeral per-process key.
    return cleaned_key or get_random_secret_key()


# Security & Environment Configuration
DEBUG = config("DEBUG", default=True, cast=bool)
SECRET_KEY = resolve_secret_key(DEBUG, config("SECRET_KEY", default=""))
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost,testserver", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="http://127.0.0.1,http://localhost", cast=Csv())

# Production Security & SSL Hardening (Toggleable via environment variables)
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HTTP Strict Transport Security (HSTS)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False, cast=bool)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Custom Core and Domain Apps (11 Modular Applications)
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "hospitals.apps.HospitalsConfig",
    "waste.apps.WasteConfig",
    "collection.apps.CollectionConfig",
    "tracking.apps.TrackingConfig",
    "treatment.apps.TreatmentConfig",
    "facilities.apps.FacilitiesConfig",
    "fleet.apps.FleetConfig",
    "reports.apps.ReportsConfig",
    "notifications.apps.NotificationsConfig",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_settings",
                "notifications.context_processors.user_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Authentication Redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard_redirect"
LOGOUT_REDIRECT_URL = "home"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Biomedical Waste Application Operational Thresholds
# (Based on Schedule I of BMW Rules 2016, tare scale sensitivity, and manual lifting safety SOPs)
MIN_WASTE_BAG_WEIGHT_KG = Decimal(config("MIN_WASTE_BAG_WEIGHT_KG", default="0.05"))
MAX_WASTE_BAG_WEIGHT_KG = Decimal(config("MAX_WASTE_BAG_WEIGHT_KG", default="25.00"))

