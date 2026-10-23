from django import forms
from django.utils import timezone

from core.forms import HttpURLField


class PublicationForm(forms.Form):
    content_id = forms.ChoiceField(label="Approved content")
    published_url = HttpURLField(label="Publication URL")
    published_by_user_id = forms.TypedChoiceField(label="Published by", coerce=int)
    published_date = forms.DateField(label="Published date", widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(label="Publication notes", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, content_choices=None, publisher_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["published_by_user_id"].choices = list(publisher_choices)
        self.fields["published_date"].widget.attrs["max"] = timezone.localdate().isoformat()
        if content_choices is None:
            del self.fields["content_id"]
        else:
            self.fields["content_id"].choices = [("", "Select approved content")] + list(content_choices)

    def clean_notes(self):
        return self.cleaned_data["notes"].strip()

    def clean_published_url(self):
        return self.cleaned_data["published_url"].strip()


class PublicationFilterForm(forms.Form):
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)
