"""Production email safeguards and log redaction of password-reset/invite tokens. No email is sent and nothing connects."""
import io
import logging
import os
import runpy
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings

from core.checks import production_email
from core.logging import REDACTED_VALUE, redact_tokens

BASE_DIR = Path(settings.BASE_DIR)
BREVO = {
    "IS_PRODUCTION": True,
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp-relay.brevo.com",
    "EMAIL_PORT": 2525,
    "EMAIL_USE_TLS": True,
    "EMAIL_USE_SSL": False,
    "EMAIL_HOST_USER": "dummy-login@example.com",
    "EMAIL_HOST_PASSWORD": "dummy-smtp-key",
    "DEFAULT_FROM_EMAIL": "SEO Dashboard <sender@example.com>",
}
TOKEN = "cz1k9q-0123456789abcdef0123456789abcdef"
RESET_PATH = f"/accounts/reset/MQ/{TOKEN}/"


def error_ids(**overrides):
    with override_settings(**{**BREVO, **overrides}):
        return [e.id for e in production_email(None)]


class ProductionEmailCheckTests(SimpleTestCase):
    def test_brevo_smtp_over_starttls_passes(self):
        self.assertEqual(error_ids(), [])

    def test_console_backend_is_an_error(self):
        self.assertEqual(error_ids(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"), ["seo.E001"])

    def test_tls_and_ssl_together_is_an_error(self):
        self.assertEqual(error_ids(EMAIL_USE_SSL=True), ["seo.E002"])

    def test_unencrypted_smtp_is_an_error(self):
        self.assertEqual(error_ids(EMAIL_USE_TLS=False), ["seo.E003"])

    def test_localhost_sender_is_an_error(self):
        self.assertEqual(error_ids(DEFAULT_FROM_EMAIL="SEO Dashboard <no-reply@localhost>"), ["seo.E004"])

    def test_messages_never_contain_credentials(self):
        with override_settings(**{**BREVO, "EMAIL_USE_SSL": True}):
            text = " ".join(f"{e.msg} {e.hint}" for e in production_email(None))
        self.assertNotIn(BREVO["EMAIL_HOST_PASSWORD"], text)
        self.assertNotIn(BREVO["EMAIL_HOST_USER"], text)

    def test_development_is_not_checked(self):
        self.assertEqual(error_ids(IS_PRODUCTION=False, EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"), [])

    def test_production_defaults_to_smtp_not_console(self):
        env = {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "dummy-" * 10, "MONGODB_USE_MOCK": "false"}
        with mock.patch.dict(os.environ, env), mock.patch("dotenv.load_dotenv"), mock.patch.object(sys, "argv", ["manage.py"]):
            os.environ.pop("EMAIL_BACKEND", None)
            namespace = runpy.run_path(str(BASE_DIR / "config" / "settings.py"))
        self.assertEqual(namespace["EMAIL_BACKEND"], "django.core.mail.backends.smtp.EmailBackend")


class RedactTokensTests(SimpleTestCase):
    def test_masks_tokens_in_every_url_form(self):
        cases = {
            f"GET {RESET_PATH} HTTP/1.1": "GET /accounts/reset/MQ/<redacted>/ HTTP/1.1",
            f"/accounts/invite/Mg/{TOKEN}": "/accounts/invite/Mg/<redacted>",
            f"/accounts/login/?next=/accounts/invite/Mg/{TOKEN}/": "/accounts/login/?next=/accounts/invite/Mg/<redacted>/",
            f"/accounts/login/?next=%2Faccounts%2Freset%2FMQ%2F{TOKEN}%2F": "/accounts/login/?next=%2Faccounts%2Freset%2FMQ%2F<redacted>%2F",
            f"/x/?next=%252faccounts%252freset%252fMQ%252f{TOKEN}": "/x/?next=%252faccounts%252freset%252fMQ%252f<redacted>",
            f"/accounts/reset/MQ%2F{TOKEN}/": "/accounts/reset/MQ%2F<redacted>/",
            f"https://dash.example.com/ACCOUNTS/reset/MQ/{TOKEN}/": "https://dash.example.com/ACCOUNTS/reset/MQ/<redacted>/",
            f"{{'path': 'accounts/reset/MQ/{TOKEN}/'}}": "{'path': 'accounts/reset/MQ/<redacted>/'}",
        }
        for raw, expected in cases.items():
            self.assertEqual(redact_tokens(raw), expected, raw)

    def test_percent_encoded_token_replaces_the_whole_value(self):
        encoded = "".join(f"%{ord(c):02X}" for c in TOKEN)
        self.assertEqual(redact_tokens(f"/accounts/reset/MQ/{encoded}/"), REDACTED_VALUE)

    def test_leaves_safe_and_unrelated_values_alone(self):
        for value in ("/accounts/reset/MQ/set-password/", "/accounts/invite/Mg/set-up/", "/accounts/reset/complete/",
                      "/brands/?q=caf%C3%A9%2Fbar", "Not Found: /brands/42/", None, 404):
            self.assertEqual(redact_tokens(value), value)


@contextmanager
def captured(handler):
    buffer = io.StringIO()
    old = handler.setStream(buffer)
    try:
        yield buffer
    finally:
        handler.setStream(old)


class DjangoLogRedactionTests(TestCase):
    """Through the real LOGGING configuration: the `django` logger's console handler, filter and JSON formatter."""

    def setUp(self):
        self.handler = logging.getLogger("django").handlers[0]
        request_logger = logging.getLogger("django.request")
        self.addCleanup(request_logger.setLevel, request_logger.level)
        request_logger.setLevel(logging.WARNING)  # tests quiet `django` to ERROR

    def test_404_log_has_no_token_but_keeps_the_path(self):
        with captured(self.handler) as out:
            self.assertEqual(Client().get(f"{RESET_PATH}extra/").status_code, 404)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertIn("Not Found: /accounts/reset/MQ/<redacted>/extra/", out.getvalue())

    def test_500_log_and_traceback_have_no_token(self):
        boom = RuntimeError(f"failed while handling {RESET_PATH}")
        with mock.patch("accounts.views.PasswordResetConfirmView.dispatch", side_effect=boom), captured(self.handler) as out:
            self.assertEqual(Client(raise_request_exception=False).get(RESET_PATH).status_code, 500)
        log = out.getvalue()
        self.assertNotIn(TOKEN, log)
        self.assertIn("Internal Server Error: /accounts/reset/MQ/<redacted>/", log)
        self.assertIn("RuntimeError: failed while handling /accounts/reset/MQ/<redacted>/", log)

    def test_parameterized_structured_and_malformed_records(self):
        class FakeRequest:
            def __str__(self):
                return f"<WSGIRequest: GET '{RESET_PATH}'>"

        log = logging.getLogger("django.request")
        try:
            raise ValueError(f"bad link {RESET_PATH}")
        except ValueError:
            exc_info = sys.exc_info()
        records = [
            log.makeRecord(log.name, logging.ERROR, __file__, 1, "Link %s failed for user %s", (RESET_PATH, 7), None,
                           extra={"path": RESET_PATH, "request": FakeRequest()}),
            log.makeRecord(log.name, logging.ERROR, __file__, 1, "Unhandled", (), exc_info),
            log.makeRecord(log.name, logging.ERROR, __file__, 1, "Two %s %s", (RESET_PATH,), None),
        ]
        with captured(self.handler) as out:
            for record in records:
                self.handler.handle(record)
        text = out.getvalue()
        self.assertNotIn(TOKEN, text)
        self.assertIn("Link /accounts/reset/MQ/<redacted>/ failed for user 7", text)
        self.assertIn("ValueError: bad link /accounts/reset/MQ/<redacted>/", text)
        self.assertIn("Two %s %s (log arguments omitted)", text)


class GunicornLogRedactionTests(SimpleTestCase):
    def setUp(self):
        from gunicorn.config import Config

        self.conf = conf = runpy.run_path(str(BASE_DIR / "gunicorn.conf.py"))
        self.assertIs(conf["logger_class"], conf["TokenRedactingLogger"])
        self.cfg = Config()
        self.cfg.set("accesslog", "-")
        self.cfg.set("errorlog", "-")
        self.logger = conf["TokenRedactingLogger"](self.cfg)
        self.logger.setup(self.cfg)  # a second setup (as on SIGHUP) must not stack filters
        self.out = io.StringIO()
        for handler in self.logger.access_log.handlers + self.logger.error_log.handlers:
            handler.setStream(self.out)

    def test_access_log_masks_path_query_and_referer(self):
        environ = {"REQUEST_METHOD": "GET", "RAW_URI": f"{RESET_PATH}?next=%2Faccounts%2Finvite%2FMg%2F{TOKEN}",
                   "PATH_INFO": RESET_PATH, "QUERY_STRING": f"next=%2Faccounts%2Finvite%2FMg%2F{TOKEN}",
                   "SERVER_PROTOCOL": "HTTP/1.1", "REMOTE_ADDR": "203.0.113.5",
                   "HTTP_REFERER": f"https://dash.example.com{RESET_PATH}", "HTTP_USER_AGENT": "test"}
        resp = SimpleNamespace(status="302 Found", headers=[("Location", "/accounts/reset/MQ/set-password/")], sent=0)
        req = SimpleNamespace(headers=[("REFERER", environ["HTTP_REFERER"])])
        self.logger.access(resp, req, environ, timedelta(milliseconds=12))
        line = self.out.getvalue()
        self.assertNotIn(TOKEN, line)
        self.assertIn('"GET /accounts/reset/MQ/<redacted>/?next=%2Faccounts%2Finvite%2FMg%2F<redacted> HTTP/1.1" 302', line)

    def test_error_log_masks_uri_and_exception(self):
        try:
            raise RuntimeError(f"worker crashed on {RESET_PATH}")
        except RuntimeError:
            self.logger.exception("Error handling request %s", RESET_PATH)
        text = self.out.getvalue()
        self.assertNotIn(TOKEN, text)
        self.assertIn("Error handling request /accounts/reset/MQ/<redacted>/", text)
        self.assertIn("RuntimeError: worker crashed on /accounts/reset/MQ/<redacted>/", text)
        self.assertEqual(self.logger.error_log.filters.count(self.conf["_redact_filter"]), 1)
