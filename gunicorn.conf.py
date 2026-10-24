"""Gunicorn settings for production. Override any value with GUNICORN_* env vars or CLI flags."""
import multiprocessing
import os

from gunicorn.glogging import Logger

# Gunicorn puts the working directory on sys.path before loading this file.
from core.logging import RedactTokensFilter, redact_tokens

_redact_filter = RedactTokensFilter()


class TokenRedactingLogger(Logger):
    """Masks password-reset/invite link tokens in the access log and the error log."""

    def setup(self, cfg):
        super().setup(cfg)
        for log in (self.error_log, self.access_log):
            log.addFilter(_redact_filter)  # same instance each time, so re-setup (SIGHUP) doesn't stack filters

    def atoms(self, resp, req, environ, request_time):
        # Also mask the atoms themselves, for any custom access_log_format or handler that reads them directly.
        return {key: redact_tokens(value) for key, value in super().atoms(resp, req, environ, request_time).items()}


bind = os.environ.get("GUNICORN_BIND", f"0.0.0.0:{os.environ.get('PORT', '8000')}")
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 8)))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = 30
keepalive = 5
max_requests = 1000          # recycle workers periodically
max_requests_jitter = 100
accesslog = "-"
logger_class = TokenRedactingLogger
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
