from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Account-setup links for invited users.

    Uses its own salt, so password-reset and invite tokens are not interchangeable,
    and its own (longer) lifetime. Like reset tokens, it is invalidated as soon as
    the user sets a password or logs in.
    """

    key_salt = "seo_dashboard.accounts.tokens.InviteTokenGenerator"

    @property
    def timeout(self):
        return getattr(settings, "INVITE_TOKEN_TIMEOUT", 72 * 3600)

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        return (self._num_seconds(self._now()) - ts) <= self.timeout


invite_token_generator = InviteTokenGenerator()
