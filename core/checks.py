"""Extra `manage.py check --deploy` warnings for production misconfiguration.

These don't stop the app; they make common deployment mistakes visible before go-live.
"""
import os
import tempfile

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def production_configuration(app_configs, **kwargs):
    if not getattr(settings, "IS_PRODUCTION", False):
        return []
    warnings = []
    if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
        warnings.append(Warning(
            "Production is using SQLite for logins and sessions.",
            hint="Set DATABASE_URL to a PostgreSQL database.", id="seo.W001"))
    if any(host in settings.MONGODB_URI for host in ("127.0.0.1", "localhost")):
        warnings.append(Warning(
            "MONGODB_URI points at this machine.",
            hint="Use your MongoDB Atlas (or managed MongoDB) connection string.", id="seo.W002"))
    if settings.EMAIL_BACKEND.endswith(("console.EmailBackend", "locmem.EmailBackend", "dummy.EmailBackend")):
        warnings.append(Warning(
            "Emails (password resets, invitations) are not actually sent.",
            hint="Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend and the EMAIL_* settings.", id="seo.W003"))
    if not settings.CACHES["default"]["BACKEND"].endswith(("DatabaseCache", "RedisCache", "PyMemcacheCache")):
        warnings.append(Warning(
            "Rate limiting uses a per-process cache, so limits don't hold across workers.",
            hint="Set CACHE_BACKEND=db and run `python manage.py createcachetable`.", id="seo.W004"))
    media = settings.MEDIA_ROOT
    try:
        os.makedirs(media, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=media):
            pass
    except OSError:
        warnings.append(Warning(
            f"Brand logos cannot be saved: MEDIA_ROOT ({media}) is not writable.",
            hint="Point MEDIA_ROOT at a persistent, writable volume.", id="seo.W005"))
    if not settings.CSRF_TRUSTED_ORIGINS:
        warnings.append(Warning(
            "DJANGO_CSRF_TRUSTED_ORIGINS is empty.",
            hint="Add your site's https:// origin, e.g. https://seo.example.com.", id="seo.W006"))
    return warnings
