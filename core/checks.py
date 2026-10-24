"""Production misconfiguration checks.

`production_configuration` adds `manage.py check --deploy` warnings that don't stop the app.
`production_email` returns errors on every check run, including `migrate` in the start command,
so a deploy whose email setup would leak links or credentials fails before gunicorn starts.
"""
import os
import tempfile

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

NON_DELIVERING_EMAIL_BACKENDS = ("console.EmailBackend", "locmem.EmailBackend", "dummy.EmailBackend", "filebased.EmailBackend")


@register(Tags.security)
def production_email(app_configs, **kwargs):
    """Never print reset/invite links to logs, and never send SMTP credentials unencrypted. Names only, no values."""
    if not getattr(settings, "IS_PRODUCTION", False):
        return []
    errors = []
    if settings.EMAIL_BACKEND.endswith(NON_DELIVERING_EMAIL_BACKENDS):
        errors.append(Error(
            "EMAIL_BACKEND does not deliver email; the console backend would write reset and invite links to the logs.",
            hint="Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend and the EMAIL_* settings.", id="seo.E001"))
        return errors
    if settings.EMAIL_BACKEND.endswith("smtp.EmailBackend"):
        if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
            errors.append(Error(
                "EMAIL_USE_TLS and EMAIL_USE_SSL are both enabled.",
                hint="Use EMAIL_USE_TLS=true for STARTTLS (ports 587/2525) or EMAIL_USE_SSL=true for port 465, not both.",
                id="seo.E002"))
        elif not (settings.EMAIL_USE_TLS or settings.EMAIL_USE_SSL):
            errors.append(Error(
                "SMTP is configured without TLS, so the password and messages would be sent unencrypted.",
                hint="Set EMAIL_USE_TLS=true (STARTTLS) or EMAIL_USE_SSL=true.", id="seo.E003"))
    if settings.DEFAULT_FROM_EMAIL.rstrip(">").lower().endswith("@localhost"):
        errors.append(Error(
            "DEFAULT_FROM_EMAIL is not set to a real sender address.",
            hint="Set DEFAULT_FROM_EMAIL to the sender verified with your email provider.", id="seo.E004"))
    return errors


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
    # seo.W003 (email not delivered) is now the error seo.E001 in production_email.
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
