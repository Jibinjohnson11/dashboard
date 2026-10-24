"""
Django settings for the SEO Management Dashboard.

All secrets and environment-specific values come from environment variables
(optionally loaded from a local .env file). Nothing sensitive is hardcoded.
"""
import os
import sys
from pathlib import Path

import certifi
import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
ENVIRONMENT = env("DJANGO_ENV", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"
DEBUG = env_bool("DJANGO_DEBUG", default=not IS_PRODUCTION) and not IS_PRODUCTION

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")
    # Development/test only: an ephemeral key (sessions reset on restart).
    from django.core.management.utils import get_random_secret_key

    SECRET_KEY = get_random_secret_key()

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
if TESTING:
    ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")
# Render sets this to the service's public hostname (e.g. dashboard-xxxx.onrender.com).
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME and not TESTING:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "dashboard",
    "brands",
    "campaigns",
    "teams",
    "content",
    "approvals",
    "drafts",
    "publications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Deny by default: every view requires login unless marked @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "core.middleware.PrincipalMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
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
                "core.context_processors.navigation",
            ],
            "builtins": ["core.templatetags.ui"],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Relational database: Django auth, sessions, permissions, migrations ---
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=env_int("DATABASE_CONN_MAX_AGE", 60),
        # Serverless Postgres (Neon) suspends idle computes; re-check a kept connection before reusing it.
        conn_health_checks=env_bool("DATABASE_CONN_HEALTH_CHECKS", True),
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    _db_options = DATABASES["default"].setdefault("OPTIONS", {})
    # sslmode=verify-ca/verify-full needs a CA bundle; use certifi's unless the URL names one.
    if _db_options.get("sslmode") in {"verify-ca", "verify-full"}:
        _db_options.setdefault("sslrootcert", certifi.where())
    # Transaction-mode poolers (e.g. Neon's "-pooler" endpoint) don't support server-side cursors.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = env_bool(
        "DATABASE_DISABLE_SERVER_SIDE_CURSORS", "-pooler" in (DATABASES["default"].get("HOST") or "")
    )

# --- MongoDB: SEO business data ---
MONGODB_URI = env("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB_NAME = env("MONGODB_DB_NAME", "seo_dashboard")
MONGODB_USE_MOCK = env_bool("MONGODB_USE_MOCK", False)
MONGODB_TIMEOUT_MS = env_int("MONGODB_TIMEOUT_MS", 5000)
if TESTING:
    # Tests always use an isolated in-memory database, never a real server.
    MONGODB_USE_MOCK = True
    MONGODB_DB_NAME = "seo_dashboard_test"
if IS_PRODUCTION and MONGODB_USE_MOCK:
    raise ImproperlyConfigured("MONGODB_USE_MOCK cannot be enabled in production.")

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "accounts.validators.CharacterClassValidator"},
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

REGISTRATION_MODE = env("REGISTRATION_MODE", "open_pending")
if REGISTRATION_MODE not in {"open_pending", "invite_only"}:
    raise ImproperlyConfigured("REGISTRATION_MODE must be 'open_pending' or 'invite_only'.")

# --- Sessions ---
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE_SECONDS", 8 * 3600)
REMEMBER_ME_AGE = env_int("REMEMBER_ME_AGE_SECONDS", 14 * 24 * 3600)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
PASSWORD_RESET_TIMEOUT = env_int("PASSWORD_RESET_TIMEOUT_SECONDS", 3600)
INVITE_TOKEN_TIMEOUT = env_int("INVITE_TOKEN_TIMEOUT_SECONDS", 72 * 3600)

# --- Rate limits ---
LOGIN_RATE_LIMIT = env_int("LOGIN_RATE_LIMIT", 5)
LOGIN_RATE_WINDOW = env_int("LOGIN_RATE_WINDOW_SECONDS", 900)
PASSWORD_RESET_RATE_LIMIT = env_int("PASSWORD_RESET_RATE_LIMIT", 5)
PASSWORD_RESET_RATE_WINDOW = env_int("PASSWORD_RESET_RATE_WINDOW_SECONDS", 3600)
TRUST_X_FORWARDED_FOR = env_bool("DJANGO_TRUST_X_FORWARDED_FOR", False)

# Production defaults to the shared database cache so rate limits hold across gunicorn workers.
if env("CACHE_BACKEND", "db" if IS_PRODUCTION else "locmem") == "db":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "django_cache"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "seo-dashboard"}}

# --- Security headers ---
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:", "blob:"],  # blob: = local preview of a logo before upload
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

if IS_PRODUCTION:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
    if env_bool("DJANGO_BEHIND_PROXY", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]

# --- Email ---
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 15)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "SEO Dashboard <no-reply@localhost>")

# --- Internationalisation ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# --- Static files ---
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_PRODUCTION
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

if not IS_PRODUCTION:
    # Serve straight from STATICFILES_DIRS in development; `collectstatic` is only needed for production.
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True
    import warnings

    warnings.filterwarnings("ignore", message="No directory at", module="whitenoise.base")

# --- Uploaded files (brand logos). Served only through authenticated views, never publicly. ---
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
BRAND_LOGO_MAX_BYTES = env_int("BRAND_LOGO_MAX_BYTES", 2 * 1024 * 1024)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PAGE_SIZE = 15

# --- Logging: JSON lines to stdout ---
LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.logging.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
        # Tests format every record (catching bad `extra=` keys) but print nothing.
        "null": {"class": "logging.NullHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO" if not TESTING else "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING" if not TESTING else "CRITICAL", "propagate": False},
        "seo": {"handlers": ["console"] if not TESTING else ["null"], "level": LOG_LEVEL if not TESTING else "DEBUG", "propagate": False},
    },
}
