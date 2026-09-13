"""Django settings for the StayLit Apparel business manager.

All environment-specific configuration is read from environment variables (loaded
from a gitignored ``.env`` file in development). No credential is ever hard-coded.
"""

from decimal import Decimal
from pathlib import Path

import dj_database_url
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key-do-not-use-in-production")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Render provides the external hostname at runtime.
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS = [f"https://{RENDER_EXTERNAL_HOSTNAME}"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    # Local apps
    "apps.core",
    "apps.finance",
    "apps.catalog",
    "apps.sales",
    "apps.supplier",
    "apps.integrations",
    "apps.analytics",
    "apps.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.web.middleware.RequireLoginMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context.range_query",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# Postgres in production via DATABASE_URL. Local development falls back to
# SQLite so the app is runnable without installing a database server. The data
# model deliberately avoids Postgres-only features so both backends behave the
# same.

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
# Open locally (DEBUG) so pytest and day-to-day work stay unblocked.
# On Render, DJANGO_DEBUG=False turns this on unless overridden.
REQUIRE_LOGIN = env.bool("DJANGO_REQUIRE_LOGIN", default=not DEBUG)

# --------------------------------------------------------------------------
# Internationalisation — UK business, GBP, London time
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

BASE_CURRENCY = "GBP"

# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --------------------------------------------------------------------------
# External integrations — credentials come from the environment only
# --------------------------------------------------------------------------

SHOPIFY = {
    "STORE": env("SHOPIFY_STORE", default=""),
    "ACCESS_TOKEN": env("SHOPIFY_ACCESS_TOKEN", default=""),
    "CLIENT_ID": env("SHOPIFY_CLIENT_ID", default=""),
    "CLIENT_SECRET": env("SHOPIFY_CLIENT_SECRET", default=""),
    "API_VERSION": env("SHOPIFY_API_VERSION", default="2025-01"),
}

INKTHREADABLE = {
    "APP_ID": env("INKTHREADABLE_APP_ID", default=""),
    "SECRET_KEY": env("INKTHREADABLE_SECRET_KEY", default=""),
    "BASE_URL": env("INKTHREADABLE_BASE_URL", default="https://www.inkthreadable.co.uk/api"),
    # How the GET signing payload is built: query | path | url.
    # Leave as "query" (the documented default). test_connections will try the
    # others if this one is rejected.
    "AUTH_STYLE": env("INKTHREADABLE_AUTH_STYLE", default="query"),
}

# --------------------------------------------------------------------------
# Profit assumptions — used only where a real figure is unavailable
# --------------------------------------------------------------------------

# Every result that leans on these reports the fact, so an estimated number is
# never mistaken for a measured one. Keep them conservative: it is better for a
# projection to disappoint upwards.
PROFIT_ASSUMPTIONS = {
    # Card processing cost, applied to the order total when the payment provider
    # does not report the actual fee. Stripe's UK standard rate.
    "payment_fee_percent": Decimal(env("PAYMENT_FEE_PERCENT", default="1.5")),
    "payment_fee_fixed": Decimal(env("PAYMENT_FEE_FIXED", default="0.20")),
    # "exclude" treats tax as money held for HMRC rather than income. Switch to
    # "include" if the business is not VAT registered and gross figures are wanted.
    "tax_treatment": env("TAX_TREATMENT", default="exclude"),
}

# --------------------------------------------------------------------------
# Security — relaxed in DEBUG, hardened otherwise
# --------------------------------------------------------------------------

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------
# Logging — integration failures must be visible, never silently swallowed
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "apps": {"level": env("APP_LOG_LEVEL", default="INFO"), "handlers": ["console"], "propagate": False},
    },
}
