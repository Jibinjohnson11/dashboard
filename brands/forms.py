from django import forms

from brands.services import STATUS_CHOICES
from core.forms import HttpURLField, clean_phone, collapse_spaces


class BrandForm(forms.Form):
    name = forms.CharField(label="Brand name", max_length=120)
    website = HttpURLField(label="Website URL", required=False)
    industry = forms.CharField(max_length=100, required=False)
    contact_name = forms.CharField(label="Contact person", max_length=120, required=False)
    contact_email = forms.EmailField(label="Contact email", max_length=254, required=False)
    contact_phone = forms.CharField(label="Contact phone", max_length=25, required=False)
    description = forms.CharField(max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial="active")

    def clean_name(self):
        name = collapse_spaces(self.cleaned_data["name"])
        if len(name) < 2:
            raise forms.ValidationError("Brand name must be at least 2 characters.")
        return name

    def clean_industry(self):
        return collapse_spaces(self.cleaned_data["industry"])

    def clean_contact_name(self):
        return collapse_spaces(self.cleaned_data["contact_name"])

    def clean_contact_email(self):
        return self.cleaned_data["contact_email"].strip().lower()

    def clean_contact_phone(self):
        return clean_phone(self.cleaned_data["contact_phone"])

    def clean_description(self):
        return self.cleaned_data["description"].strip()
