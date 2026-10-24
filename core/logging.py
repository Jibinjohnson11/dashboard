import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import unquote

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

# Password-reset and invite links carry a one-time token: accounts/(reset|invite)/<uidb64>/<token>/.
# Django swaps it for "set-password"/"set-up" after the first request, so those stay readable.
_TOKEN_URL = re.compile(
    r"(?<![A-Za-z0-9_-])accounts/(?:reset|invite)/[A-Za-z0-9_-]+/"
    r"(?!(?:set-password|set-up)(?![A-Za-z0-9_-]))([A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
REDACTED = "<redacted>"
REDACTED_VALUE = "<redacted: contained an account link token>"


def _decoded_forms(value, depth=3):
    """The value plus up to `depth` levels of percent-decoding, for detection only (never logged)."""
    forms = [value]
    for _ in range(depth):
        decoded = unquote(forms[-1])
        if decoded == forms[-1]:
            break
        forms.append(decoded)
    return forms


def redact_tokens(value):
    """Mask reset/invite link tokens in a string, including percent-encoded forms (e.g. ?next=%2Faccounts%2F...).

    Only the token is replaced; the rest of the value is returned as it was, never in decoded form.
    If a token is itself percent-encoded and can't be masked in place, the whole value is replaced.
    Non-strings are returned unchanged.
    """
    if not isinstance(value, str) or ("%" not in value and "accounts/" not in value.lower()):
        return value
    tokens = {m.group(1) for form in _decoded_forms(value) for m in _TOKEN_URL.finditer(form)}
    for token in sorted(tokens, key=len, reverse=True):
        value = value.replace(token, REDACTED)
    if any(_TOKEN_URL.search(form) for form in _decoded_forms(value)):
        return REDACTED_VALUE
    return value


class RedactTokensFilter(logging.Filter):
    """Masks link tokens in a record's message, arguments, traceback and stack before any handler formats it."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # arguments don't fit the message: drop them rather than let logging print them raw
            message = f"{record.msg} (log arguments omitted)"
        record.msg, record.args = redact_tokens(message), ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.exc_text = redact_tokens(record.exc_text)
        record.stack_info = redact_tokens(record.stack_info)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with any ``extra=`` fields included. Link tokens are masked in every field."""

    def format(self, record):
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        if record.exc_text or record.exc_info:
            payload["exc"] = record.exc_text or self.formatException(record.exc_info)
        # Structured fields (e.g. Django's `request`) are only masked here, after they've been turned into text.
        return json.dumps({k: redact_tokens(v) for k, v in payload.items()}, default=str)
