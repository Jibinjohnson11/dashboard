import re

from django.core.exceptions import ValidationError


class CharacterClassValidator:
    """Require at least one letter and one digit or symbol."""

    def validate(self, password, user=None):
        if not re.search(r"[A-Za-z]", password) or not re.search(r"[^A-Za-z]", password):
            raise ValidationError(
                "Your password must contain at least one letter and at least one number or symbol.",
                code="password_character_classes",
            )

    def get_help_text(self):
        return "Your password must contain at least one letter and at least one number or symbol."
