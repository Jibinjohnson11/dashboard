"""Shared form fields and widgets."""
import re

from django import forms
from django.core.validators import URLValidator

http_url_validator = URLValidator(schemes=["http", "https"], message="Enter a valid http:// or https:// URL.")
PHONE_RE = re.compile(r"^[0-9+()\-.\s]{6,25}$")


class HttpURLField(forms.URLField):
    default_validators = [http_url_validator]

    def __init__(self, **kwargs):
        kwargs.setdefault("assume_scheme", "https")
        kwargs.setdefault("max_length", 500)
        super().__init__(**kwargs)


class OptionDataSelect(forms.Select):
    """Select whose <option>s carry data-* attributes, e.g. data-brand for client-side filtering."""

    def __init__(self, *args, option_data=None, **kwargs):
        self.option_data = option_data or {}
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        for key, val in self.option_data.get(str(value), {}).items():
            option["attrs"][f"data-{key}"] = val
        return option


def clean_phone(value):
    value = (value or "").strip()
    if value and not PHONE_RE.match(value):
        raise forms.ValidationError("Enter a valid phone number.")
    return value


def collapse_spaces(value):
    return " ".join((value or "").split())
